"""Structured experiment memory for the AutoResearch loop.

This is the compact history the Planner and Attempt steps should read. Raw
logs, full traces, and source snapshots stay in artifacts; this file keeps the
decision-grade facts: what was tried, what metric moved, what remains, and which
snapshot is currently best.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def experiment_memory_path(root: str | Path) -> Path:
    return Path(root) / ".autoresearch" / "experiment_memory.json"


def build_experiment_memory(root: str | Path, *, state: dict | None = None, max_attempts: int = 8) -> dict:
    root = Path(root)
    state = state if isinstance(state, dict) else _read_json(root / ".autoresearch" / "state.json")
    metrics = _read_json(root / "metrics.json")
    experiments = list((state or {}).get("experiments") or [])
    best = (state or {}).get("best_experiment") or {}
    current = _metric_summary(metrics)
    best_summary = _experiment_summary(best)
    attempts = [_experiment_summary(exp) for exp in experiments[-max(1, max_attempts):]]
    regressions = [
        row for row in attempts
        if row.get("metric") is not None
        and best_summary.get("metric") is not None
        and row.get("metric") < best_summary.get("metric")
    ]
    failures = metrics.get("failures") if isinstance(metrics.get("failures"), list) else []
    payload = {
        "updated_at": time.time(),
        "current": current,
        "best": best_summary,
        "attempts": attempts,
        "remaining_failures": failures[:10],
        "regressions": regressions[-5:],
        "guidance": _guidance(current, best_summary, failures, regressions),
    }
    return payload


def write_experiment_memory(root: str | Path, *, state: dict | None = None) -> dict:
    root = Path(root)
    payload = build_experiment_memory(root, state=state)
    path = experiment_memory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    auto = root / ".autoresearch" / "experiment_memory.md"
    auto.parent.mkdir(parents=True, exist_ok=True)
    auto.write_text(render_experiment_memory_markdown(payload), encoding="utf-8")
    return payload


def render_experiment_memory_markdown(payload: dict) -> str:
    current = payload.get("current") or {}
    best = payload.get("best") or {}
    lines = [
        "# Experiment Memory",
        "",
        "Compact state for planning and repair. Prefer this over raw traces.",
        "",
        "## Current",
        f"- metric: {current.get('metric_name') or ''}={current.get('metric')}",
        f"- failures: {current.get('failure_count', 0)}",
        "",
        "## Best",
        f"- id: {best.get('experiment_id') or ''}",
        f"- metric: {best.get('metric_name') or ''}={best.get('metric')}",
        f"- snapshot: {best.get('source_snapshot_path') or ''}",
        "",
        "## Guidance",
    ]
    for item in payload.get("guidance") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Recent Attempts"])
    for row in payload.get("attempts") or []:
        lines.append(
            f"- {row.get('experiment_id')}: {row.get('metric_name')}={row.get('metric')} "
            f"decision={row.get('decision')} status={row.get('status')} "
            f"hypothesis={row.get('hypothesis', '')[:160]}"
        )
    failures = payload.get("remaining_failures") or []
    if failures:
        lines.extend(["", "## Remaining Failures"])
        for item in failures[:10]:
            lines.append("- " + json.dumps(item, ensure_ascii=False, sort_keys=True)[:700])
    return "\n".join(lines).rstrip() + "\n"


def _metric_summary(metrics: dict) -> dict:
    if not isinstance(metrics, dict):
        metrics = {}
    metric_name = str(metrics.get("primary_metric_name") or metrics.get("metric_name") or "primary_metric")
    metric = metrics.get("primary_metric", metrics.get(metric_name))
    try:
        metric = float(metric) if metric is not None else None
    except Exception:
        metric = None
    failures = metrics.get("failures") if isinstance(metrics.get("failures"), list) else []
    return {
        "metric_name": metric_name,
        "metric": metric,
        "higher_is_better": metrics.get("higher_is_better"),
        "failure_count": len(failures),
        "runtime_seconds": metrics.get("runtime_seconds", metrics.get("runtime_sec")),
        "accuracy": metrics.get("accuracy"),
        "score": metrics.get("score"),
    }


def _experiment_summary(exp: dict) -> dict:
    if not isinstance(exp, dict):
        exp = {}
    metric_name = str(exp.get("primary_metric_name") or "")
    metrics = exp.get("metrics") if isinstance(exp.get("metrics"), dict) else {}
    metric = metrics.get(metric_name) if metric_name else None
    try:
        metric = float(metric) if metric is not None else None
    except Exception:
        metric = None
    return {
        "experiment_id": exp.get("experiment_id", ""),
        "timestamp": exp.get("timestamp", ""),
        "hypothesis": str(exp.get("hypothesis") or "")[:500],
        "metric_name": metric_name,
        "metric": metric,
        "decision": exp.get("decision", ""),
        "status": exp.get("status", ""),
        "source_snapshot_path": exp.get("source_snapshot_path", ""),
        "artifact_path": exp.get("artifact_path", ""),
        "summary": str(exp.get("summary") or "")[:500],
    }


def _guidance(current: dict, best: dict, failures: list, regressions: list) -> list[str]:
    guidance: list[str] = []
    if best.get("source_snapshot_path"):
        guidance.append("Start the next patch from the best snapshot, not from the latest workspace state.")
    if failures and len(failures) <= 3:
        guidance.append("Closeout mode: only patch the listed failures and reject any regression.")
    if regressions:
        guidance.append("Recent attempts regressed from best; prefer a small local patch over full-file rewrite.")
    if not failures and current.get("accuracy") == 1.0 and current.get("metric") is not None and current.get("score") is not None:
        guidance.append("No correctness failures are listed; treat the task as a performance optimization problem.")
    return guidance


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


__all__ = [
    "build_experiment_memory",
    "experiment_memory_path",
    "render_experiment_memory_markdown",
    "write_experiment_memory",
]
