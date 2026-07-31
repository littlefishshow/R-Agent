import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_gui.runtime import AgentRuntimeService
from app_gui.runtime import LEARNING_ALLOWED_TOOLS, LearningRuntimeService
from app_gui.schemas import (
    EVENT_ERROR,
    EVENT_LLM_REQUEST_SNAPSHOT,
    EVENT_SESSION_STARTED,
    EVENT_SYSTEM_PROMPT_BUILT,
    EVENT_USER_INPUT_RECEIVED,
)
from app_gui.file_workspace import FileWorkspace
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
    message_events = [event for event in session.event_bus.events if event["event_type"] == "message_appended"]
    assert any(event["payload"].get("message_index") == 0 for event in message_events)
    assert session.store.list_events()


def test_runtime_interrupt_sets_cancel_event(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = AgentRuntimeService(store_root=tmp_path)
    service.create_session(session_id="s3", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    result = service.interrupt("s3")

    assert result["interrupted"] is True
    assert service.get_session("s3").cancel_event.is_set()


def test_runtime_interrupt_releases_session_without_waiting(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    def fake_run(self, user_message, **kwargs):
        if user_message == "first":
            started.set()
            release.wait(timeout=2)
            return "late"
        return "second-ok"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = AgentRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="interrupt-fast", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    session.send_message("first", background=True)
    assert started.wait(timeout=1)
    t0 = time.monotonic()
    result = session.interrupt()

    assert time.monotonic() - t0 < 0.5
    assert result["still_running"] is True
    assert session.running is False
    second = session.send_message("second", background=False)
    assert second["response"] == "second-ok"
    release.set()


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


def test_learning_runtime_uses_restricted_tools(monkeypatch, tmp_path):
    seen = {}

    def fake_run(self, user_message, **kwargs):
        seen["user_message"] = user_message
        seen["allowed_tools"] = kwargs.get("allowed_tools")
        self.messages.append({"role": "assistant", "content": "learning answer"})
        return "learning answer"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="learn1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    result = session.send_message("什么是反向传播？", background=False)

    assert result["response"] == "learning answer"
    assert seen["user_message"] == "什么是反向传播？"
    assert seen["allowed_tools"] == LEARNING_ALLOWED_TOOLS
    state = session.state()
    assert state["mode"] == "learning"
    assert state["title"] == "什么是反向传播？"
    assert "run_command" not in state["allowed_tools"]
    assert "web_search" in state["allowed_tools"]
    assert state["tools_enabled"] is True


def test_learning_runtime_restores_saved_sessions(monkeypatch, tmp_path):
    def fake_run(self, user_message, **kwargs):
        event_sink = kwargs.get("event_sink")
        for message in [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "restored answer"},
        ]:
            self.messages.append(message)
            if event_sink is not None:
                event_sink.emit("message_appended", {"message": message, "message_index": len(self.messages) - 1})
        return "restored answer"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="saved-learn", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    session.send_message("需要恢复的问题", background=False)
    before_events = len(session.store.events)

    restored_service = LearningRuntimeService(store_root=tmp_path)
    restored = restored_service.get_session("saved-learn")

    assert "saved-learn" in restored_service.list_sessions(account_id="default")
    assert restored.root_question == "需要恢复的问题"
    assert restored.last_question == "需要恢复的问题"
    assert any(message.get("content") == "需要恢复的问题" for message in restored.agent.messages)
    assert any(message.get("content") == "restored answer" for message in restored.agent.messages)
    assert len(restored.store.events) == before_events
    assert restored.event_bus.events == restored.store.events


def test_learning_runtime_restores_payload_refs_inside_tool_calls(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="saved-tool-call", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    ref = session.store.put_payload(json.dumps({"path": "README.md"}, ensure_ascii=False))
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_read_file",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": {"payload_ref": ref.to_dict()},
            },
        }],
    }
    session.store.append_event({
        "event_type": "message_appended",
        "payload": {"message": message, "message_index": 0},
    })

    restored_service = LearningRuntimeService(store_root=tmp_path)
    restored = restored_service.get_session("saved-tool-call")
    arguments = restored.agent.messages[0]["tool_calls"][0]["function"]["arguments"]

    assert isinstance(arguments, str)
    assert json.loads(arguments) == {"path": "README.md"}


def test_learning_runtime_nests_children_on_disk(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    root = service.create_session(session_id="root1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    child = service.create_session(
        session_id="child1",
        parent_session_id="root1",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
    )
    grandchild = service.create_session(
        session_id="grand1",
        parent_session_id="child1",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
    )

    assert Path(root.store.base_dir) == Path(tmp_path) / "root1"
    assert Path(child.store.base_dir) == Path(tmp_path) / "root1" / "children" / "child1"
    assert Path(grandchild.store.base_dir) == Path(tmp_path) / "root1" / "children" / "child1" / "children" / "grand1"
    assert (Path(grandchild.store.base_dir) / "context.json").exists()


def test_learning_runtime_restores_nested_tree(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    service.create_session(session_id="root1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    service.create_session(session_id="child1", parent_session_id="root1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    service.create_session(session_id="grand1", parent_session_id="child1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    restored = LearningRuntimeService(store_root=tmp_path)
    assert set(["root1", "child1", "grand1"]).issubset(set(restored.sessions))
    assert restored.get_session("child1").parent_session_id == "root1"
    assert restored.get_session("grand1").parent_session_id == "child1"
    # Nested base_dir must be reconstructed under the parent, not flattened.
    assert Path(restored.get_session("grand1").store.base_dir) == Path(tmp_path) / "root1" / "children" / "child1" / "children" / "grand1"


def test_delete_subtree_removes_nested_folders(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    root = service.create_session(session_id="root1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    service.create_session(session_id="child1", parent_session_id="root1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    root_dir = Path(root.store.base_dir)
    assert root_dir.exists()

    result = service.delete_subtree("root1")
    assert set(result["deleted"]) == {"root1", "child1"}
    assert not root_dir.exists()
    assert "child1" not in service.sessions


def test_setback_prunes_children_anchored_after_index(monkeypatch, tmp_path):
    def fake_run(self, user_message, **kwargs):
        event_sink = kwargs.get("event_sink")
        for message in [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "answer"},
        ]:
            self.messages.append(message)
            if event_sink is not None:
                event_sink.emit("message_appended", {"message": message, "message_index": len(self.messages) - 1})
        return "answer"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="parent1", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    parent.send_message("第一个问题", background=False)  # messages: system, user, assistant
    # A selection branch created now anchors at the current message count.
    branch = service.branch_from_selection(
        "parent1",
        selected_text="重点",
        action="explain",
        source_context={"kind": "chat"},
        background=False,
    )
    branch_id = branch["session_id"]
    assert branch_id in service.sessions
    branch_dir = Path(service.get_session(branch_id).store.base_dir)
    assert branch_dir.exists()

    # Find the first user message index and set back to it (before the branch anchor).
    user_index = next(i for i, m in enumerate(parent.agent.messages) if m.get("role") == "user")
    result = service.setback_to_message("parent1", user_index)

    assert branch_id in result["deleted"]
    assert branch_id not in service.sessions
    assert not branch_dir.exists()


def test_learning_runtime_can_disable_tool_context(monkeypatch, tmp_path):
    seen = {}

    def fake_run(self, user_message, **kwargs):
        seen["allowed_tools"] = kwargs.get("allowed_tools")
        self.messages.append({"role": "assistant", "content": "no tools"})
        return "no tools"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="learn-tools-off", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    state = service.set_tools_enabled("learn-tools-off", False)
    result = session.send_message("不使用工具回答", background=False)
    context = session.current_model_context()
    tools_module = next(module for module in context["modules"] if module["id"] == "tool_schemas")

    assert result["response"] == "no tools"
    assert seen["allowed_tools"] == set()
    assert state["tools_enabled"] is False
    assert state["allowed_tools"] == []
    assert tools_module["items"] == []
    assert tools_module["visible_to_model"] is False
    assert session.store.metadata["tools_enabled"] is False


def test_learning_context_filters_tool_schema(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="learn2", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    context = session.current_model_context()
    tools_module = next(module for module in context["modules"] if module["id"] == "tool_schemas")
    names = {schema["function"]["name"] for schema in tools_module["items"]}

    assert names
    assert names <= LEARNING_ALLOWED_TOOLS
    assert "run_command" not in names


def test_learning_selection_branch_copies_parent_context_for_explain(monkeypatch, tmp_path):
    seen = []

    def fake_run(self, user_message, **kwargs):
        seen.append(user_message)
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": "answer"})
        return "answer"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    parent.send_message("先解释梯度下降", background=False)

    result = service.branch_from_selection(
        "parent",
        selected_text="learning rate",
        action="explain",
        background=False,
    )

    assert result["parent_session_id"] == "parent"
    assert result["selection"]["action"] == "explain"
    child = service.get_session(result["session_id"])
    assert any(message.get("content") == "先解释梯度下降" for message in child.agent.messages)
    assert "learning rate" in seen[-1]
    assert "解释" in seen[-1]


def test_learning_selection_branch_includes_file_source_context(monkeypatch, tmp_path):
    seen = []

    def fake_run(self, user_message, **kwargs):
        seen.append(user_message)
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": "answer"})
        return "answer"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="source-parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    parent.send_message("父上下文", background=False)

    result = service.branch_from_selection(
        "source-parent",
        selected_text="a selected paragraph",
        action="explain",
        source_context={"kind": "pdf", "path": "outputs/papers/a.pdf", "location": "page 3"},
        background=False,
    )

    assert result["selection"]["source_context"]["path"] == "outputs/papers/a.pdf"
    child = service.get_session(result["session_id"])
    assert child.selection["selected_text"] == "a selected paragraph"
    assert child.state()["selection"]["source_context"]["location"] == "page 3"
    assert child.store.metadata["selection"]["source_context"]["path"] == "outputs/papers/a.pdf"
    assert "【来源类型】pdf" in seen[-1]
    assert "【来源文件】outputs/papers/a.pdf" in seen[-1]
    assert "【来源位置】page 3" in seen[-1]


def test_learning_translate_branch_uses_only_selected_text(monkeypatch, tmp_path):
    seen = []

    def fake_run(self, user_message, **kwargs):
        seen.append(user_message)
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": "translated"})
        return "translated"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="parent2", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    parent.send_message("这段父上下文不应进入翻译", background=False)

    result = service.branch_from_selection(
        "parent2",
        selected_text="learning rate",
        action="translate",
        target_language="英语",
        background=False,
    )

    child = service.get_session(result["session_id"])
    assert result["selection"]["target_language"] == "英语"
    assert "learning rate" in seen[-1]
    assert "目标语言" in seen[-1]
    assert "这段父上下文不应进入翻译" not in seen[-1]
    assert not any(message.get("content") == "这段父上下文不应进入翻译" for message in child.agent.messages)


def test_learning_selection_note_can_save_without_model(monkeypatch, tmp_path):
    calls = []

    def fake_run(self, user_message, **kwargs):
        calls.append(user_message)
        return "should not run"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="note-parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    result = service.save_selection_note(
        "note-parent",
        selected_text="important paragraph",
        note_text="my handwritten note",
        source_context={"kind": "markdown", "path": "read_paper/a.md", "location": "lines 3-5"},
    )
    child = service.get_session(result["session_id"])

    assert calls == []
    assert result["node_kind"] == "note"
    assert result["selection"]["action"] == "note"
    assert result["selection"]["note_text"] == "my handwritten note"
    assert "my handwritten note" in child.agent.messages[0]["content"]
    assert "important paragraph" in child.agent.messages[0]["content"]
    assert "lines 3-5" in child.agent.messages[0]["content"]


def test_learning_selection_note_can_send_to_model(monkeypatch, tmp_path):
    seen = []

    def fake_run(self, user_message, **kwargs):
        seen.append(user_message)
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": "note answer"})
        return "note answer"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="note-model-parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    parent.send_message("父上下文", background=False)

    result = service.branch_from_selection(
        "note-model-parent",
        selected_text="selected claim",
        action="note",
        note_text="check this claim later",
        source_context={"kind": "pdf", "path": "papers/a.pdf", "location": "page 2"},
        background=False,
    )

    assert result["selection"]["action"] == "note"
    assert result["selection"]["note_text"] == "check this claim later"
    assert "【我的手写笔记】" in seen[-1]
    assert "check this claim later" in seen[-1]
    assert "【来源文件】papers/a.pdf" in seen[-1]


def test_learning_branch_session_copies_parent_context(monkeypatch, tmp_path):
    seen = []

    def fake_run(self, user_message, **kwargs):
        seen.append(user_message)
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": "answer"})
        return "answer"

    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", fake_run)
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="branch-parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    parent.send_message("父分支上下文", background=False)

    result = service.branch_session("branch-parent", question="独立新问题", background=False)
    child = service.get_session(result["session_id"])

    assert result["parent_session_id"] == "branch-parent"
    assert any(message.get("content") == "父分支上下文" for message in child.agent.messages)
    assert "独立新问题" in seen[-1]


def test_learning_file_roots_are_account_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)

    a1 = service.get_or_create_file_root(account_id="a", file_path="papers/a.pdf")
    a2 = service.get_or_create_file_root(account_id="a", file_path="papers/a.pdf")
    b1 = service.get_or_create_file_root(account_id="b", file_path="papers/a.pdf")
    child = service.branch_from_selection(
        a1.session_id,
        selected_text="selection",
        action="explain",
        source_context={"kind": "pdf", "path": "papers/a.pdf", "location": "page 1"},
        background=False,
    )

    assert a1 is a2
    assert a1.session_id != b1.session_id
    assert a1.state()["node_kind"] == "file_root"
    assert a1.state()["account_id"] == "a"
    assert b1.state()["account_id"] == "b"
    assert child["parent_session_id"] == a1.session_id
    assert service.get_session(child["session_id"]).account_id == "a"
    assert set(service.list_sessions(account_id="a")) == {a1.session_id, child["session_id"]}
    assert set(service.list_sessions(account_id="b")) == {b1.session_id}
    assert service.account_roots("a")["nodes"][0]["child_count"] == 1
    assert "last_response" not in service.account_roots("a")["nodes"][0]
    assert "token_usage_breakdown" not in service.account_roots("a")["nodes"][0]
    child_node = service.child_nodes(a1.session_id)["nodes"][0]
    assert child_node["session_id"] == child["session_id"]
    assert child_node["selection"]["selected_text"] == "selection"


def test_learning_setback_and_fork_rewrite_context_file(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    session = service.create_session(session_id="rewrite", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    session.agent.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]
    session.store.append_event({
        "event_id": "legacy-user-input",
        "event_type": EVENT_USER_INPUT_RECEIVED,
        "session_id": session.session_id,
        "source": "test",
        "created_at": time.time(),
        "payload": {"content": "stale fallback user input"},
    })
    session.store.append_event({
        "event_id": "legacy-llm-request",
        "event_type": EVENT_LLM_REQUEST_SNAPSHOT,
        "session_id": session.session_id,
        "source": "test",
        "created_at": time.time(),
        "payload": {"messages": ["stale run"]},
    })
    session.store.append_event({
        "event_id": "legacy-error",
        "event_type": EVENT_ERROR,
        "session_id": session.session_id,
        "source": "test",
        "created_at": time.time(),
        "payload": {"error": "stale error"},
    })
    session.store.replace_message_events(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "second"}],
        session_id=session.session_id,
    )
    event_types = [event["event_type"] for event in session.store.list_events()]
    assert EVENT_USER_INPUT_RECEIVED not in event_types
    assert EVENT_LLM_REQUEST_SNAPSHOT not in event_types
    assert EVENT_ERROR not in event_types
    assert all(
        event.get("source") == "context_rewrite"
        for event in session.store.list_events()
        if event["event_type"] == "message_appended"
    )

    forked = service.fork_from_message("rewrite", 3)
    child = service.get_session(forked["session"]["session_id"])
    assert forked["draft"] == "second"
    assert [m["content"] for m in child.agent.messages] == ["sys", "first", "answer"]

    setback = service.setback_to_message("rewrite", 3)
    assert setback["draft"] == "second"
    assert [m["content"] for m in session.agent.messages] == ["sys", "first", "answer"]
    assert "second" not in session.store.context_path.read_text(encoding="utf-8")
    # The fork was anchored at message index 3; setting the parent back to
    # index 3 removes that anchor, so the orphaned branch is pruned.
    assert forked["session"]["session_id"] in setback["deleted"]
    with pytest.raises(KeyError):
        service.get_session(forked["session"]["session_id"])


def test_learning_branch_validates_before_creating_child(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    service.create_session(session_id="branch-parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    before = set(service.sessions)
    with pytest.raises(ValueError, match="question is empty"):
        service.branch_session("branch-parent", question="   ", background=False)
    assert set(service.sessions) == before

    with pytest.raises(KeyError):
        service.branch_session("missing-parent", question="有效问题", background=False)
    assert set(service.sessions) == before

    with pytest.raises(KeyError):
        service.branch_from_selection("missing-parent", selected_text="text", action="explain", background=False)
    assert set(service.sessions) == before


def test_learning_delete_subtree_removes_descendants(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    root = service.create_session(session_id="root", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    child = service.create_session(session_id="child", parent_session_id="root", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    grand = service.create_session(session_id="grand", parent_session_id="child", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))

    result = service.delete_subtree("child")

    assert set(result["deleted"]) == {"child", "grand"}
    assert service.get_session("root") is root
    with pytest.raises(KeyError):
        service.get_session(child.session_id)
    with pytest.raises(KeyError):
        service.get_session(grand.session_id)


def test_learning_delete_sessions_for_workspace_path_prunes_minimal_roots(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    store_root = tmp_path / "learning_context"
    service = LearningRuntimeService(store_root=store_root)
    file_root = service.create_session(
        session_id="file-root",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
        node_kind="file_root",
        file_path="papers/a.pdf",
    )
    child = service.create_session(
        session_id="file-child",
        parent_session_id="file-root",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
        node_kind="selection",
        file_path="papers/a.pdf",
        selection={"source_context": {"path": "papers/a.pdf", "location": "page 1"}},
    )
    note = service.create_session(
        session_id="note",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
        node_kind="note",
        selection={"source_context": {"path": "notes/a.md"}},
    )
    sibling = service.create_session(
        session_id="sibling",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
        node_kind="file_root",
        file_path="papers/ab.pdf",
    )

    result = service.delete_sessions_for_workspace_path("papers/a.pdf", is_directory=False)

    assert result["deleted_learning_sessions"] == ["file-root", "file-child"]
    assert not Path(file_root.store.base_dir).exists()
    with pytest.raises(KeyError):
        service.get_session(child.session_id)
    assert service.get_session(note.session_id) is note
    assert service.get_session(sibling.session_id) is sibling
    assert Path(note.store.base_dir).is_relative_to(store_root)


def test_learning_delete_sessions_for_workspace_directory_matches_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path / "learning_context")
    nested = service.create_session(
        session_id="nested",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
        node_kind="file_root",
        file_path="papers/project/a.pdf",
    )
    exact_dir = service.create_session(
        session_id="exact-dir",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
        node_kind="file_root",
        file_path="papers/project",
    )
    outside_prefix = service.create_session(
        session_id="outside-prefix",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
        node_kind="file_root",
        file_path="papers/projectile/a.pdf",
    )

    result = service.delete_sessions_for_workspace_path("papers/project", is_directory=True)

    assert set(result["deleted_learning_sessions"]) == {nested.session_id, exact_dir.session_id}
    assert service.get_session(outside_prefix.session_id) is outside_prefix


def test_learning_runtime_cleanup_saved_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    root = tmp_path / "learn"
    import os
    for index in range(4):
        session_dir = root / f"old-{index}"
        session_dir.mkdir(parents=True)
        # A real session dir must carry a context.json to be counted/cleaned.
        (session_dir / "context.json").write_text(
            json.dumps({"version": 1, "metadata": {}, "events": [], "payloads": {}}),
            encoding="utf-8",
        )
        mtime = time.time() - (index + 1) * 100
        os.utime(session_dir / "context.json", (mtime, mtime))
        os.utime(session_dir, (mtime, mtime))

    service = LearningRuntimeService(store_root=root, max_saved_sessions=2, max_session_age_hours=0)

    remaining = sorted(path.name for path in root.iterdir() if path.is_dir())
    assert len(remaining) == 2
    assert remaining == ["old-0", "old-1"]
    assert service.max_saved_sessions == 2


def test_file_workspace_initializes_papers_and_confines_paths(tmp_path):
    workspace = FileWorkspace(tmp_path / "files")

    listing = workspace.list_dir("")

    assert listing["root_name"] == "outputs"
    assert any(item["name"] == "papers" and item["type"] == "directory" for item in listing["items"])
    assert not any(item["name"] == "read_paper" for item in listing["items"])
    with pytest.raises(ValueError):
        workspace.list_dir("../outside")


def test_file_workspace_upload_copy_delete_and_pdf_metadata(tmp_path):
    workspace = FileWorkspace(tmp_path / "files")

    item = workspace.write_base64_file("papers", "paper.pdf", "JVBERi0xLjQK")
    copied = workspace.copy("papers/paper.pdf", "")
    root_listing = workspace.list_dir("")

    assert item["path"] == "papers/paper.pdf"
    assert item["is_pdf"] is True
    assert copied["name"].startswith("paper")
    assert any(entry["name"] == copied["name"] for entry in root_listing["items"])
    assert workspace.get_file("papers/paper.pdf").name == "paper.pdf"
    assert workspace.delete("papers/paper.pdf")["deleted"] == "papers/paper.pdf"
    with pytest.raises(FileNotFoundError):
        workspace.get_file("papers/paper.pdf")


def test_file_workspace_tree_and_markdown_edit(tmp_path):
    workspace = FileWorkspace(tmp_path / "files")
    (tmp_path / "files" / "read_paper").mkdir()
    note_path = tmp_path / "files" / "read_paper" / "note.md"
    note_path.write_text("# Note\n\nHello", encoding="utf-8")

    tree = workspace.tree(["", "read_paper"])
    text = workspace.read_text_file("read_paper/note.md")
    updated = workspace.write_text_file("read_paper/note.md", "# Updated")

    assert tree["root"]["name"] == "outputs"
    read_paper = next(item for item in tree["root"]["children"] if item["name"] == "read_paper")
    assert read_paper["children"][0]["name"] == "note.md"
    assert read_paper["children"][0]["is_markdown"] is True
    assert text["content"].startswith("# Note")
    assert updated["is_markdown"] is True
    assert note_path.read_text(encoding="utf-8") == "# Updated"


def test_file_workspace_tree_lazy_loads_unexpanded_dirs(tmp_path):
    workspace = FileWorkspace(tmp_path / "files")
    (tmp_path / "files" / "read_paper").mkdir()
    note_path = tmp_path / "files" / "read_paper" / "note.md"
    note_path.write_text("# Note", encoding="utf-8")

    tree = workspace.tree([""])

    read_paper = next(item for item in tree["root"]["children"] if item["name"] == "read_paper")
    assert read_paper["children"] == []
    assert read_paper["has_children"] is True


def test_file_workspace_extracts_pdf_text(tmp_path):
    fitz = pytest.importorskip("fitz")
    workspace = FileWorkspace(tmp_path / "files")
    pdf_path = tmp_path / "files" / "papers" / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF text")
    doc.save(str(pdf_path))
    doc.close()

    result = workspace.extract_pdf_text("papers/text.pdf")

    assert result["name"] == "text.pdf"
    assert result["page_count"] == 1
    assert "Hello PDF text" in result["pages"][0]["text"]
    assert result["pages"][0]["words"]
    assert result["pages"][0]["lines"]
    png = workspace.render_pdf_page_png("papers/text.pdf", 1, zoom=1.0)
    assert png.startswith(b"\x89PNG")

def test_learning_child_nodes_use_stable_source_order(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    service = LearningRuntimeService(store_root=tmp_path)
    parent = service.create_session(session_id="stable-parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    older = service.create_session(
        session_id="stable-child-later-clicked",
        parent_session_id=parent.session_id,
        source_message_index=1,
        title="later clicked",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
    )
    newer = service.create_session(
        session_id="stable-child-earlier-source",
        parent_session_id=parent.session_id,
        source_message_index=0,
        title="earlier source",
        agent=RAgent(model="test", max_iterations=1, enable_self_review=False),
    )
    for _ in range(3):
        older.event_bus.emit("message_appended", {"message": {"role": "user", "content": "clicked"}})

    nodes = service.child_nodes(parent.session_id)["nodes"]
    assert [node["session_id"] for node in nodes[:2]] == [newer.session_id, older.session_id]

def test_learning_session_state_reads_session_scoped_todo_board(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.chdir(tmp_path)
    from tools.todo_tool import todo_manage

    service = LearningRuntimeService(store_root=tmp_path / "learning")
    first = service.create_session(session_id="todo-visible-a", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    second = service.create_session(session_id="todo-visible-b", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    todo_manage(
        "init",
        '{"tasks":[{"id":"a1","description":"first board only"}]}',
        session_id=first.session_id,
    )
    todo_manage(
        "init",
        '{"tasks":[{"id":"b1","description":"second board only"}]}',
        session_id=second.session_id,
    )

    first_board = first.state()["todo_board"]
    second_board = second.state()["todo_board"]
    assert first_board["exists"] is True
    assert second_board["exists"] is True
    assert first_board["path"].endswith("todo_list_todo-visible-a.json")
    assert second_board["path"].endswith("todo_list_todo-visible-b.json")
    assert first_board["tasks"][0]["description"] == "first board only"
    assert second_board["tasks"][0]["description"] == "second board only"
    assert first_board["tasks"][0]["description"] != second_board["tasks"][0]["description"]



def test_gui_sessions_never_use_default_todo_scope(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))

    gui = AgentRuntimeService(store_root=tmp_path / "gui")
    gui_session = gui.create_session(session_id="default", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    assert gui_session.session_id != "default"
    assert gui_session.session_id.startswith("gui_")
    assert gui_session.agent.session_id == gui_session.session_id

    learning = LearningRuntimeService(store_root=tmp_path / "learn")
    root = learning.create_session(session_id="default", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    assert root.session_id != "default"
    assert root.session_id.startswith("learn_")
    assert root.agent.session_id == root.session_id


def test_learning_child_session_can_be_window_scoped_and_reports_parent_todo(monkeypatch, tmp_path):
    monkeypatch.setattr("app_gui.runtime.config.create_llm_client", lambda: _FakeClient([_response(_message("ok"))]))
    monkeypatch.setattr(RAgent, "run_conversation", lambda self, user_message, **kwargs: "child answer")
    monkeypatch.setattr("app_gui.runtime.PROJECT_ROOT", tmp_path)
    todo_dir = tmp_path / "sandbox" / "todo_lists"
    todo_dir.mkdir(parents=True)
    (todo_dir / "todo_list_parent.json").write_text(json.dumps({
        "version": 2,
        "tasks": [{"id": "p1", "description": "parent task", "status": "pending"}],
    }), encoding="utf-8")

    service = LearningRuntimeService(store_root=tmp_path / "contexts")
    parent = service.create_session(session_id="parent", agent=RAgent(model="test", max_iterations=1, enable_self_review=False))
    child_state = service.branch_session("parent", question="child question", child_session_id="win_child", background=False)

    assert child_state["session_id"] == "win_child"
    assert child_state["parent_session_id"] == "parent"
    assert child_state["parent_todo_board"]["session_id"] == "parent"
    assert child_state["parent_todo_board"]["tasks"][0]["id"] == "p1"
    assert service.get_session("win_child").agent.session_id == "win_child"
