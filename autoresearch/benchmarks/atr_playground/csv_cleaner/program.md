# Autoresearch Program: csv_cleaner

## Goal
Clean a small locally generated dirty CSV dataset.

## Fixed evaluation
Do not modify `eval.py`, `eval.sh`, `data/truth.json`, or generated `data/dirty.csv` during optimization.

## Task
For each row, output normalized fields: `name`, `age`, `email`, and `state`.

## Baseline
`train/train.py` lowercases email and strips whitespace but misses title-casing names, removing punctuation, parsing ages like `34 years`, fixing `[at]`, and normalizing full state names to two-letter codes.

## Optimization target
Maximize `primary_metric=score`, combining row exact accuracy and cell F1.

## Commands
```bash
python prepare.py
bash train/train.sh
bash eval.sh
cat metrics.json
```

## Allowed modifications
- `train/train.py`
- generated `submission/cleaner.py`
- reports/docs

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `score`
- `higher_is_better`: `true`
- `score >= 0.99`

Rationale: Rows and cells normalized essentially perfectly on the fixed dataset.
