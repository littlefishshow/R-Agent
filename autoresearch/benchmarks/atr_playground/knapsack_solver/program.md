# Autoresearch Program: knapsack_solver

## Goal
Maximize exact optimality for deterministic 0/1 knapsack instances.

## Fixed evaluation
Do not modify `eval.py`, `eval.sh`, or generated `data/test_cases.json` during optimization.

## Task
For each instance with `capacity` and item list `{weight,value}`, output the optimal total value.

## Baseline
`train/train.py` writes a greedy value/weight-ratio solver. It is deliberately non-optimal on several cases.

## Optimization target
Maximize `primary_metric=score`; exact accuracy dominates, value ratio gives partial credit.

## Commands
```bash
python prepare.py
bash train/train.sh
bash eval.sh
cat metrics.json
```

## Allowed modifications
- `train/train.py`
- generated `submission/solver.py`
- reports/docs

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `score`
- `higher_is_better`: `true`
- `score >= 0.99`

Rationale: All fixed knapsack cases solved optimally; partial value credit is not enough.
