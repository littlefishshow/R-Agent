import json
from types import SimpleNamespace

from core import config
from core.agent import RAgent
from core.context_control import compress_messages, should_compress_context
from tools.context_tool import archive_subtask


class _FakeCompletions:
    def __init__(self):
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        return SimpleNamespace(
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
        )


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_context_compress_keeps_recent_messages_whole_and_summarizes_tool_results():
    messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "请重点分析 A，并记住约束 B"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "very long tool output " * 100},
        {"role": "assistant", "content": "根据工具结果，结论 C"},
        {"role": "user", "content": "继续，输出最终方案"},
    ]

    result = compress_messages(
        messages,
        model="tiny-unknown",
        max_context_tokens=120,
        preserve_recent_messages=2,
        force=True,
    )

    assert result["compressed"] is True
    compressed = result["compressed_messages"]
    assert compressed[0] == messages[0]
    assert compressed[-2:] == messages[-2:]
    assert "用户重点" in compressed[1]["content"]
    assert "工具结果要点" in compressed[1]["content"]
    assert "read_file" in compressed[1]["content"]


def test_archive_subtask_with_messages_low_threshold_returns_original_when_not_forced():
    messages = [{"role": "system", "content": "base"}, {"role": "user", "content": "short"}]
    raw = archive_subtask(
        messages=messages,
        max_context_tokens=10000,
        trigger_ratio=0.8,
        force=False,
    )
    result = json.loads(raw)
    assert result["success"] is True
    assert result["compressed"] is False
    assert result["compressed_messages"] == messages


def test_should_compress_context_uses_80_percent_threshold():
    check = should_compress_context(
        [{"role": "user", "content": "x" * 400}],
        max_context_tokens=100,
        trigger_ratio=0.8,
    )
    assert check["threshold_tokens"] == 80
    assert check["should_compress"] is True


def test_agent_auto_compresses_before_llm_request(monkeypatch):
    monkeypatch.setattr(config, "get_llm_context_window", lambda: 180)
    monkeypatch.setattr(config, "get_context_compression_trigger_ratio", lambda: 0.2)
    monkeypatch.setattr(config, "get_context_compression_target_ratio", lambda: 0.5)
    monkeypatch.setattr(config, "get_context_compression_preserve_recent_messages", lambda: 2)

    agent = RAgent(model="test", max_iterations=1, enable_self_review=False)
    fake_client = _FakeClient()
    agent.client = fake_client
    agent.messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "旧需求" * 80},
        {"role": "assistant", "content": "旧分析" * 80},
    ]

    result = agent.run_conversation("新问题", system_message="base")

    assert result == "ok"
    sent_messages = fake_client.chat.completions.kwargs[0]["messages"]
    assert any("自动上下文压缩摘要" in (m.get("content") or "") for m in sent_messages if isinstance(m, dict))
    assert any(isinstance(m, dict) and m.get("content") == "新问题" for m in sent_messages)
    assert agent.context_usage["compressed_count"] >= 1


def test_archive_subtask_with_messages_merges_manual_summary():
    messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "旧问题" * 80},
        {"role": "assistant", "content": "旧回答" * 80},
        {"role": "user", "content": "新问题"},
    ]
    raw = archive_subtask(
        summary="阶段完成：已定位核心问题",
        next_steps="继续验证",
        messages=messages,
        max_context_tokens=120,
        preserve_recent_messages=1,
        force=True,
    )
    result = json.loads(raw)
    assert result["success"] is True
    assert result["compressed"] is True
    assert "阶段完成" in result["summary"]
    assert any("阶段完成" in (m.get("content") or "") for m in result["compressed_messages"] if isinstance(m, dict))
