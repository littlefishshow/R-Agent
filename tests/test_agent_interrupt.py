import threading

import pytest

from core.agent import AgentInterrupted, RAgent, _TRUNCATED_FLAG


class _NoopChatCompletions:
    def create(self, **kwargs):  # pragma: no cover - should not be reached in these tests
        raise AssertionError("LLM should not be called after cancellation")


class _NoopClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _NoopChatCompletions()})()


def _agent_without_llm():
    agent = RAgent(model="test-model", max_iterations=2)
    agent.client = _NoopClient()
    return agent


def test_run_conversation_interrupt_keeps_user_and_rolls_back_intermediate_messages():
    agent = _agent_without_llm()
    agent.messages.append({"role": "system", "content": "base"})
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(AgentInterrupted):
        agent.run_conversation("hello", cancel_event=cancel_event)

    assert agent.messages == [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "hello"},
    ]
    assert getattr(agent, _TRUNCATED_FLAG) is False


def test_continue_after_truncation_interrupt_rolls_back_resume_instruction():
    agent = _agent_without_llm()
    agent.messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "previous task"},
        {"role": "assistant", "content": "partial"},
    ]
    setattr(agent, _TRUNCATED_FLAG, True)
    before = list(agent.messages)
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(AgentInterrupted):
        agent.continue_after_truncation(2, cancel_event=cancel_event)

    assert agent.messages == before
    assert getattr(agent, _TRUNCATED_FLAG) is False
