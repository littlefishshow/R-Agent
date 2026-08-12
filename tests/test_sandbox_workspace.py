from pathlib import Path

import pytest

from core.agent import RAgent
from core.sandbox_workspace import (
    SandboxWorkspace,
    VIRTUAL_OUTPUTS,
    VIRTUAL_SKILLS,
    VIRTUAL_UPLOADS,
    VIRTUAL_WORKSPACE,
)


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
