from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


VALID_STATUSES = {"pending", "in_progress", "verified", "failed", "blocked", "skipped"}
VALID_TYPES = {"implementation", "experiment", "validation", "analysis", "maintenance"}
DEPENDENCY_DONE_STATUSES = {"verified"}
OPEN_STATUSES = {"pending", "in_progress"}


def todo_state_path(root: str | Path) -> Path:
    return Path(root) / ".autoresearch" / "todo_state.json"


def empty_todo_state() -> dict:
    return {"version": 1, "updated_at": time.time(), "tasks": []}


def normalize_task_id(value: str, *, fallback_index: int = 1) -> str:
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return raw or f"t{fallback_index}"


def normalize_task(task: dict, *, fallback_index: int = 1) -> dict:
    task = dict(task or {})
    task_id = normalize_task_id(task.get("task_id") or task.get("id"), fallback_index=fallback_index)
    status = str(task.get("status") or "pending").strip().lower()
    task_type = str(task.get("type") or "implementation").strip().lower()
    if status not in VALID_STATUSES:
        status = "pending"
    if task_type not in VALID_TYPES:
        task_type = "implementation"
    return {
        "task_id": task_id,
        "goal": str(task.get("goal") or task.get("title") or "").strip(),
        "type": task_type,
        "status": status,
        "priority": int(task.get("priority") or fallback_index),
        "allowed_paths": _string_list(task.get("allowed_paths")),
        "context_paths": _string_list(task.get("context_paths")),
        "depends_on": _string_list(task.get("depends_on")),
        "plan_summary": str(task.get("plan_summary") or "").strip(),
        "run_spec": _dict(task.get("run_spec")),
        "verification": _dict(task.get("verification")),
        "artifacts": _string_list(task.get("artifacts")),
        "last_result": _dict(task.get("last_result")),
        "lessons": _string_list(task.get("lessons")),
    }


def normalize_todo_state(data: dict | None) -> dict:
    data = dict(data or {})
    tasks = data.get("tasks") or []
    if not isinstance(tasks, list):
        tasks = []
    normalized = [normalize_task(t if isinstance(t, dict) else {}, fallback_index=i + 1) for i, t in enumerate(tasks)]
    seen = set()
    deduped = []
    for i, task in enumerate(normalized, start=1):
        base = task["task_id"]
        task_id = base
        suffix = 2
        while task_id in seen:
            task_id = f"{base}_{suffix}"
            suffix += 1
        task["task_id"] = task_id
        seen.add(task_id)
        deduped.append(task)
    return {"version": 1, "updated_at": float(data.get("updated_at") or time.time()), "tasks": deduped}


def load_todo_state(root: str | Path) -> dict:
    path = todo_state_path(root)
    if not path.exists():
        return empty_todo_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_todo_state()
    return normalize_todo_state(data if isinstance(data, dict) else None)


def save_todo_state(root: str | Path, state: dict) -> Path:
    path = todo_state_path(root)
    normalized = normalize_todo_state(state)
    normalized["updated_at"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def merge_todo_state(existing: dict, planned: dict) -> dict:
    """Merge a new plan into existing task state without discarding progress.

    Matching is by task_id first, then exact goal text. Planning may refresh goal,
    type, priority, allowed/context paths, plan summary, run spec, and verification,
    but status/result/artifacts/lessons survive unless the task is genuinely new.
    """
    old = normalize_todo_state(existing)
    new = normalize_todo_state(planned)
    by_id = {task["task_id"]: task for task in old["tasks"]}
    by_goal = {task["goal"]: task for task in old["tasks"] if task.get("goal")}
    merged = []
    for task in new["tasks"]:
        prior = by_id.get(task["task_id"]) or by_goal.get(task.get("goal"))
        if prior:
            task = {
                **task,
                "status": prior.get("status", task["status"]),
                "depends_on": task.get("depends_on") or prior.get("depends_on", []),
                "artifacts": prior.get("artifacts", task["artifacts"]),
                "last_result": prior.get("last_result", task["last_result"]),
                "lessons": prior.get("lessons", task["lessons"]),
            }
        merged.append(task)
    return normalize_todo_state({"version": 1, "tasks": merged})


def upsert_task(root: str | Path, task: dict) -> dict:
    state = load_todo_state(root)
    normalized = normalize_task(task, fallback_index=len(state["tasks"]) + 1)
    for i, existing in enumerate(state["tasks"]):
        if existing["task_id"] == normalized["task_id"]:
            state["tasks"][i] = {**existing, **normalized}
            save_todo_state(root, state)
            return state["tasks"][i]
    state["tasks"].append(normalized)
    save_todo_state(root, state)
    return normalized


def task_phase(task: dict) -> str:
    """Return the lifecycle phase that should own this task.

    Tasks that run commands or produce metrics belong to Run. Analysis and code
    changes belong to Execute. Maintenance follows its run_spec: command-backed
    maintenance is a Run task; otherwise it is an Execute task.
    """
    task = normalize_task(task)
    task_type = task.get("type")
    if task_type in {"validation", "experiment"}:
        return "run"
    if task_type == "maintenance" and task.get("run_spec"):
        return "run"
    return "execute"


def dependencies_satisfied(state: dict, task: dict) -> bool:
    normalized = normalize_todo_state(state)
    by_id = {t["task_id"]: t for t in normalized["tasks"]}
    for dep in _string_list(task.get("depends_on")):
        if by_id.get(dep, {}).get("status") not in DEPENDENCY_DONE_STATUSES:
            return False
    return True


def open_tasks(state: dict, *, phase: str | None = None) -> list[dict]:
    tasks = [t for t in normalize_todo_state(state)["tasks"] if t.get("status") in OPEN_STATUSES]
    if phase:
        tasks = [t for t in tasks if task_phase(t) == phase]
    tasks.sort(key=lambda t: (int(t.get("priority") or 0), t.get("task_id", "")))
    return tasks


def has_open_tasks(state: dict, *, phase: str | None = None) -> bool:
    return bool(open_tasks(state, phase=phase))


def has_failed_tasks(state: dict, *, phase: str | None = None) -> bool:
    tasks = [t for t in normalize_todo_state(state)["tasks"] if t.get("status") in {"failed", "blocked"}]
    if phase:
        tasks = [t for t in tasks if task_phase(t) == phase]
    return bool(tasks)


def ready_execute_tasks(state: dict, *, limit: int | None = None) -> list[dict]:
    """Return ready Execute tasks before the next ready Run checkpoint."""
    normalized = normalize_todo_state(state)
    ready_run = ready_tasks(normalized, phase="run", statuses=OPEN_STATUSES)
    cutoff_priority = None
    if ready_run:
        cutoff_priority = min(int(t.get("priority") or 0) for t in ready_run)
    tasks = ready_tasks(normalized, phase="execute", statuses=OPEN_STATUSES)
    if cutoff_priority is not None:
        tasks = [t for t in tasks if int(t.get("priority") or 0) < cutoff_priority]
    return tasks[:limit] if limit is not None else tasks


def has_ready_execute_tasks(state: dict) -> bool:
    return bool(ready_execute_tasks(state))


def ready_tasks(
    state: dict,
    *,
    limit: int | None = None,
    phase: str | None = None,
    statuses: set[str] | tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    normalized = normalize_todo_state(state)
    allowed_statuses = set(statuses or {"pending"})
    tasks = [t for t in normalized["tasks"] if t.get("status") in allowed_statuses]
    if phase:
        tasks = [t for t in tasks if task_phase(t) == phase]
    tasks = [t for t in tasks if dependencies_satisfied(normalized, t)]
    tasks.sort(key=lambda t: (int(t.get("priority") or 0), t.get("task_id", "")))
    return tasks[:limit] if limit is not None else tasks


def render_todo_markdown(state: dict) -> str:
    tasks = normalize_todo_state(state)["tasks"]
    lines = ["# Todo State", ""]
    if not tasks:
        lines.append("(no tasks)")
        return "\n".join(lines) + "\n"
    for task in tasks:
        lines.append(f"- [{task['status']}] {task['task_id']} ({task['type']}): {task['goal']}")
        if task.get("plan_summary"):
            lines.append(f"  - plan: {task['plan_summary']}")
        if task.get("depends_on"):
            lines.append(f"  - depends_on: `{json.dumps(task['depends_on'], ensure_ascii=False)}`")
        if task.get("run_spec"):
            lines.append(f"  - run_spec: `{json.dumps(task['run_spec'], ensure_ascii=False, sort_keys=True)}`")
    return "\n".join(lines) + "\n"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


def _dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "VALID_STATUSES",
    "VALID_TYPES",
    "dependencies_satisfied",
    "empty_todo_state",
    "has_open_tasks",
    "has_failed_tasks",
    "has_ready_execute_tasks",
    "load_todo_state",
    "merge_todo_state",
    "normalize_task",
    "normalize_task_id",
    "normalize_todo_state",
    "open_tasks",
    "ready_execute_tasks",
    "ready_tasks",
    "render_todo_markdown",
    "save_todo_state",
    "task_phase",
    "todo_state_path",
    "upsert_task",
]
