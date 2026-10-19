"""KV-cache observability helpers.

The provider-reported cache hit and the locally expected prefix reuse are two
different signals:

* local prefix reuse is the primary harness-quality metric: estimated reusable
  request-prefix tokens divided by current estimated request tokens;
* provider cache hit remains optional diagnostic evidence only.

Keeping them separate prevents a stable-looking request from being reported as
an actual cache hit when the upstream endpoint does not expose cache metrics.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional

from core.context_control import estimate_request_tokens


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
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
        public = {key: item for key, item in vars(value).items() if not key.startswith("_")}
        if public:
            return _plain(public)
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def _role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "role", "") or "")


def split_leading_system(messages: Iterable[Any]) -> tuple[list[Any], list[Any]]:
    plain_messages = [_plain(message) for message in (messages or [])]
    index = 0
    while index < len(plain_messages) and _role(plain_messages[index]) == "system":
        index += 1
    return plain_messages[:index], plain_messages[index:]


def build_request_fingerprint(
    *,
    base_messages: Iterable[Any],
    catalog_note: Optional[str],
    durable_message: Optional[dict],
    tools: Iterable[Any],
    previous: Optional[dict] = None,
    change_hints: Optional[Iterable[str]] = None,
    request_kind: str = "main",
) -> tuple[dict, dict]:
    """Return a public observation and a private comparison state."""

    system_messages, conversation = split_leading_system(base_messages)
    components = {
        "system": system_messages,
        "catalog": catalog_note or "",
        "durable": _plain(durable_message) if durable_message else None,
        "tools": [_plain(tool) for tool in (tools or [])],
    }
    hashes = {name: stable_hash(value) for name, value in components.items()}
    prefix_hash = stable_hash(components)
    conversation_plain = [_plain(message) for message in conversation]

    def assembled_messages(conversation_part: list[Any]) -> list[Any]:
        request_messages = list(system_messages)
        if catalog_note:
            request_messages.append({"role": "system", "content": catalog_note})
        if durable_message:
            request_messages.append(_plain(durable_message))
        request_messages.extend(conversation_part)
        return request_messages

    local_prompt_tokens = estimate_request_tokens(
        assembled_messages(conversation_plain),
        components["tools"],
    )
    local_reusable_tokens = 0
    common_conversation_messages = 0

    reasons: list[str] = []
    expected_reuse: Optional[bool]
    tail_append_only = False
    if not previous:
        reasons.append("first_request")
        expected_reuse = None
    else:
        previous_hashes = previous.get("component_hashes") or {}
        fixed_components_match = True
        for name, reason in (
            ("system", "system_changed"),
            ("catalog", "catalog_changed"),
            ("durable", "durable_changed"),
            ("tools", "tool_schema_changed"),
        ):
            if previous_hashes.get(name) != hashes.get(name):
                reasons.append(reason)
                fixed_components_match = False

        previous_conversation = previous.get("conversation") or []
        if fixed_components_match:
            common_limit = min(len(previous_conversation), len(conversation_plain))
            while (
                common_conversation_messages < common_limit
                and previous_conversation[common_conversation_messages]
                == conversation_plain[common_conversation_messages]
            ):
                common_conversation_messages += 1
            local_reusable_tokens = estimate_request_tokens(
                assembled_messages(conversation_plain[:common_conversation_messages]),
                components["tools"],
            )
        tail_append_only = (
            len(conversation_plain) >= len(previous_conversation)
            and conversation_plain[: len(previous_conversation)] == previous_conversation
        )
        if not tail_append_only:
            reasons.append("history_rewritten")
        expected_reuse = not reasons and tail_append_only
        if expected_reuse:
            reasons.append("tail_append_only")

    for hint in change_hints or []:
        hint = str(hint or "").strip()
        if hint and hint not in reasons:
            reasons.append(hint)

    if previous:
        invalidating_reasons = [reason for reason in reasons if reason != "tail_append_only"]
        expected_reuse = tail_append_only and not invalidating_reasons

    local_reusable_tokens = min(local_reusable_tokens, local_prompt_tokens)
    local_new_tokens = max(0, local_prompt_tokens - local_reusable_tokens)
    local_reuse_ratio = (
        local_reusable_tokens / local_prompt_tokens if local_prompt_tokens else None
    )

    public = {
        "request_kind": request_kind,
        "prefix_hash": prefix_hash,
        "component_hashes": hashes,
        "prefix_reuse_expected": expected_reuse,
        "tail_append_only": tail_append_only,
        "change_reasons": reasons,
        "local_prompt_tokens": local_prompt_tokens,
        "local_reusable_tokens": local_reusable_tokens,
        "local_new_tokens": local_new_tokens,
        "local_reuse_ratio": local_reuse_ratio,
        "common_conversation_messages": common_conversation_messages,
    }
    private = {
        "prefix_hash": prefix_hash,
        "component_hashes": hashes,
        "conversation": conversation_plain,
    }
    return public, private


def _field(container: Any, name: str) -> tuple[bool, Any]:
    if container is None:
        return False, None
    if isinstance(container, dict):
        return (name in container), container.get(name)
    if hasattr(container, name):
        return True, getattr(container, name)
    return False, None


def provider_cache_tokens(usage: Any) -> tuple[Optional[int], Optional[int]]:
    """Return ``(cache_read_tokens, cache_creation_tokens)`` when exposed.

    ``None`` means the endpoint did not expose the metric.  Zero means it did
    expose the metric and this request had no matching cached tokens.
    """

    paths = (
        ("prompt_tokens_details", "cached_tokens"),
        ("input_tokens_details", "cached_tokens"),
    )
    for parent_name, child_name in paths:
        exists, parent = _field(usage, parent_name)
        if not exists:
            continue
        child_exists, value = _field(parent, child_name)
        if child_exists:
            try:
                return max(0, int(value or 0)), None
            except (TypeError, ValueError):
                return 0, None

    for direct_name in ("cached_tokens", "cache_read_input_tokens"):
        exists, value = _field(usage, direct_name)
        if exists:
            try:
                read_tokens = max(0, int(value or 0))
            except (TypeError, ValueError):
                read_tokens = 0
            creation_exists, creation = _field(usage, "cache_creation_input_tokens")
            try:
                creation_tokens = max(0, int(creation or 0)) if creation_exists else None
            except (TypeError, ValueError):
                creation_tokens = 0
            return read_tokens, creation_tokens
    return None, None

