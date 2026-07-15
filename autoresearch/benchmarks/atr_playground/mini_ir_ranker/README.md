# mini_ir_ranker

Goal: improve a tiny information-retrieval ranker.

- Primary metric: `mean_reciprocal_rank` (higher is better)
- Fixed benchmark: embedded documents/queries in `eval.py`
- Allowed to modify: `solution.py`, `train/`
- Do not modify: `eval.py`, `eval.sh`

Baseline ranks by raw token overlap and misses synonym/IDF/phrase effects.
