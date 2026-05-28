import os
import json
from tools.registry import registry
from rich.console import Console

console = Console()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODO_FILE = os.path.join(BASE_DIR, "sandbox", "todo_list.json")

def _load_todo():
    if os.path.exists(TODO_FILE):
        try:
            with open(TODO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []

def _save_todo(todo_list):
    os.makedirs(os.path.dirname(TODO_FILE), exist_ok=True)
    with open(TODO_FILE, "w", encoding="utf-8") as f:
        json.dump(todo_list, f, indent=4, ensure_ascii=False)

def todo_manage(action: str, payload: str = "{}") -> str:
    todo_list = _load_todo()
    
    try:
        if action == "view":
            ready_tasks = []
            for t in todo_list:
                if t["status"] == "pending":
                    deps_met = all(
                        any(dt["id"] == dep and dt["status"] == "completed" for dt in todo_list)
                        for dep in t.get("dependencies", [])
                    )
                    if deps_met:
                        ready_tasks.append(t["id"])
            
            result = {
                "todo_list": todo_list,
                "ready_to_execute": ready_tasks
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
            
        elif action == "init":
            tasks = json.loads(payload)
            if not isinstance(tasks, list):
                return "Error: payload for init must be a JSON array of tasks."
            todo_list = []
            for t in tasks:
                todo_list.append({
                    "id": str(t.get("id")),
                    "description": t.get("description", ""),
                    "dependencies": t.get("dependencies", []),
                    "status": "pending", 
                    "result": ""
                })
            _save_todo(todo_list)
            console.print("\n[bold yellow]📋 任务看板已初始化[/bold yellow]")
            return "Todo list initialized successfully."
            
        elif action == "update":
            updates = json.loads(payload)
            task_id = str(updates.get("id"))
            for t in todo_list:
                if t["id"] == task_id:
                    if "status" in updates:
                        t["status"] = updates["status"]
                    if "result" in updates:
                        t["result"] = updates["result"]
                    _save_todo(todo_list)
                    console.print(f"\n[bold yellow]📋 任务 {task_id} 状态更新为 {t['status']}[/bold yellow]")
                    return f"Task {task_id} updated successfully."
            return f"Error: Task {task_id} not found."
            
        elif action == "add":
            tasks = json.loads(payload)
            for t in tasks:
                todo_list.append({
                    "id": str(t.get("id")),
                    "description": t.get("description", ""),
                    "dependencies": t.get("dependencies", []),
                    "status": "pending",
                    "result": ""
                })
            _save_todo(todo_list)
            return "Tasks added successfully."
            
        elif action == "clear":
            _save_todo([])
            console.print("\n[bold yellow]📋 任务看板已清空[/bold yellow]")
            return "Todo list cleared."
            
        else:
            return f"Error: Unknown action '{action}'"
    except Exception as e:
        return f"Error: {str(e)}"

registry.register(
    name="todo_manage",
    description=(
        "管理复杂任务的 Todo List（任务看板），支持任务拓扑依赖。父进程用它掌控全局。\n"
        "操作(action)包括：\n"
        "- 'init': 初始化看板，payload 为任务数组，如 [{\"id\": \"t1\", \"description\": \"任务描述\", \"dependencies\": []}]\n"
        "- 'view': 查看所有任务状态及当前依赖已满足、可立即执行的任务（ready_to_execute）\n"
        "- 'update': 更新任务状态，payload 如 {\"id\": \"t1\", \"status\": \"completed\", \"result\": \"成功\"} (status 可选 pending, completed, failed)\n"
        "- 'add': 追加新任务，payload 为任务数组\n"
        "- 'clear': 清空看板\n"
        "注意：在 payload 中必须传入合法的 JSON 字符串。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["init", "view", "update", "add", "clear"],
                "description": "要执行的操作类型"
            },
            "payload": {
                "type": "string",
                "description": "操作对应的数据 (JSON 字符串)，若是 view 或 clear 操作可为空 '{}'"
            }
        },
        "required": ["action"]
    },
    handler=todo_manage
)
