import json

from tools import todo_tool
from tools import delegate_tool
import core.agent


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

    assert payload[0]["status"] == "success"
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
