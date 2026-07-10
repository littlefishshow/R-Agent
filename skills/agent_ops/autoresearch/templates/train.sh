#!/usr/bin/env bash
set -euo pipefail

# Training/experiment wrapper for an autoresearch project.
# Place this file at train/train.sh and adapt TRAIN_TIMEOUT_SECONDS and command.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

: "${TRAIN_TIMEOUT_SECONDS:=0}"  # 0 means no shell timeout
mkdir -p outputs

CMD=(uv run python train/train.py --output outputs/predictions.jsonl)

if [[ "$TRAIN_TIMEOUT_SECONDS" != "0" ]]; then
  timeout "$TRAIN_TIMEOUT_SECONDS" "${CMD[@]}"
else
  "${CMD[@]}"
fi
