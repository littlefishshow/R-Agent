# byte_codec_detector

Goal: decode small mojibake/escaped text snippets into clean Unicode.

- Primary metric: `decoded_exact_accuracy` (higher is better)
- Fixed benchmark: embedded cases in `eval.py`
- Allowed to modify: `solution.py`, `train/`
- Do not modify: `eval.py`, `eval.sh`

Baseline only applies html unescape and unicode_escape, so it misses Latin-1/CP1252 mojibake and mixed escape patterns.
