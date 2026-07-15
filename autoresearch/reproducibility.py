"""Best-candidate reproducibility verification."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from autoresearch.anomalies import normalize_determinism


def default_reproducibility_threshold(determinism: str, configured: float = 0.0) -> float:
    determinism = normalize_determinism(determinism)
    if configured and configured > 0:
        return float(configured)
    if determinism == "stochastic":
        return 0.05
    if determinism == "environment-sensitive":
        return 0.10
    return 0.0


def compare_metric(
    original: Any,
    rerun: Any,
    *,
    determinism: str = "deterministic",
    threshold: float = 0.0,
    epsilon: float = 1e-12,
) -> dict:
    determinism = normalize_determinism(determinism)
    threshold = default_reproducibility_threshold(determinism, threshold)
    try:
        original_value = float(original)
        rerun_value = float(rerun)
    except Exception:
        return {
            "status": "CANNOT_VERIFY",
            "match": False,
            "detail": "metric values are not numeric",
            "original": original,
            "rerun": rerun,
            "determinism": determinism,
            "threshold": threshold,
        }
    diff = abs(original_value - rerun_value)
    denom = max(abs(original_value), abs(rerun_value), epsilon)
    rel_diff = diff / denom
    if determinism == "deterministic":
        matched = diff <= epsilon
    else:
        matched = rel_diff <= threshold
    return {
        "status": "MATCH" if matched else "MISMATCH",
        "match": matched,
        "original": original_value,
        "rerun": rerun_value,
        "abs_diff": diff,
        "relative_diff": rel_diff,
        "determinism": determinism,
        "threshold": threshold,
    }


def best_metric(best: dict) -> tuple[str, Optional[float]]:
    if not isinstance(best, dict):
        return "", None
    metrics = best.get("metrics") if isinstance(best.get("metrics"), dict) else {}
    name = str(best.get("primary_metric_name") or "")
    if not name and metrics:
        name = str(next(iter(metrics)))
    value = metrics.get(name) if name else None
    if value is None:
        value = metrics.get("primary_metric")
    try:
        return name, float(value)
    except Exception:
        return name, None


def build_reproducibility_report(
    *,
    best: dict,
    reruns: list[dict],
    determinism: str,
    threshold: float,
) -> dict:
    metric_name, original_value = best_metric(best)
    comparisons = []
    for rerun in reruns:
        metrics = rerun.get("metrics") if isinstance(rerun.get("metrics"), dict) else {}
        rerun_value = metrics.get(metric_name) if metric_name else None
        if rerun_value is None:
            rerun_value = metrics.get("primary_metric")
        comparisons.append(compare_metric(
            original_value,
            rerun_value,
            determinism=determinism,
            threshold=threshold,
        ))
    if not reruns:
        verdict = "CANNOT_VERIFY"
        passport_status = "CANNOT_VERIFY"
    elif all(row.get("match") for row in comparisons):
        verdict = "REPRODUCIBLE"
        passport_status = "VERIFIED"
    elif any(row.get("status") == "CANNOT_VERIFY" for row in comparisons):
        verdict = "CANNOT_VERIFY"
        passport_status = "CANNOT_VERIFY"
    else:
        verdict = "NOT_REPRODUCIBLE"
        passport_status = "CANNOT_VERIFY"
    return {
        "checked_at": time.time(),
        "verdict": verdict,
        "passport_status": passport_status,
        "metric_name": metric_name,
        "original": original_value,
        "determinism": normalize_determinism(determinism),
        "threshold": default_reproducibility_threshold(determinism, threshold),
        "reruns": reruns,
        "comparisons": comparisons,
    }


def read_metrics_file(root: str | Path) -> dict:
    root = Path(root)
    for rel in ("metrics.json", "results.json", ".autoresearch/metrics.json"):
        path = root / rel
        if not path.exists() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace")[:200_000])
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


__all__ = [
    "best_metric",
    "build_reproducibility_report",
    "compare_metric",
    "default_reproducibility_threshold",
    "read_metrics_file",
]
