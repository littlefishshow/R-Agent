from types import SimpleNamespace

from core.agent import RAgent, sanitize_tool_call_messages


def _role(message):
    return message.get("role") if isinstance(message, dict) else getattr(message, "role", None)


def _tool_calls(message):
    return message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)


def _assistant_with_tool_call(call_id, name="read_file", arguments="{}"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }],
    }


def test_sanitize_drops_dangling_assistant_tool_call():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "read the paper"},
        _assistant_with_tool_call("call_1"),
        # No tool response for call_1 -> dangling. Followed by a new user turn.
        {"role": "user", "content": "why no thinking?"},
    ]
    cleaned = sanitize_tool_call_messages(messages)
    assert cleaned == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "read the paper"},
        {"role": "user", "content": "why no thinking?"},
    ]


def test_sanitize_keeps_answered_tool_calls():
    messages = [
        {"role": "user", "content": "q"},
        _assistant_with_tool_call("call_1"),
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        {"role": "assistant", "content": "final"},
    ]
    assert sanitize_tool_call_messages(messages) == messages


def test_sanitize_drops_orphan_tool_message():
    messages = [
        {"role": "user", "content": "q"},
        {"role": "tool", "tool_call_id": "ghost", "content": "orphan"},
        {"role": "assistant", "content": "final"},
    ]
    cleaned = sanitize_tool_call_messages(messages)
    assert cleaned == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "final"},
    ]


class _CapturingCompletions:
    def __init__(self, holder):
        self.holder = holder

    def create(self, **kwargs):
        # Record the exact messages sent to the API for assertion.
        self.holder["messages"] = kwargs.get("messages")
        msg = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=msg)])


class _CapturingClient:
    def __init__(self, holder):
        self.chat = SimpleNamespace(completions=_CapturingCompletions(holder))


def test_run_conversation_repairs_dangling_tool_call_before_request(monkeypatch):
    monkeypatch.setattr("core.agent.registry.get_all_schemas", lambda: [])
    holder = {}
    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _CapturingClient(holder)
    # Simulate a session whose previous run died mid tool-call: an assistant
    # tool_calls message with no matching tool result already in history.
    agent.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "read the paper"},
        _assistant_with_tool_call("call_dangling"),
    ]

    result = agent.run_conversation("please continue")

    assert result == "ok"
    sent = holder["messages"]
    # The dangling assistant tool_calls message must be gone from the request.
    assert not any(_role(m) == "assistant" and _tool_calls(m) for m in sent)
    # The new user turn is still present.
    assert any(_role(m) == "user" and (m.get("content") if isinstance(m, dict) else None) == "please continue" for m in sent)
