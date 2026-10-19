#!/usr/bin/env python3
"""Run a bounded paid prompt-cache diagnostic against the configured endpoint.

The script never prints credentials or prompt contents.  It compares identical
text requests, identical requests with fixed tools, append-only conversations,
and identical requests made with a fresh client each time.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core import config
from core.cache_observability import provider_cache_tokens, stable_hash


REPEATED_PREFIX = "cache-diagnostic-stable-prefix-block " * 320
FIXED_TOOL = {
    "type": "function",
    "function": {
        "name": "cache_diagnostic_lookup",
        "description": "A fixed no-op schema used only to test prompt caching.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


def _plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    return str(value)


def _usage_value(usage: Any, name: str) -> int:
    raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _request(client, *, group: str, index: int, messages: list, tools: list | None = None) -> tuple[dict, Any]:
    kwargs = {
        "model": config.get_model(),
        "messages": messages,
        "max_completion_tokens": 16,
    }
    if tools:
        kwargs["tools"] = tools
    started = time.monotonic()
    response = client.chat.completions.create(**kwargs)
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)
    usage = getattr(response, "usage", None)
    prompt_tokens = _usage_value(usage, "prompt_tokens")
    cached_tokens, creation_tokens = provider_cache_tokens(usage)
    record = {
        "group": group,
        "index": index,
        "request_id": getattr(response, "_request_id", None),
        "model": getattr(response, "model", None) or config.get_model(),
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached_tokens,
        "cache_creation_tokens": creation_tokens,
        "cache_hit_ratio": (cached_tokens / prompt_tokens) if cached_tokens is not None and prompt_tokens else None,
        "completion_tokens": _usage_value(usage, "completion_tokens"),
        "latency_ms": elapsed_ms,
        "messages_hash": stable_hash(messages),
        "tools_hash": stable_hash(tools or []),
        "raw_usage": _plain(usage),
    }
    print(json.dumps(record, ensure_ascii=False))
    return record, response


def _base_messages(group: str) -> list:
    return [
        {
            "role": "system",
            "content": (
                f"Prompt cache diagnostic group {group}. The following block must remain exact.\n"
                + REPEATED_PREFIX
                + "\nAlways reply with exactly OK and nothing else."
            ),
        },
        {"role": "user", "content": "Reply OK."},
    ]


def _group_summary(records: list[dict]) -> dict:
    prompt = sum(item.get("prompt_tokens") or 0 for item in records)
    cached_values = [item.get("cached_tokens") for item in records if item.get("cached_tokens") is not None]
    cached = sum(cached_values)
    return {
        "requests": len(records),
        "provider_metric_available": len(cached_values) == len(records),
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "weighted_hit_ratio": cached / prompt if prompt else None,
        "nonzero_hits": sum(1 for item in records if (item.get("cached_tokens") or 0) > 0),
        "latency_ms": [item.get("latency_ms") for item in records],
    }


def run(repeats: int) -> dict:
    results: dict[str, list[dict]] = {}

    client = config.create_llm_client()
    results["identical_text"] = []
    messages = _base_messages("identical_text")
    for index in range(repeats):
        record, _ = _request(client, group="identical_text", index=index + 1, messages=messages)
        results["identical_text"].append(record)

    results["identical_fixed_tools"] = []
    messages = _base_messages("identical_fixed_tools")
    for index in range(repeats):
        record, _ = _request(
            client,
            group="identical_fixed_tools",
            index=index + 1,
            messages=messages,
            tools=[FIXED_TOOL],
        )
        results["identical_fixed_tools"].append(record)

    results["append_only_fixed_tools"] = []
    messages = _base_messages("append_only_fixed_tools")
    for index in range(repeats):
        record, response = _request(
            client,
            group="append_only_fixed_tools",
            index=index + 1,
            messages=messages,
            tools=[FIXED_TOOL],
        )
        results["append_only_fixed_tools"].append(record)
        message = response.choices[0].message
        messages = [*messages, message, {"role": "user", "content": f"Append-only step {index + 2}; reply OK."}]

    results["fresh_client_identical_text"] = []
    messages = _base_messages("fresh_client_identical_text")
    for index in range(repeats):
        fresh_client = config.create_llm_client()
        record, _ = _request(
            fresh_client,
            group="fresh_client_identical_text",
            index=index + 1,
            messages=messages,
        )
        results["fresh_client_identical_text"].append(record)

    return {
        "created_at": datetime.now().isoformat(),
        "client_type": config.get_client_type(),
        "model": config.get_model(),
        "repeats": repeats,
        "prefix_chars": len(REPEATED_PREFIX),
        "groups": results,
        "summary": {name: _group_summary(items) for name, items in results.items()},
    }


def run_followups(repeats: int) -> dict:
    """Run direct client-handoff and real R-Agent-shaped prefix checks."""
    results: dict[str, list[dict]] = {}

    messages = _base_messages("client_handoff_same_prefix")
    shared_client = config.create_llm_client()
    results["client_handoff_same_prefix"] = []
    for index in range(repeats):
        client = shared_client if index < 2 else config.create_llm_client()
        record, _ = _request(
            client,
            group="client_handoff_same_prefix",
            index=index + 1,
            messages=messages,
        )
        record["client_mode"] = "shared" if index < 2 else "fresh"
        results["client_handoff_same_prefix"].append(record)

    from core.agent import RAgent
    from core.prompt_builder import build_system_prompt
    from tools.registry import registry

    agent = RAgent(model=config.get_model(), enable_self_review=False, session_id="cache-diagnostic")
    agent.messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": "Cache diagnostic only. Do not call tools. Reply exactly OK."},
    ]
    request_tools = agent._stabilize_request_tools(
        agent._apply_deferred_tool_filter(registry.get_all_schemas())
    )
    catalog_note = agent._get_tool_catalog_note()
    messages = agent._build_request_messages(catalog_note=catalog_note)
    results["ragent_shape_append_only"] = []
    for index in range(repeats):
        record, response = _request(
            agent.client,
            group="ragent_shape_append_only",
            index=index + 1,
            messages=messages,
            tools=request_tools,
        )
        results["ragent_shape_append_only"].append(record)
        message = response.choices[0].message
        messages = [*messages, message]
        for tool_call in getattr(message, "tool_calls", None) or []:
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.function.name,
                "content": json.dumps({"success": False, "error": "disabled in cache diagnostic"}),
            })
        messages.append({"role": "user", "content": f"Append-only R-Agent step {index + 2}; reply exactly OK."})

    return {
        "created_at": datetime.now().isoformat(),
        "client_type": config.get_client_type(),
        "model": config.get_model(),
        "repeats": repeats,
        "groups": results,
        "summary": {name: _group_summary(items) for name, items in results.items()},
    }


def run_long_context(repeats: int) -> dict:
    """Test the same R-Agent prefix with a roughly 60-70K-token tail."""
    from core.agent import RAgent
    from core.prompt_builder import build_system_prompt
    from tools.registry import registry

    agent = RAgent(model=config.get_model(), enable_self_review=False, session_id="cache-diagnostic")
    agent.messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": "Cache diagnostic only. Do not call tools. Reply exactly OK."},
    ]
    request_tools = agent._stabilize_request_tools(
        agent._apply_deferred_tool_filter(registry.get_all_schemas())
    )
    messages = agent._build_request_messages(catalog_note=agent._get_tool_catalog_note())
    long_tail = "long-context-evidence-item " * 10_000
    messages = [*messages, {
        "role": "user",
        "content": long_tail + "\nThis is inert diagnostic data. Reply exactly OK.",
    }]
    records = []
    for index in range(repeats):
        record, response = _request(
            agent.client,
            group="ragent_long_context",
            index=index + 1,
            messages=messages,
            tools=request_tools,
        )
        record["long_tail_chars"] = len(long_tail)
        records.append(record)
        message = response.choices[0].message
        messages = [
            *messages,
            message,
            {"role": "user", "content": f"Append-only long-context step {index + 2}; reply exactly OK."},
        ]
    return {
        "created_at": datetime.now().isoformat(),
        "client_type": config.get_client_type(),
        "model": config.get_model(),
        "repeats": repeats,
        "groups": {"ragent_long_context": records},
        "summary": {"ragent_long_context": _group_summary(records)},
    }


def run_tool_history(repeats: int) -> dict:
    """Test caching across assistant(tool_calls) -> tool(result) history."""
    from core.agent import RAgent
    from core.prompt_builder import build_system_prompt
    from tools.registry import registry

    agent = RAgent(model=config.get_model(), enable_self_review=False, session_id="cache-diagnostic")
    agent.messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": "Call cache_diag_ping exactly once with {}. Do not call any other tool."},
    ]
    request_tools = agent._stabilize_request_tools(
        agent._apply_deferred_tool_filter(registry.get_all_schemas())
    )
    request_tools = [*request_tools, {
        "type": "function",
        "function": {
            "name": "cache_diag_ping",
            "description": "Diagnostic no-op. Call with an empty object.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }]
    messages = agent._build_request_messages(catalog_note=agent._get_tool_catalog_note())
    records = []
    for index in range(repeats):
        record, response = _request(
            agent.client,
            group="ragent_tool_history",
            index=index + 1,
            messages=messages,
            tools=request_tools,
        )
        records.append(record)
        message = response.choices[0].message
        messages = [*messages, message]
        calls = getattr(message, "tool_calls", None) or []
        for tool_call in calls:
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_call.function.name,
                "content": json.dumps({
                    "success": True,
                    "result": "diagnostic-tool-output " * 700,
                }),
            })
        messages.append({
            "role": "user",
            "content": f"Tool history step {index + 2}. Call cache_diag_ping exactly once with {{}}.",
        })
    return {
        "created_at": datetime.now().isoformat(),
        "client_type": config.get_client_type(),
        "model": config.get_model(),
        "repeats": repeats,
        "groups": {"ragent_tool_history": records},
        "summary": {"ragent_tool_history": _group_summary(records)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--output", default="")
    parser.add_argument("--followup-only", action="store_true")
    parser.add_argument("--long-context-only", action="store_true")
    parser.add_argument("--tool-history-only", action="store_true")
    args = parser.parse_args()
    repeats = max(2, min(int(args.repeats), 8))
    if args.tool_history_only:
        report = run_tool_history(repeats)
    elif args.long_context_only:
        report = run_long_context(repeats)
    elif args.followup_only:
        report = run_followups(repeats)
    else:
        report = run(repeats)
    output = Path(args.output) if args.output else Path("outputs") / "cache_diagnostics" / f"prompt_cache_{int(time.time())}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
