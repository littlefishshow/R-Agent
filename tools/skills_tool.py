import json
from core.skills import skill_manager
from tools.registry import registry

def skills_list_tool() -> str:
    """列出所有可用的技能 (返回简要信息)"""
    return json.dumps({"skills": skill_manager.list_skills()}, ensure_ascii=False)

def skill_view_tool(skill_name: str, file_path: str = None) -> str:
    """查看技能的详细说明或技能内部的特定文件"""
    if not skill_name:
        return json.dumps({"error": "skill_name is required."}, ensure_ascii=False)
    # R-Agent 的简易版，如果请求特定的 file_path 暂不处理，直接返回主 SKILL.md
    return json.dumps({"content": skill_manager.view_skill(skill_name)}, ensure_ascii=False)

def skill_create_tool(skill_name: str, description: str, content: str, category: str = "uncategorized") -> str:
    """创建或更新一个新的技能"""
    if not skill_name or not description or not content:
        return json.dumps({"error": "skill_name, description, and content are required."}, ensure_ascii=False)
    res = skill_manager.create_skill(skill_name, description, content, category)
    return json.dumps({"success": True, "message": res}, ensure_ascii=False)

def skill_delete_tool(skill_name: str) -> str:
    """删除一个技能"""
    if not skill_name:
        return json.dumps({"error": "skill_name is required."}, ensure_ascii=False)
    res = skill_manager.delete_skill(skill_name)
    return json.dumps({"success": True, "message": res}, ensure_ascii=False)

registry.register(
    name="skills_list",
    description="列出当前 Agent 拥有的所有可用技能 (返回按类别划分的名称和描述)。",
    parameters={
        "type": "object",
        "properties": {}
    },
    handler=skills_list_tool
)

registry.register(
    name="skill_view",
    description="阅读某个具体技能的完整说明 (SKILL.md)。在执行复杂任务前，必须先阅读对应的技能文档。",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "技能的名称，如 'deploy_docker'"},
            "file_path": {"type": "string", "description": "技能目录内的具体文件路径 (可选)"}
        },
        "required": ["skill_name"]
    },
    handler=skill_view_tool
)

registry.register(
    name="skill_create",
    description="创建一个新技能。当你成功解决了一个复杂问题，并且认为这个工作流以后还会用到时，使用此工具将其固化为技能。",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "技能名称，使用小写字母和下划线，例如 'git_workflow'"},
            "description": {"type": "string", "description": "一句简短的描述 (<=60字符)"},
            "content": {"type": "string", "description": "技能的主体内容，必须包含 Markdown 标题如 '## When to Use', '## Procedure' 等"},
            "category": {"type": "string", "description": "技能的分类名称（建议使用英文小写），例如 'github', 'productivity'。默认为 'uncategorized'。如果提供了新的分类名称，系统将自动创建该分类。"}
        },
        "required": ["skill_name", "description", "content"]
    },
    handler=skill_create_tool
)

registry.register(
    name="skill_delete",
    description="删除一个不再需要的技能。如果某个工作流已经过时或存在错误，可以使用此工具将其删除。",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "要删除的技能名称"}
        },
        "required": ["skill_name"]
    },
    handler=skill_delete_tool
)
