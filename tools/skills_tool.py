import json
from core.skills import skill_manager
from core.skill_usage import record_event, read_usage
from tools.registry import registry


def _json_ok(**kwargs):
    return json.dumps({"success": True, **kwargs}, ensure_ascii=False)


def _json_error(message: str):
    return json.dumps({"error": str(message)}, ensure_ascii=False)


def skills_list_tool() -> str:
    """列出所有可用的技能 (返回简要信息)"""
    return json.dumps({"skills": skill_manager.list_skills()}, ensure_ascii=False)


def skill_view_tool(skill_name: str, file_path: str = None) -> str:
    """查看技能的详细说明或技能内部的特定文件"""
    if not skill_name:
        return _json_error("skill_name is required.")
    try:
        content = skill_manager.view_skill(skill_name, file_path)
        record_event(skill_name, "view")
        return json.dumps({"content": content, "file_path": file_path or "SKILL.md"}, ensure_ascii=False)
    except Exception as exc:
        return _json_error(exc)


def skill_create_tool(skill_name: str, description: str, content: str, category: str = "uncategorized") -> str:
    """创建一个新的技能（兼容旧接口；实际逻辑委托给 skill_manage）。"""
    return skill_manage_tool(
        action="create",
        skill_name=skill_name,
        description=description,
        content=content,
        category=category,
        created_by="foreground_agent",
        write_origin="foreground",
    )


def skill_delete_tool(skill_name: str) -> str:
    """删除一个技能（兼容旧接口；实际逻辑委托给 skill_manage）。"""
    return skill_manage_tool(action="delete", skill_name=skill_name)


def skill_manage_tool(action: str, skill_name: str = "", description: str = "", content: str = "",
                      category: str = "uncategorized", file_path: str = "", old_string: str = "",
                      new_string: str = "", created_by: str = "foreground_agent",
                      write_origin: str = "foreground", overwrite: bool = False) -> str:
    """统一技能包管理工具，支持 create/patch/edit/delete/write_file/remove_file/usage。"""
    action = (action or "").strip()
    try:
        if action == "create":
            if not skill_name or not description or not content:
                return _json_error("create requires skill_name, description, content.")
            msg = skill_manager.create_skill(skill_name, description, content, category, overwrite=overwrite)
            record_event(skill_name, "create", created_by=created_by, write_origin=write_origin)
            record_event(skill_name, "patch")
            return _json_ok(action=action, message=msg)
        if action in {"edit", "write_file"}:
            if not skill_name or content is None:
                return _json_error(f"{action} requires skill_name and content.")
            msg = skill_manager.edit_skill_file(skill_name, content, file_path or None)
            record_event(skill_name, "patch")
            return _json_ok(action=action, message=msg)
        if action == "patch":
            if not skill_name:
                return _json_error("patch requires skill_name.")
            msg = skill_manager.patch_skill_file(skill_name, old_string, new_string, file_path or None)
            record_event(skill_name, "patch")
            return _json_ok(action=action, message=msg)
        if action == "remove_file":
            if not skill_name or not file_path:
                return _json_error("remove_file requires skill_name and file_path.")
            msg = skill_manager.remove_skill_file(skill_name, file_path)
            record_event(skill_name, "patch")
            return _json_ok(action=action, message=msg)
        if action == "delete":
            if not skill_name:
                return _json_error("delete requires skill_name.")
            msg = skill_manager.delete_skill(skill_name)
            return _json_ok(action=action, message=msg)
        if action == "usage":
            usage = read_usage()
            return _json_ok(action=action, usage=usage.get(skill_name) if skill_name else usage)
        return _json_error("Unsupported action. Use create, patch, edit, delete, write_file, remove_file, usage.")
    except Exception as exc:
        return _json_error(exc)


registry.register(
    name="skills_list",
    description="列出当前 Agent 拥有的所有可用技能 (返回按类别划分的名称和描述)。",
    parameters={"type": "object", "properties": {}},
    handler=skills_list_tool,
)

registry.register(
    name="skill_view",
    description="阅读某个具体技能的完整说明 (SKILL.md) 或技能目录内 supporting file。在执行复杂任务前，必须先阅读对应的技能文档。",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "技能的名称，如 'deploy_docker'"},
            "file_path": {"type": "string", "description": "技能目录内的具体文件路径 (可选)，如 references/api.md、templates/out.md、scripts/check.py、Project_progress/README.md"},
        },
        "required": ["skill_name"],
    },
    handler=skill_view_tool,
)

registry.register(
    name="skill_create",
    description="兼容旧接口：创建一个新技能。新代码优先使用 skill_manage(action=create)。",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "技能名称，使用小写字母和下划线，例如 'git_workflow'"},
            "description": {"type": "string", "description": "一句简短的描述 (<=60字符)"},
            "content": {"type": "string", "description": "技能的主体内容，必须包含 Markdown 标题如 '## When to Use', '## Procedure' 等"},
            "category": {"type": "string", "description": "技能的分类名称，例如 agent_ops、github、productivity"},
        },
        "required": ["skill_name", "description", "content"],
    },
    handler=skill_create_tool,
)

registry.register(
    name="skill_delete",
    description="兼容旧接口：删除一个技能。新代码优先使用 skill_manage(action=delete)。",
    parameters={"type": "object", "properties": {"skill_name": {"type": "string", "description": "要删除的技能名称"}}, "required": ["skill_name"]},
    handler=skill_delete_tool,
)

registry.register(
    name="skill_manage",
    description="统一管理技能包：create/patch/edit/delete/write_file/remove_file/usage。patch 要求 old_string 唯一匹配；supporting file 路径必须在技能目录内。",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "create | patch | edit | delete | write_file | remove_file | usage"},
            "skill_name": {"type": "string", "description": "技能名称"},
            "description": {"type": "string", "description": "create 时的描述"},
            "content": {"type": "string", "description": "create/edit/write_file 的完整内容"},
            "category": {"type": "string", "description": "create 时的分类"},
            "file_path": {"type": "string", "description": "技能目录内文件路径；空表示 SKILL.md"},
            "old_string": {"type": "string", "description": "patch 要替换的旧文本，必须唯一"},
            "new_string": {"type": "string", "description": "patch 替换后的新文本"},
            "created_by": {"type": "string", "description": "foreground_agent/background_review/user/system"},
            "write_origin": {"type": "string", "description": "foreground/background_review"},
            "overwrite": {"type": "boolean", "description": "create 时是否允许覆盖已有 skill；默认 false"},
        },
        "required": ["action"],
    },
    handler=skill_manage_tool,
)
