"""Agent 主循环经由 L0 Provider 层的集成测试。

验证：Provider 层默认开启时，一次「工具调用 + 收尾」的完整对话仍正常推进；助手回复
以可序列化 dict 入历史；token 记账经兼容响应对象正常累计；流式开关下增量事件转发给
``on_think``。
"""

import json
from types import SimpleNamespace

import pytest

from core import config
from core.agent import RAgent
from tools.registry import registry


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("unexpected extra LLM call")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _tool_call(name, arguments):
    return SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message, finish="stop"):
    return SimpleNamespace(
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
    )


@pytest.fixture
def echo_tool(monkeypatch):
    def echo(text="hi"):
        return "echoed:" + text

    registry.register(
        "echo_tool_test",
        "echo",
        {"type": "object", "properties": {"text": {"type": "string"}}},
        echo,
    )
    schema = registry._tools["echo_tool_test"]["schema"]
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [schema])
    monkeypatch.setattr(
        registry,
        "execute_tool_isolated",
        lambda name, args, **kwargs: registry.execute_tool(name, args),
    )
    yield
    registry._tools.pop("echo_tool_test", None)


def test_agent_loop_runs_through_provider_layer(echo_tool, monkeypatch):
    monkeypatch.setattr(config, "get_llm_provider_layer_enabled", lambda: True)
    monkeypatch.setattr(config, "get_llm_streaming_enabled", lambda: False)

    agent = RAgent(model="test-model", max_iterations=4, enable_self_review=False)
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("echo_tool_test", {"text": "world"})]), finish="tool_calls"),
        _response(_message(content="done", tool_calls=None), finish="stop"),
    ])

    assert agent.run_conversation("use echo") == "done"

    # 助手回复以可序列化 dict 入历史（经 ProviderMessage.to_dict）。
    assistant_msgs = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "assistant"]
    assert len(assistant_msgs) == 2
    first = assistant_msgs[0]
    assert first["tool_calls"][0]["function"]["name"] == "echo_tool_test"
    assert json.loads(first["tool_calls"][0]["function"]["arguments"]) == {"text": "world"}

    tool_msgs = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "echoed:world" in tool_msgs[0]["content"]

    # token 记账经兼容响应对象累计（两次调用各 5）。
    assert agent.token_usage["available"] is True
    assert agent.token_usage["total_tokens"] == 10


def test_provider_layer_streaming_forwards_deltas(echo_tool, monkeypatch):
    monkeypatch.setattr(config, "get_llm_provider_layer_enabled", lambda: True)
    monkeypatch.setattr(config, "get_llm_streaming_enabled", lambda: True)

    def _chunk(content=None, finish=None, usage=None):
        delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage)

    class _StreamCompletions:
        def __init__(self, scripts):
            self._scripts = list(scripts)

        def create(self, **kwargs):
            assert kwargs.get("stream") is True
            return iter(self._scripts.pop(0))

    class _StreamClient:
        def __init__(self, scripts):
            self.chat = SimpleNamespace(completions=_StreamCompletions(scripts))

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _StreamClient([
        [
            _chunk(content="Fin"),
            _chunk(content="al"),
            _chunk(finish="stop", usage={"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}),
        ],
    ])

    deltas = []

    def on_think(iteration, **kwargs):
        if kwargs.get("stream_delta"):
            deltas.append(kwargs["stream_delta"])

    assert agent.run_conversation("hello", on_think=on_think) == "Final"
    assert "".join(deltas) == "Final"
