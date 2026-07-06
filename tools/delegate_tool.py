import json
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from tools.registry import registry
from core import config
from app_gui.schemas import EVENT_DELEGATE_SUBAGENT_STARTED, EVENT_DELEGATE_SUBAGENT_FINISHED
from rich.panel import Panel
from tools import progress_render


def _current_cli_status():
    return progress_render._current_cli_status()


def _print_after_status(renderable=None, *args, output_kind: str = "other", **kwargs):
    """Print tool progress without colliding with the parent CLI status line."""
    progress_render.print_after_status(
        renderable,
        *args,
        status_getter=_current_cli_status,
        output_kind=output_kind,
        **kwargs,
    )


TRUNCATION_MARKER = "已达迭代上限"


STATUS_LABELS = {
    "completed": "✅ completed",
    "in_progress": "🚧 in_progress",
    "pending": "🕓 pending",
    "needs_split": "🧩 needs_split",
    "blocked": "🧱 blocked",
    "failed": "❌ failed",
    "cancelled": "🚫 cancelled",
}



def _emit_delegate_event(event_sink, event_type, payload):
    if event_sink is None:
        return
    try:
        if hasattr(event_sink, "emit"):
            event_sink.emit(event_type, payload, source="delegate_task")
        else:
            event_sink(event_type, payload)
    except Exception:
        pass


def _agent_token_usage_summary(agent):
    if agent is None:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "last_prompt_tokens": 0,
            "last_completion_tokens": 0,
            "last_total_tokens": 0,
            "available": False,
        }
    getter = getattr(agent, "get_token_usage_summary", None)
    if callable(getter):
        try:
            summary = getter(include_children=True)
            if isinstance(summary, dict):
                return summary
        except TypeError:
            try:
                summary = getter()
                if isinstance(summary, dict):
                    return summary
            except Exception:
                pass
        except Exception:
            pass
    usage = getattr(agent, "token_usage", {}) or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
        "last_prompt_tokens": int(usage.get("last_prompt_tokens", 0) or 0),
        "last_completion_tokens": int(usage.get("last_completion_tokens", 0) or 0),
        "last_total_tokens": int(usage.get("last_total_tokens", 0) or 0),
        "available": bool(usage.get("available")),
    }


def _usage_int(usage, key):
    if not isinstance(usage, dict):
        return 0
    try:
        return int(usage.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _usage_total_for_aggregation(summary):
    if not isinstance(summary, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "available": False}
    parent_available = bool(summary.get("available"))
    parent_prompt = _usage_int(summary, "prompt_tokens") if parent_available else 0
    parent_completion = _usage_int(summary, "completion_tokens") if parent_available else 0
    parent_total = _usage_int(summary, "total_tokens") if parent_available else 0
    delegated = summary.get("delegated_token_usage") if isinstance(summary.get("delegated_token_usage"), dict) else {}
    child_available = bool(delegated.get("available"))
    child_prompt = _usage_int(delegated, "prompt_tokens") if child_available else 0
    child_completion = _usage_int(delegated, "completion_tokens") if child_available else 0
    child_total = _usage_int(delegated, "total_tokens") if child_available else 0
    total = parent_total + child_total
    prompt = parent_prompt + child_prompt
    completion = parent_completion + child_completion
    available = parent_available or child_available
    if not total:
        total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "available": bool(available and any((prompt, completion, total))),
    }


def _sum_token_usage(items):
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "available": False}
    for item in items or []:
        usage = item.get("token_usage") if isinstance(item, dict) else None
        aggregate = _usage_total_for_aggregation(usage)
        if not aggregate.get("available"):
            continue
        total["prompt_tokens"] += _usage_int(aggregate, "prompt_tokens")
        total["completion_tokens"] += _usage_int(aggregate, "completion_tokens")
        total["total_tokens"] += _usage_int(aggregate, "total_tokens")
        total["available"] = True
    return total


def _task_id_of(task):
    return task.get("task_id") or task.get("id")


def _shorten(text, limit=90):
    text = str(text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _context_dir(session_id=None):
    safe = str(session_id or "default").replace("/", "_").replace("..", "_")[:80] or "default"
    path = Path("sandbox") / "delegate_contexts" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _plain_message(message):
    if message is None or isinstance(message, (str, int, float, bool)):
        return message
    if isinstance(message, dict):
        return {str(k): _plain_message(v) for k, v in message.items()}
    if isinstance(message, (list, tuple)):
        return [_plain_message(v) for v in message]
    if hasattr(message, "model_dump"):
        try:
            return _plain_message(message.model_dump())
        except Exception:
            pass
    if hasattr(message, "to_dict"):
        try:
            return _plain_message(message.to_dict())
        except Exception:
            pass
    if hasattr(message, "__dict__"):
        public = {k: v for k, v in vars(message).items() if not k.startswith("_")}
        if public:
            return _plain_message(public)
    return str(message)


def _save_subagent_context(task_id, sub_agent, reason, *, session_id=None, run_id=""):
    """Persist sub-agent context only for unfinished/error cases; never return it inline."""
    if sub_agent is None:
        return None
    try:
        messages = getattr(sub_agent, "messages", [])
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        safe_task = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in str(task_id or "adhoc"))[:80]
        path = _context_dir(session_id) / f"{stamp}_{safe_task}_{reason}.json"
        payload = {
            "task_id": task_id,
            "run_id": run_id,
            "reason": reason,
            "saved_at": time.time(),
            "message_count": len(messages or []),
            "messages": [_plain_message(m) for m in (messages or [])],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return str(path)
    except Exception:
        return None


def _clear_subagent_context(sub_agent):
    try:
        sub_agent.messages = []
    except Exception:
        pass


def _delete_context_artifact(path):
    if not path:
        return False
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve()
        root = (Path.cwd() / "sandbox" / "delegate_contexts").resolve()
        if root in candidate.parents and candidate.is_file():
            candidate.unlink()
            return True
    except Exception:
        pass
    return False


def _clear_task_context_metadata(task, *, session_id=None, delete_file=True):
    if not task:
        return False
    metadata = dict((task or {}).get("metadata") or {})
    path = metadata.pop("context_artifact_path", None)
    metadata.pop("context_saved_reason", None)
    deleted = _delete_context_artifact(path) if delete_file else False
    if path or deleted:
        try:
            from tools.todo_tool import todo_manage
            todo_manage(
                "update",
                json.dumps({"id": str(task.get("id")), "metadata": metadata}, ensure_ascii=False),
                session_id=session_id or "",
            )
        except Exception:
            pass
    return deleted


def _all_tasks_completed(session_id=None):
    try:
        state = _load_todo_state(session_id)
        tasks = state.get("tasks", []) if isinstance(state, dict) else []
        return bool(tasks) and all(t.get("status") == "completed" for t in tasks)
    except Exception:
        return False


def _cleanup_all_completed_contexts(session_id=None):
    """When the whole todo is successful, delete every saved child context together."""
    if not _all_tasks_completed(session_id):
        return 0
    deleted = 0
    try:
        state = _load_todo_state(session_id)
        for task in state.get("tasks", []):
            if _clear_task_context_metadata(task, session_id=session_id, delete_file=True):
                deleted += 1
        # Remove any orphan files for this session as a best-effort cleanup.
        ctx_dir = _context_dir(session_id)
        for path in list(ctx_dir.glob("*.json")):
            try:
                path.unlink()
                deleted += 1
            except Exception:
                pass
    except Exception:
        pass
    return deleted

def _summarize_delegate_result(result, limit=600):
    text = str(result or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _load_todo_state(session_id=None):
    """Best-effort load of the shared todo state for progress snapshots."""
    try:
        from tools import todo_tool

        return todo_tool._load_state(session_id)
    except Exception:
        return {"version": 2, "tasks": []}


def _todo_snapshot_text(label="当前任务快照", scheduled_tasks=None, session_id=None):
    """Return a compact, user-facing todo progress snapshot."""
    state = _load_todo_state(session_id)
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
    else:
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

        def list_tasks(title, filtered, limit=8, include_assignment=True):
            if not filtered:
                return
            lines.append("")
            lines.append(title)
            for t in filtered[:limit]:
                lines.append(format_task_line(t, include_assignment=include_assignment))
            if len(filtered) > limit:
                lines.append(f"- … 还有 {len(filtered) - limit} 个")

        scheduled_ids = [_task_id_of(t) for t in scheduled_tasks or [] if isinstance(t, dict) and _task_id_of(t)]
        if scheduled_ids:
            tmap = {str(t.get("id")): t for t in tasks}
            lines.append("")
            lines.append("本次准备/正在委托：")
            for task_id in scheduled_ids:
                todo_task = tmap.get(str(task_id))
                if todo_task:
                    lines.append(
                        f"- {task_id}: {_shorten(todo_task.get('description'))}"
                        f" [{todo_task.get('status')}]"
                    )
                else:
                    lines.append(f"- {task_id}: 未在 todo list 中找到，仅作为普通委托任务执行")

        completed_tasks = [t for t in tasks if t.get("status") == "completed"]
        unfinished_tasks = [t for t in tasks if t.get("status") != "completed"]
        list_tasks("✅ 已完成任务：", completed_tasks, limit=12, include_assignment=False)
        list_tasks("🕓 未完成任务：", unfinished_tasks, limit=12, include_assignment=True)

        # 下面保留重点提醒区，便于父 Agent 调度，不需要用户从明细中再筛选。
        list_tasks("🚧 正在执行：", [t for t in tasks if t.get("status") == "in_progress"])
        list_tasks("🧭 需要父 Agent 处理：", [t for t in tasks if t.get("status") in {"blocked", "needs_split", "failed"}])

        try:
            from tools import todo_tool

            ready_ids = todo_tool._ready_tasks(state)
        except Exception:
            ready_ids = []
        if ready_ids:
            lines.append("")
            lines.append("Ready 任务：" + "，".join(ready_ids[:10]))
            if len(ready_ids) > 10:
                lines.append(f"- … 还有 {len(ready_ids) - 10} 个 ready 任务")

    return "\n".join(lines)


def _print_todo_snapshot(label="当前任务快照", scheduled_tasks=None, session_id=None):
    _print_after_status(Panel(_todo_snapshot_text(label, scheduled_tasks, session_id=session_id), title="Todo Progress", border_style="yellow", expand=False), output_kind="todo_board")


def _get_todo_task(task_id, session_id=None):
    if not task_id:
        return None
    try:
        from tools import todo_tool

        state = todo_tool._load_state(session_id)
        return todo_tool._find_task(state, str(task_id))
    except Exception:
        return None


def _mark_truncated_task_blocked(task_id, result, max_iters, session_id=None):
    """Avoid leaving a claimed todo task stuck in in_progress after sub-agent truncation."""
    if not task_id:
        return None
    task = _get_todo_task(task_id, session_id=session_id)
    if not task or task.get("status") != "in_progress":
        return None

    reason = (
        f"子 Agent 达到 max_iterations={max_iters}，已强制收尾但未可靠完成任务；"
        "任务自动标记为 blocked，等待父 Agent 决定是否扩展预算、拆分任务或人工处理。\n\n"
        f"强制收尾结果：\n{result}"
    )
    try:
        from tools.todo_tool import todo_manage

        update_result = todo_manage(
            "update",
            json.dumps(
                {
                    "id": str(task_id),
                    "status": "blocked",
                    "result": reason,
                    "metadata": {
                        **(task.get("metadata") or {}),
                        "blocked_reason": "subagent_max_iterations_reached",
                        "max_iterations": max_iters,
                    },
                },
                ensure_ascii=False,
            ),
            session_id=session_id or "",
        )
        return update_result
    except Exception as exc:
        return f"Error: failed to mark task {task_id} blocked after truncation: {exc}"


def _is_truncated_result(result, sub_agent=None):
    if sub_agent is not None:
        try:
            if sub_agent.is_truncated():
                return True
        except Exception:
            pass
    return TRUNCATION_MARKER in str(result or "")



def _is_model_failure_result(result) -> bool:
    text = str(result or "")
    markers = ("模型请求失败", "模型强制收尾失败", "context_length_exceeded", "maximum context length", "too many tokens")
    return any(marker in text for marker in markers)


def _mark_task_blocked(task_id, reason, *, session_id=None, metadata=None):
    if not task_id:
        return None
    task = _get_todo_task(task_id, session_id=session_id)
    if not task or task.get("status") != "in_progress":
        return None
    try:
        from tools.todo_tool import todo_manage
        return todo_manage(
            "update",
            json.dumps({
                "id": str(task_id),
                "status": "blocked",
                "result": str(reason),
                "metadata": {**(task.get("metadata") or {}), **(metadata or {})},
            }, ensure_ascii=False),
            session_id=session_id or "",
        )
    except Exception as exc:
        return f"Error: failed to mark task {task_id} blocked: {exc}"

def delegate_task(tasks: str, max_workers: int = None, default_max_iterations: int = 30, session_id: str = "", default_wall_timeout_seconds=None, event_sink=None) -> str:
    """生成隔离上下文子智能体并行处理任务。

    tasks 是 JSON 字符串，元素可以是：
    - {"goal": "完整、自包含任务描述", "max_iterations": 20}
    - {"task_id": "t1", "goal": "可选补充说明", "worker_id": "w1", "max_iterations": 20}

    若提供 task_id，子智能体会被提示使用 todo_manage claim/get/propose_split/update 来遵循动态任务看板协议。
    delegate_task 会在启动、单个子 Agent 结束和整体结束时自动打印 todo 进度快照。
    """
    try:
        task_list = json.loads(tasks)
        if not isinstance(task_list, list):
            return json.dumps({"error": "tasks must be a JSON array of objects."}, ensure_ascii=False)
    except json.JSONDecodeError:
        return json.dumps({"error": "tasks must be a valid JSON string."}, ensure_ascii=False)

    session_id = str(session_id or "")
    if default_wall_timeout_seconds is None:
        default_wall_timeout_seconds = config.get_delegate_task_wall_timeout()
    try:
        default_wall_timeout_seconds = None if default_wall_timeout_seconds is None else float(default_wall_timeout_seconds)
    except Exception:
        default_wall_timeout_seconds = config.get_delegate_task_wall_timeout()

    if default_max_iterations is None:
        default_max_iterations = 30
    try:
        default_max_iterations = int(default_max_iterations)
    except Exception:
        default_max_iterations = 30
    default_max_iterations = max(1, min(default_max_iterations, 200))

    results = []

    # We must import RAgent here to avoid circular dependencies
    from core.agent import RAgent

    def _format_goal(task):
        task_id = _task_id_of(task)
        goal = task.get("goal") or task.get("description") or ""
        worker_id = task.get("worker_id") or (f"subagent-{task_id}" if task_id else "")
        if task_id:
            return (
                f"你被分配处理动态 todo list 中的任务。\n"
                f"task_id: {task_id}\n"
                f"worker_id: {worker_id}\n"
                f"补充目标/背景：\n{goal}\n\n"
                "必须遵循协议：\n"
                "1. 先调用 todo_manage get 查看该任务及上下文；再调用 todo_manage claim 领取该任务（payload 包含 id、worker_id、max_iterations）。\n"
                "2. 领取后先判断任务是否足够具体、可在当前 max_iterations 内完成。\n"
                "3. 如果任务过于笼统、依赖不明、需要并行拆解或验收标准不清晰，不要强行执行；调用 todo_manage propose_split 给出拆分建议。proposal 中每个子任务要包含 description、dependencies、context_summary、acceptance_criteria、deliverable；然后结束并汇报已提出拆分。\n"
                "4. 如果任务足够具体，则完成任务；完成后调用 todo_manage update 将该任务置为 completed，并写入 result。失败则置为 failed 并说明原因。\n"
                "5. 如果达到最大思考轮数或被强制收尾，应在最终文本中明确未完成事项；父进程会把仍处于 in_progress 的任务标记为 blocked，等待后续调度。\n"
                "6. 只处理自己领取的任务；不要擅自批准拆分，approve_split/reject_split 只能由父进程执行。"
            )
        return goal

    subagent_refs = {}
    subagent_refs_lock = threading.Lock()

    def _run_subagent(task_index, task, cancel_event=None):
        goal = _format_goal(task)
        max_iters = task.get("max_iterations", default_max_iterations)
        try:
            max_iters = int(max_iters)
        except Exception:
            max_iters = default_max_iterations
        max_iters = max(1, min(max_iters, 200))
        task_id = _task_id_of(task)

        if task_id:
            _print_after_status(
                f"\n[bold blue]🚀 [Sub-Agent {task_index}][/bold blue] "
                f"开始执行 task_id={task_id} max_iterations={max_iters}"
            )
        else:
            _print_after_status(
                f"\n[bold blue]🚀 [Sub-Agent {task_index}][/bold blue] "
                f"开始执行普通委托任务(max_iterations={max_iters}): {goal[:500]}"
            )
        try:
            sub_agent = RAgent(max_iterations=max_iters, session_id=session_id)
        except TypeError:
            sub_agent = RAgent(max_iterations=max_iters)
            try:
                setattr(sub_agent, "session_id", session_id)
            except Exception:
                pass
        cancel_event = cancel_event or threading.Event()
        run_id = f"delegate-{task_index}-{task_id or 'adhoc'}"
        with subagent_refs_lock:
            subagent_refs[(task_index, str(task_id or ""))] = sub_agent

        system_prompt = (
            "你是一个专注的子智能体，负责完成被委托的具体子任务。\n\n"
            "你必须保持上下文隔离：只依赖任务描述和你通过工具获得的信息，不假设知道父进程完整对话。\n"
            "你不是全局调度者；全局调度、拆分审批、子进程数量和依赖调度都由父进程负责。\n\n"
            f"你的任务目标：\n{goal}\n\n"
            "执行原则：\n"
            "- 如果任务足够具体且依赖满足，直接完成它。\n"
            "- 如果任务过于笼统、上下文不足、需要进一步拆分，必须提出拆分建议，而不是盲目执行。\n"
            "- 若使用 todo_manage propose_split，只提出建议，等待父进程判断。\n"
            "- 完成后提供清晰摘要，包含：做了什么、成果、问题、下一步建议。\n"
            "- 如果达到最大思考轮数或无法完成，明确说明未完成原因，并尽可能给出可执行的拆分建议。"
        )

        def on_think(iteration, **kwargs):
            pass

        def on_tool_start(func_name, func_args):
            _print_after_status(f"[bold cyan]🛠️  [Sub-Agent {task_index}][/bold cyan] 调用工具: {func_name}")

        def on_tool_end(func_name, result):
            pass

        _emit_delegate_event(event_sink, EVENT_DELEGATE_SUBAGENT_STARTED, {
            "run_id": run_id,
            "task_index": task_index,
            "task_id": task_id,
            "goal": task.get("goal") or task.get("description") or "",
            "max_iterations": max_iters,
            "system_prompt": system_prompt,
        })

        try:
            # 禁止子智能体再次委托，避免递归爆炸；禁止 memory 持久写用户记忆。
            result = sub_agent.run_conversation(
                user_message=goal,
                system_message=system_prompt,
                on_think=on_think,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
                exclude_tools=["delegate_task", "memory"],
                cancel_event=cancel_event,
            )
            truncated = _is_truncated_result(result, sub_agent)
            blocked_update = None
            if _is_model_failure_result(result):
                blocked_update = _mark_task_blocked(
                    task_id,
                    "子 Agent 模型请求失败，任务自动标记为 blocked。\n\n" + str(result),
                    session_id=session_id,
                    metadata={"blocked_reason": "subagent_model_request_failed", "max_iterations": max_iters},
                )
                status = "error"
            else:
                status = "truncated" if truncated else "success"
            if truncated:
                _print_after_status(
                    f"[bold yellow]⚠️ [Sub-Agent {task_index}][/bold yellow] "
                    f"达到 max_iterations={max_iters}，已强制收尾"
                )
                blocked_update = _mark_truncated_task_blocked(task_id, result, max_iters, session_id=session_id)
                if blocked_update:
                    _print_after_status(
                        f"[bold yellow]📌 task_id={task_id} 已按预算耗尽保护处理：[/bold yellow] "
                        f"{_shorten(blocked_update, 220)}"
                    )
            context_artifact_path = None
            todo_task_after_run = _get_todo_task(task_id, session_id=session_id)
            todo_status_after_run = (todo_task_after_run or {}).get("status")
            # Keep every child context until the whole todo succeeds.  Even a
            # successful leaf task may be needed while sibling/parent tasks remain
            # unfinished; it is saved as an artifact and removed only when all
            # tasks in this session are completed.
            should_keep_context = True
            context_reason = status if (status != "success" or truncated) else "success_waiting_overall"
            context_artifact_path = _save_subagent_context(task_id, sub_agent, context_reason, session_id=session_id, run_id=run_id)
            if context_artifact_path:
                if todo_status_after_run == "in_progress":
                    _mark_task_blocked(
                        task_id,
                        f"子 Agent 未可靠完成；上下文已保存到 {context_artifact_path}",
                        session_id=session_id,
                        metadata={"context_artifact_path": context_artifact_path, "context_saved_reason": context_reason},
                    )
                elif todo_task_after_run is not None:
                    try:
                        from tools.todo_tool import todo_manage
                        todo_manage(
                            "update",
                            json.dumps({
                                "id": str(task_id),
                                "metadata": {
                                    **(todo_task_after_run.get("metadata") or {}),
                                    "context_artifact_path": context_artifact_path,
                                    "context_saved_reason": context_reason,
                                },
                            }, ensure_ascii=False),
                            session_id=session_id or "",
                        )
                    except Exception:
                        pass
            token_usage_summary = _agent_token_usage_summary(sub_agent)
            # Do not discard successful child context here. It is cleaned in one
            # batch at the end of delegate_task if and only if the whole todo has
            # succeeded. In-memory messages can be cleared after artifacting to
            # avoid returning/holding them in the parent context.
            _clear_subagent_context(sub_agent)

            _print_after_status(f"[bold green]✅ [Sub-Agent {task_index}][/bold green] 子任务执行完毕")
            item = {
                "task_index": task_index,
                "task_id": task_id,
                "goal": task.get("goal") or task.get("description") or "",
                "max_iterations": max_iters,
                "status": status,
                "truncated": truncated,
                "blocked_update": blocked_update,
                "context_artifact_path": context_artifact_path,
                "token_usage": token_usage_summary,
            }
            _emit_delegate_event(event_sink, EVENT_DELEGATE_SUBAGENT_FINISHED, item)
            return item
        except Exception as e:
            _print_after_status(f"[bold red]❌ [Sub-Agent {task_index}][/bold red] 子任务执行失败: {e}")
            blocked_update = _mark_task_blocked(
                task_id,
                f"子 Agent 执行异常，任务自动标记为 blocked：{e}",
                session_id=session_id,
                metadata={"blocked_reason": "subagent_exception", "max_iterations": max_iters},
            )
            token_usage_summary = _agent_token_usage_summary(locals().get("sub_agent"))
            context_artifact_path = _save_subagent_context(task_id, locals().get("sub_agent"), "error", session_id=session_id, run_id=run_id)
            if context_artifact_path:
                _mark_task_blocked(
                    task_id,
                    f"子 Agent 执行异常；上下文已保存到 {context_artifact_path}",
                    session_id=session_id,
                    metadata={"context_artifact_path": context_artifact_path, "context_saved_reason": "error"},
                )
            item = {
                "task_index": task_index,
                "task_id": task_id,
                "goal": task.get("goal") or task.get("description") or "",
                "max_iterations": max_iters,
                "status": "error",
                "truncated": False,
                "blocked_update": blocked_update,
                "context_artifact_path": context_artifact_path,
                "token_usage": token_usage_summary,
            }
            _emit_delegate_event(event_sink, EVENT_DELEGATE_SUBAGENT_FINISHED, item)
            return item

    if len(task_list) == 0:
        return json.dumps({"error": "No valid tasks provided."}, ensure_ascii=False)

    if max_workers is None:
        effective_workers = min(3, len(task_list))
    else:
        try:
            effective_workers = int(max_workers)
        except Exception:
            effective_workers = 3
        effective_workers = max(1, min(effective_workers, len(task_list), 10))

    valid_jobs = []
    for i, task in enumerate(task_list):
        if not isinstance(task, dict):
            results.append({"task_index": i, "status": "error", "result": "Each task must be an object.", "token_usage": _agent_token_usage_summary(None)})
            continue
        if not (task.get("goal") or task.get("description") or task.get("task_id") or task.get("id")):
            results.append({"task_index": i, "status": "error", "result": "Missing goal/description/task_id in task.", "token_usage": _agent_token_usage_summary(None)})
            continue
        valid_jobs.append((i, task))

    if valid_jobs:
        _print_after_status(
            f"\n[bold yellow]📋 Delegate 准备并发执行 {len(valid_jobs)} 个任务，max_workers={effective_workers}[/bold yellow]"
        )

    executor = ThreadPoolExecutor(max_workers=effective_workers)
    future_to_job = {}
    for i, task in valid_jobs:
        cancel_event = threading.Event()
        future = executor.submit(_run_subagent, i, task, cancel_event)
        future_to_job[future] = (i, task, time.monotonic(), cancel_event)
    pending = set(future_to_job)
    try:
        while pending:
            done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                i, task, _started, _cancel_event = future_to_job[future]
                try:
                    item = future.result()
                except Exception as exc:
                    blocked_update = _mark_task_blocked(
                        _task_id_of(task),
                        f"delegate future 异常，任务自动标记为 blocked：{exc}",
                        session_id=session_id,
                        metadata={"blocked_reason": "delegate_future_exception"},
                    )
                    item = {
                        "task_index": i,
                        "task_id": _task_id_of(task),
                        "goal": task.get("goal") or task.get("description") or "",
                        "status": "error",
                        "truncated": False,
                        "blocked_update": blocked_update,
                        "context_artifact_path": None,
                        "token_usage": _agent_token_usage_summary(None),
                    }
                results.append(item)
                task_id = item.get("task_id") or f"index={item.get('task_index')}"
                _print_after_status(
                    f"[bold yellow]📋 Delegate 子任务状态更新：{task_id} -> {item.get('status')}[/bold yellow]"
                )

            now = time.monotonic()
            for future in list(pending):
                i, task, started, cancel_event = future_to_job[future]
                timeout_value = task.get("wall_timeout_seconds", default_wall_timeout_seconds)
                try:
                    timeout_value = None if timeout_value is None else float(timeout_value)
                except Exception:
                    timeout_value = default_wall_timeout_seconds
                if timeout_value is None or timeout_value <= 0 or now - started < timeout_value:
                    continue
                task_id = _task_id_of(task)
                try:
                    cancel_event.set()
                except Exception:
                    pass
                future.cancel()
                with subagent_refs_lock:
                    timed_sub_agent = subagent_refs.get((i, str(task_id or "")))
                context_artifact_path = _save_subagent_context(task_id, timed_sub_agent, "timeout", session_id=session_id, run_id=f"delegate-{i}-{task_id or 'adhoc'}")
                blocked_update = _mark_task_blocked(
                    task_id,
                    f"子 Agent 超过墙钟超时 {timeout_value:g}s，任务自动标记为 blocked；后台请求如仍在运行会被取消信号要求停止。" + (f" 上下文已保存到 {context_artifact_path}" if context_artifact_path else ""),
                    session_id=session_id,
                    metadata={"blocked_reason": "subagent_wall_timeout", "wall_timeout_seconds": timeout_value, **({"context_artifact_path": context_artifact_path, "context_saved_reason": "timeout"} if context_artifact_path else {})},
                )
                timed_token_usage = _agent_token_usage_summary(timed_sub_agent)
                item = {
                    "task_index": i,
                    "task_id": task_id,
                    "goal": task.get("goal") or task.get("description") or "",
                    "status": "timeout",
                    "truncated": False,
                    "blocked_update": blocked_update,
                    "context_artifact_path": context_artifact_path,
                    "token_usage": timed_token_usage,
                }
                results.append(item)
                pending.remove(future)
                _print_after_status(
                    f"[bold red]⏱️ Delegate 子任务超时：{task_id or i} -> timeout[/bold red]"
                )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    results.sort(key=lambda x: x.get("task_index", 0))
    delegated_token_usage = _sum_token_usage(results)
    whole_todo_completed = _all_tasks_completed(session_id)
    cleaned_context_artifacts = _cleanup_all_completed_contexts(session_id)
    if whole_todo_completed:
        for item in results:
            item["context_artifact_path"] = None
    todo_digest = None
    try:
        from tools.todo_tool import todo_manage
        todo_digest = json.loads(todo_manage("digest", "{}", session_id=session_id or ""))
    except Exception:
        todo_digest = None
    return json.dumps({
        "tasks": results,
        "delegated_token_usage": delegated_token_usage,
        "todo_digest": todo_digest,
        "cleaned_context_artifacts": cleaned_context_artifacts,
        "note": "delegate_task only returns task status and todo digest; sub-agent message history is not returned inline. Child contexts are retained as artifacts until the whole todo succeeds, then cleaned together. If a task saved context_artifact_path in todo metadata, the parent may explicitly inspect it."
    }, ensure_ascii=False, indent=2)


registry.register(
    name="delegate_task",
    description=(
        "生成一个或多个具有隔离上下文的子智能体来并行处理任务。\n"
        "支持为每个子任务设置 max_iterations 与 wall_timeout_seconds，避免子进程无限或过长思考。\n"
        "支持动态 todo list 协议：传入 task_id/id 时，子智能体会先领取任务，判断是否需要拆分；需要拆分则用 todo_manage propose_split 提交拆分建议，不会擅自批准；不需要拆分才执行并更新任务状态。\n"
        "执行期间会自动打印 todo 进度快照：启动前、每个子 Agent 结束后、全部结束后都会展示任务总数、状态统计、正在执行与阻塞任务。\n"
        "返回值只包含每个子任务状态、token_usage、可选 context_artifact_path、delegated_token_usage 汇总和 todo_digest；不会把子 Agent 完整 messages 回灌给父进程。\n"
        "子 Agent 上下文不会回灌给父进程；会以 artifact 形式保留到整个 todo 成功完成后再统一清理，失败/超时/未完成时父进程可通过 context_artifact_path 显式读取。\n"
        "如果子 Agent 达到 max_iterations 并强制收尾，且对应 todo 任务仍是 in_progress，会自动标记为 blocked，避免任务长期卡住。\n"
        "父进程应先用 todo_manage 查看 ready 任务，基于拓扑依赖决定并发子进程数量，再调用本工具。\n"
        "注意：子智能体对父进程对话历史一无所知，因此 goal/context_summary 必须完整、自包含；父进程默认只读 todo_manage digest。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "string",
                "description": (
                    "JSON 格式任务列表字符串。每个任务可包含：goal/description、task_id/id、worker_id、max_iterations、wall_timeout_seconds。"
                    "示例：'[{\"task_id\":\"t1\",\"goal\":\"完成 t1，背景...\",\"max_iterations\":20}]'"
                )
            },
            "max_workers": {
                "type": "integer",
                "description": "本次最多并行子智能体数量；父进程根据 ready 任务数量和依赖关系决定。默认 min(3,任务数)，上限 10。"
            },
            "default_max_iterations": {
                "type": "integer",
                "description": "未在单个任务中指定 max_iterations 时使用的默认最大思考轮数，默认 30。"
            },
            "session_id": {
                "type": "string",
                "description": "可选会话编号；父子 Agent 会使用同一个 session_id 读写隔离 todo list。"
            },
            "default_wall_timeout_seconds": {
                "type": "number",
                "description": "单个子任务默认墙钟超时时间；超时后标记 blocked 并让 delegate_task 返回。"
            },
            "event_sink": {
                "type": "object",
                "description": "内部 GUI 事件接收器；模型不应手动设置。"
            }
        },
        "required": ["tasks"]
    },
    handler=delegate_task
)
