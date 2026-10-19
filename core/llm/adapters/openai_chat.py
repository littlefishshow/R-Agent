"""OpenAI / Azure chat.completions 适配器。

把 OpenAI 兼容协议（含 Azure 部署）翻译成 L0 的统一 ``AssistantMessageEvent`` 流
（learning_note/L0.md 第 5 / 6 节）。R-Agent 当前所有 provider 都是 OpenAI 兼容端点
（见 ``core/config.py:create_llm_client``），因此这是首个真实 adapter。

两条路径，对外产出同一套事件序列：

* **流式**（``stream=True``）：迭代 chunk，边到边 push ``text_delta`` /
  ``thinking_delta`` / ``toolcall_delta``；工具参数 JSON 文本按 index 累积，到流末尾
  统一解析成完整 ``ToolCall`` 并补发 ``toolcall_end``；
* **非流式**（``stream=False``）：一次 ``create()`` 拿到完整 message，再**合成**出
  start / *_start / *_delta / *_end / done 事件，让消费者无差别处理。

无论哪条路径，最终 ``AssistantMessage`` 都携带归一化 ``Usage``，并把厂商原始 usage
对象放进 ``Usage.raw``，供既有 ``cache_observability.provider_cache_tokens`` 继续读取
厂商专有缓存字段，不丢任何诊断能力。

错误语义（Pi 第 7 节）：

* 返回 stream 之前：只有同步构造 kwargs 失败才可能同步抛错；
* 返回 stream 之后：首个事件前的瞬时错误按配置重试；仍失败或生成中途失败，都转成
  ``error`` 终止事件（error AssistantMessage），绝不抛给消费者或变成后台未捕获异常。
"""

from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.llm.event_stream import AssistantMessageEventStream
from core.llm.provider import ModelProvider, ModelRequest, StreamOptions
from core.llm.types import (
    AssistantMessage,
    StopReason,
    ThinkingContent,
    TextContent,
    ToolCall,
    Usage,
    event_done,
    event_error,
    event_text_delta,
    event_text_end,
    event_text_start,
    event_thinking_delta,
    event_thinking_end,
    event_thinking_start,
    event_toolcall_delta,
    event_toolcall_end,
    event_toolcall_start,
    event_start,
    make_error_message,
)


# ---------------------------------------------------------------------------
# finish_reason -> StopReason
# ---------------------------------------------------------------------------
_FINISH_REASON_MAP: Dict[str, StopReason] = {
    "stop": "stop",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "length",
    "content_filter": "content_filter",
}


def _map_finish_reason(reason: Optional[str], *, had_tool_calls: bool) -> StopReason:
    if reason:
        mapped = _FINISH_REASON_MAP.get(str(reason))
        if mapped:
            return mapped
    return "tool_use" if had_tool_calls else "stop"


def _is_transient_error(exc: Exception) -> bool:
    """瞬时错误（值得重试）判定，与 agent 层保持一致的保守口径。"""
    try:
        from openai import (  # type: ignore
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
            return True
    except ImportError:
        pass
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status is None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    msg = str(exc).lower()
    markers = (
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "bad gateway",
        "service unavailable",
    )
    return any(m in msg for m in markers)


def _usage_from_response(usage_obj: Any) -> Usage:
    """把厂商 usage 对象/字典归一化为 L0 ``Usage``，并保留原始对象。"""
    if usage_obj is None:
        return Usage(available=False, raw=None)

    def _get(name: str) -> int:
        if isinstance(usage_obj, dict):
            value = usage_obj.get(name, 0)
        else:
            value = getattr(usage_obj, name, 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    prompt = _get("prompt_tokens")
    completion = _get("completion_tokens")
    total = _get("total_tokens") or (prompt + completion)
    cached: Optional[int] = None
    creation: Optional[int] = None
    try:
        # 复用既有 provider 缓存字段解析，保持与 cache_observability 一致。
        from core.cache_observability import provider_cache_tokens

        cached, creation = provider_cache_tokens(usage_obj)
    except Exception:
        cached, creation = None, None
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        provider_cached_tokens=cached,
        provider_cache_creation_tokens=creation,
        available=bool(prompt or completion or total),
        raw=usage_obj,
    )


def _finalize_tool_call(index: int, acc: Dict[str, Any]) -> ToolCall:
    """把累积的工具调用片段解析成完整 ``ToolCall``（Pi：到 end 才解析参数）。"""
    raw_args = acc.get("arguments", "") or ""
    call_id = acc.get("id") or f"call_{index}"
    name = acc.get("name") or ""
    parsed: Dict[str, Any] = {}
    arg_error: Optional[str] = None
    if raw_args.strip():
        try:
            loaded = json.loads(raw_args)
            if isinstance(loaded, dict):
                parsed = loaded
            else:
                arg_error = "arguments JSON is not an object"
        except Exception as exc:  # noqa: BLE001 - 保留原文交给上层校验
            arg_error = f"arguments JSON parse error: {exc}"
    return ToolCall(
        id=call_id,
        name=name,
        arguments=parsed,
        raw_arguments=raw_args,
        arguments_error=arg_error,
    )


class OpenAIChatProvider(ModelProvider):
    """OpenAI 兼容 chat.completions 适配器（同时支持 Azure 客户端）。

    参数：

    * ``client``       —— 已配置好的 OpenAI/AzureOpenAI SDK 客户端；
    * ``max_retries``  —— 首个事件前的瞬时错误重试次数；
    * ``base_delay``   —— 指数退避基准秒数；
    * ``cancel_check`` —— 可选的取消检查回调，返回 True 时终止为 ``aborted``。
    """

    name = "openai_chat"

    def __init__(
        self,
        client: Any,
        *,
        max_retries: int = 0,
        base_delay: float = 0.5,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.client = client
        self.max_retries = max(0, int(max_retries))
        self.base_delay = max(0.0, float(base_delay))
        self.cancel_check = cancel_check

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def stream(self, request: ModelRequest, **provider_options: Any) -> AssistantMessageEventStream:
        want_stream = bool(provider_options.pop("stream", True))
        # 透传给 create() 的额外厂商参数（如 response_format）。
        passthrough = {k: v for k, v in provider_options.items() if v is not None}
        kwargs = request.to_openai_kwargs()
        kwargs.update(passthrough)

        out = AssistantMessageEventStream()
        worker = threading.Thread(
            target=self._produce,
            args=(out, kwargs, want_stream),
            name="r-agent-openai-stream",
            daemon=True,
        )
        worker.start()
        return out

    def stream_simple(
        self,
        request: ModelRequest,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        options = options or StreamOptions()
        provider_options: Dict[str, Any] = {"stream": options.stream}
        provider_options.update(options.passthrough or {})
        return self.stream(request, **provider_options)

    # ------------------------------------------------------------------
    # 生产者线程
    # ------------------------------------------------------------------
    def _cancelled(self) -> bool:
        try:
            return bool(self.cancel_check and self.cancel_check())
        except Exception:
            return False

    def _produce(self, out: AssistantMessageEventStream, kwargs: Dict[str, Any], want_stream: bool) -> None:
        try:
            if self._cancelled():
                out.push(event_error(make_error_message("aborted", reason="aborted"), reason="aborted"))
                return
            raw = self._create_with_retry(out, kwargs, want_stream)
            if raw is _RETRY_FAILED:
                return  # 已经 push 过 error 事件
            if want_stream:
                self._consume_stream(out, raw)
            else:
                self._consume_full(out, raw)
        except Exception as exc:  # noqa: BLE001 - 任何意外都转成 error 终止事件
            out.push(event_error(make_error_message(str(exc))))

    def _create_with_retry(self, out: AssistantMessageEventStream, kwargs: Dict[str, Any], want_stream: bool):
        call_kwargs = dict(kwargs)
        if want_stream:
            call_kwargs["stream"] = True
            # 让流式响应也带 usage（OpenAI 需要显式开启）。
            call_kwargs.setdefault("stream_options", {"include_usage": True})
        else:
            call_kwargs["stream"] = False

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            if self._cancelled():
                out.push(event_error(make_error_message("aborted", reason="aborted"), reason="aborted"))
                return _RETRY_FAILED
            try:
                return self.client.chat.completions.create(**call_kwargs)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self.max_retries or not _is_transient_error(exc):
                    out.push(event_error(make_error_message(str(exc))))
                    return _RETRY_FAILED
                delay = self.base_delay * (2 ** attempt) + random.uniform(0, self.base_delay)
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    if self._cancelled():
                        out.push(event_error(make_error_message("aborted", reason="aborted"), reason="aborted"))
                        return _RETRY_FAILED
                    time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        out.push(event_error(make_error_message(str(last_exc) if last_exc else "unknown error")))
        return _RETRY_FAILED

    # ------------------------------------------------------------------
    # 非流式：一次性合成事件
    # ------------------------------------------------------------------
    def _consume_full(self, out: AssistantMessageEventStream, response: Any) -> None:
        out.push(event_start())
        message = response.choices[0].message
        usage = _usage_from_response(getattr(response, "usage", None))

        content_blocks: List[Any] = []
        index = 0

        thinking_text = str(getattr(message, "reasoning_content", "") or "") or str(
            getattr(message, "reasoning", "") or ""
        )
        if thinking_text:
            out.push(event_thinking_start(index))
            out.push(event_thinking_delta(index, thinking_text))
            out.push(event_thinking_end(index, thinking_text))
            content_blocks.append(ThinkingContent(text=thinking_text))
            index += 1

        text = str(getattr(message, "content", "") or "")
        if text:
            out.push(event_text_start(index))
            out.push(event_text_delta(index, text))
            out.push(event_text_end(index, text))
            content_blocks.append(TextContent(text=text))
            index += 1

        raw_tool_calls = getattr(message, "tool_calls", None) or []
        for tc in raw_tool_calls:
            fn = getattr(tc, "function", None)
            raw_args = str(getattr(fn, "arguments", "") or "") if fn is not None else ""
            acc = {
                "id": getattr(tc, "id", None),
                "name": getattr(fn, "name", "") if fn is not None else "",
                "arguments": raw_args,
            }
            out.push(event_toolcall_start(index))
            if raw_args:
                out.push(event_toolcall_delta(index, raw_args))
            tool_call = _finalize_tool_call(index, acc)
            out.push(event_toolcall_end(index, tool_call))
            content_blocks.append(tool_call)
            index += 1

        stop = _map_finish_reason(
            getattr(response.choices[0], "finish_reason", None),
            had_tool_calls=bool(raw_tool_calls),
        )
        final = AssistantMessage(content=content_blocks, stop_reason=stop, usage=usage, raw=message)
        out.push(event_done(stop, final))

    # ------------------------------------------------------------------
    # 流式：边到边 push
    # ------------------------------------------------------------------
    def _consume_stream(self, out: AssistantMessageEventStream, chunks: Any) -> None:
        out.push(event_start())

        # 为了保持 content block 顺序与到达顺序一致，用一个有序的 block 列表 +
        # 分类游标。OpenAI chat 的 delta 通常是：先若干 text/reasoning，再 tool_calls。
        text_buf: List[str] = []
        thinking_buf: List[str] = []
        text_index: Optional[int] = None
        thinking_index: Optional[int] = None
        # tool_calls 按 provider 给的 index 累积。
        tool_accs: Dict[int, Dict[str, Any]] = {}
        tool_block_index: Dict[int, int] = {}
        next_index = 0
        finish_reason: Optional[str] = None
        usage_obj: Any = None

        def _alloc() -> int:
            nonlocal next_index
            idx = next_index
            next_index += 1
            return idx

        for chunk in chunks:
            if self._cancelled():
                out.push(event_error(make_error_message("aborted", reason="aborted"), reason="aborted"))
                return
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage_obj = chunk_usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            reasoning_delta = str(
                getattr(delta, "reasoning_content", "") or getattr(delta, "reasoning", "") or ""
            )
            if reasoning_delta:
                if thinking_index is None:
                    thinking_index = _alloc()
                    out.push(event_thinking_start(thinking_index))
                thinking_buf.append(reasoning_delta)
                out.push(event_thinking_delta(thinking_index, reasoning_delta))

            text_delta = str(getattr(delta, "content", "") or "")
            if text_delta:
                if text_index is None:
                    text_index = _alloc()
                    out.push(event_text_start(text_index))
                text_buf.append(text_delta)
                out.push(event_text_delta(text_index, text_delta))

            for tc in getattr(delta, "tool_calls", None) or []:
                tc_index = getattr(tc, "index", 0) or 0
                acc = tool_accs.get(tc_index)
                if acc is None:
                    acc = {"id": None, "name": "", "arguments": ""}
                    tool_accs[tc_index] = acc
                    block_idx = _alloc()
                    tool_block_index[tc_index] = block_idx
                    out.push(event_toolcall_start(block_idx))
                if getattr(tc, "id", None):
                    acc["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        acc["name"] = fn.name
                    arg_piece = getattr(fn, "arguments", None)
                    if arg_piece:
                        acc["arguments"] += arg_piece
                        out.push(event_toolcall_delta(tool_block_index[tc_index], arg_piece))

        # 收尾：补发 text_end / thinking_end / toolcall_end，组装最终消息。
        content_blocks: List[Tuple[int, Any]] = []
        if thinking_index is not None:
            thinking_text = "".join(thinking_buf)
            out.push(event_thinking_end(thinking_index, thinking_text))
            content_blocks.append((thinking_index, ThinkingContent(text=thinking_text)))
        if text_index is not None:
            text = "".join(text_buf)
            out.push(event_text_end(text_index, text))
            content_blocks.append((text_index, TextContent(text=text)))
        for tc_index in sorted(tool_accs):
            block_idx = tool_block_index[tc_index]
            tool_call = _finalize_tool_call(block_idx, tool_accs[tc_index])
            out.push(event_toolcall_end(block_idx, tool_call))
            content_blocks.append((block_idx, tool_call))

        content_blocks.sort(key=lambda item: item[0])
        ordered = [block for _, block in content_blocks]
        stop = _map_finish_reason(finish_reason, had_tool_calls=bool(tool_accs))
        final = AssistantMessage(
            content=ordered,
            stop_reason=stop,
            usage=_usage_from_response(usage_obj),
            raw=None,
        )
        out.push(event_done(stop, final))


# 内部哨兵：表示 create 重试已彻底失败且已 push error 事件。
_RETRY_FAILED = object()
