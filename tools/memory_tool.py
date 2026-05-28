import json
from tools.registry import registry
from core.memory import memory_manager

def memory_tool(action: str, target: str = "memory", content: str = None, old_text: str = None) -> str:
    """单一记忆工具，处理增加、替换和删除操作。"""
    if target not in ["memory", "user"]:
        return json.dumps({"error": "Target must be 'memory' or 'user'."}, ensure_ascii=False)
        
    if action == "add":
        if not content:
            return json.dumps({"error": "Content is required for 'add'."}, ensure_ascii=False)
        res = memory_manager.append_memory(target, content)
        return json.dumps({"success": True, "message": res}, ensure_ascii=False)
        
    elif action == "replace":
        if not old_text or not content:
            return json.dumps({"error": "old_text and content required for 'replace'."}, ensure_ascii=False)
        res = memory_manager.replace_memory(target, old_text, content)
        return json.dumps({"success": True, "message": res}, ensure_ascii=False)
        
    elif action == "remove":
        if not old_text:
            return json.dumps({"error": "old_text is required for 'remove'."}, ensure_ascii=False)
        res = memory_manager.remove_memory(target, old_text)
        return json.dumps({"success": True, "message": res}, ensure_ascii=False)
        
    else:
        return json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False)

registry.register(
    name="memory",
    description=(
        "保存持久化信息，跨会话有效。不要保存临时任务状态。\n"
        "两个目标 (target)：\n"
        "- 'user': 用户的个人偏好与身份信息\n"
        "- 'memory': 项目或环境的客观事实与约定\n"
        "三种操作 (action)：\n"
        "- add: 添加新内容\n"
        "- replace: 替换旧内容 (需要 old_text 精确匹配)\n"
        "- remove: 删除内容 (需要 old_text 精确匹配)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "replace", "remove"], "description": "操作类型"},
            "target": {"type": "string", "enum": ["memory", "user"], "description": "存储目标"},
            "content": {"type": "string", "description": "要添加或替换的新内容"},
            "old_text": {"type": "string", "description": "要替换或删除的旧文本"}
        },
        "required": ["action", "target"]
    },
    handler=memory_tool
)
