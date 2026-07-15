#!/usr/bin/env bash
set -euo pipefail

# Evaluation wrapper for an autoresearch project.
# Adapt arguments to the project-specific eval.py.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

: "${EVAL_DATA:=data/test.jsonl}"
: "${PRED_FILE:=outputs/predictions.jsonl}"
: "${METRICS_FILE:=metrics.json}"

uv run python eval.py \
  --data "$EVAL_DATA" \
  --pred "$PRED_FILE" \
  --output-json "$METRICS_FILE"
