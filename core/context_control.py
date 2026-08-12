from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# OpenAI/Azure-compatible chat APIs generally do not expose the model context
# window on each chat completion response. R-Agent therefore uses a conservative
# local mapping plus an environment/config override in core.config.
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
}

DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_TRIGGER_RATIO = 0.8
DEFAULT_TARGET_RATIO = 0.55
DEFAULT_PRESERVE_RECENT_MESSAGES = 16


def _as_plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _as_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _as_plain(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            return _as_plain(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        public = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        if public:
            return _as_plain(public)
    return str(value)


def normalize_messages_for_context(messages: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for message in messages or []:
        raw = _as_plain(message)
        if not isinstance(raw, dict):
            raw = {"role": "assistant", "content": str(raw)}
        msg = dict(raw)
        role = str(msg.get("role") or "assistant")
        msg["role"] = role
        if "content" not in msg or msg.get("content") is None:
            msg["content"] = ""
        if msg.get("tool_calls") is None:
            msg.pop("tool_calls", None)
        normalized.append(msg)
    return normalized


def estimate_tokens(value: Any) -> int:
    """Fast dependency-free token estimate for context guardrails.

    It intentionally overestimates slightly for CJK/JSON-heavy payloads. Exact
    accounting is model/tokenizer-specific and not available for every
    OpenAI-compatible endpoint used by R-Agent.
    """
    text = json.dumps(_as_plain(value), ensure_ascii=False, separators=(",", ":"))
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_chars = len(text) - ascii_chars
    # English-like text averages ~4 chars/token; CJK and JSON punctuation are
    # denser, so count non-ASCII more aggressively.
    return max(1, int(ascii_chars / 4) + int(non_ascii_chars * 0.75) + 8)


def estimate_request_tokens(messages: Sequence[Any], tools: Optional[Sequence[Any]] = None) -> int:
    total = estimate_tokens(normalize_messages_for_context(messages))
    if tools:
        total += estimate_tokens(_as_plain(tools))
    return total


def resolve_context_window(model: Optional[str], configured: Optional[int] = None) -> int:
    if configured and configured > 0:
        return int(configured)
    name = (model or "").strip().lower()
    if not name:
        return DEFAULT_CONTEXT_WINDOW_TOKENS
    if name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[name]
    # Azure deployments often append deployment/version suffixes. Prefer the
    # longest known prefix contained in the deployment name.
    for known, window in sorted(MODEL_CONTEXT_WINDOWS.items(), key=lambda item: len(item[0]), reverse=True):
        if known in name:
            return window
    return DEFAULT_CONTEXT_WINDOW_TOKENS


def should_compress_context(
    messages: Sequence[Any],
    tools: Optional[Sequence[Any]] = None,
    *,
    max_context_tokens: int,
    trigger_ratio: float = DEFAULT_TRIGGER_RATIO,
) -> Dict[str, Any]:
    estimated = estimate_request_tokens(messages, tools)
    threshold = int(max_context_tokens * trigger_ratio)
    return {
        "should_compress": estimated >= threshold,
        "estimated_tokens": estimated,
        "max_context_tokens": max_context_tokens,
        "trigger_ratio": trigger_ratio,
        "threshold_tokens": threshold,
        "usage_ratio": estimated / max_context_tokens if max_context_tokens else None,
    }


def _message_content(message: Dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return json.dumps(_as_plain(content), ensure_ascii=False)


def _compact_text(text: str, max_chars: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_chars:
        return text
    # Summary snippets may be shortened, but retained messages are never cut.
    return text[: max_chars - 40].rstrip() + f" …（已概括，原文 {len(text)} 字符）"


def _tool_call_names(message: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for call in message.get("tool_calls") or []:
        call = _as_plain(call)
        if isinstance(call, dict):
            fn = call.get("function") or {}
            if isinstance(fn, dict) and fn.get("name"):
                names.append(str(fn.get("name")))
    return names


def _unitize_messages(messages: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    """Return leading system messages and conversation units.

    A unit is a whole user/assistant message, or an assistant tool_call message
    plus its following tool result messages. This prevents compression from
    leaving an orphan tool message or splitting an assistant/tool pair.
    """
    leading_system: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(messages) and messages[idx].get("role") == "system":
        leading_system.append(messages[idx])
        idx += 1

    units: List[List[Dict[str, Any]]] = []
    while idx < len(messages):
        current = messages[idx]
        unit = [current]
        idx += 1
        if current.get("role") == "assistant" and current.get("tool_calls"):
            expected = len(current.get("tool_calls") or [])
            consumed = 0
            while idx < len(messages) and messages[idx].get("role") == "tool" and (expected <= 0 or consumed < expected):
                unit.append(messages[idx])
                idx += 1
                consumed += 1
        units.append(unit)
    return leading_system, units


def _flatten(units: Iterable[Iterable[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for unit in units:
        out.extend(unit)
    return out


def _count_messages(units: Iterable[Iterable[Dict[str, Any]]]) -> int:
    return sum(len(list(unit)) for unit in units)


def _build_summary_message(old_messages: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    user_points: List[str] = []
    assistant_points: List[str] = []
    tool_points: List[str] = []
    system_points: List[str] = []

    for idx, msg in enumerate(old_messages, start=1):
        role = msg.get("role", "")
        content = _message_content(msg)
        if role == "user":
            user_points.append(f"- 用户[{idx}]：{_compact_text(content, 900)}")
        elif role == "assistant":
            names = _tool_call_names(msg)
            if names:
                assistant_points.append(f"- 助手[{idx}] 调用工具：{', '.join(names)}")
            if content.strip():
                assistant_points.append(f"- 助手[{idx}]：{_compact_text(content, 600)}")
        elif role == "tool":
            name = msg.get("name") or "tool"
            tool_points.append(f"- 工具[{idx}] {name} 返回：{_compact_text(content, 650)}")
        elif role == "system":
            system_points.append(f"- 系统[{idx}]：{_compact_text(content, 500)}")
        else:
            assistant_points.append(f"- {role or 'unknown'}[{idx}]：{_compact_text(content, 500)}")

    def section(title: str, items: List[str], limit: int) -> str:
        if not items:
            return f"## {title}\n- 无"
        shown = items[-limit:]
        omitted = len(items) - len(shown)
        prefix = [f"- 早期同类记录已合并省略 {omitted} 条。"] if omitted > 0 else []
        return f"## {title}\n" + "\n".join(prefix + shown)

    summary = "\n\n".join([
        "【自动上下文压缩摘要】以下摘要由 context_compress/主流程判别器生成，用于替代较早的完整消息。保留原则：用户目标和约束优先；关键决策、文件路径、错误、测试结果和未完成事项优先；冗长 tool 输出只保留结论/路径/失败原因。后续消息仍按完整 message 原样保留，没有从单条保留消息中间截断。",
        section("用户重点", user_points, 8),
        section("助手决策与行动", assistant_points, 10),
        section("工具结果要点", tool_points, 12),
        section("系统/控制信息", system_points, 5),
    ])
    return {"role": "system", "content": summary}


def compress_messages(
    messages: Sequence[Any],
    tools: Optional[Sequence[Any]] = None,
    *,
    model: Optional[str] = None,
    max_context_tokens: Optional[int] = None,
    trigger_ratio: float = DEFAULT_TRIGGER_RATIO,
    target_ratio: float = DEFAULT_TARGET_RATIO,
    preserve_recent_messages: int = DEFAULT_PRESERVE_RECENT_MESSAGES,
    force: bool = False,
    summarizer: Optional[Callable[[Sequence[Dict[str, Any]]], str]] = None,
) -> Dict[str, Any]:
    normalized = normalize_messages_for_context(messages)
    if not normalized:
        return {
            "success": True,
            "compressed": False,
            "compressed_messages": [],
            "summary": "",
            "stats": {"original_messages": 0, "compressed_messages": 0},
        }

    max_tokens = resolve_context_window(model, max_context_tokens)
    original_estimated = estimate_request_tokens(normalized, tools)
    trigger = should_compress_context(normalized, tools, max_context_tokens=max_tokens, trigger_ratio=trigger_ratio)
    if not force and not trigger["should_compress"]:
        return {
            "success": True,
            "compressed": False,
            "reason": "below_threshold",
            "compressed_messages": normalized,
            "summary": "",
            "stats": {
                "original_messages": len(normalized),
                "compressed_messages": len(normalized),
                "original_estimated_tokens": original_estimated,
                "compressed_estimated_tokens": original_estimated,
                **trigger,
            },
        }

    leading_system, units = _unitize_messages(normalized)
    if not units:
        return {
            "success": True,
            "compressed": False,
            "reason": "only_system_messages",
            "compressed_messages": normalized,
            "summary": "",
            "stats": {"original_messages": len(normalized), "compressed_messages": len(normalized), **trigger},
        }

    keep_units: List[List[Dict[str, Any]]] = []
    kept_message_count = 0
    for unit in reversed(units):
        if keep_units and kept_message_count >= max(1, preserve_recent_messages):
            break
        keep_units.append(unit)
        kept_message_count += len(unit)
    keep_units.reverse()

    old_units = units[: len(units) - len(keep_units)]
    old_messages = _flatten(old_units)
    if not old_messages:
        # Not enough history to summarize safely; keep all complete messages.
        return {
            "success": True,
            "compressed": False,
            "reason": "nothing_safe_to_summarize",
            "compressed_messages": normalized,
            "summary": "",
            "stats": {"original_messages": len(normalized), "compressed_messages": len(normalized), **trigger},
        }

    summary_msg = _build_summary_message(old_messages)
    compressed = list(leading_system[:1]) + [summary_msg] + _flatten(keep_units)

    target_tokens = int(max_tokens * target_ratio)
    while len(keep_units) > 1 and estimate_request_tokens(compressed, tools) > target_tokens:
        if len(_flatten(keep_units)) <= max(1, preserve_recent_messages):
            break
        old_messages = old_messages + keep_units.pop(0)
        summary_msg = _build_summary_message(old_messages)
        compressed = list(leading_system[:1]) + [summary_msg] + _flatten(keep_units)

    summary_strategy = "heuristic"
    summary_error = None
    if summarizer is not None:
        try:
            generated = str(summarizer(old_messages) or "").strip()
            if generated:
                summary_msg = {"role": "system", "content": generated}
                compressed = list(leading_system[:1]) + [summary_msg] + _flatten(keep_units)
                summary_strategy = "llm"
            else:
                summary_strategy = "heuristic_fallback"
                summary_error = "summarizer returned empty content"
        except Exception as exc:
            summary_strategy = "heuristic_fallback"
            summary_error = str(exc)

    compressed_estimated = estimate_request_tokens(compressed, tools)
    return {
        "success": True,
        "compressed": True,
        "compressed_messages": compressed,
        "summary": summary_msg["content"],
        "stats": {
            "original_messages": len(normalized),
            "compressed_messages": len(compressed),
            "summarized_messages": len(old_messages),
            "preserved_recent_messages": len(_flatten(keep_units)),
            "original_estimated_tokens": original_estimated,
            "compressed_estimated_tokens": compressed_estimated,
            "max_context_tokens": max_tokens,
            "trigger_ratio": trigger_ratio,
            "target_ratio": target_ratio,
            "threshold_tokens": int(max_tokens * trigger_ratio),
            "target_tokens": target_tokens,
            "usage_ratio_before": original_estimated / max_tokens if max_tokens else None,
            "usage_ratio_after": compressed_estimated / max_tokens if max_tokens else None,
            "summary_strategy": summary_strategy,
            **({"summary_error": summary_error} if summary_error else {}),
        },
    }
