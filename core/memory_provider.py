"""MemoryProvider 抽象：让长期记忆 backend 可替换。

对齐 deer-flow 的 MemoryManager contract（见 deer-flow 学习文档第 7 章 / 13.3）：
把"长期记忆系统应该有哪些能力"抽成一个薄接口，默认用文件型实现，未来可换成
向量库 / DB / 第三方 memory，而不影响调用方。

13.3 建议的最小契约：

    class MemoryProvider:
        def add(thread_id, messages, agent_name=None, user_id=None): ...
        def get_context(user_id=None, agent_name=None, thread_id=None) -> str: ...
        def search(query, top_k=5, user_id=None, agent_name=None): ...

R-Agent 已有一个成熟的文件型 ``MemoryManager``（core/memory.py）。本模块**不重写**它，
而是把它包装成 ``FileMemoryProvider``，作为**默认且零配置**的 backend——默认路径与
改造前逐字节等价。抽象的价值在于：注入路径（get_context）和未来的自动写入（add）
现在有了统一入口，可被中间件模式或其它 backend 复用。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from core.memory import memory_manager as _default_memory_manager


@runtime_checkable
class MemoryProvider(Protocol):
    """长期记忆 backend 契约（backend-agnostic）。"""

    def add(self, thread_id: str, messages: list, agent_name: Optional[str] = None,
            user_id: Optional[str] = None) -> None:
        """把一段对话交给 memory 系统，让它提取要长期记住的内容。"""
        ...

    def get_context(self, user_id: Optional[str] = None, agent_name: Optional[str] = None,
                    thread_id: Optional[str] = None) -> str:
        """返回适合注入模型的记忆文本（可能为空字符串）。"""
        ...

    def search(self, query: str, top_k: int = 5, user_id: Optional[str] = None,
               agent_name: Optional[str] = None) -> dict:
        """按查询搜索记忆。"""
        ...


class FileMemoryProvider:
    """把现有文件型 ``MemoryManager`` 适配成 MemoryProvider。

    这是 R-Agent 的默认 backend：行为与改造前完全一致，只是多了一层统一接口。
    """

    def __init__(self, manager=None):
        self._manager = manager or _default_memory_manager

    @property
    def manager(self):
        return self._manager

    def add(self, thread_id: str = "", messages: Optional[list] = None,
            agent_name: Optional[str] = None, user_id: Optional[str] = None) -> None:
        """文件型 backend 目前不做自动萃取（记忆由模型显式通过 memory 工具写入）。

        保留该方法以满足契约；middleware 模式的自动写入留待后续（见 04 文档）。
        这里是有意的 no-op，而不是遗漏。
        """
        return None

    def get_context(self, user_id: Optional[str] = None, agent_name: Optional[str] = None,
                    thread_id: Optional[str] = None) -> str:
        """返回冻结的记忆快照文本，供注入使用（与现有 load_snapshot 语义一致）。"""
        try:
            return self._manager.read_memory_snapshot()
        except Exception:
            return ""

    def get_live_context(self) -> str:
        """返回实时记忆文本（不使用冻结快照）。"""
        try:
            return self._manager.read_memory_live()
        except Exception:
            return ""

    def load_snapshot(self) -> str:
        """冻结并返回一次记忆快照（透传给底层 manager，供启动期调用）。"""
        try:
            return self._manager.load_snapshot()
        except Exception:
            return ""

    def search(self, query: str, top_k: int = 5, user_id: Optional[str] = None,
               agent_name: Optional[str] = None) -> dict:
        try:
            return self._manager.search_memory(query, target="all", max_results=top_k)
        except Exception as exc:
            return {"query": query, "count": 0, "results": [], "error": str(exc)}

    def review(self, target: str = "all", long_entry_chars: int = 400) -> dict:
        """只读治理报告；不修改任何 memory 文件。"""
        try:
            return self._manager.review_memory(target=target, long_entry_chars=long_entry_chars)
        except Exception as exc:
            return {"dry_run": True, "target": target, "error": str(exc)}


# 模块级默认 provider（文件型、零配置）。
default_memory_provider = FileMemoryProvider()


def get_memory_provider(name: Optional[str] = None) -> MemoryProvider:
    """按名字解析 memory provider。

    当前只有 ``file``（默认）与 ``noop`` 两个内置实现。保留该函数是为了让
    配置层（core/config.py）与未来的向量/第三方 backend 有统一解析入口。
    """
    normalized = (name or "file").strip().lower()
    if normalized in ("", "file", "deermem", "default"):
        return default_memory_provider
    if normalized == "noop":
        return _NoopMemoryProvider()
    # 未知名字退回默认文件型，保证永不因配置错字而崩溃。
    return default_memory_provider


class _NoopMemoryProvider:
    """空实现：关闭记忆时使用。"""

    def add(self, *args, **kwargs) -> None:
        return None

    def get_context(self, *args, **kwargs) -> str:
        return ""

    def get_live_context(self, *args, **kwargs) -> str:
        return ""

    def load_snapshot(self, *args, **kwargs) -> str:
        return ""

    def search(self, query: str, top_k: int = 5, **kwargs) -> dict:
        return {"query": query, "count": 0, "results": []}

    def review(self, target: str = "all", long_entry_chars: int = 400) -> dict:
        return {
            "dry_run": True,
            "target": target,
            "entry_count": 0,
            "capacities": {},
            "duplicate_groups": [],
            "long_entries": [],
            "staleness_candidates": [],
            "recommendations": ["Memory provider 已关闭，无需治理。"],
        }
