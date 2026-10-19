"""L0 Provider 与流事件层测试。

对齐 learning_note/L0.md 的五个部分：公共语言、事件流容器、Provider 适配器、
响应状态机、运行保障。按部分组织，便于后续逐块扩展。
"""

import json
from types import SimpleNamespace

import pytest

from core.llm.event_stream import AssistantMessageEventStream, FifoQueue
from core.llm.lazy import lazy_stream
from core.llm.provider import ModelRequest, StreamOptions
from core.llm.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    event_done,
    event_error,
    event_start,
    event_text_delta,
    event_text_end,
    event_text_start,
    make_error_message,
)
from core.llm.adapters.faux import FauxProvider, FauxToolCall, FauxTurn
from core.llm.adapters.openai_chat import OpenAIChatProvider
from core.llm.compat import ProviderResponse


# ---------------------------------------------------------------------------
# 第二部分：事件流容器
# ---------------------------------------------------------------------------
def test_fifo_queue_amortized_order():
    q = FifoQueue()
    for i in range(5):
        q.enqueue(event_text_delta(0, str(i)))
    seen = []
    while len(q):
        ev = q.dequeue()
        seen.append(ev.delta)
    assert seen == ["0", "1", "2", "3", "4"]


def test_event_stream_iterates_then_result():
    stream = AssistantMessageEventStream()
    final = AssistantMessage(content=[TextContent(text="Hi")], stop_reason="stop")
    stream.push(event_start())
    stream.push(event_text_start(0))
    stream.push(event_text_delta(0, "Hi"))
    stream.push(event_text_end(0, "Hi"))
    stream.push(event_done("stop", final))

    types = [event.type for event in stream]
    assert types == ["start", "text_start", "text_delta", "text_end", "done"]
    # result() 从终止事件提取最终消息，不再消费队列。
    assert stream.result().text == "Hi"


def test_event_stream_ignores_push_after_terminal():
    stream = AssistantMessageEventStream()
    final = AssistantMessage(content=[], stop_reason="stop")
    stream.push(event_done("stop", final))
    stream.push(event_text_delta(0, "late"))  # 应被忽略
    types = [event.type for event in stream]
    assert types == ["done"]


def test_event_stream_error_is_terminal_result():
    stream = AssistantMessageEventStream()
    stream.push(event_error(make_error_message("boom")))
    msg = stream.result()
    assert msg.stop_reason == "error"
    assert msg.error_message == "boom"


# ---------------------------------------------------------------------------
# 第五部分：Faux Provider（测试 Provider）
# ---------------------------------------------------------------------------
def _request():
    return ModelRequest(model="faux", messages=[{"role": "user", "content": "hi"}])


def test_faux_text_and_thinking_sequence():
    fx = FauxProvider([FauxTurn(thinking="reason", text="Hello", chunk_size=2)])
    stream = fx.stream_simple(_request())
    types = [event.type for event in stream]
    assert types[0] == "start"
    assert "thinking_start" in types and "thinking_end" in types
    assert "text_start" in types and "text_end" in types
    assert types[-1] == "done"
    msg = stream.result()
    assert msg.text == "Hello"
    assert msg.thinking == "reason"
    assert msg.stop_reason == "stop"


def test_faux_tool_call_turn():
    fx = FauxProvider([FauxTurn(tool_calls=[FauxToolCall(name="read_file", arguments={"path": "a.md"})])])
    stream = fx.stream_simple(_request())
    types = [event.type for event in stream]
    assert "toolcall_start" in types and "toolcall_end" in types
    msg = stream.result()
    assert len(msg.tool_calls) == 1
    call = msg.tool_calls[0]
    assert call.name == "read_file"
    assert call.arguments == {"path": "a.md"}
    assert msg.stop_reason == "tool_use"


def test_faux_error_turn_keeps_partial_text():
    fx = FauxProvider([FauxTurn(text="partial", error="network down")])
    stream = fx.stream_simple(_request())
    types = [event.type for event in stream]
    assert types[-1] == "error"
    msg = stream.result()
    assert msg.stop_reason == "error"
    assert msg.error_message == "network down"
    # 已生成的部分内容保留在 content 中。
    assert any(isinstance(b, TextContent) and b.text == "partial" for b in msg.content)


# ---------------------------------------------------------------------------
# 第三/六部分：OpenAI adapter（非流式 + 流式 + 工具装配）
# ---------------------------------------------------------------------------
def _mk_message(content="", tool_calls=None, reasoning=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning is not None:
        message.reasoning_content = reasoning
    return message


def _mk_response(message, finish="stop", usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=usage,
    )


class _FullCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _Client:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


def test_openai_adapter_non_streaming_text_and_usage():
    completions = _FullCompletions([
        _mk_response(
            _mk_message(content="Hi there", reasoning="thinking"),
            finish="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        )
    ])
    provider = OpenAIChatProvider(_Client(completions))
    stream = provider.stream_simple(_request(), StreamOptions(stream=False))
    list(stream)
    msg = stream.result()
    assert completions.calls[0]["stream"] is False
    assert msg.text == "Hi there"
    assert msg.thinking == "thinking"
    assert msg.usage.total_tokens == 13
    assert msg.usage.available is True


def test_openai_adapter_non_streaming_tool_call_assembly():
    tc = SimpleNamespace(
        index=0,
        id="call_1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"a.md"}'),
    )
    completions = _FullCompletions([
        _mk_response(_mk_message(tool_calls=[tc]), finish="tool_calls", usage={"prompt_tokens": 5})
    ])
    provider = OpenAIChatProvider(_Client(completions))
    stream = provider.stream_simple(_request(), StreamOptions(stream=False))
    list(stream)
    msg = stream.result()
    assert msg.stop_reason == "tool_use"
    assert msg.tool_calls[0].arguments == {"path": "a.md"}
    assert msg.tool_calls[0].id == "call_1"


class _StreamCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


def _chunk(content=None, reasoning=None, tool=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage)


def _delta_tool(index, cid=None, name=None, args=None):
    return SimpleNamespace(index=index, id=cid, function=SimpleNamespace(name=name, arguments=args))


def test_openai_adapter_streaming_incremental_tool_call():
    chunks = [
        _chunk(reasoning="th"),
        _chunk(content="Hel"),
        _chunk(content="lo"),
        _chunk(tool=[_delta_tool(0, cid="call_x", name="read_file", args='{"path":')]),
        _chunk(tool=[_delta_tool(0, args='"a.md"}')]),
        _chunk(finish="tool_calls", usage={"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}),
    ]
    completions = _StreamCompletions(chunks)
    provider = OpenAIChatProvider(_Client(completions))
    stream = provider.stream_simple(_request(), StreamOptions(stream=True))
    types = [event.type for event in stream]
    assert completions.calls[0]["stream"] is True
    assert "text_delta" in types and "toolcall_delta" in types
    msg = stream.result()
    assert msg.text == "Hello"
    assert msg.thinking == "th"
    assert msg.tool_calls[0].name == "read_file"
    assert msg.tool_calls[0].arguments == {"path": "a.md"}
    assert msg.usage.total_tokens == 10


def test_openai_adapter_create_error_becomes_error_event():
    class _BadCompletions:
        def create(self, **kwargs):
            raise ValueError("401 unauthorized")

    provider = OpenAIChatProvider(_Client(_BadCompletions()), max_retries=0)
    stream = provider.stream_simple(_request(), StreamOptions(stream=False))
    types = [event.type for event in stream]
    assert types == ["error"]
    assert stream.result().stop_reason == "error"
    assert "401" in stream.result().error_message


# ---------------------------------------------------------------------------
# 第五部分：运行保障（lazy wrapper）
# ---------------------------------------------------------------------------
def test_lazy_stream_setup_failure_is_error_event():
    def _setup():
        raise ImportError("Cannot find module ./openai-responses.js")

    stream = lazy_stream(_setup)
    types = [event.type for event in stream]
    assert types == ["error"]
    msg = stream.result()
    assert msg.stop_reason == "error"
    assert "Cannot find module" in msg.error_message


def test_lazy_stream_forwards_inner_events():
    def _setup():
        fx = FauxProvider([FauxTurn(text="done")])
        return fx.stream_simple(_request())

    stream = lazy_stream(_setup)
    types = [event.type for event in stream]
    assert types[0] == "start"
    assert types[-1] == "done"
    assert stream.result().text == "done"


# ---------------------------------------------------------------------------
# 第一部分：公共语言 <-> OpenAI 兼容响应（compat 层）
# ---------------------------------------------------------------------------
def test_provider_response_exposes_choices_and_usage():
    assistant = AssistantMessage(
        content=[TextContent(text="answer")],
        stop_reason="stop",
    )
    assistant.usage.prompt_tokens = 7
    assistant.usage.total_tokens = 9
    assistant.usage.available = True
    response = ProviderResponse(assistant)
    message = response.choices[0].message
    assert message.content == "answer"
    assert message.tool_calls is None
    assert message.to_dict() == {"role": "assistant", "content": "answer"}
    assert response.choices[0].finish_reason == "stop"


def test_provider_response_tool_calls_roundtrip_to_openai_dict():
    assistant = AssistantMessage(
        content=[ToolCall(id="c1", name="read_file", arguments={"path": "x"}, raw_arguments='{"path": "x"}')],
        stop_reason="tool_use",
    )
    response = ProviderResponse(assistant)
    message = response.choices[0].message
    assert response.choices[0].finish_reason == "tool_calls"
    data = message.to_dict()
    assert data["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(data["tool_calls"][0]["function"]["arguments"]) == {"path": "x"}
