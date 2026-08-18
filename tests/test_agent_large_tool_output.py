import json
from pathlib import Path
from types import SimpleNamespace

from core.agent import RAgent
from tools.registry import registry
from core.context.tool_result_storage import PERSISTED_OUTPUT_TAG


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


def test_agent_persists_large_tool_result(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def huge_tool():
        return "A" * 90_000

    registry.register("huge_tool_for_test", "huge", {"type": "object", "properties": {}}, huge_tool)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["huge_tool_for_test"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False)
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("huge_tool_for_test", {})])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("use huge") == "done"
    tool_messages = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert PERSISTED_OUTPUT_TAG in tool_messages[0]["content"]
    artifacts = list((Path("sandbox") / "tool_outputs").glob("*.txt"))
    assert artifacts
    assert "A" * 100 in artifacts[0].read_text(encoding="utf-8")



def test_agent_enforces_aggregate_tool_turn_budget(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("R_AGENT_TOOL_RESULT_MAX_CHARS", "100000")
    monkeypatch.setenv("R_AGENT_TOOL_TURN_BUDGET_CHARS", "120000")
    monkeypatch.setenv("R_AGENT_TOOL_PREVIEW_CHARS", "1000")

    def medium_a():
        return "A" * 79_000

    def medium_b():
        return "B" * 78_000

    def medium_c():
        return "C" * 77_000

    registry.register("medium_a_for_turn_budget_test", "medium a", {"type": "object", "properties": {}}, medium_a)
    registry.register("medium_b_for_turn_budget_test", "medium b", {"type": "object", "properties": {}}, medium_b)
    registry.register("medium_c_for_turn_budget_test", "medium c", {"type": "object", "properties": {}}, medium_c)
    allowed = [
        registry._tools["medium_a_for_turn_budget_test"]["schema"],
        registry._tools["medium_b_for_turn_budget_test"]["schema"],
        registry._tools["medium_c_for_turn_budget_test"]["schema"],
    ]
    monkeypatch.setattr(registry, "get_all_schemas", lambda: allowed)
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False)
    agent.client = _FakeClient([
        _response(_message(tool_calls=[
            _tool_call("medium_a_for_turn_budget_test", {}),
            _tool_call("medium_b_for_turn_budget_test", {}),
            _tool_call("medium_c_for_turn_budget_test", {}),
        ])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("use medium tools") == "done"
    tool_messages = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_messages) == 3
    contents = {m["name"]: m["content"] for m in tool_messages}
    assert PERSISTED_OUTPUT_TAG in contents["medium_a_for_turn_budget_test"]
    assert PERSISTED_OUTPUT_TAG not in contents["medium_b_for_turn_budget_test"]
    assert PERSISTED_OUTPUT_TAG not in contents["medium_c_for_turn_budget_test"]
    assert '"result": "' + ("B" * 100) in contents["medium_b_for_turn_budget_test"]
    assert '"result": "' + ("C" * 100) in contents["medium_c_for_turn_budget_test"]
    artifacts = list((Path("sandbox") / "tool_outputs").glob("*.txt"))
    assert len(artifacts) == 1
    assert '"result": "' + ("A" * 100) in artifacts[0].read_text(encoding="utf-8")


def test_large_tool_result_migrates_to_per_session_sandbox(monkeypatch, tmp_path):
    """启用 per-session 沙箱后，大工具结果落盘到 <session-root>/tool_outputs。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", "sandbox/sessions")

    def huge_tool():
        return "Z" * 90_000

    registry.register("huge_tool_for_migration_test", "huge", {"type": "object", "properties": {}}, huge_tool)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["huge_tool_for_migration_test"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False, session_id="gui/a")
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("huge_tool_for_migration_test", {})])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("use huge") == "done"
    # 落盘到该 session 沙箱下，而非全局 sandbox/tool_outputs
    scoped = list((Path("sandbox") / "sessions" / "gui_a" / "tool_outputs").glob("*.txt"))
    assert len(scoped) == 1
    assert "Z" * 100 in scoped[0].read_text(encoding="utf-8")
    assert not (Path("sandbox") / "tool_outputs").exists()

    # artifact 工具仍能按 <persisted-output> 里的路径读取该文件
    tool_messages = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
    persisted = tool_messages[0]["content"]
    assert PERSISTED_OUTPUT_TAG in persisted
    saved_line = next(line for line in persisted.splitlines() if "Full output saved to:" in line)
    saved_path = saved_line.split("Full output saved to:", 1)[1].strip()
    assert Path(saved_path).is_absolute()
    assert "/sandbox/sessions/gui_a/tool_outputs/" in saved_path
    from tools.artifact_tools import artifact_slice_tool

    sliced = json.loads(artifact_slice_tool(saved_path, offset=1, limit=1))
    assert "Z" in sliced.get("content", "")
