# autoresearch program: mini_ir_ranker

## Research objective
Improve `solution.rank(query, documents)` so the relevant document appears as high as possible.

- Primary metric: mean_reciprocal_rank
- Direction: higher_is_better
- Baseline: raw token overlap
- Budget suggestion: 5-15 experiments, seconds per eval.

## Allowed files
`solution.py`, `train/`, `artifacts/`.

## Fixed files
`eval.py`, `eval.sh`, embedded corpus and metrics.

## Baseline
```bash
python3 prepare.py
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json
```

## Useful experiment ideas
Try stemming, stopword removal, TF-IDF/BM25, synonym expansion, phrase bonuses, and numeric token matching. Change one idea per round.

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `mean_reciprocal_rank`
- `higher_is_better`: `true`
- `mean_reciprocal_rank >= 1`

Rationale: Relevant document ranks first for every embedded query.
