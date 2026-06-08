import json
from concurrent.futures import ThreadPoolExecutor
from tools.registry import registry
from rich.console import Console

console = Console()

def delegate_task(tasks: str) -> str:
    try:
        task_list = json.loads(tasks)
        if not isinstance(task_list, list):
            return json.dumps({"error": "tasks must be a JSON array of objects."})
    except json.JSONDecodeError:
        return json.dumps({"error": "tasks must be a valid JSON string."})
        
    results = []
    
    # We must import RAgent here to avoid circular dependencies
    from core.agent import RAgent
    
    def _run_subagent(task_index, goal):
        # Create a fresh isolated sub-agent
        console.print(f"\n[bold blue]🚀 [Sub-Agent {task_index}][/bold blue] 开始执行子任务: {goal}")
        sub_agent = RAgent(max_iterations=30)
        
        # 遵循 hermes-agent 的上下文隔离 trick：子智能体拥有专属、聚焦的 system prompt
        system_prompt = (
            "你是一个专注的子智能体，负责完成被委托的具体子任务。\n\n"
            f"你的任务目标：\n{goal}\n\n"
            "请使用你可用的工具来完成这个任务。完成后，请提供一个清晰、简明的摘要，包含：\n"
            "- 你做了什么\n"
            "- 你的发现或完成的成果\n"
            "- 遇到的任何问题"
        )
        
        # 定义回调以避免默认的 print 输出造成终端混乱，同时显示执行进度
        def on_think(iteration, **kwargs):
            pass
            
        def on_tool_start(func_name, func_args):
            console.print(f"[bold cyan]🛠️  [Sub-Agent {task_index}][/bold cyan] 调用工具: {func_name}")
            
        def on_tool_end(func_name, result):
            pass
            
        try:
            # 权限 trick：禁止子智能体再次调用 delegate_task (防止无限递归) 和 memory 等敏感工具
            result = sub_agent.run_conversation(
                user_message=goal, 
                system_message=system_prompt,
                on_think=on_think,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
                exclude_tools=["delegate_task", "memory"]
            )
            console.print(f"[bold green]✅ [Sub-Agent {task_index}][/bold green] 子任务执行完毕")
            return {"task_index": task_index, "goal": goal, "status": "success", "result": result}
        except Exception as e:
            console.print(f"[bold red]❌ [Sub-Agent {task_index}][/bold red] 子任务执行失败: {e}")
            return {"task_index": task_index, "goal": goal, "status": "error", "result": str(e)}

    # Execute sub-tasks in parallel using a thread pool
    max_workers = min(3, len(task_list)) # Cap at 3 concurrent workers
    if max_workers == 0:
        return json.dumps({"error": "No valid tasks provided."})
        
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for i, task in enumerate(task_list):
            goal = task.get("goal")
            if not goal:
                results.append({"task_index": i, "status": "error", "result": "Missing 'goal' in task."})
                continue
            future = executor.submit(_run_subagent, i, goal)
            futures.append(future)
            
        for future in futures:
            results.append(future.result())
            
    # Sort results by original task index to maintain order
    results.sort(key=lambda x: x.get("task_index", 0))
    
    return json.dumps(results, ensure_ascii=False, indent=2)

registry.register(
    name="delegate_task",
    description=(
        "生成一个或多个具有隔离上下文的子智能体来并行处理任务。\n"
        "【重要使用时机】：当遇到包含多个独立子任务、可以并行处理的复杂请求时（例如同时研究多个不同主题、查询多个实体），"
        "必须主动使用此工具将任务拆解为多份，以提高效率。\n"
        "注意：子智能体对你的对话历史一无所知，因此请在 'goal' 中提供绝对完整、自包含的背景和目标说明。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "string",
                "description": "JSON 格式的任务列表字符串。每个任务应是一个包含 'goal' 字符串的字典。示例: '[{\"goal\": \"研究主题 A，包含所有需要的背景信息\"}, {\"goal\": \"分析数据集 B\"}]'"
            }
        },
        "required": ["tasks"]
    },
    handler=delegate_task
)
