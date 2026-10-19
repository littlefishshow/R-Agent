"""Stable-prefix dynamic tool discovery.

Search returns full contracts in the chronological tool-result message.  The
top-level ``tools=`` prefix stays unchanged; dynamic execution is routed through
the fixed ``invoke_dynamic_tool`` schema and revalidated by the parent Agent.
"""

import json

from tools.registry import registry


def tool_search(query: str = "", limit: int = 8) -> str:
    """按关键词检索工具并把完整参数契约放进本次 tool result。"""
    try:
        limit = int(limit)
    except Exception:
        limit = 8
    limit = max(1, min(limit, 30))
    matches = registry.search_catalog(query or "", limit=limit)
    schemas = registry.get_schemas_for([item.get("name") for item in matches])
    schema_by_name = {
        schema.get("function", {}).get("name"): schema.get("function", {})
        for schema in schemas
        if isinstance(schema, dict) and schema.get("function", {}).get("name")
    }
    contracts = []
    contract_chars = 0
    omitted = 0
    for item in matches:
        function = schema_by_name.get(item.get("name")) or {}
        contract = {
            "name": item.get("name"),
            "summary": item.get("summary") or "",
            "category": item.get("category") or "",
            "description": function.get("description") or item.get("summary") or "",
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        rendered = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if contract_chars + len(rendered) > 60_000:
            omitted += 1
            continue
        contracts.append(contract)
        contract_chars += len(rendered)
    return json.dumps(
        {
            "success": True,
            "query": query or "",
            "count": len(matches),
            "returned_contracts": len(contracts),
            "omitted_contracts": omitted,
            "matches": contracts,
            "hint": (
                "这些完整工具契约已追加到当前消息尾部。需要使用时调用固定工具 "
                "invoke_dynamic_tool，并传入 tool_name 与 arguments；不要直接假设顶层已挂载该工具。"
            ),
        },
        ensure_ascii=False,
    )


registry.register(
    name="tool_search",
    description=(
        "检索当前可用工具目录并返回匹配工具的完整参数契约。当你需要未直接挂载的能力时，"
        "先查询，再使用 invoke_dynamic_tool 按返回契约传入 tool_name 与 arguments。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查找的能力关键词，如 'web search'、'read file'"},
            "limit": {"type": "integer", "description": "返回条数上限，默认 8"},
        },
        "required": ["query"],
    },
    handler=tool_search,
    metadata={"summary": "检索动态工具并把完整契约追加到消息尾部", "category": "meta"},
)


def invoke_dynamic_tool(tool_name: str, arguments: dict | None = None) -> str:
    """Schema-only fallback; RAgent intercepts and securely dispatches this call."""
    return json.dumps({
        "success": False,
        "error": (
            "invoke_dynamic_tool must be dispatched by the parent RAgent so the discovered-tool "
            "allowlist and active skill policy can be validated."
        ),
        "tool_name": tool_name,
    }, ensure_ascii=False)


registry.register(
    name="invoke_dynamic_tool",
    description=(
        "调用已经通过 tool_search 发现的动态工具。tool_name 必须来自当前会话已发现的契约；"
        "arguments 必须符合该契约的 parameters。执行前系统会再次校验注册表、权限和 Skill policy。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "tool_search 返回的动态工具名"},
            "arguments": {"type": "object", "description": "符合已发现工具 parameters 的参数对象"},
        },
        "required": ["tool_name", "arguments"],
    },
    handler=invoke_dynamic_tool,
    metadata={"summary": "稳定前缀的动态工具调用器", "category": "meta"},
)
