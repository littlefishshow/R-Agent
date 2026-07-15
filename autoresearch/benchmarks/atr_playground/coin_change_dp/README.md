# coin_change_dp

Autoresearch toy project for optimizing exact coin change. The baseline writes a correct recursive memoized solver, but repeated evaluation exposes avoidable recursion/function-call overhead.

Quick start:

```bash
python prepare.py
bash train/train.sh
bash eval.sh
cat metrics.json
```

Expected improvement: replace `submission/solver.py` generation in `train/train.py` with bottom-up dynamic programming while preserving exact answers.
