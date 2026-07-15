# log_anomaly_f1

Goal: classify synthetic log lines as normal or anomalous.

- Primary metric: `positive_f1` (higher is better)
- Fixed benchmark: deterministic cases in `eval.py`
- Allowed to modify: `solution.py`, `train/`
- Do not modify: `eval.py`, `eval.sh`

Baseline only checks severe keywords and misses latency spikes, retry storms, HTTP status patterns, and resource exhaustion hints.
