import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from tools.registry import registry
from rich.console import Console
from rich.panel import Panel

console = Console()


def _current_cli_status():
    """Return the active CLI Rich status exposed by main.py/__main__."""
    try:
        import sys

        for module_name in ("__main__", "main"):
            module = sys.modules.get(module_name)
            status = getattr(module, "ACTIVE_STATUS", None) if module is not None else None
            if status is not None:
                return status
    except Exception:
        return None
    return None


def _print_after_status(renderable=None, *args, **kwargs):
    """Print tool progress without colliding with the parent CLI status line.

    In concise mode the parent CLI keeps a Rich status spinner active while tool
    code writes directly to stdout. Pausing the status before printing prevents
    panels from being rendered on the same physical terminal line as the spinner.
    """
    status = _current_cli_status()
    stopped = False
    if status is not None:
        try:
            status.stop()
            stopped = True
        except Exception:
            stopped = False
    try:
        if renderable is None:
            console.print(*args, **kwargs)
        else:
            console.print(renderable, *args, **kwargs)
    finally:
        if stopped:
            try:
                status.start()
            except Exception:
                pass


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


def _task_id_of(task):
    return task.get("task_id") or task.get("id")


def _shorten(text, limit=90):
    text = str(text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _load_todo_state():
    """Best-effort load of the shared todo state for progress snapshots."""
    try:
        from tools import todo_tool

        return todo_tool._load_state()
    except Exception:
        return {"version": 2, "tasks": []}


def _todo_snapshot_text(label="当前任务快照", scheduled_tasks=None):
    """Return a compact, user-facing todo progress snapshot."""
    state = _load_todo_state()
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

        def list_tasks(title, filtered, limit=8):
            if not filtered:
                return
            lines.append("")
            lines.append(title)
            for t in filtered[:limit]:
                assigned = t.get("assigned_to") or (t.get("claim") or {}).get("worker_id") or "未分配"
                lines.append(
                    f"- {t.get('id')}: {_shorten(t.get('description'))}"
                    f" [{t.get('status')}, {assigned}]"
                )
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

        list_tasks("正在执行：", [t for t in tasks if t.get("status") == "in_progress"])
        list_tasks("需要父 Agent 处理：", [t for t in tasks if t.get("status") in {"blocked", "needs_split", "failed"}])

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


def _print_todo_snapshot(label="当前任务快照", scheduled_tasks=None):
    _print_after_status(Panel(_todo_snapshot_text(label, scheduled_tasks), title="Todo Progress", border_style="yellow", expand=False))


def _get_todo_task(task_id):
    if not task_id:
        return None
    try:
        from tools import todo_tool

        state = todo_tool._load_state()
        return todo_tool._find_task(state, str(task_id))
    except Exception:
        return None


def _mark_truncated_task_blocked(task_id, result, max_iters):
    """Avoid leaving a claimed todo task stuck in in_progress after sub-agent truncation."""
    if not task_id:
        return None
    task = _get_todo_task(task_id)
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


def delegate_task(tasks: str, max_workers: int = None, default_max_iterations: int = 30) -> str:
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

    def _run_subagent(task_index, task):
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
        sub_agent = RAgent(max_iterations=max_iters)

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

        try:
            # 禁止子智能体再次委托，避免递归爆炸；禁止 memory 持久写用户记忆。
            result = sub_agent.run_conversation(
                user_message=goal,
                system_message=system_prompt,
                on_think=on_think,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
                exclude_tools=["delegate_task", "memory"]
            )
            truncated = _is_truncated_result(result, sub_agent)
            blocked_update = None
            if truncated:
                _print_after_status(
                    f"[bold yellow]⚠️ [Sub-Agent {task_index}][/bold yellow] "
                    f"达到 max_iterations={max_iters}，已强制收尾"
                )
                blocked_update = _mark_truncated_task_blocked(task_id, result, max_iters)
                if blocked_update:
                    _print_after_status(
                        f"[bold yellow]📌 task_id={task_id} 已按预算耗尽保护处理：[/bold yellow] "
                        f"{_shorten(blocked_update, 220)}"
                    )
            _print_after_status(f"[bold green]✅ [Sub-Agent {task_index}][/bold green] 子任务执行完毕")
            return {
                "task_index": task_index,
                "task_id": task_id,
                "goal": task.get("goal") or task.get("description") or "",
                "max_iterations": max_iters,
                "status": "truncated" if truncated else "success",
                "truncated": truncated,
                "blocked_update": blocked_update,
                "result": result,
            }
        except Exception as e:
            _print_after_status(f"[bold red]❌ [Sub-Agent {task_index}][/bold red] 子任务执行失败: {e}")
            return {
                "task_index": task_index,
                "task_id": task_id,
                "goal": task.get("goal") or task.get("description") or "",
                "max_iterations": max_iters,
                "status": "error",
                "truncated": False,
                "result": str(e),
            }

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
            results.append({"task_index": i, "status": "error", "result": "Each task must be an object."})
            continue
        if not (task.get("goal") or task.get("description") or task.get("task_id") or task.get("id")):
            results.append({"task_index": i, "status": "error", "result": "Missing goal/description/task_id in task."})
            continue
        valid_jobs.append((i, task))

    if valid_jobs:
        _print_todo_snapshot(
            f"Delegate 启动前任务快照（准备并发执行 {len(valid_jobs)} 个任务，max_workers={effective_workers}）",
            scheduled_tasks=[task for _, task in valid_jobs],
        )

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_job = {
            executor.submit(_run_subagent, i, task): (i, task)
            for i, task in valid_jobs
        }
        for future in as_completed(future_to_job):
            i, task = future_to_job[future]
            try:
                item = future.result()
            except Exception as exc:
                item = {
                    "task_index": i,
                    "task_id": _task_id_of(task),
                    "goal": task.get("goal") or task.get("description") or "",
                    "status": "error",
                    "truncated": False,
                    "result": str(exc),
                }
            results.append(item)
            task_id = item.get("task_id") or f"index={item.get('task_index')}"
            _print_todo_snapshot(f"Sub-Agent 结束后任务快照：{task_id}", scheduled_tasks=[task])

    if valid_jobs:
        _print_todo_snapshot("本轮 delegate_task 完成后的最终任务快照")

    results.sort(key=lambda x: x.get("task_index", 0))
    return json.dumps(results, ensure_ascii=False, indent=2)


registry.register(
    name="delegate_task",
    description=(
        "生成一个或多个具有隔离上下文的子智能体来并行处理任务。\n"
        "支持为每个子任务设置 max_iterations，避免子进程无限或过长思考。\n"
        "支持动态 todo list 协议：传入 task_id/id 时，子智能体会先领取任务，判断是否需要拆分；需要拆分则用 todo_manage propose_split 提交拆分建议，不会擅自批准；不需要拆分才执行并更新任务状态。\n"
        "执行期间会自动打印 todo 进度快照：启动前、每个子 Agent 结束后、全部结束后都会展示任务总数、状态统计、正在执行与阻塞任务。\n"
        "如果子 Agent 达到 max_iterations 并强制收尾，且对应 todo 任务仍是 in_progress，会自动标记为 blocked，避免任务长期卡住。\n"
        "父进程应先用 todo_manage 查看 ready 任务，基于拓扑依赖决定并发子进程数量，再调用本工具。\n"
        "注意：子智能体对父进程对话历史一无所知，因此 goal/context_summary 必须完整、自包含。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "string",
                "description": (
                    "JSON 格式任务列表字符串。每个任务可包含：goal/description、task_id/id、worker_id、max_iterations。"
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
            }
        },
        "required": ["tasks"]
    },
    handler=delegate_task
)
