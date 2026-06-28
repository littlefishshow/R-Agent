import json
from types import SimpleNamespace

from app_gui.event_bus import ContextEventBus
from app_gui.normalizer import build_llm_request_snapshot, normalize_message
from app_gui.schemas import (
    EVENT_LLM_REQUEST_SNAPSHOT,
    EVENT_MESSAGE_APPENDED,
    EVENT_TOOL_CALL_FINISHED,
    EVENT_TOOL_CALL_STARTED,
    EVENT_TOOL_RESULT_APPENDED,
)
from app_gui.snapshot_store import ContextSnapshotStore
from core.agent import RAgent
from tools.registry import registry


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


def _tool_call(name, arguments):
    return SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message):
    return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=message)])


def test_normalize_message_supports_dict_and_sdk_like_tool_calls():
    sdk_msg = _message(tool_calls=[_tool_call("read_file", {"path": "README.md"})])
    normalized = normalize_message(sdk_msg)

    assert normalized["role"] == "assistant"
    assert normalized["tool_calls"][0]["function"]["name"] == "read_file"
    assert "README.md" in normalized["tool_calls"][0]["function"]["arguments"]

    tool_msg = normalize_message({"role": "tool", "name": "read_file", "tool_call_id": "call1", "content": "hello"})
    assert tool_msg["role"] == "tool"
    assert tool_msg["name"] == "read_file"
    assert tool_msg["tool_call_id"] == "call1"


def test_snapshot_store_payload_ref_and_event_jsonl(tmp_path):
    store = ContextSnapshotStore(tmp_path, preview_chars=5)
    ref = store.put_payload("abcdefghijklmnopqrstuvwxyz")

    assert ref.truncated is True
    assert ref.preview == "abcde"
    assert store.get_payload(ref.id) == "abcdefghijklmnopqrstuvwxyz"

    event = store.append_event({"event_type": "demo", "payload": {"payload_ref": ref.to_dict()}})
    assert event["event_type"] == "demo"
    assert store.list_events(event_type="demo")
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip()


def test_build_llm_request_snapshot_contains_messages_and_tool_schemas():
    snapshot = build_llm_request_snapshot(
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "demo", "parameters": {"type": "object"}}}],
        iteration=3,
    )

    assert snapshot["model"] == "test-model"
    assert snapshot["iteration"] == 3
    assert snapshot["messages"][0]["content"] == "hello"
    assert snapshot["tools"][0]["function"]["name"] == "demo"


def test_agent_event_sink_captures_llm_tool_context(monkeypatch):
    def demo_tool(value):
        return "tool-result:" + value

    registry.register("gui_demo_tool", "demo", {"type": "object", "properties": {"value": {"type": "string"}}}, demo_tool)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["gui_demo_tool"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False)
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("gui_demo_tool", {"value": "abc"})])),
        _response(_message(content="final answer", tool_calls=None)),
    ])
    bus = ContextEventBus(session_id="test-session")

    result = agent.run_conversation("use tool", system_message="system prompt", event_sink=bus)

    assert result == "final answer"
    event_types = [event["event_type"] for event in bus.events]
    assert event_types.count(EVENT_LLM_REQUEST_SNAPSHOT) == 2
    assert EVENT_TOOL_CALL_STARTED in event_types
    assert EVENT_TOOL_CALL_FINISHED in event_types
    assert EVENT_TOOL_RESULT_APPENDED in event_types

    first_request = next(event for event in bus.events if event["event_type"] == EVENT_LLM_REQUEST_SNAPSHOT)
    assert first_request["payload"]["model"] == "test-model"
    assert any(msg["role"] == "system" and msg["content"] == "system prompt" for msg in first_request["payload"]["messages"])
    assert first_request["payload"]["tools"][0]["function"]["name"] == "gui_demo_tool"

    tool_finished = next(event for event in bus.events if event["event_type"] == EVENT_TOOL_CALL_FINISHED)
    assert "tool-result:abc" in tool_finished["payload"]["result"]

    appended = [event["payload"]["message"] for event in bus.events if event["event_type"] == EVENT_MESSAGE_APPENDED]
    assert any(message["role"] == "tool" and "tool-result:abc" in message["content"] for message in appended)
