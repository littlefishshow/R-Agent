# route_heuristic_optimizer

Goal: improve a small Euclidean route heuristic.

- Primary metric: `route_quality_score` (higher is better; 1.0 means matching known best length for these tiny instances)
- Fixed benchmark: embedded coordinate instances in `eval.py`
- Allowed to modify: `solution.py`, `train/`
- Do not modify: `eval.py`, `eval.sh`

Baseline uses nearest neighbor from node 0; 2-opt, multi-start, or exact DP for small N can improve it.
