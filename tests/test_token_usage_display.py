from types import SimpleNamespace

from core.agent import RAgent
from main import _format_token_usage_label, _token_usage_panel_subtitle


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        if not self._responses:
            raise AssertionError("unexpected extra LLM call")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


class _ContextLengthError(Exception):
    pass


class _FailingCompletions:
    def create(self, **kwargs):
        raise _ContextLengthError("code: context_length_exceeded; message: Input tokens exceed the configured limit of 922000 tokens. Your messages resulted in 3560137 tokens. Please reduce the length of the messages.")


class _FailingClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FailingCompletions())


def _response_with_usage(usage):
    return SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
    )


def test_agent_accumulates_token_usage_from_response_objects():
    agent = RAgent(model="test", max_iterations=1)
    agent.client = _FakeClient([
        _response_with_usage(SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7)),
        _response_with_usage({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}),
    ])

    agent._chat_completion_with_retry(model="test", messages=[])
    agent._chat_completion_with_retry(model="test", messages=[])

    assert agent.token_usage["prompt_tokens"] == 13
    assert agent.token_usage["completion_tokens"] == 6
    assert agent.get_token_usage_total() == 19
    assert agent.get_last_token_usage_total() == 12


def test_agent_token_usage_falls_back_to_prompt_plus_completion():
    agent = RAgent(model="test", max_iterations=1)
    agent.client = _FakeClient([
        _response_with_usage({"prompt_tokens": 5, "completion_tokens": 6}),
    ])

    agent._chat_completion_with_retry(model="test", messages=[])

    assert agent.get_token_usage_total() == 11


def test_agent_token_usage_unavailable_when_response_has_no_usage():
    agent = RAgent(model="test", max_iterations=1)

    agent._record_token_usage(SimpleNamespace())

    assert agent.get_token_usage_total() == "unavailable"
    assert _format_token_usage_label(agent) == "last/session tokens: unavailable/unavailable"
    assert _token_usage_panel_subtitle(agent) == "[dim]last/session tokens: unavailable/unavailable[/dim]"


def test_token_usage_label_and_panel_subtitle_show_total_tokens():
    agent = RAgent(model="test", max_iterations=1)
    agent._record_token_usage(_response_with_usage({"total_tokens": 42}))

    assert _format_token_usage_label(agent) == "last/session tokens: 42/42"
    assert _token_usage_panel_subtitle(agent) == "[dim]last/session tokens: 42/42[/dim]"


def test_large_single_message_completion_tokens_prints_warning(capsys):
    agent = RAgent(model="test", max_iterations=1)

    agent._record_token_usage(_response_with_usage({
        "prompt_tokens": 10,
        "completion_tokens": 50_001,
        "total_tokens": 50_011,
    }))

    captured = capsys.readouterr()
    assert "单次模型返回 message token 数过大" in captured.out
    assert "completion_tokens=50001" in captured.out


def test_message_completion_tokens_at_threshold_does_not_print_warning(capsys):
    agent = RAgent(model="test", max_iterations=1)

    agent._record_token_usage(_response_with_usage({
        "prompt_tokens": 10,
        "completion_tokens": 50_000,
        "total_tokens": 50_010,
    }))

    captured = capsys.readouterr()
    assert captured.out == ""


def test_context_length_failure_saves_top_three_long_messages(tmp_path, monkeypatch, capsys):
    import core.agent as agent_mod

    monkeypatch.setattr(agent_mod, "LONG_CONTEXT_OUTPUT_DIR", tmp_path)
    agent = RAgent(model="test", max_iterations=1)
    agent.client = _FailingClient()
    messages = [
        {"role": "system", "content": "s" * 10},
        {"role": "user", "content": "u" * 100},
        {"role": "assistant", "content": "a" * 300},
        {"role": "tool", "name": "read_file", "content": "t" * 200},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": "x" * 250}}]},
    ]

    try:
        agent._chat_completion_with_retry(model="test", messages=messages)
    except _ContextLengthError:
        pass
    else:
        raise AssertionError("expected context length error")

    captured = capsys.readouterr()
    assert "已保存最长的 3 条 message" in captured.out
    summaries = list(tmp_path.glob("*_summary.json"))
    assert len(summaries) == 1
    saved_messages = sorted(tmp_path.glob("*_rank*.json"))
    assert len(saved_messages) == 3
    names = "\n".join(p.name for p in saved_messages)
    assert "idx2" in names
    assert "idx3" in names
    assert "idx4" in names


def test_loop_context_length_failure_returns_saved_path(tmp_path, monkeypatch):
    import core.agent as agent_mod

    monkeypatch.setattr(agent_mod, "LONG_CONTEXT_OUTPUT_DIR", tmp_path)
    agent = RAgent(model="test", max_iterations=1)
    agent.client = _FailingClient()

    result = agent.run_conversation("hello " + "u" * 100, system_message="system")

    assert "context_length_exceeded" in result
    assert "已保存最长的 3 条 message 到" in result
    assert len(list(tmp_path.glob("*_summary.json"))) == 1
    assert len(list(tmp_path.glob("*_rank*.json"))) == 2
