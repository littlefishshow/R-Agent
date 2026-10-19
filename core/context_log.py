"""虚拟上下文：会话级、append-only、无损的时间流（Context Log）。

## 为什么需要它

R-Agent 原本只有一个**物理上下文** ``ThreadState.messages``：它既是真相，又是发给
模型的窗口。上下文压缩（``_maybe_compress_context`` / ``_compress_after_archive``）会
**就地**把旧消息替换成一条摘要——原始完整消息在内存里随之消失。这让"排查 bug / Agent
溯源过去任务"失去可靠底座：既有的 ``RunEventStore`` 是 per-run、只存预览、fail-open 丢
事件；GUI ``ContextSnapshotStore`` 的 fork/truncate 也是破坏性重写。

## 设计：虚拟上下文 vs 物理上下文（二者隔离）

```text
真相源（虚拟上下文）              投影视图（物理上下文）
ContextLog（本文件）     --project-->   messages（发给模型的有界窗口）
 append-only / 无损 / 会话级            可被压缩/重建/KV 稳定化，丢了也能复原
```

两条不变量：

1. **ContextLog 只增不改不删**。压缩不删数据，只追加一个 ``fold`` 标记（记录被折叠的
   ``[start_seq, end_seq]`` 区间与摘要）；原始消息仍以它们已记录的 entry 留在日志里。
2. 物理上下文始终可从日志复算；因此它可以安全地被压缩。

## 与 L0 事件流的衔接

L0（``core/llm``）是**模型侧**的事件源：``AssistantMessageEvent`` 描述一次响应如何
产生，``AssistantMessage`` 是其最终归约。ContextLog 是**会话侧**的事件源：把每条进入
对话的消息（含 L0 最终消息里的 ``thinking``，而物理 messages 会丢弃它）、每次折叠、
每次 durable 晋升、每轮 run 边界，按 ``seq`` 追加成一条可回放的时间流。

一句话：L0 回答"这一次模型调用怎么产生的"，ContextLog 回答"这个会话一路发生了什么、
为什么模型现在看不到早期内容"。

## 落盘

每个会话一个 ``<dir>/<session>.context.jsonl``，一行一个 entry。写入 fail-open（不拖垮
主循环），但读取端 ``slice`` / ``search`` 做无损回放。``seq`` 在会话内单调递增、跨 run
连续，用于确定性排序与折叠区间引用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Entry 类型常量
# ---------------------------------------------------------------------------
# 对话消息镜像（物理 messages 的无损副本，外加物理层会丢弃的 thinking）。
ET_USER = "user"
ET_ASSISTANT = "assistant"
ET_TOOL_RESULT = "tool_result"
ET_SYSTEM = "system"
# 控制 / 元事件（解释上下文为什么变化，供溯源）。
ET_FOLD = "fold"                 # 一次压缩/归档，折叠 [start_seq, end_seq] 为摘要
ET_DURABLE_PROMOTE = "durable_promote"
ET_RUN_START = "run_start"
ET_RUN_END = "run_end"
ET_BRANCH = "branch"             # fork/truncate 锚点（不回写父历史）

_MESSAGE_TYPES = {ET_USER, ET_ASSISTANT, ET_TOOL_RESULT, ET_SYSTEM}


@dataclass
class ContextEntry:
    """时间流里的一条记录。

    * ``seq``      —— 会话内单调递增、跨 run 连续的顺序号；折叠区间用它引用；
    * ``run_id``   —— 属于哪一次用户请求；
    * ``ts``       —— Unix 时间戳（排序强保证仍来自 ``seq``）；
    * ``type``     —— 见上面的 ET_* 常量；
    * ``role``     —— 消息类 entry 的角色（user/assistant/tool/system）；
    * ``msg_id``   —— 稳定消息 id，关联物理 message 与日志 entry；
    * ``content``  —— **全量**内容（消息类为原文；元事件为结构化负载）；
    * ``meta``     —— 附加信息（tool 名、折叠统计、thinking、epoch 等）。
    """

    seq: int
    run_id: str
    ts: float
    type: str
    role: str = ""
    msg_id: str = ""
    content: Any = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_message(self) -> bool:
        return self.type in _MESSAGE_TYPES


class ContextLog:
    """会话级 append-only 无损时间流。

    线程安全：一把锁保护 ``seq`` 自增、内存列表与文件追加（兼容 delegate 多线程）。
    降级安全：写盘异常被吞掉、只记一次 warning，绝不抛给主循环（与 RunEventStore 一致）。
    但内存中的 entries 始终完整，``slice`` / ``search`` 据此做无损回放。
    """

    def __init__(
        self,
        session_id: str,
        *,
        base_dir: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self.session_id = str(session_id or "default")
        self.enabled = bool(enabled)
        self._seq = 0
        self._entries: List[ContextEntry] = []
        self._lock = threading.RLock()
        self._warned = False
        self.last_error: Optional[str] = None
        base = base_dir or os.path.join("sandbox", "context_log")
        self._dir = Path(base)
        self._path = self._dir / f"{_safe_name(self.session_id)}.context.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def next_seq(self) -> int:
        with self._lock:
            return self._seq + 1

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def append(
        self,
        entry_type: str,
        content: Any = "",
        *,
        run_id: str = "",
        role: str = "",
        msg_id: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[ContextEntry]:
        """追加一条 entry，返回它（禁用时返回 None）。内存始终记录，落盘 fail-open。"""
        if not self.enabled:
            return None
        with self._lock:
            self._seq += 1
            entry = ContextEntry(
                seq=self._seq,
                run_id=str(run_id or ""),
                ts=time.time(),
                type=str(entry_type),
                role=str(role or ""),
                msg_id=str(msg_id or ""),
                content=content,
                meta=dict(meta or {}),
            )
            self._entries.append(entry)
            self._persist(entry)
            return entry

    def record_message(self, message: Any, *, run_id: str = "", msg_id: str = "", thinking: str = "") -> Optional[ContextEntry]:
        """把一条对话消息无损镜像进日志。

        ``message`` 可为 OpenAI 风格 dict 或 SDK 对象。``thinking`` 用于保留 L0 最终消息
        里的思考内容——物理 messages 丢弃它，但虚拟上下文完整保留，便于溯源。
        """
        role = _msg_field(message, "role") or "assistant"
        entry_type = {
            "user": ET_USER,
            "assistant": ET_ASSISTANT,
            "tool": ET_TOOL_RESULT,
            "system": ET_SYSTEM,
        }.get(str(role), ET_ASSISTANT)
        content = _msg_field(message, "content")
        meta: Dict[str, Any] = {}
        tool_calls = _msg_field(message, "tool_calls")
        if tool_calls:
            meta["tool_calls"] = _plain(tool_calls)
        name = _msg_field(message, "name")
        if name:
            meta["name"] = name
        tcid = _msg_field(message, "tool_call_id")
        if tcid:
            meta["tool_call_id"] = tcid
        if thinking:
            meta["thinking"] = thinking
        return self.append(
            entry_type,
            content if content is not None else "",
            run_id=run_id,
            role=str(role),
            msg_id=msg_id,
            meta=meta,
        )

    def record_fold(
        self,
        *,
        start_seq: int,
        end_seq: int,
        summary: str,
        run_id: str = "",
        reason: str = "compaction",
        stats: Optional[Dict[str, Any]] = None,
    ) -> Optional[ContextEntry]:
        """记录一次折叠：物理层把 [start_seq, end_seq] 替换成摘要，日志只追加该标记。

        原始消息仍以其 entry 留在日志里，``slice(start_seq, end_seq)`` 可无损取回。
        """
        return self.append(
            ET_FOLD,
            summary or "",
            run_id=run_id,
            meta={
                "start_seq": int(start_seq),
                "end_seq": int(end_seq),
                "reason": str(reason),
                "stats": dict(stats or {}),
            },
        )

    # ------------------------------------------------------------------
    # 读取 / 溯源
    # ------------------------------------------------------------------
    def entries(self) -> List[ContextEntry]:
        with self._lock:
            return list(self._entries)

    def slice(self, start_seq: int, end_seq: int, *, messages_only: bool = True) -> List[ContextEntry]:
        """取回 ``[start_seq, end_seq]``（含端点）内的 entry。

        用于回放被折叠段的原始消息——这是无损溯源的核心操作。
        """
        start_seq = int(start_seq)
        end_seq = int(end_seq)
        with self._lock:
            out = [
                e for e in self._entries
                if start_seq <= e.seq <= end_seq and (not messages_only or e.is_message)
            ]
        return out

    def search(self, query: str, *, limit: int = 50, messages_only: bool = True) -> List[ContextEntry]:
        """在整条日志（含已折叠段）里按子串检索；大小写不敏感。"""
        q = str(query or "").strip().lower()
        if not q:
            return []
        with self._lock:
            source = list(self._entries)
        hits: List[ContextEntry] = []
        for e in source:
            if messages_only and not e.is_message:
                continue
            haystack = _entry_text(e).lower()
            if q in haystack:
                hits.append(e)
                if len(hits) >= max(1, limit):
                    break
        return hits

    def timeline(self, *, include_folded: bool = True) -> List[ContextEntry]:
        """返回全部 entry 的时间线（供回放器）。"""
        with self._lock:
            if include_folded:
                return list(self._entries)
            folded = self._folded_seq_set()
            return [e for e in self._entries if e.seq not in folded]

    def _folded_seq_set(self) -> set:
        folded: set = set()
        for e in self._entries:
            if e.type == ET_FOLD:
                s = int(e.meta.get("start_seq") or 0)
                t = int(e.meta.get("end_seq") or 0)
                folded.update(range(s, t + 1))
        return folded

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def _persist(self, entry: ContextEntry) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 - observability 绝不打断主循环
            self.last_error = str(exc)
            if not self._warned:
                self._warned = True
                try:
                    import warnings

                    warnings.warn(f"ContextLog 写入失败，已降级为静默：{exc}")
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _safe_name(value: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "_.-") else "_" for ch in str(value or "session"))
    return safe[:120] or "session"


def _msg_field(message: Any, key: str) -> Any:
    if isinstance(message, dict):
        return message.get(key)
    return getattr(message, key, None)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _plain(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            return _plain(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        public = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        if public:
            return _plain(public)
    return str(value)


def _entry_text(entry: ContextEntry) -> str:
    parts = [str(entry.content or "")]
    meta = entry.meta or {}
    if meta.get("thinking"):
        parts.append(str(meta["thinking"]))
    if meta.get("name"):
        parts.append(str(meta["name"]))
    tool_calls = meta.get("tool_calls")
    if tool_calls:
        try:
            parts.append(json.dumps(tool_calls, ensure_ascii=False, default=str))
        except Exception:
            parts.append(str(tool_calls))
    return "\n".join(p for p in parts if p)


def read_context_log(path: str | Path) -> List[Dict[str, Any]]:
    """读取一个 context log 文件，按 seq 排序返回（用于回放/测试）。"""
    p = Path(path)
    rows: List[Dict[str, Any]] = []
    if not p.exists():
        return rows
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    rows.sort(key=lambda r: r.get("seq", 0))
    return rows
