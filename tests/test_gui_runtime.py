import json
from types import SimpleNamespace

import pytest

from app_gui.runtime import AgentRuntimeService
from app_gui.schemas import EVENT_SESSION_STARTED, EVENT_SYSTEM_PROMPT_BUILT, EVENT_USER_INPUT_RECEIVED
from core.agent import RAgent


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


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message):
    return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=message)])


def test_runtime_create_session_emits_prompt_and_memory_events(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = AgentRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="s1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    event_types = [event["event_type"] for event in session.event_bus.events]
    assert EVENT_SESSION_STARTED in event_types
    assert EVENT_SYSTEM_PROMPT_BUILT in event_types
    prompt_event = next(event for event in session.event_bus.events if event["event_type"] == EVENT_SYSTEM_PROMPT_BUILT)
    payload_id = prompt_event["payload"]["payload_ref"]["id"]
    assert "R-Agent" in session.store.get_payload(payload_id)


def test_runtime_send_message_sync_records_events(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("hello from gui"))]))
    service = AgentRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="s2", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    result = service.send_message("s2", "hi", background=False)

    assert result["status"] == "completed"
    assert result["response"] == "hello from gui"
    assert session.last_response == "hello from gui"
    assert any(event["event_type"] == EVENT_USER_INPUT_RECEIVED for event in session.event_bus.events)
    assert session.store.list_events()


def test_runtime_interrupt_sets_cancel_event(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = AgentRuntimeService(store_root=tmp_path)
    service.create_session(session_id="s3", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    result = service.interrupt("s3")

    assert result["interrupted"] is True
    assert service.get_session("s3").cancel_event.is_set()


def test_server_module_imports_without_fastapi_or_creates_app_when_available():
    import app_gui.server as server

    if server.app is None:
        with pytest.raises(RuntimeError, match="fastapi"):
            server.create_app(AgentRuntimeService())
    else:
        assert server.app.title == "R-Agent Cockpit API"


def test_runtime_resources_include_tools_skills_memory_and_reviews(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = AgentRuntimeService(store_root=tmp_path)
    service.create_session(session_id="s4", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    resources = service.resources("s4")

    assert resources["session_id"] == "s4"
    assert "tools" in resources and "schemas" in resources["tools"]
    assert "skills" in resources and resources["skills"]["list_ref"]["id"]
    assert "memory" in resources and resources["memory"]["frozen_ref"]["id"]
    assert "self_evolution" in resources


def test_runtime_current_model_context_is_simplified_next_turn_view(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = AgentRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="s5", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    context = service.current_model_context("s5")

    assert context["session_id"] == "s5"
    ids = [module["id"] for module in context["modules"]]
    assert "system_prompt" in ids
    assert "messages" in ids
    assert "tool_schemas" in ids
    assert "skills_note" in ids
    assert any(module["visible_to_model"] for module in context["modules"])
