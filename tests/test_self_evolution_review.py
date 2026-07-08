import json
from types import SimpleNamespace

from core.agent import RAgent
from tools.self_evolution_tool import self_evolution_review


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


def _response(message):
    return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=message)])


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def test_tool_call_guard_denies_non_whitelisted_tool(monkeypatch):
    agent = RAgent(model="test", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("run_command", {"command": "echo unsafe"})])),
        _response(_message(content="done", tool_calls=None)),
    ])

    result = agent.run_conversation(
        "review",
        tool_call_guard=lambda name, args: "DENIED" if name == "run_command" else None,
    )

    assert result == "done"
    tool_msgs = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert tool_msgs
    assert tool_msgs[0]["content"] == "DENIED"


def test_self_evolution_heuristic_review_writes_log(tmp_path, monkeypatch):
    import tools.self_evolution_tool as tool

    monkeypatch.setattr(tool, "LOG_DIR", tmp_path)
    result = json.loads(self_evolution_review(
        messages_snapshot=[{"role": "user", "content": "以后请保持简洁，这是一条用户偏好"}],
        mode="heuristic",
        dry_run=True,
        use_forked_agent=False,
    ))

    assert result["success"] is True
    assert result["suggestions"][0]["target"] == "memory"
    assert (tmp_path / "latest_review.json").exists()


def test_self_evolution_forked_review_dry_run_blocks_memory_write(monkeypatch, tmp_path):
    import tools.self_evolution_tool as tool

    monkeypatch.setattr(tool, "LOG_DIR", tmp_path)

    class FakeReviewAgent:
        def __init__(self, *args, **kwargs):
            self.messages = []

        def run_conversation(self, user_message, system_message=None, exclude_tools=None, tool_call_guard=None, **kwargs):
            denial = tool_call_guard(
                "memory",
                json.dumps({"action": "add", "target": "user", "content": "用户偏好：简洁"}, ensure_ascii=False),
            )
            self.messages.append({"role": "tool", "name": "memory", "content": denial})
            return json.dumps({"summary": "checked", "actions_taken": [], "suggestions": ["would save memory"]}, ensure_ascii=False)

    monkeypatch.setattr("core.agent.RAgent", FakeReviewAgent)
    result = json.loads(self_evolution_review(
        messages_snapshot=[{"role": "user", "content": "以后请保持简洁"}],
        mode="background_review",
        dry_run=True,
        use_forked_agent=True,
        max_iterations=2,
    ))

    assert result["success"] is True
    assert result["use_forked_agent"] is True
    assert "dry_run" in json.dumps(result["tool_actions"], ensure_ascii=False)
    assert (tmp_path / "latest_review.json").exists()


def test_self_review_interval_zero_disables_background_review(monkeypatch):
    monkeypatch.setenv("SELF_EVOLUTION_REVIEW_INTERVAL", "0")
    agent = RAgent(model="test", max_iterations=1, enable_self_review=True)
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    called = {"value": False}

    def fake_schedule():
        called["value"] = True

    monkeypatch.setattr(agent, "_schedule_self_evolution_review", fake_schedule)

    assert agent.run_conversation("hello") == "ok"
    assert called["value"] is False


def test_self_review_interval_positive_schedules_heuristic_background(monkeypatch):
    monkeypatch.setenv("SELF_EVOLUTION_REVIEW_INTERVAL", "1")
    agent = RAgent(model="test", max_iterations=1, enable_self_review=True)
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    captured = {}

    def fake_run_review(snapshot):
        captured["snapshot"] = snapshot
        return "{}"

    monkeypatch.setattr(agent, "_run_self_evolution_review", fake_run_review)

    assert agent.run_conversation("hello") == "ok"
    assert agent.shutdown_background_tasks(timeout=1.0) == 0
    assert agent._background_errors == []
    assert any(isinstance(msg, dict) and msg.get("content") == "hello" for msg in captured["snapshot"])


def test_shutdown_background_tasks_sets_shutdown_and_joins():
    agent = RAgent(model="test", max_iterations=1, enable_self_review=False)
    released = []

    def worker():
        while not agent._shutdown_event.is_set():
            import time
            time.sleep(0.01)
        released.append(True)

    thread = __import__("threading").Thread(target=worker, daemon=True)
    with agent._background_lock:
        agent._background_threads.append(thread)
    thread.start()

    assert agent.shutdown_background_tasks(timeout=1.0) == 0
    assert released == [True]
