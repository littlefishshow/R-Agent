# Autoresearch Program: string_matcher

## Goal
Optimize multi-pattern string matching on deterministic local data.

## Fixed evaluation
Do not modify `eval.py`, `eval.sh`, or generated `data/test_cases.json` during optimization.

## Task
For each test case, count how many times each pattern appears in the text, including overlapping occurrences.

## Baseline
`train/train.py` writes a naive matcher checking every pattern at every position.

## Optimization target
Maximize `primary_metric=score`: exact accuracy is weighted heavily; runtime is secondary.

## Commands
```bash
python prepare.py
bash train/train.sh
bash eval.sh
cat metrics.json
```

## Allowed modifications
- `train/train.py`
- generated `submission/matcher.py`
- reports/docs

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `score`
- `higher_is_better`: `true`
- `score >= 0.99`

Rationale: All fixed counts correct and runtime fast enough that the score is effectively maximal.
