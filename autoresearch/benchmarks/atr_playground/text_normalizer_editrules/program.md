# autoresearch program: text_normalizer_editrules

## Research objective
Improve `solution.normalize(text: str) -> str` so noisy short strings normalize to canonical labels.

- Primary metric: exact_match_accuracy
- Direction: higher_is_better
- Baseline: run `bash train/train.sh && bash eval.sh`
- Budget suggestion: 5-15 short experiments; each eval is deterministic and stdlib-only.

## In-scope files
- `solution.py`
- `train/train.py`
- `train/train.sh`
- optional artifacts under `artifacts/`

## Fixed files / do not modify without user approval
- `eval.py`
- `eval.sh`
- metric definitions and embedded evaluation cases

## Setup and baseline
```bash
python3 prepare.py
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json
```

## Experiment loop
Make one minimal change per iteration: e.g. punctuation cleanup, typo table, abbreviation expansion, roman numerals, token sorting, or learned edit rules from visible examples. Re-run train and eval, keep only improvements.

## Stop conditions
Stop after the user-approved round/time budget or when no simple rule improves exact match.

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `exact_match_accuracy`
- `higher_is_better`: `true`
- `exact_match_accuracy >= 1`

Rationale: Every embedded noisy string normalizes to its canonical label.
