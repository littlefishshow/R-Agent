"""Schema/revision validation for persisted AutoResearch state.

This module is deliberately tolerant of older state files: missing schema fields
are reported as warnings and filled on the next write.  Hard failures are
reserved for corrupt JSON or invariants that would make resume unsafe.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from autoresearch.state.memory import PHASES, read_phase

STATE_SCHEMA_VERSION = 1
REQUIRED_STATE_LISTS = ("observations", "experiments", "pareto_front", "useful_failures")
REQUIRED_STATE_KEYS = ("summary", "buckets", "last_finalized_experiment_count")
TODO_STATUSES = {"pending", "in_progress", "verified", "failed", "blocked", "skipped"}
GATE_KEYS = {
    "best_experiment_id",
    "experiment_count",
    "pareto_count",
    "pareto_changed",
    "plateau_counter",
    "plan_still_valid",
    "needs_replan",
    "blocked_reason",
}


def stamp_state_revision(state: dict) -> dict:
    """Return state with schema_version/revision bookkeeping updated."""
    data = dict(state or {})
    data["schema_version"] = STATE_SCHEMA_VERSION
    try:
        data["revision"] = max(0, int(data.get("revision") or 0)) + 1
    except Exception:
        data["revision"] = 1
    data["updated_at"] = time.time()
    return data


def validate_autoresearch_state(root: str | Path) -> dict:
    root = Path(root)
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    state_path = root / ".autoresearch" / "state.json"
    state = _read_json_file(state_path, required=False)
    _add_file_check(checks, "state_json", state_path, state)
    if not state.get("ok") and not state.get("missing"):
        errors.append(f"state.json invalid: {state.get('error', 'unreadable')}")
    if state.get("ok") and isinstance(state.get("data"), dict):
        _validate_state_payload(state["data"], warnings, errors)

    todo_path = root / ".autoresearch" / "todo_state.json"
    todo = _read_json_file(todo_path, required=False)
    _add_file_check(checks, "todo_state_json", todo_path, todo)
    if not todo.get("ok") and not todo.get("missing"):
        errors.append(f"todo_state.json invalid: {todo.get('error', 'unreadable')}")
    if todo.get("ok") and isinstance(todo.get("data"), dict):
        _validate_todo_payload(todo["data"], warnings, errors)

    gate_path = root / ".autoresearch" / "gate_signals.json"
    gate = _read_json_file(gate_path, required=False)
    _add_file_check(checks, "gate_signals_json", gate_path, gate)
    if not gate.get("ok") and not gate.get("missing"):
        errors.append(f"gate_signals.json invalid: {gate.get('error', 'unreadable')}")
    if gate.get("ok") and isinstance(gate.get("data"), dict):
        _validate_gate_payload(gate["data"], warnings, errors)

    project_path = root / "project.md"
    if project_path.exists():
        try:
            phase, _reason = read_phase(project_path.read_text(encoding="utf-8", errors="replace"))
            if phase not in PHASES:
                errors.append(f"project.md has invalid phase {phase!r}")
            checks.append({"name": "project_md", "path": str(project_path), "ok": True, "phase": phase})
        except Exception as exc:
            errors.append(f"project.md unreadable: {exc}")
            checks.append({"name": "project_md", "path": str(project_path), "ok": False, "error": str(exc)})
    else:
        warnings.append("project.md missing; init will scaffold it on next run")
        checks.append({"name": "project_md", "path": str(project_path), "ok": False, "missing": True})

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "checked_at": time.time(),
    }


def _read_json_file(path: Path, *, required: bool) -> dict:
    if not path.exists():
        return {"ok": not required, "missing": True, "path": str(path), "data": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "path": str(path), "error": str(exc), "data": {}}
    if not isinstance(data, dict):
        return {"ok": False, "path": str(path), "error": "top-level JSON is not an object", "data": {}}
    return {"ok": True, "path": str(path), "data": data}


def _add_file_check(checks: list[dict], name: str, path: Path, result: dict) -> None:
    check = {
        "name": name,
        "path": str(path),
        "ok": bool(result.get("ok")),
    }
    if result.get("missing"):
        check["missing"] = True
    if result.get("error"):
        check["error"] = result.get("error")
    checks.append(check)


def _validate_state_payload(data: dict, warnings: list[str], errors: list[str]) -> None:
    if data.get("schema_version") not in (None, STATE_SCHEMA_VERSION):
        errors.append(f"state.json schema_version {data.get('schema_version')!r} is unsupported")
    if data.get("schema_version") is None:
        warnings.append("state.json has no schema_version; next write will stamp v1")
    if data.get("revision") is None:
        warnings.append("state.json has no revision; next write will start revision tracking")
    elif not _positive_int(data.get("revision")):
        errors.append("state.json revision must be a positive integer")
    for key in REQUIRED_STATE_KEYS:
        if key not in data:
            warnings.append(f"state.json missing {key!r}; default state loader will fill it")
    for key in REQUIRED_STATE_LISTS:
        if key in data and not isinstance(data.get(key), list):
            errors.append(f"state.json {key!r} must be a list")
    if data.get("best_experiment") is not None and not isinstance(data.get("best_experiment"), dict):
        errors.append("state.json best_experiment must be object or null")


def _validate_todo_payload(data: dict, warnings: list[str], errors: list[str]) -> None:
    tasks = data.get("tasks")
    if tasks is None:
        warnings.append("todo_state.json missing tasks list")
        return
    if not isinstance(tasks, list):
        errors.append("todo_state.json tasks must be a list")
        return
    seen: set[str] = set()
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            errors.append(f"todo_state.json task #{index} is not an object")
            continue
        task_id = str(task.get("task_id") or "")
        if not task_id:
            errors.append(f"todo_state.json task #{index} missing task_id")
        elif task_id in seen:
            errors.append(f"todo_state.json duplicate task_id {task_id!r}")
        seen.add(task_id)
        status = str(task.get("status") or "pending")
        if status not in TODO_STATUSES:
            errors.append(f"todo_state.json task {task_id or index} has invalid status {status!r}")


def _validate_gate_payload(data: dict, warnings: list[str], errors: list[str]) -> None:
    missing = sorted(GATE_KEYS - set(data.keys()))
    if missing:
        warnings.append("gate_signals.json missing keys: " + ", ".join(missing))
    for key in ("experiment_count", "pareto_count", "plateau_counter"):
        if key in data and not isinstance(data.get(key), int):
            errors.append(f"gate_signals.json {key} must be an integer")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


__all__ = [
    "STATE_SCHEMA_VERSION",
    "stamp_state_revision",
    "validate_autoresearch_state",
]
