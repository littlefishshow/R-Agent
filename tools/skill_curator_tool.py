"""Deterministic skill curator tools for R-Agent self-evolution."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from core.skills import skill_manager
from core import skill_usage
from tools.registry import registry


def _json_ok(**kwargs):
    return json.dumps({"success": True, **kwargs}, ensure_ascii=False)


def _json_error(message: str):
    return json.dumps({"error": str(message)}, ensure_ascii=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _archive_root() -> Path:
    return Path(skill_manager.skills_dir).resolve() / ".archive"


def _find_archived_dir(skill_name: str, rec: Dict[str, Any] | None = None) -> Path | None:
    candidates: List[Path] = []
    if rec and rec.get("archive_path"):
        candidates.append(Path(str(rec["archive_path"])))
    candidates.append(_archive_root() / skill_name)
    for p in candidates:
        try:
            if p.exists() and p.is_dir():
                return p.resolve()
        except OSError:
            continue
    return None


def _usage_rows() -> List[Dict[str, Any]]:
    rows = []
    usage = skill_usage.read_usage()
    for name, rec in sorted(usage.items()):
        rec = skill_usage.normalize_record(name, rec)
        rows.append({
            "name": name,
            "state": rec.get("state", skill_usage.STATE_ACTIVE),
            "pinned": bool(rec.get("pinned")),
            "created_by": rec.get("created_by"),
            "write_origin": rec.get("write_origin"),
            "activity_count": skill_usage.activity_count(rec),
            "last_activity_at": skill_usage.latest_activity_at(rec),
            "created_at": rec.get("created_at"),
            "archived_at": rec.get("archived_at"),
            "archive_path": rec.get("archive_path"),
        })
    return rows


def skill_curator_status_tool() -> str:
    """Return deterministic curator status and usage summary."""
    rows = _usage_rows()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return _json_ok(counts=counts, skills=rows)


def skill_curator_pin_tool(skill_name: str, pinned: bool = True) -> str:
    """Pin/unpin a skill so the deterministic curator will or will not skip it."""
    if not skill_name:
        return _json_error("skill_name is required.")
    rec = skill_usage.set_pinned(skill_name, bool(pinned))
    return _json_ok(skill_name=skill_name, pinned=rec.get("pinned"))


def skill_curator_restore_tool(skill_name: str) -> str:
    """Restore an archived skill directory from skills/.archive/<skill>."""
    if not skill_name:
        return _json_error("skill_name is required.")
    rec = skill_usage.get_record(skill_name)
    archived = _find_archived_dir(skill_name, rec)
    if archived is None:
        return _json_error(f"Archived skill '{skill_name}' not found.")
    dest = Path(skill_manager.skills_dir).resolve() / "restored" / skill_name
    if dest.exists():
        return _json_error(f"Restore destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(archived), str(dest))
    rec = skill_usage.update_record(
        skill_name,
        state=skill_usage.STATE_ACTIVE,
        archived_at=None,
        archive_path=None,
        last_patched_at=_now().isoformat(),
    )
    return _json_ok(skill_name=skill_name, restored_to=str(dest), record=rec)


def skill_curator_run_tool(stale_after_days: int = 30, archive_after_days: int = 90, dry_run: bool = True) -> str:
    """Run deterministic skill lifecycle review: active -> stale -> archived.

    Only agent-created records are eligible. Pinned records are skipped. In dry_run
    mode no files or usage records are changed.
    """
    try:
        stale_after_days = int(stale_after_days)
        archive_after_days = int(archive_after_days)
    except (TypeError, ValueError):
        return _json_error("stale_after_days and archive_after_days must be integers.")
    if stale_after_days < 0 or archive_after_days < stale_after_days:
        return _json_error("Require 0 <= stale_after_days <= archive_after_days.")

    now = _now()
    usage = skill_usage.read_usage()
    actions = []
    checked = 0
    for name, rec in sorted(usage.items()):
        rec = skill_usage.normalize_record(name, rec)
        if not skill_usage.is_agent_created(rec):
            continue
        checked += 1
        if rec.get("pinned"):
            actions.append({"skill": name, "action": "skip", "reason": "pinned"})
            continue
        anchor_raw = skill_usage.latest_activity_at(rec) or rec.get("created_at")
        anchor = _parse_iso(anchor_raw)
        if anchor is None:
            if not dry_run:
                rec["created_at"] = now.isoformat()
                usage[name] = rec
            actions.append({"skill": name, "action": "seed_created_at", "reason": "no activity timestamp"})
            continue
        age_days = (now - anchor).days
        state = rec.get("state", skill_usage.STATE_ACTIVE)
        if age_days >= archive_after_days:
            archive_path = str(_archive_root() / name)
            actions.append({"skill": name, "action": "archive", "age_days": age_days, "archive_path": archive_path})
            if not dry_run:
                try:
                    src = skill_manager.resolve_skill_dir(name)
                    dst = Path(archive_path)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        dst = dst.parent / f"{name}-{now.strftime('%Y%m%d%H%M%S')}"
                    shutil.move(str(src), str(dst))
                    rec["archive_path"] = str(dst)
                except Exception as exc:
                    actions[-1]["error"] = str(exc)
                    continue
                rec["state"] = skill_usage.STATE_ARCHIVED
                rec["archived_at"] = now.isoformat()
                usage[name] = rec
        elif age_days >= stale_after_days and state != skill_usage.STATE_STALE:
            actions.append({"skill": name, "action": "mark_stale", "age_days": age_days})
            if not dry_run:
                rec["state"] = skill_usage.STATE_STALE
                usage[name] = rec
        elif state == skill_usage.STATE_STALE and age_days < stale_after_days:
            actions.append({"skill": name, "action": "reactivate", "age_days": age_days})
            if not dry_run:
                rec["state"] = skill_usage.STATE_ACTIVE
                usage[name] = rec
        else:
            actions.append({"skill": name, "action": "keep", "age_days": age_days, "state": state})
    if not dry_run:
        skill_usage.write_usage(usage)
    return _json_ok(dry_run=bool(dry_run), checked=checked, actions=actions)


def skill_curator_manage_tool(action: str, skill_name: str = "", pinned: bool = True,
                              stale_after_days: int = 30, archive_after_days: int = 90,
                              dry_run: bool = True) -> str:
    """统一 skill 生命周期治理入口，支持 status/run/pin/restore。"""
    action = (action or "status").strip().lower()
    if action == "status":
        return skill_curator_status_tool()
    if action == "run":
        return skill_curator_run_tool(stale_after_days=stale_after_days, archive_after_days=archive_after_days, dry_run=dry_run)
    if action == "pin":
        return skill_curator_pin_tool(skill_name=skill_name, pinned=pinned)
    if action == "restore":
        return skill_curator_restore_tool(skill_name=skill_name)
    return _json_error("Unsupported action. Use status, run, pin, or restore.")


registry.register(
    name="skill_curator_manage",
    description="统一管理 skill 生命周期治理：action=status|run|pin|restore。status 汇总状态，run 执行 deterministic curator，pin 设置 pinned，restore 从归档恢复。",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "status | run | pin | restore；默认 status"},
            "skill_name": {"type": "string", "description": "pin/restore 时的技能名称"},
            "pinned": {"type": "boolean", "description": "pin 时 true=pin，false=unpin；默认 true"},
            "stale_after_days": {"type": "integer", "description": "run 时多少天未活跃后标记 stale，默认 30"},
            "archive_after_days": {"type": "integer", "description": "run 时多少天未活跃后归档到 skills/.archive，默认 90"},
            "dry_run": {"type": "boolean", "description": "run 时 true 只预览不修改，默认 true"},
        },
        "required": ["action"],
    },
    handler=skill_curator_manage_tool,
)
