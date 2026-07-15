"""Run anomaly classification and lightweight monitoring helpers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_STALL_TOLERANCE = 3


def normalize_run_spec(run_spec: dict | None) -> dict:
    """Normalize optional monitoring/reproducibility fields in a run_spec."""
    spec = dict(run_spec or {})
    expected_outputs = spec.get("expected_outputs") or []
    if isinstance(expected_outputs, str):
        expected_outputs = [expected_outputs]
    spec["expected_outputs"] = [str(p).strip() for p in expected_outputs if str(p).strip()]

    monitor_files = spec.get("monitor_files") or []
    if isinstance(monitor_files, str):
        monitor_files = [monitor_files]
    spec["monitor_files"] = [str(p).strip() for p in monitor_files if str(p).strip()]

    experiment_type = str(spec.get("experiment_type") or "generic").strip().lower()
    if experiment_type not in {"training", "analysis", "etl", "simulation", "generic"}:
        experiment_type = "generic"
    spec["experiment_type"] = experiment_type

    if "stall_tolerance" in spec:
        try:
            spec["stall_tolerance"] = max(1, int(spec.get("stall_tolerance") or DEFAULT_STALL_TOLERANCE))
        except Exception:
            spec["stall_tolerance"] = DEFAULT_STALL_TOLERANCE
    if "success_criteria" in spec:
        spec["success_criteria"] = str(spec.get("success_criteria") or "")
    if "determinism" in spec:
        spec["determinism"] = normalize_determinism(spec.get("determinism"))
    if "reproducibility_threshold" in spec:
        try:
            spec["reproducibility_threshold"] = max(0.0, float(spec.get("reproducibility_threshold") or 0.0))
        except Exception:
            spec["reproducibility_threshold"] = 0.0
    return spec


def normalize_determinism(value: str | None) -> str:
    raw = str(value or "deterministic").strip().lower().replace("_", "-")
    if raw in {"deterministic", "stochastic", "environment-sensitive"}:
        return raw
    if raw in {"environment", "env-sensitive", "hardware", "hardware-dependent"}:
        return "environment-sensitive"
    return "deterministic"


def snapshot_files(root: str | Path, rel_paths: list[str]) -> dict[str, dict]:
    root = Path(root)
    snap: dict[str, dict] = {}
    for rel in rel_paths or []:
        if not rel:
            continue
        path = root / str(rel)
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
        except Exception:
            snap[str(rel)] = {"exists": False, "unsafe": True}
            continue
        if not path.exists() or not path.is_file():
            snap[str(rel)] = {"exists": False, "size": 0, "mtime": 0.0}
            continue
        stat = path.stat()
        snap[str(rel)] = {"exists": True, "size": int(stat.st_size), "mtime": float(stat.st_mtime)}
    return snap


def detect_run_anomalies(
    *,
    root: str | Path,
    run_spec: dict | None,
    result: dict,
    observations: list[Any] | None = None,
    before_files: dict[str, dict] | None = None,
    after_files: dict[str, dict] | None = None,
    elapsed_seconds: float = 0.0,
) -> list[dict]:
    spec = normalize_run_spec(run_spec)
    result = dict(result or {})
    anomalies: list[dict] = []
    status = str(result.get("status") or "")
    returncode = result.get("returncode")
    stderr = str(result.get("stderr") or "")
    stdout = str(result.get("stdout") or "")
    text = "\n".join([stdout[-2000:], stderr[-2000:]]).lower()

    if result.get("timeout"):
        anomalies.append(_anomaly("HARD_TIMEOUT", "run exceeded configured timeout", severity="error", action="mandatory_stop"))
    if status == "failed" or (returncode not in (None, 0) and status != "ok"):
        kind = _classify_failure_text(text)
        anomalies.append(_anomaly(kind, _failure_detail(kind, text), severity="error", action="repair_before_next_run"))

    expected_outputs = spec.get("expected_outputs") or []
    if expected_outputs:
        after = after_files if after_files is not None else snapshot_files(root, expected_outputs)
        missing = [rel for rel in expected_outputs if not after.get(rel, {}).get("exists")]
        empty = [rel for rel in expected_outputs if after.get(rel, {}).get("exists") and int(after.get(rel, {}).get("size") or 0) <= 0]
        if missing:
            anomalies.append(_anomaly("MISSING_OUTPUT", "expected output files were not produced: " + ", ".join(missing[:8]), severity="error"))
        if empty:
            anomalies.append(_anomaly("EMPTY_OUTPUT", "expected output files are empty: " + ", ".join(empty[:8]), severity="warning"))

    monitor_files = spec.get("monitor_files") or []
    if monitor_files:
        before = before_files or {}
        after = after_files if after_files is not None else snapshot_files(root, monitor_files)
        unchanged = [
            rel for rel in monitor_files
            if before.get(rel, {}).get("exists")
            and after.get(rel, {}).get("exists")
            and before.get(rel, {}).get("size") == after.get(rel, {}).get("size")
            and before.get(rel, {}).get("mtime") == after.get(rel, {}).get("mtime")
        ]
        if unchanged:
            anomalies.append(_anomaly("OUTPUT_STALL", "monitored files did not change: " + ", ".join(unchanged[:8]), severity="warning"))

    max_seconds = float(spec.get("max_seconds") or 0.0)
    if max_seconds and elapsed_seconds > max_seconds:
        anomalies.append(_anomaly("HARD_TIMEOUT", f"elapsed {elapsed_seconds:.3f}s exceeded max_seconds={max_seconds}", severity="error", action="stop_or_reduce_scope"))

    return _dedupe_anomalies(anomalies)


def write_anomaly_report(root: str | Path, anomalies: list[dict], *, run_id: str = "") -> Path:
    path = Path(root) / ".autoresearch" / "anomalies.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for item in anomalies or []:
            row = {"ts": time.time(), "run_id": run_id, **dict(item)}
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def render_anomalies_markdown(anomalies: list[dict]) -> str:
    if not anomalies:
        return ""
    lines = ["\n## Anomalies Detected", ""]
    for item in anomalies:
        lines.append(
            f"- {item.get('type')}: severity={item.get('severity')} action={item.get('action', 'advisory')} "
            f"detail={item.get('detail', '')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def _classify_failure_text(text: str) -> str:
    if "submission schema error" in text or "json object keyed by case id" in text:
        return "SCHEMA_ERROR"
    if "modulenotfounderror" in text or "importerror" in text or "no module named" in text:
        return "IMPORT_ERROR"
    if "out of memory" in text or "cuda oom" in text or "oom" in text:
        return "RESOURCE_ANOMALY"
    if "timed out" in text or "timeout" in text:
        return "HARD_TIMEOUT"
    if "traceback" in text or "exception" in text or "error" in text:
        return "CRASHED"
    return "RUN_FAILED"


def _failure_detail(kind: str, text: str) -> str:
    if kind == "SCHEMA_ERROR":
        return "prediction/submission artifact shape is incompatible with the evaluator"
    if kind == "IMPORT_ERROR":
        return "run failed while importing a module"
    if kind == "RESOURCE_ANOMALY":
        return "run hit a resource or memory failure"
    if kind == "HARD_TIMEOUT":
        return "run timed out"
    if kind == "CRASHED":
        return "run crashed; inspect stderr traceback"
    return "run exited non-zero"


def _anomaly(kind: str, detail: str, *, severity: str = "warning", action: str = "advisory") -> dict:
    return {
        "type": str(kind or "UNKNOWN"),
        "severity": str(severity or "warning"),
        "detail": str(detail or ""),
        "action": str(action or "advisory"),
    }


def _dedupe_anomalies(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for item in items:
        key = (str(item.get("type")), str(item.get("detail")))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


__all__ = [
    "DEFAULT_STALL_TOLERANCE",
    "detect_run_anomalies",
    "normalize_determinism",
    "normalize_run_spec",
    "render_anomalies_markdown",
    "snapshot_files",
    "write_anomaly_report",
]
