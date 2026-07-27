import json
from types import SimpleNamespace

import core.agent
from core.agent import RAgent
from tools import todo_tool
from tools import delegate_tool
from tools.registry import registry


def test_todo_manage_session_id_isolates_files(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(todo_tool, "TODO_LIST_DIR", str(tmp_path / "todo_lists"))

    todo_tool.todo_manage("init", json.dumps({"tasks": [{"id": "a", "description": "A"}]}), session_id="s1")
    todo_tool.todo_manage("init", json.dumps({"tasks": [{"id": "b", "description": "B"}]}), session_id="s2")

    s1 = json.loads(todo_tool.todo_manage("view", "{}", session_id="s1"))
    s2 = json.loads(todo_tool.todo_manage("view", "{}", session_id="s2"))

    assert [t["id"] for t in s1["todo_list"]] == ["a"]
    assert [t["id"] for t in s2["todo_list"]] == ["b"]
    assert (tmp_path / "todo_lists" / "todo_list_s1.json").exists()
    assert (tmp_path / "todo_lists" / "todo_list_s2.json").exists()


class _SessionAwareAgent:
    def __init__(self, max_iterations=None, session_id=None):
        self.max_iterations = max_iterations
        self.session_id = session_id
        self.messages = []

    def run_conversation(self, **kwargs):
        text = kwargs["user_message"]
        task_id = text.split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = text.split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage("claim", json.dumps({"id": task_id, "worker_id": worker_id}), session_id=self.session_id)
        todo_tool.todo_manage("update", json.dumps({"id": task_id, "status": "completed", "result": "done"}), session_id=self.session_id)
        return "done"

    def is_truncated(self):
        return False


def test_delegate_task_passes_session_id_to_subagent_and_todo(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(todo_tool, "TODO_LIST_DIR", str(tmp_path / "todo_lists"))
    monkeypatch.setattr(core.agent, "RAgent", _SessionAwareAgent)

    todo_tool.todo_manage("init", json.dumps({"tasks": [{"id": "t1", "description": "T1"}]}), session_id="deleg-s")

    payload = json.loads(delegate_tool.delegate_task(
        tasks=json.dumps([{"task_id": "t1", "goal": "finish"}]),
        max_workers=1,
        default_max_iterations=2,
        session_id="deleg-s",
        default_wall_timeout_seconds=5,
    ))

    assert payload["tasks"][0]["status"] == "success"
    assert "sub_agent_messages" not in payload["tasks"][0]
    assert payload["todo_digest"]["status_counts"]["completed"] == 1
    state = json.loads(todo_tool.todo_manage("view", "{}", session_id="deleg-s"))
    assert state["todo_list"][0]["status"] == "completed"


def test_reap_stale_claims_blocks_expired_task(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(todo_tool, "TODO_LIST_DIR", str(tmp_path / "todo_lists"))
    todo_tool.todo_manage("init", json.dumps({"tasks": [{"id": "old", "description": "old"}]}), session_id="lease")
    todo_tool.todo_manage("claim", json.dumps({"id": "old", "worker_id": "w", "lease_minutes": 0.0001}), session_id="lease")

    import time
    time.sleep(0.02)
    result = json.loads(todo_tool.todo_manage("reap_stale_claims", "{}", session_id="lease"))
    assert result["reaped"] == ["old"]
    state = json.loads(todo_tool.todo_manage("view", "{}", session_id="lease"))
    assert state["todo_list"][0]["status"] == "blocked"
    assert state["todo_list"][0]["metadata"]["blocked_reason"] == "stale_claim_reaped"


class _FailingAgent:
    def __init__(self, max_iterations=None, session_id=None):
        self.max_iterations = max_iterations
        self.session_id = session_id
        self.messages = [{"role": "user", "content": "private subagent context"}]

    def run_conversation(self, **kwargs):
        text = kwargs["user_message"]
        task_id = text.split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = text.split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage("claim", json.dumps({"id": task_id, "worker_id": worker_id}), session_id=self.session_id)
        return "模型请求失败: context_length_exceeded"

    def is_truncated(self):
        return False


def test_delegate_saves_failed_context_by_artifact_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(todo_tool, "TODO_LIST_DIR", str(tmp_path / "todo_lists"))
    monkeypatch.setattr(core.agent, "RAgent", _FailingAgent)
    todo_tool.todo_manage("init", json.dumps({"tasks": [{"id": "bad", "description": "bad"}]}), session_id="ctx-s")

    payload = json.loads(delegate_tool.delegate_task(
        tasks=json.dumps([{"task_id": "bad", "goal": "fail"}]),
        max_workers=1,
        session_id="ctx-s",
        default_wall_timeout_seconds=5,
    ))

    item = payload["tasks"][0]
    assert item["status"] == "error"
    assert "sub_agent_messages" not in item
    assert item["context_artifact_path"]
    assert (tmp_path / item["context_artifact_path"]).exists()
    digest_task = payload["todo_digest"]["tasks"][0]
    assert digest_task["status"] == "blocked"
    assert digest_task["context_artifact_path"] == item["context_artifact_path"]


def test_todo_ready_defaults_to_ids_and_digest_params(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(todo_tool, "TODO_LIST_DIR", str(tmp_path / "todo_lists"))
    long_result = "x" * 50
    todo_tool.todo_manage("init", json.dumps({"tasks": [
        {"id": "done", "description": "Done", "status": "completed", "result": long_result, "metadata": {"context_artifact_path": "sandbox/delegate_contexts/s/a.json"}},
        {"id": "ready", "description": "Ready", "result": "ready-result", "metadata": {"context_artifact_path": "sandbox/delegate_contexts/s/b.json"}},
    ]}), session_id="compact")

    ready_default = json.loads(todo_tool.todo_manage("ready", "{}", session_id="compact"))
    assert ready_default == {"ready_to_execute": ["ready"]}

    ready_with_tasks = json.loads(todo_tool.todo_manage("ready", json.dumps({"include_tasks": True, "include_artifacts": False}), session_id="compact"))
    assert ready_with_tasks["ready_to_execute"] == ["ready"]
    assert ready_with_tasks["tasks"][0]["id"] == "ready"
    assert "claim" not in ready_with_tasks["tasks"][0]
    assert "context_artifact_path" not in ready_with_tasks["tasks"][0]

    digest = json.loads(todo_tool.todo_manage("digest", json.dumps({
        "include_completed": False,
        "result_summary_chars": 10,
        "include_artifacts": False,
    }), session_id="compact"))
    assert [t["id"] for t in digest["tasks"]] == ["ready"]
    assert digest["tasks"][0]["result_summary"] == "ready-resu…"
    assert "context_artifact_path" not in digest["tasks"][0]

class _CapturingExcludeAgent:
    captured_kwargs = None

    def __init__(self, max_iterations=None, session_id=None):
        self.max_iterations = max_iterations
        self.session_id = session_id
        self.messages = []

    def run_conversation(self, **kwargs):
        type(self).captured_kwargs = kwargs
        return "done"

    def is_truncated(self):
        return False


def test_delegate_task_excludes_child_side_effect_tools(monkeypatch):
    _CapturingExcludeAgent.captured_kwargs = None
    monkeypatch.setattr(core.agent, "RAgent", _CapturingExcludeAgent)

    payload = json.loads(delegate_tool.delegate_task(
        tasks=json.dumps([{"goal": "capture exclude tools"}]),
        max_workers=1,
        default_max_iterations=1,
        default_wall_timeout_seconds=5,
        allowed_tools=["web_search", "delegate_task", "read_file"],
    ))

    assert payload["tasks"][0]["status"] == "success"
    excluded = set(_CapturingExcludeAgent.captured_kwargs["exclude_tools"])
    assert {
        "delegate_task",
        "memory",
        "speak_text",
        "text_to_speech",
        "self_evolution_review",
    }.issubset(excluded)
    assert _CapturingExcludeAgent.captured_kwargs["allowed_tools"] == {"web_search", "read_file"}


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


def test_agent_inherits_current_session_when_todo_manage_gets_default(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(todo_tool, "TODO_LIST_DIR", str(tmp_path / "todo_lists"))
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False, session_id="learn_x")
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("todo_manage", {
            "action": "init",
            "payload": json.dumps({"tasks": [{"id": "learn-task", "description": "Learn"}]}),
            "session_id": "default",
        })])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("init todo") == "done"
    state = json.loads(todo_tool.todo_manage("view", "{}", session_id="learn_x"))
    assert [t["id"] for t in state["todo_list"]] == ["learn-task"]
    legacy = json.loads(todo_tool.todo_manage("view", "{}", session_id="default"))
    assert legacy["todo_list"] == []


def test_agent_inherits_current_session_when_delegate_task_gets_default(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(todo_tool, "TODO_LIST_DIR", str(tmp_path / "todo_lists"))
    monkeypatch.setattr(core.agent, "RAgent", _SessionAwareAgent)

    todo_tool.todo_manage(
        "init",
        json.dumps({"tasks": [{"id": "tdel", "description": "Delegated"}]}),
        session_id="learn_x",
    )

    parent = RAgent(model="test-model", max_iterations=3, enable_self_review=False, session_id="learn_x")
    parent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("delegate_task", {
            "tasks": json.dumps([{"task_id": "tdel", "goal": "finish"}]),
            "max_workers": 1,
            "default_max_iterations": 2,
            "session_id": "default",
            "default_wall_timeout_seconds": 5,
        })])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert parent.run_conversation("delegate", event_sink=lambda *args, **kwargs: None) == "done"
    state = json.loads(todo_tool.todo_manage("view", "{}", session_id="learn_x"))
    assert state["todo_list"][0]["status"] == "completed"
    legacy = json.loads(todo_tool.todo_manage("view", "{}", session_id="default"))
    assert legacy["todo_list"] == []

