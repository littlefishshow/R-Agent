import os
import json
import time
import threading
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Tuple
from tools.registry import registry
from rich.panel import Panel
from tools import progress_render

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODO_FILE = os.path.join(BASE_DIR, "sandbox", "todo_list.json")
TODO_LIST_DIR = os.path.join(BASE_DIR, "sandbox", "todo_lists")
TODO_LOCK = threading.RLock()
TODO_LOCK_FILE = f"{TODO_FILE}.lock"
_ACTIVE_SESSION_ID: ContextVar[str] = ContextVar("todo_session_id", default="")


def _safe_session_id(session_id: Optional[str]) -> str:
    raw = str(session_id or "").strip()
    if not raw or raw == "default":
        return ""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe[:80] or ""


def _current_session_id(session_id: Optional[str] = None) -> str:
    return _safe_session_id(session_id or _ACTIVE_SESSION_ID.get() or os.environ.get("R_AGENT_SESSION_ID", ""))


def _todo_file_path(session_id: Optional[str] = None) -> str:
    sid = _current_session_id(session_id)
    if not sid:
        return TODO_FILE
    try:
        from core import config
        from core.sandbox_workspace import SandboxWorkspace

        if config.get_session_sandbox_enabled():
            workspace = SandboxWorkspace(
                session_id=sid,
                root=config.get_session_sandbox_root(),
            )
            workspace.ensure()
            return str(workspace.todo_lists / "todo_list.json")
    except Exception:
        # 路由异常时回退既有 per-session todo 路径，不能影响任务看板。
        pass
    return os.path.join(TODO_LIST_DIR, f"todo_list_{sid}.json")


@contextmanager
def _todo_session(session_id: Optional[str] = None):
    sid = _current_session_id(session_id)
    token = _ACTIVE_SESSION_ID.set(sid)
    try:
        yield sid
    finally:
        _ACTIVE_SESSION_ID.reset(token)

VALID_STATUSES = {
    "pending",        # 等待执行/等待领取
    "in_progress",    # 已被父进程分配给某个子进程或当前进程正在处理
    "needs_split",    # 子进程/父进程判断过于笼统，需要父进程审批拆分
    "blocked",        # 被子任务或外部条件阻塞
    "completed",
    "failed",
    "cancelled",
}

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now() -> int:
    return int(time.time())


def _default_state() -> Dict[str, Any]:
    return {"version": 2, "tasks": []}


@contextmanager
def _todo_file_lock(session_id: Optional[str] = None):
    """Serialize todo read-modify-write actions across threads and processes.

    The file path is scoped by session_id so multiple terminals / GUI sessions do
    not overwrite the same sandbox/todo_list.json unless they explicitly share a
    session id.
    """
    todo_file = _todo_file_path(session_id)
    with TODO_LOCK:
        os.makedirs(os.path.dirname(todo_file), exist_ok=True)
        lock_path = f"{todo_file}.lock"
        with open(lock_path, "a", encoding="utf-8") as lock_file:
            try:
                import fcntl  # Unix/macOS; unavailable on Windows.

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except ImportError:
                yield


def _load_state(session_id: Optional[str] = None) -> Dict[str, Any]:
    todo_file = _todo_file_path(session_id)
    with TODO_LOCK:
        if os.path.exists(todo_file):
            try:
                with open(todo_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # v1 兼容：旧版直接是 list
                if isinstance(data, list):
                    return {"version": 2, "tasks": [_normalize_task(t) for t in data]}
                if isinstance(data, dict):
                    tasks = data.get("tasks", [])
                    if isinstance(tasks, list):
                        data["version"] = data.get("version", 2)
                        data["tasks"] = [_normalize_task(t) for t in tasks]
                        return data
            except Exception:
                pass
        return _default_state()


def _save_state(state: Dict[str, Any], session_id: Optional[str] = None) -> None:
    todo_file = _todo_file_path(session_id)
    with TODO_LOCK:
        os.makedirs(os.path.dirname(todo_file), exist_ok=True)
        state["version"] = 2
        sid = _current_session_id(session_id)
        if sid:
            state["session_id"] = sid
        tmp_file = f"{todo_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, todo_file)


def _normalize_task(t: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    status = t.get("status", "pending")
    if status not in VALID_STATUSES:
        status = "pending"
    return {
        "id": str(t.get("id")),
        "description": t.get("description", ""),
        "parent_id": t.get("parent_id"),
        "dependencies": [str(x) for x in t.get("dependencies", [])],
        "status": status,
        "result": t.get("result", ""),
        "context_summary": t.get("context_summary", ""),
        "acceptance_criteria": t.get("acceptance_criteria", []),
        "deliverable": t.get("deliverable", ""),
        "assigned_to": t.get("assigned_to", ""),
        "claim": t.get("claim", {}),
        "split_proposal": t.get("split_proposal"),
        "metadata": t.get("metadata", {}),
        "created_at": t.get("created_at", now),
        "updated_at": t.get("updated_at", now),
    }


def _parse_payload(payload: str) -> Any:
    if payload is None or payload == "":
        return {}
    return json.loads(payload)


def _tasks(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return state.setdefault("tasks", [])


def _task_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {t["id"]: t for t in _tasks(state)}


def _find_task(state: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    return _task_map(state).get(str(task_id))


def _ensure_unique_ids(tasks: List[Dict[str, Any]]) -> Optional[str]:
    seen = set()
    for t in tasks:
        tid = str(t.get("id"))
        if not tid or tid == "None":
            return "Task id is required."
        if tid in seen:
            return f"Duplicate task id: {tid}"
        seen.add(tid)
    return None


def _deps_met(task: Dict[str, Any], tmap: Dict[str, Dict[str, Any]]) -> bool:
    for dep in task.get("dependencies", []):
        dt = tmap.get(str(dep))
        if not dt or dt.get("status") != "completed":
            return False
    return True


def _has_children(task_id: str, tasks: List[Dict[str, Any]]) -> bool:
    return any(t.get("parent_id") == task_id for t in tasks)


def _ready_tasks(state: Dict[str, Any]) -> List[str]:
    tmap = _task_map(state)
    all_tasks = _tasks(state)
    ready = []
    for t in all_tasks:
        # 有子任务的父任务由子任务推进，不直接领取执行
        if t.get("status") == "pending" and not _has_children(t["id"], all_tasks) and _deps_met(t, tmap):
            ready.append(t["id"])
    return ready


STATUS_LABELS = {
    "completed": "✅ completed",
    "in_progress": "🚧 in_progress",
    "pending": "🕓 pending",
    "needs_split": "🧩 needs_split",
    "blocked": "🧱 blocked",
    "failed": "❌ failed",
    "cancelled": "🚫 cancelled",
}


def _print_after_status(renderable=None, *args, output_kind: str = "other", **kwargs):
    """Print todo progress without colliding with the parent CLI spinner."""
    progress_render.print_after_status(renderable, *args, output_kind=output_kind, **kwargs)


def _shorten(text, limit=90):
    text = str(text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"



def _task_digest(task: Dict[str, Any], *, result_summary_chars: int = 600, include_artifacts: bool = True) -> Dict[str, Any]:
    metadata = task.get("metadata") or {}
    digest = {
        "id": task.get("id"),
        "description": task.get("description"),
        "parent_id": task.get("parent_id"),
        "dependencies": task.get("dependencies", []),
        "status": task.get("status"),
        "assigned_to": task.get("assigned_to", ""),
        "deliverable": task.get("deliverable", ""),
        "updated_at": task.get("updated_at"),
    }
    if task.get("split_proposal"):
        proposal = task.get("split_proposal") or {}
        digest["split_proposal"] = {
            "rationale": proposal.get("rationale", ""),
            "task_count": len(proposal.get("tasks", []) or []),
            "tasks": proposal.get("tasks", []),
        }
    if metadata.get("blocked_reason"):
        digest["blocked_reason"] = metadata.get("blocked_reason")
    if include_artifacts and metadata.get("context_artifact_path"):
        digest["context_artifact_path"] = metadata.get("context_artifact_path")
    result = str(task.get("result") or "").strip()
    try:
        result_summary_chars = int(result_summary_chars)
    except Exception:
        result_summary_chars = 600
    result_summary_chars = max(0, result_summary_chars)
    if result and result_summary_chars > 0:
        digest["result_summary"] = result[:result_summary_chars] + ("…" if len(result) > result_summary_chars else "")
    return digest


def _todo_digest(
    state: Dict[str, Any],
    *,
    include_completed: bool = True,
    result_summary_chars: int = 600,
    include_artifacts: bool = True,
) -> Dict[str, Any]:
    all_tasks = _tasks(state)
    tasks = all_tasks if include_completed else [t for t in all_tasks if t.get("status") != "completed"]
    status_counts = {s: sum(1 for t in all_tasks if t.get("status") == s) for s in sorted(VALID_STATUSES)}
    return {
        "version": state.get("version", 2),
        "session_id": state.get("session_id", _current_session_id()),
        "total": len(all_tasks),
        "status_counts": status_counts,
        "ready_to_execute": _ready_tasks(state),
        "tasks": [
            _task_digest(t, result_summary_chars=result_summary_chars, include_artifacts=include_artifacts)
            for t in tasks
        ],
    }

def _todo_snapshot_text(state: Dict[str, Any], label: str = "当前任务看板") -> str:
    """Return a user-facing todo board snapshot for direct todo_manage updates."""
    tasks = state.get("tasks", []) if isinstance(state, dict) else []
    total = len(tasks)
    status_order = [
        "completed",
        "in_progress",
        "pending",
        "needs_split",
        "blocked",
        "failed",
        "cancelled",
    ]
    counts = {status: sum(1 for t in tasks if t.get("status") == status) for status in status_order}

    lines = [f"📋 {label}", ""]
    if total == 0:
        lines.append("当前 todo list 为空。")
        return "\n".join(lines)

    completed = counts.get("completed", 0)
    progress = (completed / total * 100) if total else 0
    lines.append(f"总任务：{total}，完成进度：{completed}/{total} ({progress:.1f}%)")
    lines.append("状态统计：" + "，".join(
        f"{STATUS_LABELS[status]} {counts[status]}" for status in status_order
    ))

    def format_task_line(t, include_assignment=True):
        assigned = t.get("assigned_to") or (t.get("claim") or {}).get("worker_id") or "未分配"
        status = t.get("status") or "unknown"
        suffix = f" [{status}, {assigned}]" if include_assignment else f" [{status}]"
        return f"- {t.get('id')}: {_shorten(t.get('description'))}{suffix}"

    def list_tasks(title, filtered, limit=12, include_assignment=True):
        if not filtered:
            return
        lines.append("")
        lines.append(title)
        for t in filtered[:limit]:
            lines.append(format_task_line(t, include_assignment=include_assignment))
        if len(filtered) > limit:
            lines.append(f"- … 还有 {len(filtered) - limit} 个")

    completed_tasks = [t for t in tasks if t.get("status") == "completed"]
    unfinished_tasks = [t for t in tasks if t.get("status") != "completed"]
    list_tasks("✅ 已完成任务：", completed_tasks, include_assignment=False)
    list_tasks("🕓 未完成任务：", unfinished_tasks, include_assignment=True)
    list_tasks("🚧 正在执行：", [t for t in tasks if t.get("status") == "in_progress"])
    list_tasks("🧭 需要父 Agent 处理：", [t for t in tasks if t.get("status") in {"blocked", "needs_split", "failed"}])

    ready_ids = _ready_tasks(state)
    if ready_ids:
        lines.append("")
        lines.append("Ready 任务：" + "，".join(ready_ids[:10]))
        if len(ready_ids) > 10:
            lines.append(f"- … 还有 {len(ready_ids) - 10} 个 ready 任务")

    return "\n".join(lines)


def _print_todo_snapshot(state: Dict[str, Any], label: str = "当前任务看板") -> None:
    _print_after_status(Panel(_todo_snapshot_text(state, label), title="Todo Progress", border_style="yellow", expand=False), output_kind="todo_board")


def _children_of(state: Dict[str, Any], parent_id: Optional[str]) -> List[Dict[str, Any]]:
    return [t for t in _tasks(state) if t.get("parent_id") == parent_id]


def _build_tree(state: Dict[str, Any], parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    result = []
    for t in _children_of(state, parent_id):
        node = dict(t)
        node["children"] = _build_tree(state, t["id"])
        result.append(node)
    return result


def _subtree_ids(state: Dict[str, Any], root_id: str) -> List[str]:
    ids = [root_id]
    for child in _children_of(state, root_id):
        ids.extend(_subtree_ids(state, child["id"]))
    return ids


def _next_child_id(state: Dict[str, Any], parent_id: str) -> str:
    existing = {t["id"] for t in _tasks(state)}
    i = 1
    while f"{parent_id}.{i}" in existing:
        i += 1
    return f"{parent_id}.{i}"


def _add_tasks(state: Dict[str, Any], raw_tasks: List[Dict[str, Any]], default_parent: Optional[str] = None) -> Tuple[bool, str, List[str]]:
    current_ids = {t["id"] for t in _tasks(state)}
    new_tasks = []
    created_ids = []
    for raw in raw_tasks:
        item = dict(raw)
        if not item.get("id") or str(item.get("id")) == "None":
            if not default_parent:
                return False, "Task id is required unless parent_id/default parent is provided.", []
            i = 1
            while f"{default_parent}.{i}" in current_ids:
                i += 1
            item["id"] = f"{default_parent}.{i}"
        item["id"] = str(item["id"])
        if item["id"] in current_ids:
            return False, f"Duplicate task id: {item['id']}", []
        if default_parent and not item.get("parent_id"):
            item["parent_id"] = default_parent
        new_tasks.append(_normalize_task(item))
        created_ids.append(item["id"])
        current_ids.add(item["id"])
    _tasks(state).extend(new_tasks)
    return True, "Tasks added successfully.", created_ids



def _reap_stale_claims(state: Dict[str, Any], *, mode: str = "blocked") -> List[str]:
    """Mark expired in_progress claims so parent schedulers do not wait forever."""
    now = time.time()
    changed = []
    for task in _tasks(state):
        if task.get("status") != "in_progress":
            continue
        claim = task.get("claim") or {}
        claimed_at = int(claim.get("claimed_at") or task.get("updated_at") or now)
        lease_minutes = claim.get("lease_minutes", 60)
        try:
            lease_seconds = max(0.001, float(lease_minutes) * 60.0)
        except Exception:
            lease_seconds = 3600
        if now - claimed_at <= lease_seconds:
            continue
        if mode == "pending":
            task["status"] = "pending"
            task["assigned_to"] = ""
            task["claim"] = {}
        else:
            task["status"] = "blocked"
            task["result"] = (
                f"任务 claim 已超过 lease_minutes={lease_minutes}，自动标记为 blocked；"
                "等待父 Agent 判断是否重试、释放或拆分。"
            )
            task["metadata"] = {**(task.get("metadata") or {}), "blocked_reason": "stale_claim_reaped"}
        task["updated_at"] = now
        changed.append(task["id"])
    return changed

def _todo_manage_unlocked(action: str, payload: str = "{}") -> str:
    """管理树状、动态、带拓扑依赖的任务看板。调用方必须持有 TODO_LOCK。"""
    state = _load_state()

    try:
        data = _parse_payload(payload)
        if isinstance(data, dict) and data.get("session_id"):
            # Wrapper normally sets session before loading; keep payload session for diagnostics only.
            state["session_id"] = _current_session_id(data.get("session_id")) or state.get("session_id")

        if action == "digest":
            include_completed = True if not isinstance(data, dict) else data.get("include_completed", True)
            result_summary_chars = 600 if not isinstance(data, dict) else data.get("result_summary_chars", 600)
            include_artifacts = True if not isinstance(data, dict) else data.get("include_artifacts", True)
            return json.dumps(
                _todo_digest(
                    state,
                    include_completed=bool(include_completed),
                    result_summary_chars=result_summary_chars,
                    include_artifacts=bool(include_artifacts),
                ),
                ensure_ascii=False,
                indent=2,
            )

        if action == "view":
            include_tree = True if not isinstance(data, dict) else data.get("include_tree", True)
            status_filter = None if not isinstance(data, dict) else data.get("status")
            parent_id = None if not isinstance(data, dict) else data.get("parent_id")
            tasks = _tasks(state)
            if status_filter:
                tasks = [t for t in tasks if t.get("status") == status_filter]
            if parent_id is not None:
                tasks = [t for t in tasks if t.get("parent_id") == parent_id]
            result = {
                "version": state.get("version", 2),
                "todo_list": tasks,
                "ready_to_execute": _ready_tasks(state),
                "status_counts": {s: sum(1 for t in _tasks(state) if t.get("status") == s) for s in sorted(VALID_STATUSES)},
            }
            if include_tree:
                result["tree"] = _build_tree(state)
            return json.dumps(result, ensure_ascii=False, indent=2)

        elif action == "ready":
            ready_ids = _ready_tasks(state)
            result = {"ready_to_execute": ready_ids}
            include_tasks = bool(data.get("include_tasks")) if isinstance(data, dict) else False
            if include_tasks:
                tmap = _task_map(state)
                result_summary_chars = data.get("result_summary_chars", 600) if isinstance(data, dict) else 600
                include_artifacts = data.get("include_artifacts", True) if isinstance(data, dict) else True
                result["tasks"] = [
                    _task_digest(tmap[i], result_summary_chars=result_summary_chars, include_artifacts=bool(include_artifacts))
                    for i in ready_ids
                    if i in tmap
                ]
            return json.dumps(result, ensure_ascii=False, indent=2)

        elif action == "get":
            task_id = str(data.get("id"))
            task = _find_task(state, task_id)
            if not task:
                return f"Error: Task {task_id} not found."
            subtree = [_find_task(state, i) for i in _subtree_ids(state, task_id)]
            return json.dumps({"task": task, "subtree": subtree}, ensure_ascii=False, indent=2)

        elif action == "init":
            raw_tasks = data.get("tasks", data) if isinstance(data, dict) else data
            if not isinstance(raw_tasks, list):
                return "Error: payload for init must be a JSON array of tasks or {tasks:[...]}."
            normalized = [_normalize_task(t) for t in raw_tasks]
            err = _ensure_unique_ids(normalized)
            if err:
                return f"Error: {err}"
            state = {"version": 2, "tasks": normalized}
            _save_state(state)
            _print_todo_snapshot(state, "树状任务看板已初始化")
            return "Todo list initialized successfully."

        elif action == "add":
            raw_tasks = data.get("tasks", data) if isinstance(data, dict) else data
            default_parent = data.get("parent_id") if isinstance(data, dict) else None
            if not isinstance(raw_tasks, list):
                return "Error: payload for add must be a JSON array of tasks or {tasks:[...]}."
            ok, msg, created = _add_tasks(state, raw_tasks, default_parent)
            if not ok:
                return f"Error: {msg}"
            _save_state(state)
            _print_todo_snapshot(state, "任务已添加")
            return json.dumps({"message": msg, "created_ids": created}, ensure_ascii=False)

        elif action == "update":
            task_id = str(data.get("id"))
            task = _find_task(state, task_id)
            if not task:
                return f"Error: Task {task_id} not found."
            allowed_fields = {
                "description", "parent_id", "dependencies", "status", "result", "context_summary",
                "acceptance_criteria", "deliverable", "assigned_to", "claim", "split_proposal", "metadata"
            }
            for key, value in data.items():
                if key == "id":
                    continue
                if key in allowed_fields:
                    if key == "status" and value not in VALID_STATUSES:
                        return f"Error: invalid status {value}."
                    task[key] = value
            task["updated_at"] = _now()
            _save_state(state)
            _print_todo_snapshot(state, f"任务 {task_id} 状态更新为 {task.get('status')}")
            return f"Task {task_id} updated successfully."

        elif action == "claim":
            task_id = str(data.get("id"))
            worker_id = data.get("worker_id", f"worker-{_now()}")
            task = _find_task(state, task_id)
            if not task:
                return f"Error: Task {task_id} not found."
            if task.get("status") != "pending":
                return f"Error: Task {task_id} is not pending; current status={task.get('status')}"
            if task_id not in _ready_tasks(state):
                return f"Error: Task {task_id} is not ready; dependencies may be unmet or it has children."
            task["status"] = "in_progress"
            task["assigned_to"] = worker_id
            task["claim"] = {
                "worker_id": worker_id,
                "claimed_at": _now(),
                "lease_minutes": data.get("lease_minutes", 60),
                "max_iterations": data.get("max_iterations"),
            }
            task["updated_at"] = _now()
            _save_state(state)
            _print_todo_snapshot(state, f"任务 {task_id} 已领取")
            return json.dumps({"message": "Task claimed.", "task": task}, ensure_ascii=False, indent=2)

        elif action == "release":
            task_id = str(data.get("id"))
            task = _find_task(state, task_id)
            if not task:
                return f"Error: Task {task_id} not found."
            task["status"] = "pending"
            task["assigned_to"] = ""
            task["claim"] = {}
            task["updated_at"] = _now()
            _save_state(state)
            _print_todo_snapshot(state, f"任务 {task_id} 已释放")
            return f"Task {task_id} released."

        elif action == "propose_split":
            task_id = str(data.get("id"))
            task = _find_task(state, task_id)
            if not task:
                return f"Error: Task {task_id} not found."
            proposal = data.get("proposal", data.get("tasks", []))
            if not isinstance(proposal, list) or not proposal:
                return "Error: proposal/tasks must be a non-empty array."
            task["status"] = "needs_split"
            task["split_proposal"] = {
                "rationale": data.get("rationale", ""),
                "tasks": proposal,
                "proposed_by": data.get("proposed_by", task.get("assigned_to", "")),
                "created_at": _now(),
            }
            task["updated_at"] = _now()
            _save_state(state)
            _print_todo_snapshot(state, f"任务 {task_id} 提出拆分建议")
            return json.dumps({"message": "Split proposal recorded; parent process must approve or reject.", "task": task}, ensure_ascii=False, indent=2)

        elif action == "approve_split":
            task_id = str(data.get("id"))
            task = _find_task(state, task_id)
            if not task:
                return f"Error: Task {task_id} not found."
            proposal_tasks = data.get("tasks")
            if proposal_tasks is None:
                proposal_tasks = (task.get("split_proposal") or {}).get("tasks", [])
            if not isinstance(proposal_tasks, list) or not proposal_tasks:
                return "Error: no proposal tasks to approve."
            ok, msg, created = _add_tasks(state, proposal_tasks, default_parent=task_id)
            if not ok:
                return f"Error: {msg}"
            task["status"] = data.get("parent_status", "blocked")
            task["result"] = data.get("result", task.get("result", "拆分已批准，等待子任务完成。"))
            task["updated_at"] = _now()
            _save_state(state)
            _print_todo_snapshot(state, f"任务 {task_id} 拆分已批准")
            return json.dumps({"message": "Split approved.", "parent_id": task_id, "created_ids": created}, ensure_ascii=False, indent=2)

        elif action == "reject_split":
            task_id = str(data.get("id"))
            task = _find_task(state, task_id)
            if not task:
                return f"Error: Task {task_id} not found."
            task["status"] = data.get("set_status", "pending")
            task["result"] = data.get("reason", "拆分建议被父进程拒绝。")
            task["updated_at"] = _now()
            _save_state(state)
            _print_todo_snapshot(state, f"任务 {task_id} 拆分建议已拒绝")
            return f"Split proposal for task {task_id} rejected."

        elif action == "reap_stale_claims":
            mode = data.get("mode", "blocked") if isinstance(data, dict) else "blocked"
            changed = _reap_stale_claims(state, mode=mode)
            if changed:
                _save_state(state)
                _print_todo_snapshot(state, "已回收过期 claim")
            return json.dumps({"reaped": changed, "mode": mode, "session_id": state.get("session_id", _current_session_id())}, ensure_ascii=False, indent=2)

        elif action == "clear":
            state = _default_state()
            _save_state(state)
            _print_todo_snapshot(state, "任务看板已清空")
            return "Todo list cleared."

        else:
            return f"Error: Unknown action '{action}'"

    except Exception as e:
        return f"Error: {str(e)}"


def _session_from_payload(payload: str) -> str:
    try:
        data = _parse_payload(payload)
        if isinstance(data, dict):
            return _current_session_id(data.get("session_id"))
    except Exception:
        pass
    return ""


def todo_manage(action: str, payload: str = "{}", session_id: str = "") -> str:
    """管理树状、动态、带拓扑依赖的任务看板。

    session_id 会隔离 todo 文件；启用 SESSION_SANDBOX_ENABLED 时写到当前 session
    沙箱的 todo_lists/todo_list.json，否则沿用 sandbox/todo_lists/todo_list_<session>.json。
    CLI/GUI/父子 Agent 会自动注入同一个 session_id，避免多个终端互相覆盖。
    未提供 session_id 时保留旧 sandbox/todo_list.json 兼容直接工具调用和旧测试。
    """
    sid = _current_session_id(session_id) or _session_from_payload(payload)
    with _todo_session(sid):
        with _todo_file_lock(sid):
            return _todo_manage_unlocked(action, payload)


registry.register(
    name="todo_manage",
    description=(
        "管理复杂任务的动态 Todo List（树状任务看板），支持父子任务、拓扑依赖、领取、拆分提案与审批。父进程用它统筹全局，子任务只在足够具体时执行。\n"
        "操作(action)包括：\n"
        "- 'init': 初始化看板，payload 为任务数组或 {tasks:[...]}。任务字段可含 id, description, parent_id, dependencies, context_summary, acceptance_criteria, deliverable。\n"
        "- 'view': 查看任务、树结构、状态统计和 ready_to_execute。payload 可含 include_tree/status/parent_id。\n"
        "- 'digest': 返回任务看板梗概，payload 可含 include_completed(default true)、result_summary_chars(default 600)、include_artifacts(default true)；包含任务状态、摘要、错误和可选 context_artifact_path，不返回子进程完整上下文。\n"
        "- 'ready': 默认只返回 {ready_to_execute:[ids]}；payload include_tasks=true 时返回对应 compact/digest 任务（非完整 task），也支持 result_summary_chars/include_artifacts。\n"
        "- 'get': 查看单个任务及其子树，payload {id}。\n"
        "- 'add': 追加任务，payload 为任务数组或 {parent_id, tasks:[...]}。\n"
        "- 'update': 更新任务字段或状态，status 可选 pending/in_progress/needs_split/blocked/completed/failed/cancelled。\n"
        "- 'claim': 领取 ready 任务，payload {id, worker_id, lease_minutes, max_iterations}；会置为 in_progress。\n"
        "- 'release': 释放任务回 pending，payload {id}。\n"
        "- 'propose_split': 记录拆分建议但不执行拆分，payload {id, rationale, proposal:[...]}，会置为 needs_split。\n"
        "- 'approve_split': 父进程批准拆分，payload {id} 使用已有提案，或 {id,tasks:[...]}；新增子任务并默认把父任务置为 blocked。\n"
        "- 'reject_split': 父进程拒绝拆分，payload {id, reason, set_status}。\n"
        "- 'reap_stale_claims': 回收超过 lease_minutes 的 in_progress 任务，默认标记 blocked。\n- 'clear': 清空看板。\n"
        "注意：payload 必须是合法 JSON 字符串。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["init", "view", "digest", "ready", "get", "add", "update", "claim", "release", "propose_split", "approve_split", "reject_split", "reap_stale_claims", "clear"],
                "description": "要执行的操作类型"
            },
            "payload": {
                "type": "string",
                "description": "操作对应的数据 JSON 字符串；view/ready/clear 可为空 '{}'；digest 支持 include_completed/result_summary_chars/include_artifacts；ready 支持 include_tasks"
            },
            "session_id": {
                "type": "string",
                "description": "可选会话编号；通常不要手动传，CLI/GUI/父 Agent 会自动注入。启用 session sandbox 时看板位于该 session 的 todo_lists/todo_list.json，否则沿用全局 todo_lists 兼容路径；'default' 是旧版空 session，不代表当前会话。"
            }
        },
        "required": ["action"]
    },
    handler=todo_manage
)
