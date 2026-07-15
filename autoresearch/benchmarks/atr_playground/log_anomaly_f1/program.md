# autoresearch program: log_anomaly_f1

## Research objective
Improve `solution.is_anomaly(line: str) -> bool` for deterministic synthetic service logs.

- Primary metric: positive_f1
- Direction: higher_is_better
- Baseline: simple severe keyword matching

## Allowed files
`solution.py`, `train/`, `artifacts/`.

## Fixed files
`eval.py`, `eval.sh`, cases and metric definitions.

## Baseline
```bash
python3 prepare.py
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json
```

## Experiment ideas
Add numeric threshold parsing for latency/error rates, HTTP 5xx detection, retry storm patterns, disk/memory pressure, and distinguish benign warning/info lines. Change one idea per round.

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `positive_f1`
- `higher_is_better`: `true`
- `positive_f1 >= 1`

Rationale: No false positives or false negatives on embedded log cases.
