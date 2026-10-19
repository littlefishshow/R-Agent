"""Faux Provider：确定性的测试 Provider（learning_note/L0.md 第 7 节）。

与普通 mock 不同，Faux 会真实地产生完整事件序列（start -> *_delta -> done/error），
因此可以验证 harness 的**时序**，而不仅是最终值：

* 可脚本化地产出 text / thinking / tool call；
* 可产出 ``error`` 与 ``aborted`` 终止；
* 可控制 chunk 切分，模拟增量到达；
* 不依赖真实网络、认证或厂商 SDK。

它是学习与回归 L0 事件流的最佳入口。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.llm.event_stream import AssistantMessageEventStream
from core.llm.provider import ModelProvider, ModelRequest, StreamOptions
from core.llm.types import (
    AssistantMessage,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
    event_done,
    event_error,
    event_start,
    event_text_delta,
    event_text_end,
    event_text_start,
    event_thinking_delta,
    event_thinking_end,
    event_thinking_start,
    event_toolcall_delta,
    event_toolcall_end,
    event_toolcall_start,
    make_error_message,
)


@dataclass
class FauxToolCall:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass
class FauxTurn:
    """一次 Faux 响应脚本。

    * ``text``        —— 要吐出的文本（会按 ``chunk_size`` 切成多个 delta）；
    * ``thinking``    —— 可选的思考文本；
    * ``tool_calls``  —— 要生成的工具调用；
    * ``stop_reason`` —— 结束原因；有 tool_calls 时默认 tool_use；
    * ``error``       —— 若非空，产出 error 终止（文本仍会先吐出已生成部分）；
    * ``usage``       —— 可选的 token 计量。
    """

    text: str = ""
    thinking: str = ""
    tool_calls: List[FauxToolCall] = field(default_factory=list)
    stop_reason: Optional[StopReason] = None
    error: str = ""
    usage: Optional[Usage] = None
    chunk_size: int = 8


def _chunks(text: str, size: int) -> List[str]:
    if size <= 0:
        return [text] if text else []
    return [text[i : i + size] for i in range(0, len(text), size)] or ([] if not text else [text])


class FauxProvider(ModelProvider):
    """按脚本确定性产出事件的测试 Provider。

    ``turns`` 为逐次调用的脚本队列；每次 ``stream*`` 弹出一条。队列耗尽后默认产出
    一个空文本 done（可通过 ``default_turn`` 覆盖）。``synchronous=True`` 时在当前线程
    直接生产（便于单测断言，不引入线程时序）。
    """

    name = "faux"

    def __init__(
        self,
        turns: Optional[List[FauxTurn]] = None,
        *,
        default_turn: Optional[FauxTurn] = None,
        synchronous: bool = True,
    ) -> None:
        self.turns: List[FauxTurn] = list(turns or [])
        self.default_turn = default_turn or FauxTurn(text="")
        self.synchronous = synchronous
        self.requests: List[ModelRequest] = []

    def stream(self, request: ModelRequest, **provider_options: Any) -> AssistantMessageEventStream:
        self.requests.append(request)
        turn = self.turns.pop(0) if self.turns else self.default_turn
        out = AssistantMessageEventStream()
        if self.synchronous:
            self._produce(out, turn)
        else:
            threading.Thread(target=self._produce, args=(out, turn), daemon=True).start()
        return out

    def stream_simple(
        self,
        request: ModelRequest,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        return self.stream(request, stream=(options or StreamOptions()).stream)

    def _produce(self, out: AssistantMessageEventStream, turn: FauxTurn) -> None:
        out.push(event_start())
        content: List[Any] = []
        index = 0

        if turn.thinking:
            out.push(event_thinking_start(index))
            for piece in _chunks(turn.thinking, turn.chunk_size):
                out.push(event_thinking_delta(index, piece))
            out.push(event_thinking_end(index, turn.thinking))
            content.append(ThinkingContent(text=turn.thinking))
            index += 1

        if turn.text:
            out.push(event_text_start(index))
            for piece in _chunks(turn.text, turn.chunk_size):
                out.push(event_text_delta(index, piece))
            out.push(event_text_end(index, turn.text))
            content.append(TextContent(text=turn.text))
            index += 1

        if turn.error:
            # 已吐出的部分内容保留在 content 里，error 作为最终终止。
            msg = make_error_message(turn.error)
            msg.content = content
            out.push(event_error(msg))
            return

        for i, call in enumerate(turn.tool_calls):
            import json as _json

            raw_args = _json.dumps(call.arguments, ensure_ascii=False)
            out.push(event_toolcall_start(index))
            for piece in _chunks(raw_args, turn.chunk_size):
                out.push(event_toolcall_delta(index, piece))
            tool_call = ToolCall(
                id=call.id or f"call_faux_{i}",
                name=call.name,
                arguments=dict(call.arguments),
                raw_arguments=raw_args,
            )
            out.push(event_toolcall_end(index, tool_call))
            content.append(tool_call)
            index += 1

        stop: StopReason = turn.stop_reason or ("tool_use" if turn.tool_calls else "stop")
        usage = turn.usage or Usage(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            available=True,
        )
        out.push(event_done(stop, AssistantMessage(content=content, stop_reason=stop, usage=usage)))
