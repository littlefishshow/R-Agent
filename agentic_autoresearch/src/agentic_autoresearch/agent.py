from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .debug import DebugLog
from .tools import ToolRegistry
from .types import AgentResult, StepSpec
from .utils import atomic_write_json, extract_json_object, truncate_middle


class AgentLoop:
    """A small R-Agent-style tool-calling loop.

    The loop has no CLI concerns. It owns one step-local message history, exposes
    a whitelisted tool schema list, executes requested tools, and stops only when
    the assistant's final content contains ``{DONE_TAG: true}``.
    """

    def __init__(
        self,
        *,
        client,
        model: str,
        tools: ToolRegistry,
        debug: DebugLog | None = None,
        trace_dir: str | Path | None = None,
    ):
        self.client = client
        self.model = model
        self.tools = tools
        self.debug = debug or DebugLog(".", enabled=False)
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.usage = {"llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def run_step(
        self,
        *,
        spec: StepSpec,
        context: dict[str, Any],
        max_iterations: int,
    ) -> AgentResult:
        messages = [
            {"role": "system", "content": _compose_system_prompt(spec)},
            {"role": "user", "content": _compose_user_payload(spec, context)},
        ]
        tool_events: list[dict[str, Any]] = []
        llm_events: list[dict[str, Any]] = []
        final_content = ""
        error = ""
        done = False
        iterations_used = 0
        step_started = time.time()
        usage_before = dict(self.usage)
        initial_messages = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
        context_manifest = _context_manifest(context, messages)

        for iteration in range(max(1, int(max_iterations or 1))):
            iterations_used = iteration + 1
            self.debug.inflight_start("llm", step=spec.name, detail=f"iteration {iterations_used}")
            started = time.time()
            prompt_messages = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
            usage_pre_call = dict(self.usage)
            try:
                response = self._chat(messages, spec.allowed_tools)
            except Exception as exc:
                self.debug.inflight_finish("llm", step=spec.name, error=str(exc))
                finished = time.time()
                llm_events.append({
                    "iteration": iterations_used,
                    "started_at": started,
                    "finished_at": finished,
                    "duration_seconds": round(finished - started, 3),
                    "model": self.model,
                    "status": "error",
                    "error": str(exc),
                    "messages_before": _truncate_messages(prompt_messages),
                    "usage_before": usage_pre_call,
                    "usage_after": dict(self.usage),
                    "usage_delta": _usage_delta(usage_pre_call, self.usage),
                })
                error = f"llm_error: {exc}"
                break
            finished = time.time()
            self.debug.inflight_finish("llm", step=spec.name, elapsed_seconds=round(finished - started, 3))
            usage_delta = self._record_usage(response)

            message = _response_message(response)
            assistant_msg = _message_to_dict(message)
            llm_events.append({
                "iteration": iterations_used,
                "started_at": started,
                "finished_at": finished,
                "duration_seconds": round(finished - started, 3),
                "model": self.model,
                "status": "ok",
                "messages_before": _truncate_messages(prompt_messages),
                "assistant_message": _truncate_message(assistant_msg),
                "tool_call_count": len(_message_tool_calls(message)),
                "usage_before": usage_pre_call,
                "usage_after": dict(self.usage),
                "usage_delta": usage_delta,
            })
            messages.append(assistant_msg)
            tool_calls = _message_tool_calls(message)
            if tool_calls:
                for call in tool_calls:
                    name = call["name"]
                    args = call.get("arguments") or "{}"
                    call_id = call.get("id") or f"call_{len(tool_events) + 1}"
                    t0 = time.time()
                    if name not in set(spec.allowed_tools):
                        result = json.dumps({"error": f"tool {name} is not allowed in step {spec.name}"}, ensure_ascii=False)
                        finished_tool = time.time()
                        status = "blocked"
                    else:
                        self.debug.inflight_start("tool", step=spec.name, detail=name)
                        result = self.tools.execute(name, args)
                        finished_tool = time.time()
                        self.debug.inflight_finish("tool", step=spec.name, detail=name, elapsed_seconds=round(finished_tool - t0, 3))
                        self.usage["tool_calls"] += 1
                        status = "ok"
                    tool_events.append({
                        "id": call_id,
                        "iteration": iterations_used,
                        "name": name,
                        "arguments": args,
                        "result": result,
                        "status": status,
                        "started_at": t0,
                        "finished_at": finished_tool,
                        "duration_seconds": round(finished_tool - t0, 3),
                    })
                    messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result})
                    if _machine_step_done(spec, tool_events):
                        done = True
                        final_content = json.dumps({
                            spec.done_tag: True,
                            "summary": "machine completion gate satisfied by read_eval.solved=true",
                        }, ensure_ascii=False)
                        break
                if done:
                    break
                continue

            final_content = str(assistant_msg.get("content") or "")
            done = _tag_is_true(final_content, spec.done_tag)
            if done:
                break
            error = f"missing_done_tag: expected JSON marker {spec.done_tag}=true"
            break

        if not done and _machine_step_done(spec, tool_events):
            done = True
            error = ""
            final_content = json.dumps({
                spec.done_tag: True,
                "summary": "machine completion gate satisfied by read_eval.solved=true",
            }, ensure_ascii=False)

        trace_path = self._write_trace(
            spec=spec,
            context=context,
            context_manifest=context_manifest,
            initial_messages=initial_messages,
            messages=messages,
            llm_events=llm_events,
            tool_events=tool_events,
            done=done,
            final_content=final_content,
            error=error,
            iterations=iterations_used,
            step_started=step_started,
            usage_before=usage_before,
        )
        stats = _step_stats(
            started_at=step_started,
            finished_at=time.time(),
            usage_before=usage_before,
            usage_after=self.usage,
            llm_events=llm_events,
            tool_events=tool_events,
        )
        return AgentResult(
            content=final_content,
            done=done,
            iterations=iterations_used,
            tag=spec.done_tag,
            trace_path=trace_path,
            error=error if not done else "",
            token_usage=dict(self.usage),
            stats=stats,
        )

    def _chat(self, messages: list[dict[str, Any]], allowed_tools: tuple[str, ...]):
        schemas = self.tools.schemas(allowed_tools)
        kwargs = {"model": self.model, "messages": messages}
        if schemas:
            kwargs["tools"] = schemas
        return self.client.chat.completions.create(**kwargs)

    def _record_usage(self, response) -> dict[str, int]:
        before = dict(self.usage)
        self.usage["llm_calls"] += 1
        usage = getattr(response, "usage", None)
        if isinstance(response, dict):
            usage = response.get("usage", usage)
        if usage is None:
            return _usage_delta(before, self.usage)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = _usage_get(usage, key)
            if value is not None:
                self.usage[key] += int(value or 0)
        return _usage_delta(before, self.usage)

    def _write_trace(
        self,
        *,
        spec: StepSpec,
        context: dict[str, Any],
        context_manifest: dict[str, Any],
        initial_messages: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        llm_events: list[dict[str, Any]],
        tool_events: list[dict[str, Any]],
        done: bool,
        final_content: str,
        error: str,
        iterations: int,
        step_started: float,
        usage_before: dict[str, int],
    ) -> str:
        if self.trace_dir is None:
            return ""
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        finished = time.time()
        path = self.trace_dir / f"{int(time.time() * 1000)}_{spec.name}.json"
        payload = {
            "step": spec.name,
            "done_tag": spec.done_tag,
            "done": done,
            "iterations": iterations,
            "error": error,
            "final_content": final_content,
            "started_at": step_started,
            "finished_at": finished,
            "duration_seconds": round(finished - step_started, 3),
            "context_manifest": context_manifest,
            "context": context,
            "initial_messages": _truncate_messages(initial_messages),
            "messages": _truncate_messages(messages),
            "llm_events": llm_events,
            "tool_events": tool_events,
            "usage": self.usage,
            "usage_before": usage_before,
            "usage_delta": _usage_delta(usage_before, self.usage),
            "step_stats": _step_stats(
                started_at=step_started,
                finished_at=finished,
                usage_before=usage_before,
                usage_after=self.usage,
                llm_events=llm_events,
                tool_events=tool_events,
            ),
            "created_at": time.time(),
        }
        atomic_write_json(path, payload)
        context_path = self.trace_dir / f"{int(time.time() * 1000)}_{spec.name}_context.json"
        atomic_write_json(context_path, {
            "step": spec.name,
            "done_tag": spec.done_tag,
            "context_manifest": context_manifest,
            "context": context,
            "initial_messages": _truncate_messages(initial_messages),
            "created_at": time.time(),
            "parent_trace": str(path),
        })
        return str(path)


def _compose_system_prompt(spec: StepSpec) -> str:
    return (
        spec.system_prompt.rstrip()
        + "\n\nYou are executing exactly one autoresearch step.\n"
        + "Use tools when needed. Do not assume hidden CLI state or hidden parent conversation.\n"
        + "When and only when this step is complete, your final assistant message MUST include a JSON object "
        + f"with exactly this completion marker set to true: {{\"{spec.done_tag}\": true}}.\n"
        + f"If the step is not complete, do not set {spec.done_tag} to true.\n"
    )


def _compose_user_payload(spec: StepSpec, context: dict[str, Any]) -> str:
    return json.dumps(
        {
            "step": spec.name,
            "goal": spec.user_goal,
            "done_tag": spec.done_tag,
            "context": context,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _response_message(response):
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if not choices:
            return {}
        first = choices[0]
        return first.get("message") if isinstance(first, dict) else getattr(first, "message", {})
    return response.choices[0].message


def _message_to_dict(message) -> dict[str, Any]:
    if isinstance(message, dict):
        role = message.get("role", "assistant")
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")
    else:
        role = getattr(message, "role", "assistant")
        content = getattr(message, "content", "") or ""
        tool_calls = getattr(message, "tool_calls", None)
    data = {"role": role, "content": content}
    calls = []
    for call in _normalize_tool_calls(tool_calls):
        calls.append({
            "id": call["id"],
            "type": "function",
            "function": {"name": call["name"], "arguments": call.get("arguments") or "{}"},
        })
    if calls:
        data["tool_calls"] = calls
    return data


def _message_tool_calls(message) -> list[dict[str, str]]:
    if isinstance(message, dict):
        return _normalize_tool_calls(message.get("tool_calls"))
    return _normalize_tool_calls(getattr(message, "tool_calls", None))


def _normalize_tool_calls(raw) -> list[dict[str, str]]:
    calls = []
    for idx, call in enumerate(raw or [], start=1):
        if isinstance(call, dict):
            fn = call.get("function") or {}
            calls.append({
                "id": str(call.get("id") or f"call_{idx}"),
                "name": str(fn.get("name") or call.get("name") or ""),
                "arguments": str(fn.get("arguments") or call.get("arguments") or "{}"),
            })
        else:
            fn = getattr(call, "function", None)
            calls.append({
                "id": str(getattr(call, "id", f"call_{idx}")),
                "name": str(getattr(fn, "name", "")),
                "arguments": str(getattr(fn, "arguments", "{}") or "{}"),
            })
    return [c for c in calls if c["name"]]


def _tag_is_true(content: str, tag: str) -> bool:
    marker = re.compile(r'["\']?' + re.escape(tag) + r'["\']?\s*:\s*true\b', re.IGNORECASE)
    if marker.search(str(content or "")):
        return True
    data = extract_json_object(content)
    return data.get(tag) is True


def _machine_step_done(spec: StepSpec, tool_events: list[dict[str, Any]]) -> bool:
    """Machine completion gate for steps with objective metric evidence.

    The LLM should still produce the done tag, but if the framework has already
    read a solved eval result, do not waste cycles or fail only because the text
    marker was omitted.
    """
    if spec.name not in {"attempt", "conclude"}:
        return False
    for event in tool_events:
        if event.get("name") != "read_eval":
            continue
        try:
            payload = json.loads(event.get("result") or "{}")
            result = payload.get("result") if isinstance(payload, dict) else {}
            if isinstance(result, dict) and result.get("solved") is True:
                return True
        except Exception:
            continue
    return False


def _usage_get(usage, key: str):
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def _usage_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = ("llm_calls", "tool_calls", "prompt_tokens", "completion_tokens", "total_tokens")
    return {key: int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0) for key in keys}


def _context_manifest(context: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    raw = json.dumps(context, ensure_ascii=False, default=str)
    files = context.get("files") if isinstance(context, dict) else {}
    artifacts = context.get("artifacts") if isinstance(context, dict) else []
    return {
        "context_chars": len(raw),
        "context_keys": sorted(str(k) for k in context.keys()) if isinstance(context, dict) else [],
        "file_keys": sorted(str(k) for k in files.keys()) if isinstance(files, dict) else [],
        "file_chars": {str(k): len(str(v)) for k, v in (files or {}).items()} if isinstance(files, dict) else {},
        "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
        "initial_message_chars": [len(str(m.get("content", ""))) for m in messages],
    }


def _step_stats(
    *,
    started_at: float,
    finished_at: float,
    usage_before: dict[str, int],
    usage_after: dict[str, int],
    llm_events: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
) -> dict[str, Any]:
    llm_seconds = sum(float(e.get("duration_seconds") or 0.0) for e in llm_events)
    tool_seconds = sum(float(e.get("duration_seconds") or 0.0) for e in tool_events)
    return {
        "duration_seconds": round(finished_at - started_at, 3),
        "llm_seconds": round(llm_seconds, 3),
        "tool_seconds": round(tool_seconds, 3),
        "llm_calls": len(llm_events),
        "tool_calls": len(tool_events),
        "usage_delta": _usage_delta(usage_before, usage_after),
    }


def _truncate_message(message: dict[str, Any], *, max_chars: int = 12_000) -> dict[str, Any]:
    item = dict(message)
    if "content" in item:
        item["content"] = truncate_middle(str(item.get("content") or ""), max_chars)
    return item


def _truncate_messages(messages: list[dict[str, Any]], *, max_chars: int = 12_000) -> list[dict[str, Any]]:
    result = []
    for message in messages:
        result.append(_truncate_message(message, max_chars=max_chars))
    return result
