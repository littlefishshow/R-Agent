import json
from tools.registry import registry
import core.memory as core_memory
from core.memory import memory_manager

MemoryOperationError = getattr(
    core_memory,
    "MemoryOperationError",
    getattr(core_memory, "MemoryError", Exception),
)


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def memory_search(query: str, target: str = "all", max_results: int = 5) -> str:
    """在持久化 memory 中搜索关键词，返回匹配行摘要。"""
    try:
        return _json({"success": True, **memory_manager.search_memory(query, target, max_results)})
    except MemoryOperationError as e:
        return _json({"success": False, "error": str(e)})
    except Exception as e:
        return _json({"success": False, "error": f"Unexpected memory_search error: {e}"})


def memory_get(target: str, from_line: int = 1, lines: int = 50) -> str:
    """按行分页读取 USER.md 或 MEMORY.md，用于获取搜索结果附近的完整上下文。"""
    try:
        return _json({"success": True, **memory_manager.get_memory(target, from_line, lines)})
    except MemoryOperationError as e:
        return _json({"success": False, "error": str(e)})
    except Exception as e:
        return _json({"success": False, "error": f"Unexpected memory_get error: {e}"})


registry.register(
    name="memory_search",
    description=(
        "搜索长期 memory（USER.md / MEMORY.md）的关键词，返回匹配行和 snippet。"
        "适合在不想把完整 memory 放入上下文时查找相关偏好或项目事实；"
        "target 可为 all/user/memory，max_results 会被限制在 1-50。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词；可包含多个空格分隔词"},
            "target": {"type": "string", "enum": ["all", "user", "memory"], "description": "搜索范围，默认 all"},
            "max_results": {"type": "integer", "description": "最大返回结果数，范围 1-50，默认 5"},
        },
        "required": ["query"],
    },
    handler=memory_search,
)

registry.register(
    name="memory_get",
    description=(
        "按行分页读取长期 memory 文件内容。"
        "用于查看 memory_search 命中的附近上下文，或人工审计 USER.md / MEMORY.md。"
        "target 只能为 user 或 memory；lines 会被限制在 1-200。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": ["user", "memory"], "description": "要读取的 memory 目标"},
            "from_line": {"type": "integer", "description": "起始行号，默认 1"},
            "lines": {"type": "integer", "description": "读取行数，范围 1-200，默认 50"},
        },
        "required": ["target"],
    },
    handler=memory_get,
)
