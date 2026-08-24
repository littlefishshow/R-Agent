"""tool_search：延迟工具暴露（deferred tool exposure）的检索入口。
本工具只做**检索**（返回匹配的 name+summary+category）。真正的"提升"由 `core/agent.py`
的主循环解析本工具的返回结果后完成——因为工具运行在隔离子进程里，不能直接改主进程状态。
"""

import json

from tools.registry import registry


def tool_search(query: str = "", limit: int = 8) -> str:
    """按关键词检索可用工具目录，返回匹配项（供模型决定要提升哪些工具）。"""
    try:
        limit = int(limit)
    except Exception:
        limit = 8
    limit = max(1, min(limit, 30))
    matches = registry.search_catalog(query or "", limit=limit)
    return json.dumps(
        {
            "success": True,
            "query": query or "",
            "count": len(matches),
            "matches": matches,
            "hint": "调用你需要的工具名即可；系统已为本次会话提升这些工具的完整用法。",
        },
        ensure_ascii=False,
    )


registry.register(
    name="tool_search",
    description=(
        "检索当前可用工具目录。当你不确定有哪些工具、或需要某类能力（如搜索网页、读文件、"
        "跑命令）时，先用简短关键词调用本工具查看匹配的工具名与简介，再调用具体工具。"
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
    metadata={"summary": "检索可用工具目录并提升相关工具", "category": "meta"},
)
