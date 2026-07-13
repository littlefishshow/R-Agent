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
    normalized = {
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
    # Preserve non-scheduling metadata used by higher-level loop helpers.  Keep
    # this intentionally narrow so arbitrary planner output does not bloat the
    # persisted task state.
    if task.get("repairs_task_id"):
        normalized["repairs_task_id"] = str(task.get("repairs_task_id"))
    if task.get("failure_evidence"):
        normalized["failure_evidence"] = _single_line(str(task.get("failure_evidence")), limit=700)
    return normalized


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

    Matching is by exact goal first, then stable non-generated task_id. Planning may refresh goal,
    type, priority, allowed/context paths, plan summary, run spec, and verification,
    but status/result/artifacts/lessons survive unless the task is genuinely new.
    """
    old = normalize_todo_state(existing)
    new = normalize_todo_state(planned)
    by_id = {task["task_id"]: task for task in old["tasks"] if not _is_generated_task_id(task.get("task_id"))}
    by_goal = {task["goal"]: task for task in old["tasks"] if task.get("goal")}
    merged = []
    for task in new["tasks"]:
        prior = by_goal.get(task.get("goal")) or by_id.get(task["task_id"])
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


def _is_generated_task_id(task_id: str | None) -> bool:
    return bool(re.fullmatch(r"t\d+(?:_\d+)?", str(task_id or "")))


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


def has_blocking_failed_tasks(state: dict, *, phase: str | None = None) -> bool:
    """Return failures that should force replanning.

    A failed Run checkpoint is often useful evidence, not proof that the plan is
    dead. If there is still open Execute work later in the DAG, the attempt loop
    should get a chance to repair the failure before the controller throws away
    the whole plan.
    """
    normalized = normalize_todo_state(state)
    failed = [t for t in normalized["tasks"] if t.get("status") in {"failed", "blocked"}]
    if phase:
        failed = [t for t in failed if task_phase(t) == phase]
    if not failed:
        return False
    open_execute = [t for t in normalized["tasks"] if t.get("status") in OPEN_STATUSES and task_phase(t) == "execute"]
    if not open_execute:
        return True
    min_open_execute_priority = min(int(t.get("priority") or 0) for t in open_execute)
    for task in failed:
        if task_phase(task) == "execute":
            return True
        if int(task.get("priority") or 0) >= min_open_execute_priority:
            return True
    return False


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


def repair_failed_run_tasks(state: dict) -> dict:
    """Convert failed run checkpoints into explicit implementation repair work.

    This closes a common loop failure: `train/eval` fails, the run task becomes
    failed, and dependencies prevent the next implementation from ever becoming
    ready. Instead, add one implementation task right after the failed run and
    redirect later tasks that depended on the failed run to depend on the repair.
    """
    normalized = normalize_todo_state(state)
    tasks = list(normalized["tasks"])
    if not tasks:
        return normalized
    existing_ids = {task["task_id"] for task in tasks}
    existing_repairs = {
        str(task.get("repairs_task_id") or "")
        for task in tasks
        if str(task.get("repairs_task_id") or "")
    }
    changed = False
    result: list[dict] = []
    for task in tasks:
        result.append(task)
        if task.get("status") != "failed" or task_phase(task) != "run":
            continue
        failed_id = str(task.get("task_id") or "")
        if failed_id in existing_repairs:
            continue
        repair_id = normalize_task_id(f"repair_{failed_id}", fallback_index=len(result) + 1)
        suffix = 2
        base = repair_id
        while repair_id in existing_ids:
            repair_id = f"{base}_{suffix}"
            suffix += 1
        existing_ids.add(repair_id)
        last = task.get("last_result") or {}
        summary = _single_line(str(last.get("summary") or last.get("note") or ""), limit=700)
        repair = normalize_task({
            "task_id": repair_id,
            "goal": (
                "Fix the train-side pipeline so the failed run checkpoint can execute. "
                "Create or update the missing train-side entrypoint and preserve submission JSON generation."
            ),
            "type": "implementation",
            "status": "pending",
            "priority": int(task.get("priority") or len(result)) + 1,
            "allowed_paths": ["train/**"],
            "context_paths": ["program.md", "README.md", "train/train.sh", "train/train.py"],
            "depends_on": [],
            "plan_summary": "Repair failed train/eval checkpoint before continuing experiments.",
            "repairs_task_id": failed_id,
            "failure_evidence": summary,
        }, fallback_index=len(result) + 1)
        result.append(repair)
        changed = True
    if not changed:
        return normalized
    repair_ids = {
        str(task.get("repairs_task_id")): task["task_id"]
        for task in result
        if str(task.get("repairs_task_id") or "")
    }
    for task in result:
        deps = list(task.get("depends_on") or [])
        replaced = [repair_ids.get(dep, dep) for dep in deps]
        if replaced != deps:
            task["depends_on"] = replaced
    for index, task in enumerate(result, start=1):
        task["priority"] = index
    return normalize_todo_state({"version": 1, "tasks": result})


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


def _single_line(value: str, *, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    return text[: max(0, int(limit))]


__all__ = [
    "VALID_STATUSES",
    "VALID_TYPES",
    "dependencies_satisfied",
    "empty_todo_state",
    "has_open_tasks",
    "has_failed_tasks",
    "has_blocking_failed_tasks",
    "has_ready_execute_tasks",
    "load_todo_state",
    "merge_todo_state",
    "normalize_task",
    "normalize_task_id",
    "normalize_todo_state",
    "open_tasks",
    "ready_execute_tasks",
    "ready_tasks",
    "repair_failed_run_tasks",
    "render_todo_markdown",
    "save_todo_state",
    "task_phase",
    "todo_state_path",
    "upsert_task",
]
