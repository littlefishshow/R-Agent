from __future__ import annotations

import json
import os
import time
from pathlib import Path


def debug_enabled(root: str | Path) -> bool:
    root = Path(root)
    return (root / ".autoresearch" / "DEBUG").exists()


def ensure_debug_from_settings(settings) -> None:
    try:
        if bool(getattr(settings, "debug_mode", False)):
            set_debug(settings.root(), True)
    except Exception:
        pass


def set_debug(root: str | Path, enabled: bool) -> Path:
    flag = Path(root) / ".autoresearch" / "DEBUG"
    flag.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        flag.write_text(f"enabled at {time.strftime('%F %T')}\n", encoding="utf-8")
    else:
        flag.unlink(missing_ok=True)
    return flag


def debug_dir(root: str | Path) -> Path:
    return Path(root) / ".autoresearch" / "debug"


def debug_event(root: str | Path, event: str, **payload) -> None:
    if not debug_enabled(root):
        return
    d = debug_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.time(),
        "time": time.strftime("%F %T"),
        "event": event,
        **payload,
    }
    path = d / "debug.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def inflight_start(root: str | Path, kind: str, **payload) -> None:
    if not debug_enabled(root):
        return
    d = debug_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    row = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "started_time": time.strftime("%F %T"),
        "kind": kind,
        **payload,
    }
    (d / "inflight.json").write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    debug_event(root, f"{kind}_start", **payload)


def inflight_finish(root: str | Path, kind: str, **payload) -> None:
    if not debug_enabled(root):
        return
    p = debug_dir(root) / "inflight.json"
    old = {}
    try:
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        old = {}
    elapsed = None
    if old.get("started_at"):
        elapsed = round(time.time() - float(old["started_at"]), 3)
    debug_event(root, f"{kind}_finish", elapsed_seconds=elapsed, **payload)
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass


def read_inflight(root: str | Path) -> dict:
    p = debug_dir(root) / "inflight.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if data.get("started_at"):
        data["age_seconds"] = round(time.time() - float(data["started_at"]), 1)
    return data
