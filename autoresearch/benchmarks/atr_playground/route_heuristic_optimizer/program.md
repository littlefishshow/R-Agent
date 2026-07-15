# autoresearch program: route_heuristic_optimizer

## Research objective
Improve `solution.solve(points) -> list[int]`, returning a Hamiltonian cycle order for small 2D point sets.

- Primary metric: route_quality_score = known_best_length / produced_length, averaged over instances
- Direction: higher_is_better
- Baseline: nearest neighbor from node 0

## Allowed files
`solution.py`, `train/`, `artifacts/`.

## Fixed files
`eval.py`, `eval.sh`, benchmark coordinates, metric definitions.

## Baseline
```bash
python3 prepare.py
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json
```

## Experiment ideas
Try 2-opt local search, multiple starting nodes, farthest insertion, deterministic random restarts, or exact Held-Karp for these small N. Keep runtime under seconds.

## Completion Criteria
This project is solved only when the official evaluation in `metrics.json` reports:

- `metric_name`: `route_quality_score`
- `higher_is_better`: `true`
- `route_quality_score >= 0.999`

Rationale: Tours match the known optimum for all small deterministic instances, allowing tiny floating error.
