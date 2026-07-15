"""Regression cases and closeout mode for AutoResearch.

The framework uses this module to turn evaluator feedback into a compact,
machine-owned repair contract. LLMs may propose patches, but the framework owns
the decision about when the task is in closeout mode and what failures must not
regress.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from autoresearch.state.experiment_memory import build_experiment_memory


def regression_cases_path(root: str | Path) -> Path:
    return Path(root) / ".autoresearch" / "regression_cases.json"


def build_regression_cases(root: str | Path, *, max_failures: int = 12) -> dict:
    root = Path(root)
    memory = build_experiment_memory(root)
    current = memory.get("current") or {}
    best = memory.get("best") or {}
    failures = list(memory.get("remaining_failures") or [])[:max(1, max_failures)]
    closeout = should_enter_closeout(current=current, best=best, failures=failures)
    payload = {
        "updated_at": time.time(),
        "closeout": closeout,
        "current": current,
        "best": best,
        "must_fix": failures,
        "must_not_regress": _must_not_regress(root, failures),
        "instructions": closeout_instructions(closeout, failures),
    }
    return payload


def write_regression_cases(root: str | Path) -> dict:
    root = Path(root)
    payload = build_regression_cases(root)
    path = regression_cases_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return payload


def should_enter_closeout(*, current: dict, best: dict, failures: list) -> bool:
    # Closeout is only for a small, explicit failure set. High-but-unsolved
    # performance scores still need exploration, not hard small-patch mode.
    failure_count = len(failures)
    return 0 < failure_count <= 3


def closeout_instructions(closeout: bool, failures: list) -> list[str]:
    if not closeout:
        return [
            "Broad implementation changes are allowed; use the regression cases as context, not as a hard constraint.",
            "Prefer changes that produce fresh metrics and clear failure evidence.",
        ]
    return [
        "Closeout hint: only a few failures remain. Prefer a small local patch if it is sufficient.",
        "If a broader rewrite is clearly necessary, it is allowed, but preserve already-passing behavior.",
        "Use the must_fix cases to focus the patch and verify with the official eval.",
    ]


def _must_not_regress(root: Path, failures: list) -> list[dict]:
    metrics = _read_json(root / "metrics.json")
    total = metrics.get("num_cases")
    correct = metrics.get("num_correct")
    if isinstance(total, (int, float)) and isinstance(correct, (int, float)):
        return [{"kind": "aggregate", "num_cases": total, "min_correct": correct}]
    if failures:
        return [{"kind": "metric_floor", "metric_name": metrics.get("metric_name") or metrics.get("primary_metric_name"), "min_metric": metrics.get("primary_metric")}]
    return []


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


__all__ = [
    "build_regression_cases",
    "closeout_instructions",
    "regression_cases_path",
    "should_enter_closeout",
    "write_regression_cases",
]
