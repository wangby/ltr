// Copyright 2012 Yandex

#ifndef LTR_LEARNERS_DECISION_TREE_DECISION_TREE_LEARNER_H_
#define LTR_LEARNERS_DECISION_TREE_DECISION_TREE_LEARNER_H_

#include <vector>
#include <string>
#include <functional>

#include "logog/logog.h"

#include "ltr/learners/learner.h"

#include "ltr/learners/decision_tree/utility/utility.h"
#include "ltr/learners/decision_tree/decision_vertex.h"
#include "ltr/learners/decision_tree/leaf_vertex.h"
#include "ltr/learners/decision_tree/splitting_quality.h"
#include "ltr/learners/decision_tree/conditions_learner.h"

#include "ltr/scorers/decision_tree_scorer.h"

using std::string;

namespace ltr {
namespace decision_tree {
/**
 * \class DecisionTreeLearner
 * Builds decision tree for given data.
 */
class DecisionTreeLearner : public BaseLearner<Object, DecisionTreeScorer> {
 public:
  void setDefaultParameters();

  void checkParameters() const;

  GET_SET(ConditionsLearner::Ptr, conditions_learner);
  GET_SET(SplittingQuality::Ptr, splitting_quality);
  GET_SET(int, min_vertex_size);
  GET_SET(double, label_eps);

  explicit DecisionTreeLearner(const ParametersContainer& parameters);

  string toString() const;

  explicit DecisionTreeLearner(
    int min_vertex_size = 3, double label_eps = 0.001);

 private:
  virtual void setParametersImpl(const ParametersContainer& parameters);
  /**
   * Function creates one decision or leaf vertex for given data.
   * Uses ConditionsLearner and SplittingQuality to create it.
   */
  Vertex<double>::Ptr createOneVertex(const DataSet<Object>& data);

  void learnImpl(const DataSet<Object>& data, DecisionTreeScorer* scorer);

  virtual string getDefaultAlias() const;

  int min_vertex_size_;
  double label_eps_;
  /**
   * Object, used to generate different conditions for splitting data set
   */
  ConditionsLearner::Ptr conditions_learner_;
  /**
   * Object, used to select the best split of the data set
   */
  SplittingQuality::Ptr splitting_quality_;
};
};
};

#endif  // LTR_LEARNERS_DECISION_TREE_DECISION_TREE_LEARNER_H_
