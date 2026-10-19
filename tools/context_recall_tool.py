"""context_recall / context_search：Agent 的虚拟上下文溯源工具。

当上下文被压缩后，物理 ``messages`` 里的早期原文已被摘要替换，但虚拟上下文时间流
（``core/context_log.py``）仍无损保留全部原始消息。这组工具让 Agent 在"需要回忆早期
任务 / 核对被折叠掉的细节 / 溯源某个结论从何而来"时，按需取回原文，而不是凭空猜测。

读取方式与写入解耦：工具进程不持有 Agent 的内存 ContextLog 实例，而是直接读取该会话
落盘的 ``<session>.context.jsonl`` 文件。这样它能作为延迟工具（deferred tools）按需发现
与调用，天然不占用稳定前缀 token（见 docs/06、docs/11、docs/12）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.registry import registry
from core.context_log import read_context_log, _entry_text  # type: ignore


_MESSAGE_TYPES = {"user", "assistant", "tool_result", "system"}


def _safe_name(value: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "_.-") else "_" for ch in str(value or "session"))
    return safe[:120] or "session"


def _resolve_log_path(session_id: str = "") -> Optional[Path]:
    """定位某会话的 context log 文件，复用与写入端一致的目录解析。"""
    from core import config

    sid_raw = str(session_id or os.environ.get("R_AGENT_SESSION_ID", "") or "").strip()
    sid = sid_raw if sid_raw and sid_raw != "default" else ""

    candidates: List[Path] = []
    # per-session 沙箱路径优先。
    if sid:
        try:
            from core.sandbox_workspace import SandboxWorkspace

            if config.get_session_sandbox_enabled():
                workspace = SandboxWorkspace(session_id=sid, root=config.get_session_sandbox_root())
                candidates.append(Path(workspace.root) / "context_log" / f"{_safe_name(sid)}.context.jsonl")
        except Exception:
            pass
    # 全局目录兜底。
    base = config.get_context_log_dir()
    name = _safe_name(sid) if sid else "default"
    candidates.append(Path(base) / f"{name}.context.jsonl")

    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else None


def _compact_entry(row: Dict[str, Any], *, max_chars: int) -> Dict[str, Any]:
    content = str(row.get("content") or "")
    meta = row.get("meta") or {}
    out: Dict[str, Any] = {
        "seq": row.get("seq"),
        "type": row.get("type"),
        "role": row.get("role"),
        "run_id": row.get("run_id"),
    }
    if len(content) > max_chars:
        out["content"] = content[:max_chars] + f" …（截断，原文 {len(content)} 字符）"
        out["truncated"] = True
    else:
        out["content"] = content
    if meta.get("thinking"):
        thinking = str(meta["thinking"])
        out["thinking"] = thinking[:max_chars] + (" …" if len(thinking) > max_chars else "")
    if meta.get("name"):
        out["tool_name"] = meta["name"]
    if row.get("type") == "fold":
        out["fold"] = {
            "start_seq": meta.get("start_seq"),
            "end_seq": meta.get("end_seq"),
            "reason": meta.get("reason"),
        }
    return out


def context_recall(start_seq: int = 0, end_seq: int = 0, session_id: str = "", max_chars: int = 4000) -> str:
    """取回虚拟上下文时间流中 ``[start_seq, end_seq]`` 区间的原始消息（含被折叠的历史）。

    典型用法：先 ``context_search`` 或读取 fold 标记拿到被折叠区间，再用本工具取原文。
    """
    try:
        path = _resolve_log_path(session_id)
        if not path or not path.exists():
            return json.dumps({"success": False, "error": "没有找到该会话的虚拟上下文时间流。"}, ensure_ascii=False)
        rows = read_context_log(path)
        start = int(start_seq or 0)
        end = int(end_seq or 0) or (rows[-1]["seq"] if rows else 0)
        selected = [
            r for r in rows
            if start <= int(r.get("seq") or 0) <= end and r.get("type") in _MESSAGE_TYPES
        ]
        return json.dumps(
            {
                "success": True,
                "start_seq": start,
                "end_seq": end,
                "count": len(selected),
                "entries": [_compact_entry(r, max_chars=max_chars) for r in selected],
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def context_search(query: str = "", session_id: str = "", limit: int = 20, max_chars: int = 1200) -> str:
    """在整条虚拟上下文时间流（含已折叠段）里按关键词检索，返回命中的消息及其 seq。"""
    try:
        q = str(query or "").strip().lower()
        if not q:
            return json.dumps({"success": False, "error": "query 不能为空。"}, ensure_ascii=False)
        path = _resolve_log_path(session_id)
        if not path or not path.exists():
            return json.dumps({"success": False, "error": "没有找到该会话的虚拟上下文时间流。"}, ensure_ascii=False)
        rows = read_context_log(path)
        hits: List[Dict[str, Any]] = []
        folds: List[Dict[str, Any]] = []
        for r in rows:
            if r.get("type") == "fold":
                summary = str(r.get("content") or "")
                meta = r.get("meta") or {}
                if q in summary.lower():
                    folds.append({
                        "seq": r.get("seq"),
                        "start_seq": meta.get("start_seq"),
                        "end_seq": meta.get("end_seq"),
                        "summary": summary[:max_chars],
                    })
                continue
            if r.get("type") not in _MESSAGE_TYPES:
                continue
            haystack = _entry_text(_RowEntry(r)).lower()
            if q in haystack:
                hits.append(_compact_entry(r, max_chars=max_chars))
                if len(hits) >= max(1, int(limit)):
                    break
        return json.dumps(
            {
                "success": True,
                "query": query,
                "count": len(hits),
                "hits": hits,
                "matching_folds": folds,
                "hint": "命中落在已折叠区间时，可用 context_recall(start_seq, end_seq) 取回该区间全部原文。",
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


class _RowEntry:
    """让 dict 行复用 context_log._entry_text 的轻量适配器。"""

    def __init__(self, row: Dict[str, Any]) -> None:
        self.content = row.get("content")
        self.meta = row.get("meta") or {}


registry.register(
    name="context_recall",
    description=(
        "从虚拟上下文时间流取回被压缩/折叠掉的原始消息。上下文压缩后，早期对话在当前"
        "窗口里只剩摘要；需要核对被折叠掉的细节、文件路径、工具原始结论或溯源某结论来源时，"
        "用本工具按 seq 区间取回无损原文，不要凭空猜测。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "start_seq": {"type": "integer", "description": "起始 seq（含）。配合 context_search 的 fold 区间使用。"},
            "end_seq": {"type": "integer", "description": "结束 seq（含）；为 0 时取到时间流末尾。"},
            "session_id": {"type": "string", "description": "会话 id；留空用当前会话。"},
            "max_chars": {"type": "integer", "description": "每条消息返回的最大字符数，默认 4000。"},
        },
        "required": [],
    },
    handler=context_recall,
    metadata={"summary": "取回被折叠的原始上下文消息（无损溯源）", "category": "context"},
)


registry.register(
    name="context_search",
    description=(
        "在整条虚拟上下文时间流（含已被压缩折叠的历史）里按关键词检索，定位某句话/某个文件/"
        "某个结论最早出现在哪条消息，返回其 seq 与所在的折叠区间，便于再用 context_recall 取回原文。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词（子串匹配，大小写不敏感）。"},
            "session_id": {"type": "string", "description": "会话 id；留空用当前会话。"},
            "limit": {"type": "integer", "description": "最多返回命中条数，默认 20。"},
            "max_chars": {"type": "integer", "description": "每条命中返回的最大字符数，默认 1200。"},
        },
        "required": ["query"],
    },
    handler=context_search,
    metadata={"summary": "检索虚拟上下文时间流（含已折叠历史）", "category": "context"},
)
