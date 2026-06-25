import json

from core.skills import SkillManager
from tools.skills_tool import skill_create_tool, skill_delete_tool, skill_manage_tool, skill_view_tool


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


def test_legacy_skill_create_delete_delegate_to_skill_manage(tmp_path, monkeypatch):
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

    created = json.loads(skill_create_tool("legacy_demo", "d", "# Demo", "agent_ops"))
    assert created["success"] is True
    assert created["action"] == "create"
    usage_data = json.loads(skill_manage_tool(action="usage", skill_name="legacy_demo"))["usage"]
    assert usage_data["patch_count"] >= 1

    deleted = json.loads(skill_delete_tool("legacy_demo"))
    assert deleted["success"] is True
    assert deleted["action"] == "delete"


def test_create_rejects_duplicate_category_traversal_and_skill_md_subpath(tmp_path, monkeypatch):
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

    first = json.loads(skill_manage_tool(action="create", skill_name="demo", description="d", content="# Demo", category="agent_ops"))
    assert first["success"] is True

    duplicate = json.loads(skill_manage_tool(action="create", skill_name="demo", description="d", content="# Demo", category="agent_ops"))
    assert "已存在" in duplicate["error"]

    bad_category = json.loads(skill_manage_tool(action="create", skill_name="badcat", description="d", content="# Demo", category="agent_ops/nested"))
    assert "category" in bad_category["error"]

    bad_path = json.loads(skill_manage_tool(action="write_file", skill_name="demo", file_path="SKILL.md/child", content="x"))
    assert "SKILL.md cannot be used as a directory" in bad_path["error"]


def test_resolve_skill_dir_rejects_duplicate_skill_names(tmp_path):
    manager = SkillManager(str(tmp_path / "skills"))
    (tmp_path / "skills" / "agent_ops" / "dupe").mkdir(parents=True)
    (tmp_path / "skills" / "agent_ops" / "dupe" / "SKILL.md").write_text("# One", encoding="utf-8")
    (tmp_path / "skills" / "productivity" / "dupe").mkdir(parents=True)
    (tmp_path / "skills" / "productivity" / "dupe" / "SKILL.md").write_text("# Two", encoding="utf-8")

    try:
        manager.resolve_skill_dir("dupe")
    except ValueError as exc:
        assert "存在多个匹配" in str(exc)
    else:
        raise AssertionError("Expected duplicate skill names to be rejected")


def test_create_overwrite_updates_same_category_but_rejects_new_duplicate_category(tmp_path):
    manager = SkillManager(str(tmp_path / "skills"))
    manager.create_skill("demo", "d", "# One", "agent_ops")
    updated = manager.create_skill("demo", "d", "# Two", "agent_ops", overwrite=True)
    assert "updated" in updated
    assert "# Two" in manager.view_skill("demo")

    try:
        manager.create_skill("demo", "d", "# Three", "productivity", overwrite=True)
    except FileExistsError as exc:
        assert "同名副本" in str(exc)
    else:
        raise AssertionError("Expected overwrite in another category to be rejected")
