import json
from concurrent.futures import ThreadPoolExecutor
from tools.registry import registry
from rich.console import Console

console = Console()


def delegate_task(tasks: str, max_workers: int = None, default_max_iterations: int = 30) -> str:
    """生成隔离上下文子智能体并行处理任务。

    tasks 是 JSON 字符串，元素可以是：
    - {"goal": "完整、自包含任务描述", "max_iterations": 20}
    - {"task_id": "t1", "goal": "可选补充说明", "worker_id": "w1", "max_iterations": 20}

    若提供 task_id，子智能体会被提示使用 todo_manage claim/get/propose_split/update 来遵循动态任务看板协议。
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
        task_id = task.get("task_id") or task.get("id")
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
                "5. 只处理自己领取的任务；不要擅自批准拆分，approve_split/reject_split 只能由父进程执行。"
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

        console.print(f"\n[bold blue]🚀 [Sub-Agent {task_index}][/bold blue] 开始执行子任务(max_iterations={max_iters}): {goal[:500]}")
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
            console.print(f"[bold cyan]🛠️  [Sub-Agent {task_index}][/bold cyan] 调用工具: {func_name}")

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
            console.print(f"[bold green]✅ [Sub-Agent {task_index}][/bold green] 子任务执行完毕")
            return {
                "task_index": task_index,
                "task_id": task.get("task_id") or task.get("id"),
                "goal": task.get("goal") or task.get("description") or "",
                "max_iterations": max_iters,
                "status": "success",
                "result": result,
            }
        except Exception as e:
            console.print(f"[bold red]❌ [Sub-Agent {task_index}][/bold red] 子任务执行失败: {e}")
            return {
                "task_index": task_index,
                "task_id": task.get("task_id") or task.get("id"),
                "goal": task.get("goal") or task.get("description") or "",
                "max_iterations": max_iters,
                "status": "error",
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

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = []
        for i, task in enumerate(task_list):
            if not isinstance(task, dict):
                results.append({"task_index": i, "status": "error", "result": "Each task must be an object."})
                continue
            if not (task.get("goal") or task.get("description") or task.get("task_id") or task.get("id")):
                results.append({"task_index": i, "status": "error", "result": "Missing goal/description/task_id in task."})
                continue
            futures.append(executor.submit(_run_subagent, i, task))

        for future in futures:
            results.append(future.result())

    results.sort(key=lambda x: x.get("task_index", 0))
    return json.dumps(results, ensure_ascii=False, indent=2)


registry.register(
    name="delegate_task",
    description=(
        "生成一个或多个具有隔离上下文的子智能体来并行处理任务。\n"
        "支持为每个子任务设置 max_iterations，避免子进程无限或过长思考。\n"
        "支持动态 todo list 协议：传入 task_id/id 时，子智能体会先领取任务，判断是否需要拆分；需要拆分则用 todo_manage propose_split 提交拆分建议，不会擅自批准；不需要拆分才执行并更新任务状态。\n"
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
