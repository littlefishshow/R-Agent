import os
import json
import time
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple
from tools.registry import registry
from rich.console import Console

console = Console()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODO_FILE = os.path.join(BASE_DIR, "sandbox", "todo_list.json")
TODO_LOCK = threading.RLock()
TODO_LOCK_FILE = f"{TODO_FILE}.lock"

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
def _todo_file_lock():
    """Serialize todo read-modify-write actions across threads and processes."""
    with TODO_LOCK:
        os.makedirs(os.path.dirname(TODO_FILE), exist_ok=True)
        lock_path = f"{TODO_FILE}.lock"
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


def _load_state() -> Dict[str, Any]:
    with TODO_LOCK:
        if os.path.exists(TODO_FILE):
            try:
                with open(TODO_FILE, "r", encoding="utf-8") as f:
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


def _save_state(state: Dict[str, Any]) -> None:
    with TODO_LOCK:
        os.makedirs(os.path.dirname(TODO_FILE), exist_ok=True)
        state["version"] = 2
        tmp_file = f"{TODO_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, TODO_FILE)


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


def _todo_manage_unlocked(action: str, payload: str = "{}") -> str:
    """管理树状、动态、带拓扑依赖的任务看板。调用方必须持有 TODO_LOCK。"""
    state = _load_state()

    try:
        data = _parse_payload(payload)

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
            tmap = _task_map(state)
            return json.dumps({"ready_to_execute": ready_ids, "tasks": [tmap[i] for i in ready_ids]}, ensure_ascii=False, indent=2)

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
            console.print("\n[bold yellow]📋 树状任务看板已初始化[/bold yellow]")
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
            console.print(f"\n[bold yellow]📋 任务 {task_id} 状态更新为 {task.get('status')}[/bold yellow]")
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
            return f"Split proposal for task {task_id} rejected."

        elif action == "clear":
            _save_state(_default_state())
            console.print("\n[bold yellow]📋 任务看板已清空[/bold yellow]")
            return "Todo list cleared."

        else:
            return f"Error: Unknown action '{action}'"

    except Exception as e:
        return f"Error: {str(e)}"


def todo_manage(action: str, payload: str = "{}") -> str:
    """管理树状、动态、带拓扑依赖的任务看板。

    delegate_task 会并发运行多个子 Agent；子 Agent 的 todo_manage 调用可能
    来自多个线程或隔离工具子进程。对完整 action 加线程锁 + 文件锁，避免
    “读旧 state -> 覆盖新 state”的 JSON 写丢失，保证进度快照稳定。
    """
    with _todo_file_lock():
        return _todo_manage_unlocked(action, payload)


registry.register(
    name="todo_manage",
    description=(
        "管理复杂任务的动态 Todo List（树状任务看板），支持父子任务、拓扑依赖、领取、拆分提案与审批。父进程用它统筹全局，子任务只在足够具体时执行。\n"
        "操作(action)包括：\n"
        "- 'init': 初始化看板，payload 为任务数组或 {tasks:[...]}。任务字段可含 id, description, parent_id, dependencies, context_summary, acceptance_criteria, deliverable。\n"
        "- 'view': 查看任务、树结构、状态统计和 ready_to_execute。payload 可含 include_tree/status/parent_id。\n"
        "- 'ready': 只返回依赖已满足、没有子任务、可立即领取的 pending 任务。\n"
        "- 'get': 查看单个任务及其子树，payload {id}。\n"
        "- 'add': 追加任务，payload 为任务数组或 {parent_id, tasks:[...]}。\n"
        "- 'update': 更新任务字段或状态，status 可选 pending/in_progress/needs_split/blocked/completed/failed/cancelled。\n"
        "- 'claim': 领取 ready 任务，payload {id, worker_id, lease_minutes, max_iterations}；会置为 in_progress。\n"
        "- 'release': 释放任务回 pending，payload {id}。\n"
        "- 'propose_split': 记录拆分建议但不执行拆分，payload {id, rationale, proposal:[...]}，会置为 needs_split。\n"
        "- 'approve_split': 父进程批准拆分，payload {id} 使用已有提案，或 {id,tasks:[...]}；新增子任务并默认把父任务置为 blocked。\n"
        "- 'reject_split': 父进程拒绝拆分，payload {id, reason, set_status}。\n"
        "- 'clear': 清空看板。\n"
        "注意：payload 必须是合法 JSON 字符串。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["init", "view", "ready", "get", "add", "update", "claim", "release", "propose_split", "approve_split", "reject_split", "clear"],
                "description": "要执行的操作类型"
            },
            "payload": {
                "type": "string",
                "description": "操作对应的数据 JSON 字符串；view/ready/clear 可为空 '{}'"
            }
        },
        "required": ["action"]
    },
    handler=todo_manage
)
