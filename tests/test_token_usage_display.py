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
    assert _format_token_usage_label(agent) == "tokens: unavailable"
    assert _token_usage_panel_subtitle(agent) == "[dim]tokens: unavailable[/dim]"


def test_token_usage_label_and_panel_subtitle_show_total_tokens():
    agent = RAgent(model="test", max_iterations=1)
    agent._record_token_usage(_response_with_usage({"total_tokens": 42}))

    assert _format_token_usage_label(agent) == "tokens: 42"
    assert _token_usage_panel_subtitle(agent) == "[dim]tokens: 42[/dim]"
