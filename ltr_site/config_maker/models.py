# Copyright 2013 Yandex
"""This file contains all the models used in LTR Site."""

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.test.client import Client

import subprocess
import threading
import itertools
import os

from file_utility import get_unique_filename, ensure_path_exists


class cached_property(object):

    """Decorator that converts a method with a single self argument into a
    property cached on the instance.

    Taken from http://habrahabr.ru/post/159099/.
    In Django >= 1.4 can be found in django.utils.functional.

    """

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, type=None):
        result = instance.__dict__[self.func.__name__] = self.func(instance)
        return result


class ObjectTypeController:
    """Manages object hierarchy and registered types."""
    _registered_types = {}

    @cached_property
    def categories(self):
        return self._registered_types.keys()

    @cached_property
    def all_object_types(self):
        return tuple(itertools.chain.from_iterable(
            self._registered_types.itervalues()))

    def get_object_types(self, category):
        return self._registered_types.get(category, [])

    def register(self, category, type_):
        if not (category in self._registered_types):
            self._registered_types[category] = []
        self._registered_types[category].append(type_)

object_controller = ObjectTypeController()


def get_current_solution(request_or_client):
    """Returns solution belonging to current user (or anonymous).

    Works for Request and Client objects. Returns None if client has neither
    login nor session."""
    if isinstance(request_or_client, Client):
        client = request_or_client
        if '_auth_user_id' in client.session:
            user = User.objects.get(pk=client.session['_auth_user_id'])
            if not hasattr(user, 'solution'):
                solution = Solution(user=user, session=None)
                solution.save()
                createSSCChoices(solution)
            return user.solution
        else:
            if hasattr(client.session, 'session_key'):
                session = Session.objects.get(pk=client.session.session_key)
                return session.solution
            else:
                return None
    else:
        request = request_or_client
        if request.user.is_authenticated():
            if not hasattr(request.user, 'solution'):
                solution = Solution(user=request.user, session=None)
                solution.save()
                createSSCChoices(solution)
            return request.user.solution
        else:
            if not request.session.exists(request.session.session_key):
                request.session.create()
            session = Session.objects.get(pk=request.session.session_key)
            if not hasattr(session, 'solution'):
                solution = Solution(user=None, session=session)
                solution.save()
                createSSCChoices(solution)
            return session.solution


class Solution(models.Model):
    """User's solution containing group of objects."""
    user = models.OneToOneField(User, blank=True, null=True)
    session = models.OneToOneField(Session, blank=True, null=True)

    def get_objects(self, object_type):
        """Returns objects belonging to this solution."""
        return object_type.objects.filter(solution=self)

    def get_content_filename(self, filename):
        """Creates filename for file being saved to disk."""
        if self.user is not None:
            username = self.user.username
        else:
            username = 'Anonymous'
        path = os.path.join(username, 'data')
        ensure_path_exists(settings.MEDIA_ROOT + path)
        return get_unique_filename(os.path.join(path, filename))

    def export_objects(self):
        """Exports all the objects to JSON."""
        raise NotImplementedError()

    def import_objects(self, data):
        """Imports all the objects from JSON."""
        raise NotImplementedError()


def choices(list):
    return zip(list, list)

APPROACH_CHOICES = choices(('pointwise', 'pairwise', 'listwise'))

FORMAT_CHOICES = choices(('Yandex', 'ARFF', 'SVMLIGHT', 'CSV', 'TSV'))

METRIC_CHOICES = choices(('EUCLIDEAN_METRIC', 'MANHATTAN_METRIC'))

NEIGHBOR_WEIGHTER_CHOICES = choices(('INVERSE_LINEAR_DISTANCE',
                                     'INVERSE_POWER_DISTANCE',
                                     'INVERSE_ORDER'))

PREDICTIONS_AGGREGATOR_CHOICES = choices((
    'AVERAGE_PREDICTIONS_AGGREGATOR',
    'SUM_PREDICTIONS_AGGREGATOR',
    'VOTE_PREDICTIONS_AGGREGATOR',
    'ORDER_STATISTIC_PREDICTIONS_AGGREGATOR',
    'MAX_WEIGHT_PREDICTIONS_AGGREGATOR'))

BASE_SPLITTER_CHOICES = choices(('ID3_SPLITTER', 'OBLIVIOUS_TREE_SPLITTER'))

LEAF_GENERATOR_CHOICES = choices(('MOST_COMMON_LABEL_LEAF_GENERATOR',))

MAX_FILE_PATH_LENGTH = 260
MAX_STRING_LENGTH = 45


class Task(models.Model):

    """Stores information about launched ltr_client, its config and results."""

    solution = models.ForeignKey(Solution)
    config_filename = models.CharField(max_length=MAX_FILE_PATH_LENGTH,
                                       unique=False)
    log_filename = models.CharField(max_length=MAX_FILE_PATH_LENGTH,
                                    unique=False)
    report_filename = models.CharField(max_length=MAX_FILE_PATH_LENGTH,
                                       unique=True)
    is_complete = models.BooleanField()
    working_dir = ""

    @staticmethod
    def create(solution, launchable_name):
        """Creates a Task instance."""
        task = Task()
        task.solution = solution
        task.working_dir = get_unique_filename(
            settings.MEDIA_ROOT +
            '/'.join([task.solution.user.username, 'task']))

        ensure_path_exists(task.working_dir)
        task.config_filename = get_unique_filename(task.working_dir +
                                                   '/config.xml')
        task.log_filename = get_unique_filename(task.working_dir + '/log.txt')
        file_config = open(task.config_filename, "w")
        file_config.write(task.make_config(launchable_name))
        file_config.close()
        task.report_filename = get_unique_filename(
            task.working_dir + '/report.html')
        open(task.report_filename, 'w').close()
        task.is_complete = False
        task.save()
        return task

    def make_config(self, launchable_name):
        """Creates XML config for ltr_client."""
        from django.template.loader import render_to_string

        db_objects = self.solution.get_objects(LtrObject)
        ltr_objects = tuple(obj.cast() for obj in db_objects)

        db_datas = self.solution.get_objects(Data)
        ltr_datas = tuple(data.cast() for data in db_datas)

        all_objects = self.solution.get_objects(BaseObject)
        launchable = all_objects.get(name=launchable_name).cast()

        ltr_trains, ltr_crossvalidations = (), ()
        if launchable in self.solution.get_objects(Train):
            ltr_trains = (launchable,)
        elif launchable in self.solution.get_objects(CrossValidation):
            ltr_crossvalidations = (launchable,)

        return render_to_string('config.xml',
                                {'objects': ltr_objects,
                                 'datas': ltr_datas,
                                 'trains': ltr_trains,
                                 'crossvalidations': ltr_crossvalidations,
                                 'root_directory': self.working_dir})

    def run(self):
        """Runs ltr_client."""
        def run_in_thread(popen_args, task):
            process = subprocess.Popen(popen_args)
            process.wait()
            task.is_complete = True
            task.save()

        popen_args = [settings.LTR_CLIENT_PATH,
                      self.config_filename,
                      '-w',
                      '-d',
                      '-l', self.log_filename]
        thread = threading.Thread(target=run_in_thread,
                                  args=(popen_args, self))
        thread.start()


class InheritanceCastModel(models.Model):

    """An abstract base class that provides a "real_type" FK to ContentType.

    For use in trees of inherited models, to be able to downcast
    parent instances to their child types.

    Taken from http://stackoverflow.com/questions/929029/how-do-i-access\
-the-child-classes-of-an-object-in-django-without-knowing-the-nam

    """

    real_type = models.ForeignKey(ContentType, editable=False)

    def save(self, *args, **kwargs):
        if not self.id:
            self.real_type = self._get_real_type()
        super(InheritanceCastModel, self).save(*args, **kwargs)

    def _get_real_type(self):
        return ContentType.objects.get_for_model(type(self))

    def cast(self):
        """  Downcasts object to its real type """
        return self.real_type.get_object_for_this_type(pk=self.pk)

    class Meta:
        abstract = True


OBJECT_NAME_REGEX = '^[A-Za-z]\w{0,29}$'


class BaseObject(InheritanceCastModel):

    """Base class in object hierarchy."""

    solution = models.ForeignKey(Solution)

    name = models.CharField(
        max_length=MAX_STRING_LENGTH,
        validators=[RegexValidator(
            regex=OBJECT_NAME_REGEX,
            code='invalid',
            message='Name must be alphanumeric, max 30 symbols,letter first')])

    def __unicode__(self):
        return self.name

    @staticmethod
    def __is_auxiliary_field(field):
        return field.name in ('id', 'solution', 'real_type')

    @staticmethod
    def __is_pointer_to_base_class(field):
        return field.name.endswith('_ptr')

    def get_properties(self):
        """Returns object properties that should be stored in XML config."""

        fields = {}
        for field in self._meta.fields:
            if not (self.__is_auxiliary_field(field) or
                    self.__is_pointer_to_base_class(field)):
                value = getattr(self, field.name)
                if isinstance(value, BaseObject):
                    value = value.name
                fields[field.name] = value
        for field in self._meta.many_to_many:
            value = getattr(self, field.name)
            names = [item.name for item in value.all()]
            fields[field.name] = ','.join(names)
        return fields

    def clean(self):
        super(BaseObject, self).clean()

        names = self.solution.get_objects(BaseObject).values_list('name',
                                                                  flat=True)
        lowercase_names = [name.lower() for name in names]
        if self.name.lower() in lowercase_names:
            raise ValidationError("This name is already used.")
        for field in self._meta.fields:
            if not (self.__is_auxiliary_field(field) or
                    self.__is_pointer_to_base_class(field)):
                value = getattr(self, field.name)
                if (isinstance(value, BaseObject) and
                        value.solution != self.solution):
                    raise ValidationError("Related object belongs to other " +
                                          "user.")

    class Meta:
        unique_together = (("name", "solution"),)


@receiver(m2m_changed)
def check_m2m_solution_accordance(sender,
                                  instance,
                                  action,
                                  reverse,
                                  model,
                                  pk_set,
                                  **kwargs):
    if action != 'pre_add' or not hasattr(instance, 'solution'):
        return
    objects = model.objects.filter(pk__in=pk_set)
    for second_object in objects:
        if (hasattr(second_object, 'solution') and
                instance.solution != second_object.solution):
            raise ValidationError("Related object(s) belongs to other user.")


class LtrObject(BaseObject):

    """Base class for objects that should be turned into <object> tag in XML
    config.

    """

    pass


def type_decorator(cls):
    """Class decorator used to register objects in object hierarchy."""
    cls.get_type = staticmethod(lambda: cls.__name__)
    object_controller.register(cls.get_category(), cls.__name__)
    return cls


def category_decorator(cls):
    """Class decorator used to register categories in object hierarchy."""
    cls.get_category = staticmethod(lambda: cls.__name__.lower())
    return cls


@category_decorator
class Launch(BaseObject):
    pass


@category_decorator
class Data(BaseObject):
    pass


@category_decorator
class Measure(LtrObject):
    pass


@category_decorator
class Learner(LtrObject):
    pass


@category_decorator
class Splitter(LtrObject):
    pass


## LAUNCHABLE OBJECTS

@type_decorator
class Train(Launch):
    train_data = models.ForeignKey(Data, related_name='+')
    learner = models.ForeignKey(Learner)
    predict = models.ManyToManyField(Data)
    generate_cpp = models.BooleanField()


@type_decorator
class CrossValidation(Launch):
    data = models.ForeignKey(Data)
    learners = models.ManyToManyField(Learner)
    measures = models.ManyToManyField(Measure)
    splitter = models.ForeignKey(Splitter)


## DATA FILES

@type_decorator
class File(Data):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    format = models.CharField(max_length=MAX_STRING_LENGTH,
                              choices=FORMAT_CHOICES)
    file = models.FileField(
        upload_to=lambda x, y: x.solution.get_content_filename(y))


## MEASURES

@type_decorator
class AbsError(Measure):
    pass


@type_decorator
class NDCG(Measure):
    number_of_objects_to_consider = models.PositiveIntegerField()


@type_decorator
class DCG(Measure):
    number_of_objects_to_consider = models.PositiveIntegerField()


@type_decorator
class Accuracy(Measure):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=choices(('pointwise', 'pairwise')))


@type_decorator
class AveragePrecision(Measure):
    score_for_relevant = models.DecimalField(decimal_places=5, max_digits=8)


@type_decorator
class BinaryClassificationAccuracy(Measure):
    pass


@type_decorator
class GMRR(Measure):
    max_label = models.DecimalField(decimal_places=5, max_digits=8)
    number_of_objects_to_consider = models.PositiveIntegerField()


@type_decorator
class NormalizedMeasure(Measure):
    weak_measure = models.ForeignKey(Measure, related_name='weak_measure')
    worst = models.DecimalField(decimal_places=5, max_digits=8)
    best = models.DecimalField(decimal_places=5, max_digits=8)


@type_decorator
class PFound(Measure):
    p_break = models.DecimalField(decimal_places=5, max_digits=8)
    max_label = models.DecimalField(decimal_places=5, max_digits=8)
    number_of_objects_to_consider = models.PositiveIntegerField()


@type_decorator
class ReciprocalRank(Measure):
    score_for_relevant = models.DecimalField(decimal_places=5, max_digits=8)


@type_decorator
class SquaredError(Measure):
    pass


@type_decorator
class TruePoint(Measure):
    pass


## LEARNERS

@type_decorator
class BestFeatureLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    measure = models.ForeignKey(Measure)


@type_decorator
class GPLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    population_size = models.PositiveIntegerField()
    number_of_generations = models.PositiveIntegerField()
    min_init_depth = models.PositiveIntegerField()
    max_init_depth = models.PositiveIntegerField()
    init_grow_probability = models.DecimalField(max_digits=4,
                                                decimal_places=3)
    seed = models.PositiveIntegerField()


@type_decorator
class NNLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    metric = models.CharField(max_length=MAX_STRING_LENGTH,
                              choices=METRIC_CHOICES)
    neighbor_weighter = models.CharField(
        max_length=MAX_STRING_LENGTH,
        choices=NEIGHBOR_WEIGHTER_CHOICES)
    predictions_aggregator = models.CharField(
        max_length=MAX_FILE_PATH_LENGTH,
        choices=PREDICTIONS_AGGREGATOR_CHOICES)
    number_of_neighbors_to_process = models.PositiveIntegerField()


@type_decorator
class LinearLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=choices(('pointwise', 'listwise')))


# This is a stub class designed to process stop splitting criteria choices
# in Desicion Tree Learner
class StopSplittingCriteriaChoice(models.Model):
    name = models.CharField(max_length=MAX_STRING_LENGTH)
    solution = models.ForeignKey(Solution)

    def __unicode__(self):
        return self.name


def registerSSCChoice(name, solution):
    obj = StopSplittingCriteriaChoice()
    obj.name = name
    obj.solution = solution
    obj.save()


def createSSCChoices(solution):
    registerSSCChoice('SAME_LABEL_STOP_SPLITTING_CRITERIA', solution)
    registerSSCChoice('DATA_SIZE_STOP_SPLITTING_CRITERIA', solution)


@type_decorator
class DecisionTreeLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    # Django requires that name 'splitter' is used
    # only for Splitter class instance
    # need to rewrite ltr in this part
    base_splitter = models.CharField(max_length=MAX_FILE_PATH_LENGTH,
                                     choices=BASE_SPLITTER_CHOICES)
    leaf_generator = models.CharField(max_length=MAX_STRING_LENGTH,
                                      choices=LEAF_GENERATOR_CHOICES)
    stop_splitting_criterias = models.ManyToManyField(
        StopSplittingCriteriaChoice)


@type_decorator
class FisherDiscriminantLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


@type_decorator
class QuadraticDiscriminantLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


@type_decorator
class NormalNaiveBayesLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


@type_decorator
class FakeFeatureConverterLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


@type_decorator
class FeatureNormalizerLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    min = models.PositiveIntegerField()
    max = models.PositiveIntegerField()


@type_decorator
class FeatureSamplerLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    indices = models.CharField(max_length=MAX_FILE_PATH_LENGTH)


@type_decorator
class NanToAverageConverterLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


@type_decorator
class NanToZeroConverterLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


@type_decorator
class NominalToBoolConverterLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


@type_decorator
class RemoveNominalConverterLearner(Learner):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)


## SPLITTERS

@type_decorator
class KFoldSimpleSplitter(Splitter):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    k = models.PositiveIntegerField()


@type_decorator
class TKFoldSimpleSplitter(Splitter):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
    k = models.PositiveIntegerField()
    t = models.PositiveIntegerField()


@type_decorator
class LeaveOneOutSplitter(Splitter):
    approach = models.CharField(max_length=MAX_STRING_LENGTH,
                                choices=APPROACH_CHOICES)
