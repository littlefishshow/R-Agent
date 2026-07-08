import json

from core.skills import SkillManager
from tools.registry import registry
from tools.skills_tool import skill_manage_tool
from tools.skill_hierarchy_tool import skill_search
from tools.skill_curator_tool import skill_curator_manage_tool


def _patch_skill_modules(tmp_path, monkeypatch):
    import core.skills as skills_mod
    import tools.skills_tool as st
    import tools.skill_hierarchy_tool as hierarchy
    import tools.skill_curator_tool as curator
    import core.skill_usage as usage

    manager = SkillManager(str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "skill_manager", manager)
    monkeypatch.setattr(st, "skill_manager", manager)
    monkeypatch.setattr(hierarchy, "skill_manager", manager)
    monkeypatch.setattr(curator, "skill_manager", manager)
    monkeypatch.setattr(usage, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "skills" / ".usage.json")
    monkeypatch.setattr(st, "record_event", usage.record_event)
    monkeypatch.setattr(st, "read_usage", usage.read_usage)
    return usage


def test_skill_search_combines_categories_by_category_and_keyword(tmp_path, monkeypatch):
    _patch_skill_modules(tmp_path, monkeypatch)
    skill_manage_tool(
        action="create",
        skill_name="deploy_demo",
        description="Deploy demo service",
        content="# Demo\n\n## When to Use\nUse for Kubernetes rollout checks.\n\n## Procedure\nRun checks.",
        category="agent_ops",
    )
    skill_manage_tool(
        action="create",
        skill_name="writing_demo",
        description="Draft prose",
        content="# Writing\n\n## When to Use\nUse for creative copy.",
        category="creative",
    )

    categories = skill_search(action="categories")
    assert {c["category"] for c in categories["categories"]} == {"agent_ops", "creative"}

    grouped = skill_search(action="by_category", categories=["agent_ops"], include_when_to_use=True)
    assert grouped["categories"]["agent_ops"]["skills"][0]["name"] == "deploy_demo"
    assert "Kubernetes" in grouped["categories"]["agent_ops"]["skills"][0]["when_to_use"]

    found = skill_search(action="search", query="rollout", include_when_to_use=True)
    assert found["count"] == 1
    assert found["matches"][0]["name"] == "deploy_demo"


def test_skill_curator_manage_dispatches_lifecycle_actions(tmp_path, monkeypatch):
    usage = _patch_skill_modules(tmp_path, monkeypatch)
    skill_manage_tool(action="create", skill_name="managed_demo", description="d", content="# Demo", category="agent_ops")

    pinned = json.loads(skill_curator_manage_tool(action="pin", skill_name="managed_demo", pinned=True))
    assert pinned["success"] is True
    assert pinned["pinned"] is True
    assert usage.get_record("managed_demo")["pinned"] is True

    status = json.loads(skill_curator_manage_tool(action="status"))
    assert status["success"] is True
    assert any(row["name"] == "managed_demo" for row in status["skills"])

    run = json.loads(skill_curator_manage_tool(action="run", dry_run=True))
    assert run["success"] is True
    assert run["dry_run"] is True


def test_default_registry_exposes_five_core_skill_tools_only():
    registry.reload_all()
    names = set(registry._tools)
    assert {"skill_search", "skill_view", "skill_manage", "skill_relocate", "skill_curator_manage"} <= names
    assert not {
        "skill_categories",
        "skills_by_category",
        "skills_list",
        "skill_create",
        "skill_delete",
        "skill_curator_status",
        "skill_curator_run",
        "skill_curator_pin",
        "skill_curator_restore",
    } & names
