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


def memory_review(target: str = "all", long_entry_chars: int = 400) -> str:
    """只读审计长期 memory；只报告候选问题，不自动修改。"""
    try:
        return _json({
            "success": True,
            **memory_manager.review_memory(target=target, long_entry_chars=long_entry_chars),
        })
    except MemoryOperationError as e:
        return _json({"success": False, "error": str(e)})
    except Exception as e:
        return _json({"success": False, "error": f"Unexpected memory_review error: {e}"})


def memory_consolidate(target: str = "all", confirm: bool = False) -> str:
    """去重合并长期 memory：仅删除重复条目（保留每组首次出现）。

    人工批准闸门：``confirm`` 缺省为 false，只返回计划（dry-run）；必须显式传
    ``confirm=true`` 才真正删除。过长/易过期条目不在本操作范围内。
    """
    try:
        result = memory_manager.consolidate_memory(target=target, apply=bool(confirm))
        return _json({"success": True, **result})
    except MemoryOperationError as e:
        return _json({"success": False, "error": str(e)})
    except Exception as e:
        return _json({"success": False, "error": f"Unexpected memory_consolidate error: {e}"})


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

registry.register(
    name="memory_review",
    description=(
        "只读审计长期 memory 的健康状况：容量占用、重复条目、过长条目、"
        "疑似易过期的日期/任务/提交引用。该工具是 dry-run，只给人工复核建议，"
        "绝不会自动删除或修改 USER.md / MEMORY.md。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["all", "user", "memory"],
                "description": "审计范围，默认 all",
            },
            "long_entry_chars": {
                "type": "integer",
                "description": "超过多少字符视为过长候选，默认 400",
            },
        },
    },
    handler=memory_review,
    metadata={"summary": "只读审计长期记忆的重复、容量和易过期风险", "category": "memory"},
)

registry.register(
    name="memory_consolidate",
    description=(
        "去重合并长期 memory：仅删除重复条目（保留每组首次出现），不动过长/易过期条目。"
        "**人工批准闸门**：默认 confirm=false 只返回删除计划（dry-run，不改文件）；"
        "必须在向用户说明后显式传 confirm=true 才会真正删除。请先用 memory_review 复核，"
        "再在得到确认后执行。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["all", "user", "memory"],
                "description": "处理范围，默认 all",
            },
            "confirm": {
                "type": "boolean",
                "description": "false（默认）只返回计划；true 才真正删除重复条目",
            },
        },
    },
    handler=memory_consolidate,
    metadata={"summary": "去重合并长期记忆（需 confirm=true 才落盘）", "category": "memory"},
)
