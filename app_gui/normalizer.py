from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

from app_gui.schemas import LLMRequestSnapshot, MessageRecord


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


def normalize_tool_call(tool_call: Any) -> Dict[str, Any]:
    raw = _as_plain(tool_call)
    if isinstance(raw, dict):
        call_id = raw.get("id")
        function = raw.get("function") or {}
        if not isinstance(function, dict):
            function = _as_plain(function) if isinstance(_as_plain(function), dict) else {}
        return {
            "id": call_id,
            "type": raw.get("type", "function"),
            "function": {
                "name": function.get("name", ""),
                "arguments": function.get("arguments", ""),
            },
        }
    return {"id": None, "type": "unknown", "raw": str(raw)}


def normalize_message(message: Any, *, source: str = "main_agent") -> Dict[str, Any]:
    if isinstance(message, MessageRecord):
        return message.to_dict()

    raw_type = type(message).__name__
    if isinstance(message, dict):
        role = str(message.get("role", ""))
        content = message.get("content") or ""
        tool_calls = [normalize_tool_call(tc) for tc in (message.get("tool_calls") or [])]
        return MessageRecord(
            role=role,
            content=str(content),
            name=message.get("name"),
            tool_call_id=message.get("tool_call_id"),
            tool_calls=tool_calls,
            source=source,
            raw_type="dict",
        ).to_dict()

    role = str(getattr(message, "role", "assistant") or "assistant")
    content = getattr(message, "content", "") or ""
    tool_calls = [normalize_tool_call(tc) for tc in (getattr(message, "tool_calls", None) or [])]
    return MessageRecord(
        role=role,
        content=str(content),
        name=getattr(message, "name", None),
        tool_call_id=getattr(message, "tool_call_id", None),
        tool_calls=tool_calls,
        source=source,
        raw_type=raw_type,
    ).to_dict()


def normalize_messages(messages: Iterable[Any], *, source: str = "main_agent") -> List[Dict[str, Any]]:
    return [normalize_message(message, source=source) for message in messages]


def normalize_tool_schemas(tools: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for tool in tools or []:
        plain = _as_plain(tool)
        if isinstance(plain, dict):
            normalized.append(plain)
        else:
            normalized.append({"raw": str(plain)})
    return normalized


def build_llm_request_snapshot(*, model: str, messages: Iterable[Any], tools: Iterable[Any], iteration: int) -> Dict[str, Any]:
    return LLMRequestSnapshot(
        model=model,
        messages=normalize_messages(messages),
        tools=normalize_tool_schemas(tools),
        iteration=iteration,
    ).to_dict()


def safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(_as_plain(value), ensure_ascii=False, default=str)
    except Exception:
        return str(value)
