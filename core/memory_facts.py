"""FactStore：结构化事实库（JSONL）存储层。

对齐 deer-flow 的 deermem storage 思想（见 memory_progress/01_Phase0_数据层FactStore.md）：
把长期记忆从"两个 markdown 文件里的 bullet"升级为"一行一个 JSON 事实"的结构化库。
每条 fact 带 id/content/category/confidence + 准入分类（scope/durability/authority）
+ 生命周期（created_at/expected_valid_days）+ 来源（source/source_error）。

工程化复用现有 ``core/memory.py:MemoryManager`` 的纪律：
- 进程间 advisory lock（fcntl）+ 进程内线程锁；
- 原子写（临时文件 + fsync + os.replace + 目录 fsync），要么完整成功要么原文件不变；
- 内容去重用 casefold key（移植 deer-flow ``_fact_content_key``）；
- 容量淘汰按 confidence 保留最高的（移植 ``_trim_facts_to_max`` + confidence coercion）。

存储布局：``memories/facts.jsonl``（全局）。JSONL 逐行解析、坏行可跳过而不毁整库。
本模块不涉及 LLM、不涉及注入——只做数据层，保证可独立测试。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - Windows fallback
    fcntl = None


# fact 的准入分类字段（Phase 2 的 scope gate 依据）。
FACT_CLASSIFICATION_FIELDS = ("scope", "durability", "authority")

# session 级情节记忆的默认落盘子目录（相对 memory_dir）。
SESSION_FACTS_SUBDIR = "sessions"

import re as _re

_SESSION_ID_SAFE_RE = _re.compile(r"[^A-Za-z0-9._-]+")


def safe_session_id(session_id: str) -> str:
    """把任意 session_id 规范成安全文件名片段（防路径穿越/非法字符）。

    对齐 deer-flow safe_user_id 思路：非白名单字符折叠为 ``_``；空/全非法时给稳定占位。
    """
    raw = (session_id or "").strip()
    if not raw:
        return "default"
    cleaned = _SESSION_ID_SAFE_RE.sub("_", raw).strip("._-")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "_")
    return cleaned or "default"


def utc_now_iso_z() -> str:
    """返回 ``2026-08-13T10:00:00Z`` 形式的 UTC 时间戳（对齐 deer-flow utc_now_iso_z）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def coerce_confidence(fact: dict[str, Any]) -> float:
    """把 fact 的 confidence 读成 [0,1] 内的有限 float，缺省 0.5。

    移植 deer-flow ``_coerce_source_confidence``：防范 null / bool / 非数值 /
    非有限值（来自损坏或手改的记忆文件），否则排序时会 crash。
    """
    raw = fact.get("confidence")
    if raw is None or isinstance(raw, bool):
        return 0.5
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(val, 1.0)) if math.isfinite(val) else 0.5


def content_key(content: Any) -> Optional[str]:
    """内容去重键（移植 deer-flow ``_fact_content_key``）：casefold + strip。"""
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    return stripped.casefold()


def generate_fact_id() -> str:
    """生成 fact id（对齐 deer-flow ``fact_{uuid4().hex[:8]}``）。"""
    return f"fact_{uuid.uuid4().hex[:8]}"


def trim_facts_to_max(facts: list[dict[str, Any]], max_facts: int) -> list[dict[str, Any]]:
    """按 confidence 保留最高的 ``max_facts`` 条（移植 deer-flow ``_trim_facts_to_max``）。

    confidence 经 :func:`coerce_confidence` 处理，legacy/导入的 null/非数值 confidence
    永不 crash 排序。保序不重要（trim 只关心保留集合），但为稳定性用 reverse 排序。
    """
    if max_facts is None or max_facts <= 0 or len(facts) <= max_facts:
        return facts
    return sorted(facts, key=coerce_confidence, reverse=True)[:max_facts]


class FactStore:
    """JSONL 事实库。线程/进程安全的读写 + 去重 + 容量淘汰。"""

    def __init__(self, memory_dir: str = "memories", filename: str = "facts.jsonl"):
        self.memory_dir = memory_dir
        os.makedirs(self.memory_dir, exist_ok=True)
        self.facts_file = os.path.join(self.memory_dir, filename)
        # 锁文件按 facts 文件名派生，避免同目录下多个 session store 共用一把锁、
        # 以及 delete_store 删掉别人的锁。
        self.lock_file = os.path.join(self.memory_dir, f".{filename}.lock")
        self._thread_lock = threading.RLock()
        self._ensure_file(self.facts_file)

    # ------------------------------------------------------------------
    # 基础文件工具（复用 MemoryManager 的锁 + 原子写纪律）
    # ------------------------------------------------------------------
    def _ensure_file(self, path: str) -> None:
        if not os.path.exists(path):
            self._atomic_write(path, "")

    @contextmanager
    def _lock(self):
        """进程间 advisory lock；fcntl 不可用时至少保留进程内线程锁。"""
        os.makedirs(self.memory_dir, exist_ok=True)
        with self._thread_lock:
            with open(self.lock_file, "a+", encoding="utf-8") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, path: str, text: str) -> None:
        """安全写文件：要么完整成功，要么原文件保持不变。"""
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-facts-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            try:
                dir_fd = os.open(directory, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):  # pragma: no cover - platform dependent
                pass
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def _parse_facts(self, text: str) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # 坏行跳过，绝不因一行脏数据毁整库。
                continue
            if isinstance(obj, dict) and isinstance(obj.get("content"), str):
                facts.append(obj)
        return facts

    def load_facts(self) -> list[dict[str, Any]]:
        """读取全部 fact；坏行被跳过。"""
        with self._lock():
            if not os.path.exists(self.facts_file):
                return []
            with open(self.facts_file, "r", encoding="utf-8") as f:
                text = f.read()
        return self._parse_facts(text)

    def count(self) -> int:
        return len(self.load_facts())

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def _serialize(self, facts: list[dict[str, Any]]) -> str:
        if not facts:
            return ""
        return "\n".join(json.dumps(fact, ensure_ascii=False) for fact in facts) + "\n"

    def _write_all_unlocked(self, facts: list[dict[str, Any]]) -> None:
        self._atomic_write(self.facts_file, self._serialize(facts))

    def write_all(self, facts: list[dict[str, Any]]) -> None:
        """锁内一次性原子重写全部 fact（apply 层批量落盘用）。"""
        with self._lock():
            self._write_all_unlocked(list(facts))

    def make_fact(
        self,
        content: str,
        *,
        category: str = "context",
        confidence: float = 0.5,
        scope: Optional[str] = None,
        durability: Optional[str] = None,
        authority: Optional[str] = None,
        source: str = "unknown",
        expected_valid_days: Optional[int] = None,
        source_error: Optional[str] = None,
        created_at: Optional[str] = None,
        fact_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict[str, Any]:
        """构造一条规范化 fact（不落盘）。

        metadata：可选的溯源元数据（如 dia_id/session/date/speaker）。原样保留，
        供 session 级情节记忆做官方 evidence recall 与检索排序。仅收非空标量/字符串值。
        """
        fact: dict[str, Any] = {
            "id": fact_id or generate_fact_id(),
            "content": content.strip(),
            "category": category or "context",
            "confidence": coerce_confidence({"confidence": confidence}),
            "created_at": created_at or utc_now_iso_z(),
            "source": source or "unknown",
        }
        for field, value in (("scope", scope), ("durability", durability), ("authority", authority)):
            if isinstance(value, str) and value.strip():
                fact[field] = value.strip().lower()
        if isinstance(expected_valid_days, int) and expected_valid_days > 0:
            fact["expected_valid_days"] = expected_valid_days
        if isinstance(source_error, str) and source_error.strip():
            fact["source_error"] = source_error.strip()
        if isinstance(metadata, dict):
            clean_meta = {}
            for k, v in metadata.items():
                if not isinstance(k, str) or not k.strip():
                    continue
                if isinstance(v, str):
                    v = v.strip()
                    if v:
                        clean_meta[k.strip()] = v
                elif isinstance(v, (int, float, bool)):
                    clean_meta[k.strip()] = v
            if clean_meta:
                fact["metadata"] = clean_meta
        return fact

    def append_fact(self, fact: dict[str, Any]) -> bool:
        """追加一条 fact（内容去重后）。返回 True 表示已写入，False 表示重复被跳过。"""
        key = content_key(fact.get("content"))
        if key is None:
            return False
        with self._lock():
            facts = self._parse_facts(self._read_text_unlocked())
            existing_keys = {content_key(f.get("content")) for f in facts}
            if key in existing_keys:
                return False
            if "id" not in fact:
                fact = {"id": generate_fact_id(), **fact}
            facts.append(fact)
            self._write_all_unlocked(facts)
        return True

    def remove_facts(self, ids) -> int:
        """按 id 批量删除，返回删除条数。"""
        id_set = {i for i in (ids or []) if isinstance(i, str) and i}
        if not id_set:
            return 0
        with self._lock():
            facts = self._parse_facts(self._read_text_unlocked())
            kept = [f for f in facts if f.get("id") not in id_set]
            removed = len(facts) - len(kept)
            if removed:
                self._write_all_unlocked(kept)
        return removed

    def replace_fact(self, old_id: str, new_fact: dict[str, Any]) -> bool:
        """矛盾替换：删旧 id + 加新 fact（新 fact 若内容重复则不重复添加）。"""
        with self._lock():
            facts = self._parse_facts(self._read_text_unlocked())
            kept = [f for f in facts if f.get("id") != old_id]
            changed = len(kept) != len(facts)
            key = content_key(new_fact.get("content"))
            existing_keys = {content_key(f.get("content")) for f in kept}
            if key is not None and key not in existing_keys:
                if "id" not in new_fact:
                    new_fact = {"id": generate_fact_id(), **new_fact}
                kept.append(new_fact)
                changed = True
            if changed:
                self._write_all_unlocked(kept)
        return changed

    def trim_to_max(self, max_facts: int) -> int:
        """按 confidence 淘汰超出 ``max_facts`` 的低置信度 fact，返回删除条数。"""
        with self._lock():
            facts = self._parse_facts(self._read_text_unlocked())
            trimmed = trim_facts_to_max(facts, max_facts)
            removed = len(facts) - len(trimmed)
            if removed:
                self._write_all_unlocked(trimmed)
        return removed

    def clear(self) -> None:
        """清空事实库（session 结束时用，让 session 级情节记忆随之消失）。"""
        with self._lock():
            self._write_all_unlocked([])

    def delete_store(self) -> None:
        """删除底层文件（session teardown 用，彻底不留痕）。"""
        with self._lock():
            for path in (self.facts_file, self.lock_file):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # 内部：锁内读原文（供 append/remove/replace 在同一临界区读-改-写）
    # ------------------------------------------------------------------
    def _read_text_unlocked(self) -> str:
        if not os.path.exists(self.facts_file):
            return ""
        with open(self.facts_file, "r", encoding="utf-8") as f:
            return f.read()


def session_fact_store(session_id: str, memory_dir: str = "memories") -> "FactStore":
    """为某个 session 打开一个 ephemeral 的 FactStore。

    落盘在 ``<memory_dir>/sessions/facts_<safe_session_id>.jsonl``。session 结束时调
    ``clear()`` / ``delete_store()`` 让这些细粒度情节记忆随之消失。文件名经
    :func:`safe_session_id` 规范化，防路径穿越。
    """
    sid = safe_session_id(session_id)
    sessions_dir = os.path.join(memory_dir, SESSION_FACTS_SUBDIR)
    return FactStore(memory_dir=sessions_dir, filename=f"facts_{sid}.jsonl")
