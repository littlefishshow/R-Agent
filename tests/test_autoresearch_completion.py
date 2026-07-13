from autoresearch.state.completion import is_metric_solved, parse_completion_criteria


def test_parse_completion_criteria_from_metric_expression():
    criteria = parse_completion_criteria(
        """
# Task

## Completion Criteria
- metric_name: z
- higher_is_better: false
- z <= 0.001
"""
    )

    assert criteria is not None
    assert criteria.metric_name == "z"
    assert criteria.operator == "<="
    assert criteria.threshold == 0.001
    assert criteria.higher_is_better is False
    assert is_metric_solved(0.0, criteria) is True
    assert is_metric_solved(1.0, criteria) is False


def test_no_completion_criteria_means_not_solved():
    assert parse_completion_criteria("Goal: improve the project") is None
    assert is_metric_solved(0.0, None) is False


def test_completion_criteria_after_generic_stop_conditions():
    criteria = parse_completion_criteria(
        """
# Task

## Stop conditions
Stop after the user-approved round budget or when no simple rule improves.

## Completion Criteria
This project is solved only when metrics.json reports:
- metric_name: exact_match_accuracy
- higher_is_better: true
- exact_match_accuracy >= 1
"""
    )

    assert criteria is not None
    assert criteria.metric_name == "exact_match_accuracy"
    assert criteria.operator == ">="
    assert criteria.threshold == 1.0
    assert criteria.higher_is_better is True
