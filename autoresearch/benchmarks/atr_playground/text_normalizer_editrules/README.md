# text_normalizer_editrules

Goal: improve a deterministic text normalizer for noisy product/category strings.

- Primary metric: `exact_match_accuracy` (higher is better)
- Fixed benchmark: embedded in `eval.py`
- Allowed to modify: `solution.py`, `train/`
- Do not modify during optimization: `eval.py`, `eval.sh`, hidden cases in eval

Baseline only lowercases and trims whitespace, so it fails spelling fixes, punctuation normalization, roman numeral expansion, and abbreviation handling.
