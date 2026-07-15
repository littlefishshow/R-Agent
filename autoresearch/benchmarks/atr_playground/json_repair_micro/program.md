# autoresearch program: json_repair_micro

## Research objective
Improve `solution.repair_json(text: str) -> str` so it returns parseable JSON semantically equal to the expected object.

- Primary metric: repair_exact_accuracy
- Direction: higher_is_better
- Baseline: simple quote/trailing-comma fixes

## Allowed files
`solution.py`, `train/`, `artifacts/`.

## Fixed files
`eval.py`, `eval.sh`, embedded cases and metric definitions.

## Baseline
```bash
python3 prepare.py
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json
```

## Experiment ideas
Handle single quotes, unquoted keys, Python booleans/None, trailing commas, missing braces, comments, and quote balancing. Avoid overfitting by using general transformations.

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `repair_exact_accuracy`
- `higher_is_better`: `true`
- `repair_exact_accuracy >= 1`

Rationale: All embedded malformed JSON snippets repair to semantically equal JSON.
