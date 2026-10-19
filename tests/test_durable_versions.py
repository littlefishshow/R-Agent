from core.agent import RAgent
from tools.registry import registry


def _schema(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} description",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }


def test_durable_current_stays_frozen_while_next_collects_runtime_state(monkeypatch):
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "system")
    monkeypatch.setattr(registry, "get_schemas_for", lambda names: [_schema(name) for name in names])

    agent = RAgent(model="test", enable_self_review=False, session_id="durable-test")
    monkeypatch.setattr(agent, "_collect_unfinished_todos", lambda: [{
        "id": "t1",
        "description": "finish cache migration",
        "status": "blocked",
    }])
    agent.state.summary_text = "summary v1"
    current_v1 = agent._get_durable_snapshot()

    agent._register_dynamic_tool_contracts([{
        "name": "dynamic_demo",
        "description": "dynamic_demo description",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }])
    agent.state.add_skill_context({"skill": "cache-skill", "summary": "cache workflow"})
    agent.state.active_skill_policy = {
        "skill": "cache-skill",
        "allowed_tools": ["dynamic_demo"],
    }
    draft = agent._stage_durable_next()

    # The visible current prefix remains byte-identical before compaction.
    assert agent._get_durable_snapshot() is current_v1
    assert "dynamic_demo" not in current_v1["content"]
    assert draft["unfinished_todos"][0]["id"] == "t1"
    assert draft["dynamic_tools"][0]["name"] == "dynamic_demo"
    assert draft["skill_context"][0]["skill"] == "cache-skill"

    agent.state.summary_text = "summary v2"
    agent._on_context_compacted()
    current_v2 = agent._get_durable_snapshot()

    assert current_v2 is not current_v1
    assert "summary v2" in current_v2["content"]
    assert "<durable_unfinished_todos>" in current_v2["content"]
    assert "finish cache migration" in current_v2["content"]
    assert "<durable_dynamic_tools>" in current_v2["content"]
    assert "dynamic_demo" in current_v2["content"]
    assert agent.state.durable_current["epoch"] == 2
    assert agent.state.durable_next["base_epoch"] == 2
    assert agent.state.durable_next["target_epoch"] == 3


def test_durable_versions_are_json_serializable(monkeypatch):
    import json

    monkeypatch.setenv("MEMORY_INJECTION_MODE", "system")
    agent = RAgent(model="test", enable_self_review=False)
    monkeypatch.setattr(agent, "_collect_unfinished_todos", lambda: [])
    agent.state.summary_text = "serializable"
    agent._get_durable_snapshot()

    payload = json.dumps({
        "current": agent.state.durable_current,
        "next": agent.state.durable_next,
    }, ensure_ascii=False)
    assert "serializable" in payload
