"""Append-only 运行事件流（Run Event Stream）。

对齐 deer-flow 的 RunEventStore 思想（见 deer-flow 学习文档第 11 章 / 13.7）：
主循环每一步都往一个 append-only 的 JSONL 里追加一条带 ``seq`` 的事件，
供事后回放、调试、审计。它与 GUI 用的实时 ``event_sink`` 互补——``event_sink``
负责“现在正在发生什么”，本模块负责“这次 run 到底发生过什么”，落盘可回放。

设计要点（都来自本仓库既有约定）：

* 事件信封字段对齐 deer-flow：
  ``thread_id / run_id / seq / event_type / category / content / metadata / created_at``。
* 落盘风格沿用 ``autoresearch/observability/debug.py``：``open("a")`` 追加、
  ``json.dumps(..., ensure_ascii=False, default=str)``。
* **永不拖垮主循环**：任何写入异常都被吞掉，只记一次内部 warning，绝不抛出。
  这一点与 ``core/agent.py:_emit_event`` 的“observability must never break the loop”一致。
* ``seq`` 在单个 store 实例内单调递增，用于严格排序回放。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 事件类型与分类常量
# ---------------------------------------------------------------------------
# 事件类型对齐 deer-flow 学习文档 13.7 建议 R-Agent 建立的事件集合。
EV_RUN_START = "run.start"
EV_RUN_END = "run.end"
EV_RUN_ERROR = "run.error"
EV_LLM_REQUEST = "llm.request"
EV_LLM_RESPONSE = "llm.response"
EV_TOOL_CALL = "tool.call"
EV_TOOL_RESULT = "tool.result"
EV_CONTEXT_COMPACT = "context.compact"
EV_MEMORY_INJECT = "memory.inject"
EV_MEMORY_UPDATE = "memory.update"
EV_DELEGATE_START = "delegate.start"
EV_DELEGATE_STEP = "delegate.step"
EV_DELEGATE_END = "delegate.end"
EV_ARTIFACT_CREATED = "artifact.created"

# category 对齐 deer-flow 第 11 章 RunEventStore 的 category 维度。
_CATEGORY_BY_TYPE = {
    EV_RUN_START: "trace",
    EV_RUN_END: "outputs",
    EV_RUN_ERROR: "error",
    EV_LLM_REQUEST: "trace",
    EV_LLM_RESPONSE: "trace",
    EV_TOOL_CALL: "trace",
    EV_TOOL_RESULT: "trace",
    EV_CONTEXT_COMPACT: "context",
    EV_MEMORY_INJECT: "context",
    EV_MEMORY_UPDATE: "context",
    EV_DELEGATE_START: "subagent",
    EV_DELEGATE_STEP: "subagent",
    EV_DELEGATE_END: "subagent",
    EV_ARTIFACT_CREATED: "workspace",
}


def category_for(event_type: str) -> str:
    """把事件类型映射到 deer-flow 风格的 category；未知类型归入 message。"""
    return _CATEGORY_BY_TYPE.get(event_type, "message")


# ---------------------------------------------------------------------------
# 事件信封
# ---------------------------------------------------------------------------
@dataclass
class RunEvent:
    """一条运行事件，字段对齐 deer-flow RunEventStore envelope。"""

    thread_id: str
    run_id: str
    seq: int
    event_type: str
    category: str
    content: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Append-only store
# ---------------------------------------------------------------------------
class RunEventStore:
    """把运行事件按 seq 顺序追加到 ``<dir>/<run_id>.jsonl``。

    线程安全：``emit`` 用一把锁保护 seq 自增与文件追加，兼容 delegate 的多线程。
    降级安全：任何写入错误都不会抛给调用方，只在实例上记一次 warning。
    """

    def __init__(
        self,
        run_id: str,
        thread_id: str = "",
        base_dir: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self.run_id = str(run_id or "run")
        self.thread_id = str(thread_id or "")
        self.enabled = bool(enabled)
        self._seq = 0
        self._lock = threading.Lock()
        self._warned = False
        self.last_error: Optional[str] = None
        base = base_dir or os.path.join("sandbox", "run_events")
        self._dir = Path(base)
        self._path = self._dir / f"{_safe_run_id(self.run_id)}.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event_type: str, content: Any = None, **metadata) -> Optional[RunEvent]:
        """追加一条事件；返回写入的 RunEvent（失败或禁用时返回 None）。

        ``content`` 传 dict 时原样作为事件负载；传其它类型时包成
        ``{"value": ...}``，避免 JSONL 里出现非对象顶层结构。
        """
        if not self.enabled:
            return None
        if isinstance(content, dict):
            payload = content
        elif content is None:
            payload = {}
        else:
            payload = {"value": content}
        try:
            with self._lock:
                self._seq += 1
                event = RunEvent(
                    thread_id=self.thread_id,
                    run_id=self.run_id,
                    seq=self._seq,
                    event_type=event_type,
                    category=category_for(event_type),
                    content=payload,
                    metadata=dict(metadata or {}),
                )
                self._dir.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
                return event
        except Exception as exc:  # noqa: BLE001 - observability 绝不打断主循环
            self.last_error = str(exc)
            if not self._warned:
                self._warned = True
                try:
                    import warnings

                    warnings.warn(f"RunEventStore 写入失败，已降级为静默：{exc}")
                except Exception:
                    pass
            return None


def _safe_run_id(value: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "_.-") else "_" for ch in str(value or "run"))
    return safe[:120] or "run"


def read_events(path: str | Path) -> list[dict]:
    """读取一个事件文件，按 seq 排序返回（用于回放/测试）。"""
    p = Path(path)
    rows: list[dict] = []
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
