import json

from core.skills import SkillManager
from tools.skills_tool import skill_manage_tool, skill_view_tool


def test_skill_view_supporting_file_and_usage(tmp_path, monkeypatch):
    import core.skills as skills_mod
    import tools.skills_tool as st
    import core.skill_usage as usage

    manager = SkillManager(str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "skill_manager", manager)
    monkeypatch.setattr(st, "skill_manager", manager)
    monkeypatch.setattr(usage, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "skills" / ".usage.json")
    monkeypatch.setattr(st, "record_event", usage.record_event)
    monkeypatch.setattr(st, "read_usage", usage.read_usage)

    res = json.loads(skill_manage_tool(action="create", skill_name="demo", description="d", content="# Demo", category="agent_ops"))
    assert res["success"] is True
    res = json.loads(skill_manage_tool(action="write_file", skill_name="demo", file_path="references/api.md", content="API ref"))
    assert res["success"] is True
    viewed = json.loads(skill_view_tool("demo", "references/api.md"))
    assert viewed["content"] == "API ref"
    usage_data = json.loads(skill_manage_tool(action="usage", skill_name="demo"))["usage"]
    assert usage_data["view_count"] >= 1
    assert usage_data["patch_count"] >= 2


def test_skill_patch_rejects_ambiguous_and_path_traversal(tmp_path, monkeypatch):
    import core.skills as skills_mod
    import tools.skills_tool as st

    manager = SkillManager(str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "skill_manager", manager)
    monkeypatch.setattr(st, "skill_manager", manager)
    skill_manage_tool(action="create", skill_name="demo", description="d", content="alpha alpha", category="agent_ops")
    ambiguous = json.loads(skill_manage_tool(action="patch", skill_name="demo", old_string="alpha", new_string="beta"))
    assert "ambiguous" in ambiguous["error"]
    escaped = json.loads(skill_view_tool("demo", "../x"))
    assert "cannot contain" in escaped["error"] or "inside" in escaped["error"]
