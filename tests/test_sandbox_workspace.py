from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from app_gui.file_workspace import FileWorkspace
from app_gui.server import _workspace_for_session
from core.agent import RAgent
from core.sandbox_workspace import (
    SandboxWorkspace,
    VIRTUAL_OUTPUTS,
    VIRTUAL_SKILLS,
    VIRTUAL_UPLOADS,
    VIRTUAL_WORKSPACE,
)
from tools import file_tools, todo_tool
from tools.registry import registry


def test_workspace_is_lazy_and_session_isolated(tmp_path):
    root = tmp_path / "sessions"
    first = SandboxWorkspace("session-a", root=root, skills_root=tmp_path / "skills")
    second = SandboxWorkspace("session-b", root=root, skills_root=tmp_path / "skills")

    assert not first.root.exists()
    assert not second.root.exists()

    first.ensure()
    assert first.workspace.is_dir()
    assert first.uploads.is_dir()
    assert first.outputs.is_dir()
    assert not second.root.exists()
    assert first.root != second.root


def test_virtual_paths_resolve_and_block_escape(tmp_path):
    workspace = SandboxWorkspace("s", root=tmp_path / "sessions", skills_root=tmp_path / "skills")

    assert workspace.resolve_virtual(f"{VIRTUAL_WORKSPACE}/a.txt") == workspace.workspace / "a.txt"
    assert workspace.resolve_virtual(f"{VIRTUAL_UPLOADS}/input.pdf") == workspace.uploads / "input.pdf"
    assert workspace.resolve_virtual(f"{VIRTUAL_OUTPUTS}/report.md") == workspace.outputs / "report.md"
    assert workspace.resolve_virtual(f"{VIRTUAL_SKILLS}/demo/SKILL.md") == (tmp_path / "skills" / "demo" / "SKILL.md")

    with pytest.raises(ValueError):
        workspace.resolve_virtual(f"{VIRTUAL_WORKSPACE}/../../outside.txt")
    with pytest.raises(ValueError):
        workspace.resolve_virtual("/tmp/not-supported")


def test_agent_sandbox_disabled_has_no_side_effect(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "0")
    agent = RAgent(model="m", enable_self_review=False, session_id="disabled")

    assert agent.get_sandbox_workspace() is None
    assert agent.state.sandbox == {}
    assert not (tmp_path / "sandbox" / "sessions").exists()


def test_agent_sandbox_enabled_updates_thread_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", str(tmp_path / "session_sandboxes"))
    agent = RAgent(model="m", enable_self_review=False, session_id="gui/a")

    workspace = agent.get_sandbox_workspace()

    assert workspace is not None
    assert workspace.sandbox_id == "gui_a"
    assert Path(agent.state.sandbox["workspace"]).is_dir()
    assert agent.state.sandbox["virtual_paths"][VIRTUAL_OUTPUTS] == str(workspace.outputs)


def test_todo_routes_into_session_sandbox(monkeypatch, tmp_path):
    root = tmp_path / "session_sandboxes"
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", str(root))

    todo_tool.todo_manage(
        "init",
        json.dumps({"tasks": [{"id": "t1", "description": "Scoped"}]}),
        session_id="todo/a",
    )

    scoped = SandboxWorkspace("todo_a", root=root)
    todo_path = scoped.todo_lists / "todo_list.json"
    assert todo_path.exists()
    assert not (tmp_path / "sandbox" / "todo_lists").exists()
    state = json.loads(todo_tool.todo_manage("view", "{}", session_id="todo/a"))
    assert [task["id"] for task in state["todo_list"]] == ["t1"]


def test_file_tools_share_isolated_session_workspace(monkeypatch, tmp_path):
    root = tmp_path / "session_sandboxes"
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", str(root))

    first = json.loads(file_tools.write_file_tool("note.md", "first", session_id="s1"))
    second = json.loads(file_tools.write_file_tool("note.md", "second", session_id="s2"))
    assert first["success"] is True and second["success"] is True

    s1 = SandboxWorkspace("s1", root=root)
    s2 = SandboxWorkspace("s2", root=root)
    assert (s1.workspace / "note.md").read_text(encoding="utf-8") == "first"
    assert (s2.workspace / "note.md").read_text(encoding="utf-8") == "second"

    read = json.loads(file_tools.read_file_tool("note.md", session_id="s1"))
    assert "first" in read["content"]
    assert read["resolved_path"] == str(s1.workspace / "note.md")


def test_file_tools_resolve_virtual_paths_and_preserve_legacy_mode(monkeypatch, tmp_path):
    root = tmp_path / "session_sandboxes"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", str(root))

    virtual = json.loads(
        file_tools.write_file_tool(
            f"{VIRTUAL_OUTPUTS}/report.md",
            "report",
            session_id="virtual",
        )
    )
    scoped = SandboxWorkspace("virtual", root=root)
    assert virtual["resolved_path"] == str(scoped.outputs / "report.md")

    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "0")
    monkeypatch.setattr(file_tools, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(file_tools, "SANDBOX_DIR", str(tmp_path / "sandbox"))
    legacy = json.loads(file_tools.write_file_tool("legacy.md", "legacy", session_id="ignored"))
    assert legacy["resolved_path"] == str(tmp_path / "legacy.md")
    assert (tmp_path / "legacy.md").read_text(encoding="utf-8") == "legacy"


def test_gui_workspace_remains_shared_outputs_library(monkeypatch, tmp_path):
    root = tmp_path / "session_sandboxes"
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", str(root))
    default = FileWorkspace(tmp_path / "legacy_gui")

    scoped = _workspace_for_session(default, "gui/a")
    assert _workspace_for_session(default, "gui/a") is default
    assert scoped.root == (tmp_path / "legacy_gui").resolve()
    assert not SandboxWorkspace("gui/a", root=root).root.exists()


def test_agent_injects_session_id_into_file_tool(monkeypatch, tmp_path):
    root = tmp_path / "session_sandboxes"
    monkeypatch.setenv("SESSION_SANDBOX_ENABLED", "1")
    monkeypatch.setenv("SESSION_SANDBOX_ROOT", str(root))
    monkeypatch.setattr(
        registry,
        "execute_tool_isolated",
        lambda name, args, **kwargs: registry.execute_tool(name, args),
    )
    monkeypatch.setattr(
        registry,
        "get_all_schemas",
        lambda: [registry._tools["write_file"]["schema"]],
    )

    tool_call = SimpleNamespace(
        id="call_write",
        function=SimpleNamespace(
            name="write_file",
            arguments=json.dumps({"path": "agent.md", "content": "scoped"}),
        ),
    )
    responses = [
        SimpleNamespace(
            usage={"total_tokens": 1},
            choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call]))],
        ),
        SimpleNamespace(
            usage={"total_tokens": 1},
            choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))],
        ),
    ]

    class _Completions:
        def create(self, **kwargs):
            return responses.pop(0)

    agent = RAgent(model="m", max_iterations=2, enable_self_review=False, session_id="agent-file")
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    assert agent.run_conversation("write") == "done"
    scoped = SandboxWorkspace("agent-file", root=root)
    assert (scoped.workspace / "agent.md").read_text(encoding="utf-8") == "scoped"
