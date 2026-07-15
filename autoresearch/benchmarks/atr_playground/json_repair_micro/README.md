# json_repair_micro

Goal: repair common small JSON corruptions.

- Primary metric: `repair_exact_accuracy` (higher is better)
- Fixed benchmark: embedded malformed strings in `eval.py`
- Allowed to modify: `solution.py`, `train/`
- Do not modify: `eval.py`, `eval.sh`

Baseline handles only a couple simple replacements; better finite-state/rule-based repair should improve it.
