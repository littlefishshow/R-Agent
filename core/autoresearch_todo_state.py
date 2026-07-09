from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


VALID_STATUSES = {"pending", "in_progress", "verified", "failed", "blocked", "skipped"}
VALID_TYPES = {"implementation", "experiment", "validation", "analysis", "maintenance"}


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


def ready_tasks(state: dict, *, limit: int | None = None) -> list[dict]:
    tasks = [t for t in normalize_todo_state(state)["tasks"] if t.get("status") == "pending"]
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
    "empty_todo_state",
    "load_todo_state",
    "merge_todo_state",
    "normalize_task",
    "normalize_task_id",
    "normalize_todo_state",
    "ready_tasks",
    "render_todo_markdown",
    "save_todo_state",
    "todo_state_path",
    "upsert_task",
]
