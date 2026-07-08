"""Skill usage telemetry for R-Agent self-evolution.

This module is intentionally best-effort: a broken telemetry sidecar should not
break the user-facing skill tools. The sidecar lives at ``skills/.usage.json``
and is used by the deterministic curator.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
SKILLS_DIR = BASE_DIR / "skills"
USAGE_FILE = SKILLS_DIR / ".usage.json"
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
VALID_STATES = {STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED}

try:  # Unix/macOS
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - platform fallback
    fcntl = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _default_record(skill_name: str) -> Dict[str, Any]:
    return {
        "skill_name": skill_name,
        "created_by": "unknown",
        "write_origin": "unknown",
        "use_count": 0,
        "view_count": 0,
        "patch_count": 0,
        "last_used_at": None,
        "last_viewed_at": None,
        "last_patched_at": None,
        "created_at": None,
        "state": STATE_ACTIVE,
        "pinned": False,
        "archived_at": None,
        "archive_path": None,
    }


def normalize_record(skill_name: str, rec: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(rec, dict):
        rec = _default_record(skill_name)
    for k, v in _default_record(skill_name).items():
        rec.setdefault(k, v)
    if rec.get("state") not in VALID_STATES:
        rec["state"] = STATE_ACTIVE
    return rec


@contextmanager
def _usage_file_lock():
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = USAGE_FILE.with_suffix(".json.lock")
    with open(lock_path, "a+", encoding="utf-8") as fd:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError:
                pass
        try:
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass


def read_usage() -> Dict[str, Dict[str, Any]]:
    try:
        if not USAGE_FILE.exists():
            return {}
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): normalize_record(str(k), v) for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def write_usage(data: Dict[str, Dict[str, Any]]) -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".usage.", suffix=".json", dir=str(SKILLS_DIR))
    try:
        normalized = {str(k): normalize_record(str(k), v) for k, v in data.items() if isinstance(v, dict)}
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, USAGE_FILE)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def latest_activity_at(record: Dict[str, Any]) -> Optional[str]:
    latest_dt: Optional[datetime] = None
    latest_raw: Optional[str] = None
    for key in ("last_used_at", "last_viewed_at", "last_patched_at"):
        raw = record.get(key)
        dt = _parse_iso(raw)
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_raw = str(raw)
    return latest_raw


def activity_count(record: Dict[str, Any]) -> int:
    total = 0
    for key in ("use_count", "view_count", "patch_count"):
        try:
            total += int(record.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def is_agent_created(record: Dict[str, Any]) -> bool:
    return str(record.get("created_by") or "") in {"foreground_agent", "background_review", "agent"}


def get_record(skill_name: str) -> Dict[str, Any]:
    data = read_usage()
    return normalize_record(skill_name, data.get(skill_name))


def update_record(skill_name: str, **fields: Any) -> Dict[str, Any]:
    with _usage_file_lock():
        data = read_usage()
        rec = normalize_record(skill_name, data.get(skill_name))
        rec.update(fields)
        data[skill_name] = rec
        write_usage(data)
        return rec


def set_pinned(skill_name: str, pinned: bool) -> Dict[str, Any]:
    return update_record(skill_name, pinned=bool(pinned))


def record_event(skill_name: str, event: str, *, created_by: str | None = None, write_origin: str | None = None) -> None:
    if not skill_name:
        return
    try:
        with _usage_file_lock():
            data = read_usage()
            rec = normalize_record(skill_name, data.get(skill_name))
            now = _now()
            if event == "view":
                rec["view_count"] = int(rec.get("view_count") or 0) + 1
                rec["last_viewed_at"] = now
            elif event == "use":
                rec["use_count"] = int(rec.get("use_count") or 0) + 1
                rec["last_used_at"] = now
            elif event == "patch":
                rec["patch_count"] = int(rec.get("patch_count") or 0) + 1
                rec["last_patched_at"] = now
                if rec.get("state") == STATE_ARCHIVED:
                    rec["state"] = STATE_ACTIVE
            elif event == "create":
                rec["created_at"] = rec.get("created_at") or now
                rec["state"] = STATE_ACTIVE
            if created_by:
                rec["created_by"] = created_by
            if write_origin:
                rec["write_origin"] = write_origin
            data[skill_name] = rec
            write_usage(data)
    except Exception:
        return
