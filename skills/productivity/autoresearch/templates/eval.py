#!/usr/bin/env python3
"""
Template evaluation entry for an autoresearch project.
Adapt this file to the user task before running experiments.

Contract:
- keep metric computation stable during experiments;
- print a machine-readable summary block;
- write metrics.json;
- exit non-zero on invalid inputs or evaluation failure.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate autoresearch experiment outputs")
    p.add_argument("--data", type=Path, default=Path("data/test.jsonl"), help="Evaluation dataset or split file")
    p.add_argument("--pred", type=Path, default=Path("outputs/predictions.jsonl"), help="Prediction/output file from training or inference")
    p.add_argument("--output-json", type=Path, default=Path("metrics.json"), help="Where to write metrics")
    p.add_argument("--metric-name", default="primary_metric", help="Name of the primary metric")
    p.add_argument("--higher-is-better", action="store_true", help="Set if larger primary metric is better")
    return p.parse_args()


def evaluate(data_path: Path, pred_path: Path) -> dict:
    """Replace this with task-specific metric computation.

    Examples:
    - classification: accuracy/F1 against labels in data_path;
    - generation: BLEU/ROUGE/exact match/pass@k;
    - training objective: validation loss/perplexity/bpb;
    - systems task: latency, throughput, memory with correctness gate.
    """
    if not data_path.exists():
        raise FileNotFoundError(f"Missing evaluation data: {data_path}")
    if not pred_path.exists():
        raise FileNotFoundError(f"Missing predictions/output file: {pred_path}")

    # Placeholder: count non-empty prediction lines as a sanity metric.
    # MUST be replaced for real research tasks.
    total = 0
    non_empty = 0
    with pred_path.open("r", encoding="utf-8") as f:
        for line in f:
            total += 1
            if line.strip():
                non_empty += 1
    score = non_empty / total if total else 0.0
    return {
        "primary_metric": score,
        "metric_name": "non_empty_prediction_rate_placeholder",
        "higher_is_better": True,
        "num_predictions": total,
    }


def main() -> None:
    args = parse_args()
    start = time.time()
    metrics = evaluate(args.data, args.pred)
    metrics["runtime_seconds"] = round(time.time() - start, 3)
    args.output_json.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("---")
    print(f"primary_metric: {metrics['primary_metric']:.6f}")
    print(f"primary_metric_name: {metrics['metric_name']}")
    print(f"higher_is_better: {str(metrics['higher_is_better']).lower()}")
    print(f"runtime_seconds: {metrics['runtime_seconds']}")
    print(f"metrics_json: {args.output_json}")


if __name__ == "__main__":
    main()
