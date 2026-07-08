import json
from datetime import datetime, timedelta, timezone

from core.skills import SkillManager
from tools.skills_tool import skill_manage_tool
from tools.skill_curator_tool import skill_curator_run_tool, skill_curator_status_tool


def _patch_managers(tmp_path, monkeypatch):
    import core.skills as skills_mod
    import tools.skills_tool as st
    import tools.skill_curator_tool as curator
    import core.skill_usage as usage

    manager = SkillManager(str(tmp_path / "skills"))
    monkeypatch.setattr(skills_mod, "skill_manager", manager)
    monkeypatch.setattr(st, "skill_manager", manager)
    monkeypatch.setattr(curator, "skill_manager", manager)
    monkeypatch.setattr(usage, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "skills" / ".usage.json")
    monkeypatch.setattr(st, "record_event", usage.record_event)
    monkeypatch.setattr(st, "read_usage", usage.read_usage)
    return usage


def test_deterministic_curator_dry_run_and_stale(tmp_path, monkeypatch):
    usage = _patch_managers(tmp_path, monkeypatch)
    created = json.loads(skill_manage_tool(action="create", skill_name="old_demo", description="d", content="# Demo", category="agent_ops"))
    assert created["success"] is True
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    usage.update_record("old_demo", created_by="foreground_agent", created_at=old, last_patched_at=old)

    preview = json.loads(skill_curator_run_tool(stale_after_days=30, archive_after_days=90, dry_run=True))
    assert any(a["action"] == "mark_stale" for a in preview["actions"])
    assert usage.get_record("old_demo")["state"] == "active"

    applied = json.loads(skill_curator_run_tool(stale_after_days=30, archive_after_days=90, dry_run=False))
    assert applied["success"] is True
    assert usage.get_record("old_demo")["state"] == "stale"
    status = json.loads(skill_curator_status_tool())
    assert status["counts"]["stale"] == 1
