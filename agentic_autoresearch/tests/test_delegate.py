from __future__ import annotations

import json
from types import SimpleNamespace

from agentic_autoresearch.steps import ATTEMPT_TOOLS, CONCLUDE_TOOLS, PLAN_TOOLS
from agentic_autoresearch.tools import build_default_tools


class FakeResponse:
    def __init__(self, content):
        self.choices = [SimpleNamespace(message=SimpleNamespace(role="assistant", content=content, tool_calls=[]))]
        self.usage = SimpleNamespace(prompt_tokens=2, completion_tokens=3, total_tokens=5)


class FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        tool_names = [tool["function"]["name"] for tool in kwargs.get("tools", [])]
        assert "delegate_task" not in tool_names
        return FakeResponse('child complete {"DELEGATE_DONE": true}')


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


def test_delegate_task_tool_scope():
    assert "delegate_task" in PLAN_TOOLS
    assert "delegate_task" in ATTEMPT_TOOLS
    assert "delegate_task" not in CONCLUDE_TOOLS


def test_delegate_task_runs_child_agent_and_writes_artifacts(tmp_path):
    client = FakeClient()
    tools = build_default_tools(tmp_path, client=client, model="fake", enable_delegate=True)
    result = json.loads(tools.execute("delegate_task", {
        "goal": "summarize program",
        "context": {"hint": "small"},
        "parent_step": "plan",
        "max_iterations": 2,
    }))

    assert result["success"] is True
    payload = result["result"]
    assert payload["done"] is True
    assert payload["trace_path"]
    assert payload["context_path"]
    assert payload["result_path"]
    assert (tmp_path / ".autoresearch" / "child_contexts").exists()
    assert (tmp_path / ".autoresearch" / "child_results").exists()
    assert client.chat.completions.calls == 1
