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

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from core.memory import memory_manager as _default_memory_manager

logger = logging.getLogger(__name__)


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
               agent_name: Optional[str] = None, thread_id: Optional[str] = None) -> dict:
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
               agent_name: Optional[str] = None,
               thread_id: Optional[str] = None) -> dict:
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

    def consolidate(self, target: str = "all", apply: bool = False) -> dict:
        """去重合并治理；apply=False 只返回计划，apply=True 才落盘（人工批准闸门）。"""
        try:
            return self._manager.consolidate_memory(target=target, apply=apply)
        except Exception as exc:
            return {"dry_run": not apply, "applied": False, "target": target, "error": str(exc)}


# 模块级默认 provider（文件型、零配置）。
default_memory_provider = FileMemoryProvider()

# deermem provider 单例（惰性构造，避免默认 file 路径付出任何代价）。
_deermem_provider = None
_deermem_lock = threading.Lock()


def _get_deermem_provider():
    global _deermem_provider
    if _deermem_provider is None:
        with _deermem_lock:
            if _deermem_provider is None:
                _deermem_provider = DeerMemProvider()
    return _deermem_provider


def get_memory_provider(name: Optional[str] = None) -> MemoryProvider:
    """按名字解析 memory provider。

    - ``file`` / ``default`` / 空 / 未知名字：文件型（默认、零配置、行为不变）。
    - ``deermem``：结构化 JSONL 事实库 backend（自动蒸馏 + gate + 注入 + FTS 检索 + 治理）。
    - ``noop``：空实现（关闭记忆）。
    未知名字容错退回默认文件型，保证永不因配置错字而崩溃。
    """
    normalized = (name or "file").strip().lower()
    if normalized == "deermem":
        return _get_deermem_provider()
    if normalized == "noop":
        return _NoopMemoryProvider()
    if normalized in ("", "file", "default"):
        return default_memory_provider
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


# ===========================================================================
# DeerMemProvider：结构化 JSONL 事实库 backend
# ===========================================================================
# 准入闸门（Phase 2，移植 deer-flow updater._fact_scope_gate_reason / _removal_scope_gate_reason）
_FACT_CLASSIFICATION_FIELDS = ("scope", "durability", "authority")


def _normalize_gate_label(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _fact_scope_gate_reason(fact: dict) -> Optional[str]:
    """新 fact 的确定性拒绝理由；None 表示通过。

    只有 scope=user + durability=durable + authority=descriptive 的事实才落盘。
    """
    if any(_normalize_gate_label(fact.get(f)) is None for f in _FACT_CLASSIFICATION_FIELDS):
        return "missing"
    if _normalize_gate_label(fact.get("scope")) != "user":
        return "scope"
    if _normalize_gate_label(fact.get("durability")) != "durable":
        return "durability"
    if _normalize_gate_label(fact.get("authority")) != "descriptive":
        return "authority"
    return None


def _session_fact_gate_reason(fact: dict) -> Optional[str]:
    """Session facts must be transient descriptive user/project/task facts."""
    from core.memory_facts import FACT_SCOPES

    if any(
        _normalize_gate_label(fact.get(field)) is None
        for field in _FACT_CLASSIFICATION_FIELDS
    ):
        return "missing"
    if _normalize_gate_label(fact.get("scope")) not in FACT_SCOPES:
        return "scope"
    if _normalize_gate_label(fact.get("durability")) != "transient":
        return "durability"
    if _normalize_gate_label(fact.get("authority")) != "descriptive":
        return "authority"
    return None


def _removal_scope_gate_reason(removal: dict) -> Optional[str]:
    """矛盾删除的确定性拒绝理由；None 表示通过。要求 scope=user 且有 reason。"""
    scope = _normalize_gate_label(removal.get("scope"))
    reason = removal.get("reason")
    if scope is None or not isinstance(reason, str) or not reason.strip():
        return "missing"
    if scope != "user":
        return "scope"
    return None


# ── 检索：FTS5 可用性探测（一次性缓存） ──────────────────────────────────
_FTS5_AVAILABLE: Optional[bool] = None


def _fts5_available() -> bool:
    global _FTS5_AVAILABLE
    if _FTS5_AVAILABLE is None:
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
            conn.close()
            _FTS5_AVAILABLE = True
        except Exception:
            _FTS5_AVAILABLE = False
    return bool(_FTS5_AVAILABLE)


# ── 分词：中文检索的关键（对齐 deer-flow retrieval._tokenize） ──────────────
# deer-flow 用可选 jieba 在 index+query 两侧预分词，缺失时退回 whitespace split。
# R-Agent 承诺零新依赖，因此这里：优先用 jieba（若恰好装了），否则用「CJK 逐字 +
# 相邻双字（bigram）+ 连续 ASCII 词」的无依赖切分——whitespace split 对中文无效，
# 而 unigram+bigram 能让「参加支持团体」这类查询命中「Caroline 参加了支持团体」。
try:  # 可选依赖，装了就用，没装也不影响。
    import jieba as _jieba  # type: ignore

    _JIEBA_AVAILABLE = True
except Exception:  # pragma: no cover - jieba 通常未安装
    _jieba = None
    _JIEBA_AVAILABLE = False

import re as _re

_ASCII_WORD_RE = _re.compile(r"[A-Za-z0-9]+")


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _cjk_bigrams_in_order(text: str) -> list[str]:
    """按原文顺序生成严格相邻的 CJK bigram（不跨非 CJK 字符）。"""
    bigrams: list[str] = []
    run: list[str] = []
    for ch in text:
        if _is_cjk(ch):
            run.append(ch)
        else:
            for i in range(len(run) - 1):
                bigrams.append(run[i] + run[i + 1])
            run = []
    for i in range(len(run) - 1):
        bigrams.append(run[i] + run[i + 1])
    return bigrams


def _search_tokens(text: str) -> list[str]:
    """检索用 token 集合：jieba 分词，或 ASCII 词 + CJK unigram + 严格相邻 bigram。"""
    if not text or not text.strip():
        return []
    if _JIEBA_AVAILABLE:
        return [t for t in _jieba.cut(text) if t.strip()]
    tokens: list[str] = [m.group(0).lower() for m in _ASCII_WORD_RE.finditer(text)]
    tokens.extend(ch for ch in text if _is_cjk(ch))
    tokens.extend(_cjk_bigrams_in_order(text))
    return tokens


def _estimate_tokens(text: str) -> int:
    """字符近似的 token 估算（CJK 友好、无新依赖）。约 2 字符 ≈ 1 token。"""
    return max(1, (len(text) + 1) // 2)


def _render_fact_line(fact: dict) -> str:
    return "- " + str(fact.get("content", "")).strip()


def format_facts_for_injection(
    facts: list[dict],
    *,
    max_tokens: int,
    guaranteed_categories: list[str],
    guaranteed_token_budget: int,
) -> str:
    """把 facts 按预算渲染成注入文本（移植 deer-flow format_memory_for_injection 思路）。

    保底类别先占 ``guaranteed_token_budget``，其余按 confidence 降序填充至 ``max_tokens``。
    """
    from core.memory_facts import coerce_confidence

    if not facts:
        return ""
    guaranteed_set = {c.strip().lower() for c in (guaranteed_categories or [])}
    guaranteed = [f for f in facts if str(f.get("category", "")).lower() in guaranteed_set]
    regular = [f for f in facts if str(f.get("category", "")).lower() not in guaranteed_set]
    guaranteed.sort(key=coerce_confidence, reverse=True)
    regular.sort(key=coerce_confidence, reverse=True)

    selected: list[dict] = []
    used = 0
    g_used = 0
    for f in guaranteed:
        line = _render_fact_line(f)
        t = _estimate_tokens(line)
        if g_used + t > guaranteed_token_budget:
            continue
        selected.append(f)
        g_used += t
        used += t
    for f in regular:
        line = _render_fact_line(f)
        t = _estimate_tokens(line)
        if used + t > max_tokens:
            continue
        selected.append(f)
        used += t

    selected.sort(key=coerce_confidence, reverse=True)
    return "\n".join(_render_fact_line(f) for f in selected)


class DeerMemProvider:
    """结构化 JSONL 事实库 backend：自动蒸馏 + 准入闸门 + 预算注入 + FTS 检索 + 治理。

    默认异步抽取（后台线程），失败绝不打断主 loop。可注入 ``store`` / ``extractor``
    并关闭 ``async_extract`` 以便测试。
    """

    def __init__(self, store=None, extractor=None, async_extract: bool = True, memory_dir: str = "memories"):
        from core.memory_facts import FactStore
        from core.memory_extractor import MemoryExtractor

        self._memory_dir = memory_dir
        self._store = store or FactStore(memory_dir=memory_dir)
        self._extractor = extractor if extractor is not None else MemoryExtractor()
        self._async_extract = async_extract
        self._watermark: dict[str, int] = {}
        self._apply_lock = threading.RLock()
        self._last_thread: Optional[threading.Thread] = None
        # session 级情节记忆：细粒度、带溯源 metadata、可检索、session 结束即消失。
        self._session_id: Optional[str] = None
        self._session_stores: dict[str, Any] = {}
        self._session_lock = threading.RLock()
        self._governance_state_file = os.path.join(
            self._memory_dir, ".deermem_governance.json"
        )

    @property
    def store(self):
        return self._store

    # ── session 级情节记忆管理 ──────────────────────────────────────────
    def set_session(self, session_id: Optional[str]):
        """切到某个 session；返回该 session 的 ephemeral FactStore（无则不建）。

        情节记忆按 session 粒度保存在独立 jsonl，与跨会话的 durable 事实库分离。
        """
        from core.memory_facts import session_fact_store

        with self._session_lock:
            if not session_id:
                self._session_id = None
                return None
            self._session_id = session_id
            store = self._session_stores.get(session_id)
            if store is None:
                store = session_fact_store(session_id, memory_dir=self._memory_dir)
                self._session_stores[session_id] = store
            return store

    def _get_session_store(self, session_id: Optional[str] = None):
        """返回当前 session store；session 模式开启但未显式设置时用 thread_id 兜底。"""
        with self._session_lock:
            key = session_id or self._session_id
            return self._session_stores.get(key) if key else None

    def end_session(
        self,
        session_id: Optional[str] = None,
        delete: bool = True,
    ) -> None:
        """结束当前 session：清空（或删除）其情节记忆，让这些细节随 session 消失。"""
        from core.memory_facts import session_fact_store

        with self._session_lock:
            key = session_id or self._session_id
            store = self._session_stores.pop(key, None) if key else None
            if key == self._session_id:
                self._session_id = None
        if store is None and key:
            # session memory 可能由隔离工具子进程创建；父进程没有内存映射，但仍可按
            # 约定路径打开并清理。
            store = session_fact_store(key, memory_dir=self._memory_dir)
        if store is not None:
            try:
                if delete:
                    store.delete_store()
                else:
                    store.clear()
            except Exception:
                logger.exception("deermem end_session cleanup failed (ignored)")

    # ── 写入路径（Phase 1 + 2） ─────────────────────────────────────────
    def add(self, thread_id: str = "", messages: Optional[list] = None,
            agent_name: Optional[str] = None, user_id: Optional[str] = None) -> None:
        """一轮结束后自动蒸馏记忆。watermark 去重 + 后台异步，绝不打断主 loop。"""
        try:
            from core.memory_extractor import prepare_update

            msgs = list(messages or [])
            key = thread_id or ""
            wm = self._watermark.get(key, 0)
            if wm > len(msgs):  # 新会话复用了同一 thread_id，重置水位
                wm = 0
            tail = msgs[wm:]
            if prepare_update(tail) is None:
                # 尚无完整「user + 最终 AI」交换，或全是附和轮：不抽取、不推进水位。
                return
            # 立即推进水位，避免异步未完成前重复抽取同一段。
            self._watermark[key] = len(msgs)
            if self._async_extract:
                t = threading.Thread(
                    target=self._extract_and_apply, args=(key, tail), daemon=True
                )
                t.start()
                self._last_thread = t
            else:
                self._extract_and_apply(key, tail)
        except Exception:
            # 记忆写入是增强项，绝不打断主循环。
            return

    def add_compression(
        self,
        thread_id: str = "",
        messages: Optional[list] = None,
    ) -> None:
        """处理一个已经确认发生的上下文压缩批次。

        压缩后 ``agent.messages`` 会被替换，旧的 message-index watermark 不再适用；
        因此压缩 hook 传入的批次应完整抽取一次，只依赖 fact 内容去重保证幂等。
        """
        batch = list(messages or [])
        if not batch:
            return
        if self._async_extract:
            thread = threading.Thread(
                target=self._extract_and_apply,
                args=(thread_id or "", batch),
                daemon=True,
            )
            thread.start()
            self._last_thread = thread
        else:
            self._extract_and_apply(thread_id or "", batch)

    def create_fact(
        self,
        content: str,
        *,
        category: str = "context",
        confidence: float = 1.0,
        source: str = "manual",
        metadata: Optional[dict] = None,
    ) -> dict:
        """手动创建一条 durable fact（供对话中的 ``memory add`` 工具调用）。"""
        from core import config

        fact = {
            "content": content,
            "category": category,
            "confidence": confidence,
            "scope": "user",
            "durability": "durable",
            "authority": "descriptive",
            "metadata": metadata or {},
        }
        stats = self._apply_updates(
            {
                "user": {},
                "history": {},
                "newFacts": [fact],
                "factsToRemove": [],
            },
            source,
            allow_governance=False,
        )
        return {"success": bool(stats.get("added")), **stats}

    def delete_fact(self, fact_id: str) -> bool:
        return bool(self._store.remove_facts([fact_id]))

    def replace_fact(self, fact_id: str, content: str) -> bool:
        facts = self._store.load_facts()
        old = next((fact for fact in facts if fact.get("id") == fact_id), None)
        if old is None:
            return False
        replacement = self._store.make_fact(
            content,
            category=old.get("category", "context"),
            confidence=old.get("confidence", 1.0),
            scope=old.get("scope", "user"),
            durability=old.get("durability", "durable"),
            authority=old.get("authority", "descriptive"),
            source="manual-replace",
            metadata=old.get("metadata"),
        )
        return self._store.replace_fact(fact_id, replacement)

    def _extract_and_apply(self, thread_id: str, tail: list) -> None:
        try:
            current_facts = self._store.load_facts()
            governance_due = self._governance_due(current_facts)
            try:
                from core import config

                update = self._extractor.extract(
                    tail,
                    current_facts,
                    governance_due=governance_due,
                    session_facts_enabled=config.get_memory_session_facts_enabled(),
                )
            except TypeError as exc:
                # 兼容测试/第三方注入的旧 extractor 签名。
                if (
                    "governance_due" not in str(exc)
                    and "session_facts_enabled" not in str(exc)
                ):
                    raise
                update = self._extractor.extract(tail, current_facts)
            if not update:
                return
            # 1) durable 事实库：经 scope gate 的跨会话用户级事实。
            self._apply_updates(
                update,
                thread_id,
                allow_governance=governance_due,
            )
            if governance_due:
                self._mark_governance_run(self._store.load_facts())
            # 2) session 工作记忆：user/project/task 的 transient descriptive facts，
            #    session 结束即消失；可保留 LoCoMo provenance 与工程任务中间事实。
            self._apply_session_facts(update, thread_id)
        except Exception:
            logger.exception("deermem extraction/apply failed (ignored)")

    def _load_governance_state(self) -> dict:
        try:
            with open(self._governance_state_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def _write_governance_state(self, state: dict) -> None:
        directory = os.path.dirname(self._governance_state_file) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_path = self._governance_state_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self._governance_state_file)

    def _governance_due(self, facts: list[dict], now: Optional[datetime] = None) -> bool:
        """只有“距上次整理已满间隔”且“有新增 fact”时才允许自动治理。

        首次调用只建立基线，不整理；状态持久化，进程重启不会重新频繁触发。
        """
        from core import config

        if not (
            config.get_memory_staleness_enabled()
            or config.get_memory_consolidation_enabled()
        ):
            return False
        now = now or datetime.now(timezone.utc)
        fact_ids = {
            str(fact.get("id"))
            for fact in facts
            if isinstance(fact, dict) and fact.get("id")
        }
        with self._apply_lock:
            state = self._load_governance_state()
            last_run = self._parse_dt(state.get("last_run_at", ""))
            previous_ids = {
                str(item)
                for item in state.get("fact_ids", [])
                if isinstance(item, str) and item
            }
            if last_run is None:
                self._write_governance_state({
                    "last_run_at": now.isoformat().replace("+00:00", "Z"),
                    "fact_ids": sorted(fact_ids),
                })
                return False
            interval = timedelta(days=config.get_memory_governance_interval_days())
            return now - last_run >= interval and bool(fact_ids - previous_ids)

    def _mark_governance_run(
        self,
        facts: list[dict],
        now: Optional[datetime] = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        fact_ids = sorted(
            str(fact.get("id"))
            for fact in facts
            if isinstance(fact, dict) and fact.get("id")
        )
        with self._apply_lock:
            self._write_governance_state({
                "last_run_at": now.isoformat().replace("+00:00", "Z"),
                "fact_ids": fact_ids,
            })

    def _apply_session_facts(self, update: dict, thread_id: str) -> int:
        """把 transient descriptive working facts 写入当前 session memory。

        ``scope`` may be user, project, or task. Durable, imperative, unknown-scope,
        low-confidence, empty, and duplicate facts never enter the session store.
        Session facts disappear when the session ends.
        """
        from core import config
        from core.memory_facts import (
            SESSION_PRIORITY_CATEGORIES,
            coerce_confidence,
            content_key,
        )

        if not config.get_memory_session_facts_enabled():
            return 0
        store = self._get_session_store(thread_id)
        # session 未显式 set 时，用 thread_id 兜底建一个（保证细节不丢）。
        if store is None and thread_id:
            store = self.set_session(thread_id)
        if store is None:
            return 0

        new_facts = update.get("newFacts", [])
        if not new_facts:
            return 0
        threshold = config.get_memory_session_fact_confidence_threshold()
        max_facts = config.get_memory_session_max_facts()

        def retention_key(fact: dict) -> tuple[int, int, float, str]:
            category = str(fact.get("category") or "context").strip().lower()
            metadata = fact.get("metadata")
            has_provenance = int(
                isinstance(metadata, dict)
                and bool(
                    metadata.get("source_turn_ids")
                    or metadata.get("primary_turn_id")
                    or metadata.get("dia_id")
                )
            )
            return (
                int(category in SESSION_PRIORITY_CATEGORIES),
                has_provenance,
                coerce_confidence(fact),
                str(fact.get("created_at") or ""),
            )

        added = 0
        with self._session_lock:
            existing = store.load_facts()
            existing_keys = {
                content_key(f.get("content")) for f in existing
                if content_key(f.get("content")) is not None
            }
            for fact in new_facts:
                if _session_fact_gate_reason(fact) is not None:
                    continue
                confidence = coerce_confidence(fact)
                if confidence < threshold:
                    continue
                key = content_key(fact.get("content"))
                if key is None or key in existing_keys:
                    continue
                entry = store.make_fact(
                    fact["content"],
                    category=fact.get("category", "context"),
                    confidence=confidence,
                    scope=fact.get("scope"),
                    durability=fact.get("durability"),
                    authority=fact.get("authority"),
                    source=thread_id or self._session_id or "session",
                    source_error=fact.get("source_error"),
                    metadata=fact.get("metadata"),
                )
                existing.append(entry)
                existing_keys.add(key)
                added += 1
            if added:
                if len(existing) > max_facts:
                    existing = sorted(existing, key=retention_key, reverse=True)[:max_facts]
                store.write_all(existing)
        return added

    def _apply_updates(
        self,
        update: dict,
        thread_id: str,
        *,
        allow_governance: bool = True,
    ) -> dict:
        """准入闸门 + 容量淘汰 + 矛盾删除 + 可选治理，一次性原子落盘。"""
        from core import config
        from core.memory_facts import coerce_confidence, content_key, trim_facts_to_max

        threshold = config.get_memory_fact_confidence_threshold()
        max_facts = config.get_memory_max_facts()
        creation_cap = int(
            config.get_memory_staleness_age_days()
            * config.get_memory_staleness_max_lifetime_multiplier()
        )
        stats = {"added": 0, "rejected": 0, "removed": 0}

        with self._apply_lock:
            facts = self._store.load_facts()
            existing_keys = {
                content_key(f.get("content"))
                for f in facts
                if content_key(f.get("content")) is not None
            }

            # ── Phase 5（可选）：staleness / consolidation 在新增之前跑 ──
            if allow_governance and config.get_memory_staleness_enabled():
                facts = self._apply_staleness(facts, update)
            if allow_governance and config.get_memory_consolidation_enabled():
                facts = self._apply_consolidation(facts, update, thread_id)
                existing_keys = {
                    content_key(f.get("content"))
                    for f in facts
                    if content_key(f.get("content")) is not None
                }

            # ── 新增 fact：scope gate + 置信度阈值 + 去重 ──
            for fact in update.get("newFacts", []):
                if _fact_scope_gate_reason(fact) is not None:
                    stats["rejected"] += 1
                    continue
                conf = coerce_confidence(fact)
                if conf < threshold:
                    stats["rejected"] += 1
                    continue
                key = content_key(fact.get("content"))
                if key is None or key in existing_keys:
                    continue
                evd = fact.get("expected_valid_days")
                capped_evd = None
                if isinstance(evd, int) and not isinstance(evd, bool) and evd > 0:
                    capped_evd = min(evd, creation_cap)
                entry = self._store.make_fact(
                    fact["content"],
                    category=fact.get("category", "context"),
                    confidence=conf,
                    scope=fact.get("scope"),
                    durability=fact.get("durability"),
                    authority=fact.get("authority"),
                    source=thread_id or "unknown",
                    expected_valid_days=capped_evd,
                    source_error=fact.get("source_error"),
                    metadata=fact.get("metadata"),
                )
                facts.append(entry)
                existing_keys.add(key)
                stats["added"] += 1

            # ── 容量淘汰（按 confidence 保留最高的） ──
            facts = trim_facts_to_max(facts, max_facts)

            # ── 矛盾删除：scope/reason gate，在新增+trim 之后 ──
            remove_ids: set[str] = set()
            for removal in update.get("factsToRemove", []):
                if not isinstance(removal, dict):
                    continue
                if _removal_scope_gate_reason(removal) is not None:
                    continue
                rid = removal.get("id")
                if isinstance(rid, str) and rid:
                    remove_ids.add(rid)
            if remove_ids:
                before = len(facts)
                facts = [f for f in facts if f.get("id") not in remove_ids]
                stats["removed"] = before - len(facts)

            self._store.write_all(facts)
        return stats

    # ── Phase 5 治理（默认关闭；apply-layer 硬护栏独立于 LLM 行为） ────────
    def _apply_staleness(self, facts: list[dict], update: dict) -> list[dict]:
        """过期 fact 的受控删除/续期。带 per-cycle cap + 保护类别 + id 交集校验。"""
        from core import config
        from core.memory_facts import coerce_confidence

        protected = {c.strip().lower() for c in config.get_memory_staleness_protected_categories()}
        age_days = config.get_memory_staleness_age_days()
        max_removals = config.get_memory_staleness_max_removals_per_cycle()
        max_ext = config.get_memory_staleness_max_extension_days()

        candidate_ids = {
            f["id"]
            for f in self._select_stale_candidates(facts, age_days, protected)
            if f.get("id")
        }

        stale_removals = update.get("staleFactsToRemove") or []
        stale_extensions = update.get("staleFactsToExtend") or []

        proposed_remove_ids = {
            e["id"] for e in stale_removals if isinstance(e, dict) and isinstance(e.get("id"), str)
        }
        remove_ids = proposed_remove_ids & candidate_ids
        # per-cycle cap：超限时优先删最低置信度的
        if len(remove_ids) > max_removals:
            stale_facts = [f for f in facts if f.get("id") in remove_ids]
            stale_facts.sort(key=coerce_confidence)
            remove_ids = {f["id"] for f in stale_facts[:max_removals]}
        if remove_ids:
            facts = [f for f in facts if f.get("id") not in remove_ids]

        # 续期：候选中未被提议删除的 fact 才可续期
        extendable_ids = candidate_ids - proposed_remove_ids
        ext_by_id = {
            e["id"]: e
            for e in stale_extensions
            if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"] in extendable_ids
        }
        if ext_by_id:
            now = datetime.now(timezone.utc)
            updated = []
            for fact in facts:
                ext = ext_by_id.get(fact.get("id"))
                if ext is not None:
                    extend_by = ext.get("extend_by_days")
                    if isinstance(extend_by, (int, float)) and not isinstance(extend_by, bool) and int(extend_by) > 0:
                        created = self._parse_dt(fact.get("created_at", ""))
                        if created is not None:
                            days_since = int((now - created).total_seconds() // 86400)
                            new_evd = min(days_since + int(extend_by), max_ext)
                            fact = {**fact, "expected_valid_days": new_evd}
                updated.append(fact)
            facts = updated
        return facts

    def _select_stale_candidates(self, facts: list[dict], age_days: int, protected: set) -> list[dict]:
        now = datetime.now(timezone.utc)
        candidates = []
        for f in facts:
            if str(f.get("category", "")).lower() in protected:
                continue
            created = self._parse_dt(f.get("created_at", ""))
            if created is None:
                continue
            days = (now - created).total_seconds() / 86400
            evd = f.get("expected_valid_days")
            threshold = evd if isinstance(evd, int) and not isinstance(evd, bool) and evd > 0 else age_days
            if days >= threshold:
                candidates.append(f)
        return candidates

    def _apply_consolidation(self, facts: list[dict], update: dict, thread_id: str) -> list[dict]:
        """把 ≥2 条相关 fact 合并成 1 条；置信度取 min、createdAt 取最早、保护类别不参与。"""
        from core import config
        from core.memory_facts import coerce_confidence, content_key

        protected = {c.strip().lower() for c in config.get_memory_staleness_protected_categories()}
        max_groups = config.get_memory_consolidation_max_groups_per_cycle()
        max_sources = config.get_memory_consolidation_max_sources()

        groups = update.get("factsToConsolidate") or []
        by_id = {f.get("id"): f for f in facts}
        applied = 0
        removed_ids: set[str] = set()
        new_entries: list[dict] = []

        for group in groups:
            if applied >= max_groups:
                break
            if not isinstance(group, dict):
                continue
            source_ids = group.get("sourceIds")
            consolidated = group.get("consolidated")
            if not isinstance(source_ids, list) or not isinstance(consolidated, dict):
                continue
            clean_ids = list(dict.fromkeys(s for s in source_ids if isinstance(s, str) and s in by_id))
            if len(clean_ids) < 2 or len(clean_ids) > max_sources:
                continue
            sources = [by_id[i] for i in clean_ids]
            # 保护类别不参与合并
            if any(str(s.get("category", "")).lower() in protected for s in sources):
                continue
            content = consolidated.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            # 置信度取 min(LLM 值, 源最大值)，不膨胀
            llm_conf = coerce_confidence(consolidated)
            source_max = max(coerce_confidence(s) for s in sources)
            merged_conf = min(llm_conf, source_max)
            # createdAt 取最早、继承最短复查期
            created_ats = [s.get("created_at") for s in sources if s.get("created_at")]
            earliest = min(created_ats) if created_ats else None
            evds = [s.get("expected_valid_days") for s in sources
                    if isinstance(s.get("expected_valid_days"), int) and not isinstance(s.get("expected_valid_days"), bool)]
            shortest_evd = min(evds) if evds else None
            entry = self._store.make_fact(
                content.strip(),
                category=consolidated.get("category", "context"),
                confidence=merged_conf,
                scope=_normalize_gate_label(consolidated.get("scope")) or "user",
                durability=_normalize_gate_label(consolidated.get("durability")) or "durable",
                authority=_normalize_gate_label(consolidated.get("authority")) or "descriptive",
                source="consolidation",
                expected_valid_days=shortest_evd,
                created_at=earliest,
            )
            removed_ids.update(clean_ids)
            new_entries.append(entry)
            applied += 1

        if removed_ids or new_entries:
            facts = [f for f in facts if f.get("id") not in removed_ids]
            # 合并结果去重
            existing_keys = {content_key(f.get("content")) for f in facts}
            for e in new_entries:
                k = content_key(e.get("content"))
                if k is not None and k not in existing_keys:
                    facts.append(e)
                    existing_keys.add(k)
        return facts

    @staticmethod
    def _parse_dt(raw: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    # ── 读取路径（Phase 3） ─────────────────────────────────────────────
    def get_context(self, user_id: Optional[str] = None, agent_name: Optional[str] = None,
                    thread_id: Optional[str] = None) -> str:
        from core import config

        try:
            facts = self._store.load_facts()
            if not facts:
                return ""
            return format_facts_for_injection(
                facts,
                max_tokens=config.get_memory_max_injection_tokens(),
                guaranteed_categories=config.get_memory_guaranteed_categories(),
                guaranteed_token_budget=config.get_memory_guaranteed_token_budget(),
            )
        except Exception:
            return ""

    def get_live_context(self) -> str:
        return self.get_context()

    def load_snapshot(self) -> str:
        return self.get_context()

    # ── 检索路径（Phase 4 + session 情节记忆） ─────────────────────────
    def _searchable_facts(self, thread_id: Optional[str] = None) -> list[dict]:
        """Return a defensive, content-deduplicated durable/session union.

        New writes are mutually exclusive by durability, but old stores may
        contain the same fact in both locations. Session entries override an
        equal-content durable entry because they usually carry richer provenance.
        """
        from core.memory_facts import content_key

        durable_facts = list(self._store.load_facts())
        session_facts = []
        store = self._get_session_store(thread_id)
        if store is None and thread_id:
            store = self.set_session(thread_id)
        if store is not None:
            try:
                session_facts = store.load_facts()
            except Exception:
                logger.exception("deermem session facts load failed (ignored)")

        merged: dict[str, dict] = {}
        unkeyed: list[dict] = []
        for fact in durable_facts:
            key = content_key(fact.get("content"))
            if key is None:
                unkeyed.append(fact)
            else:
                merged[key] = fact
        for fact in session_facts:
            key = content_key(fact.get("content"))
            if key is None:
                unkeyed.append(fact)
            else:
                merged[key] = fact
        return [*merged.values(), *unkeyed]

    def search(self, query: str, top_k: int = 5, user_id: Optional[str] = None,
               agent_name: Optional[str] = None,
               thread_id: Optional[str] = None) -> dict:
        q = (query or "").strip()
        try:
            k = int(top_k)
        except (TypeError, ValueError):
            k = 5
        k = max(1, min(k, 50))
        if not q:
            return {"query": query, "count": 0, "results": []}
        facts = self._searchable_facts(thread_id)
        # 优先 FTS5；无命中/不可用时退回子串匹配（对齐 deer-flow：empty 也 fallback，
        # 因为 FTS5 默认 unicode61 分词不切分 CJK，"中文" 作为子串常查不到 token）。
        results = self._fts_search(q, k, facts)
        if not results:
            results = self._substring_search(q, k, facts)
        return {"query": q, "count": len(results), "results": results}

    def _public_fact(self, fact: dict, score: Optional[float] = None) -> dict:
        from core.memory_facts import coerce_confidence

        out = {
            "id": fact.get("id"),
            "content": fact.get("content"),
            "category": fact.get("category", "context"),
            "confidence": coerce_confidence(fact),
        }
        for field in ("scope", "durability", "authority"):
            value = fact.get(field)
            if isinstance(value, str) and value:
                out[field] = value
        # 溯源 metadata（dia_id/session/date/speaker）随结果返回，供 evidence recall
        # 与检索排序使用。
        meta = fact.get("metadata")
        if isinstance(meta, dict) and meta:
            out["metadata"] = meta
        if score is not None:
            out["score"] = score
        return out

    def _build_match_expr(self, query: str) -> Optional[str]:
        # 用与索引一致的分词切 query（CJK 逐字/bigram 或 jieba），再引号包裹 + OR 连接。
        tokens = _search_tokens(query)
        # 去重保序，去掉纯符号 token。
        seen = set()
        clean = []
        for t in tokens:
            t = t.strip().replace('"', '""')
            if not t or t in seen:
                continue
            seen.add(t)
            clean.append(t)
        if not clean:
            return None
        return " OR ".join(f'"{t}"' for t in clean)

    def _fts_search(self, query: str, top_k: int, facts: Optional[list[dict]] = None) -> Optional[list[dict]]:
        """SQLite FTS5 全文检索；FTS5 不可用/出错时返回 None 触发 fallback。

        content 在索引时用与 query 一致的分词预切并 space-join（对齐 deer-flow：
        FTS5 unicode61 不切分 CJK，必须靠预分词，中文才能命中）。
        """
        if not _fts5_available():
            return None
        try:
            if facts is None:
                facts = self._searchable_facts()
            if not facts:
                return []
            match_expr = self._build_match_expr(query)
            if not match_expr:
                return None
            conn = sqlite3.connect(":memory:")
            try:
                conn.execute("CREATE VIRTUAL TABLE facts_fts USING fts5(fid UNINDEXED, content)")
                conn.executemany(
                    "INSERT INTO facts_fts(fid, content) VALUES (?, ?)",
                    [
                        (str(idx), " ".join(_search_tokens(str(f.get("content", "")))))
                        for idx, f in enumerate(facts)
                    ],
                )
                rows = conn.execute(
                    "SELECT fid, bm25(facts_fts) AS rank FROM facts_fts "
                    "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                    (match_expr, top_k),
                ).fetchall()
            finally:
                conn.close()
            results = []
            for fid, rank in rows:
                try:
                    f = facts[int(fid)]
                except (ValueError, IndexError, TypeError):
                    continue
                # bm25 越小越相关；取负让 score 越大越相关。
                results.append(self._public_fact(f, score=-float(rank)))
            return results
        except Exception:
            logger.exception("deermem FTS search failed; falling back to substring")
            return None

    def _substring_search(self, query: str, top_k: int, facts: Optional[list[dict]] = None) -> list[dict]:
        """无 FTS/无命中时的词法 fallback：token 重叠打分（中文用 unigram+bigram）。

        先按整句 substring 命中（最强信号）；否则按 query 与 content 的 token 重叠数
        打分，重叠越多越相关，再按 confidence 兜底排序。这样中文 query 不再只能整句
        匹配，"参加支持团体" 可命中 "Caroline 参加了支持团体"。
        """
        from core.memory_facts import coerce_confidence

        q = query.strip()
        if not q:
            return []
        if facts is None:
            facts = self._searchable_facts()
        ql = q.lower()
        query_tokens = set(t.lower() for t in _search_tokens(q))

        scored: list[tuple[float, float, dict]] = []
        for f in facts:
            content = f.get("content")
            if not isinstance(content, str):
                continue
            cl = content.lower()
            overlap = 0.0
            if ql in cl:
                # 整句子串命中：强信号，给一个不会被普通重叠超过的基线分。
                overlap = float(len(query_tokens) + 1) if query_tokens else 1.0
            elif query_tokens:
                content_tokens = set(t.lower() for t in _search_tokens(content))
                overlap = float(len(query_tokens & content_tokens))
            if overlap > 0:
                scored.append((overlap, coerce_confidence(f), f))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [self._public_fact(f, score=ov) for ov, _c, f in scored[:top_k]]
