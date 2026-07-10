from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .utils import atomic_write_json, read_json


class DebugLog:
    def __init__(self, root: str | Path, *, enabled: bool = False):
        self.root = Path(root)
        self.enabled = bool(enabled)
        self.dir = self.root / ".autoresearch" / "debug"
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **payload) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": time.time(),
            "time": time.strftime("%F %T"),
            "event": event,
            **payload,
        }
        with (self.dir / "debug.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def inflight_start(self, kind: str, **payload) -> None:
        if not self.enabled:
            return
        row = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "started_time": time.strftime("%F %T"),
            "kind": kind,
            **payload,
        }
        atomic_write_json(self.dir / "inflight.json", row)
        self.event(f"{kind}_start", **payload)

    def inflight_finish(self, kind: str, **payload) -> None:
        if not self.enabled:
            return
        old = read_json(self.dir / "inflight.json", {}) or {}
        elapsed = None
        if old.get("started_at"):
            elapsed = round(time.time() - float(old["started_at"]), 3)
        if "elapsed_seconds" in payload:
            payload = dict(payload)
            payload["reported_elapsed_seconds"] = payload.pop("elapsed_seconds")
        self.event(f"{kind}_finish", elapsed_seconds=elapsed, **payload)
        try:
            (self.dir / "inflight.json").unlink(missing_ok=True)
        except Exception:
            pass


def read_inflight(root: str | Path) -> dict:
    path = Path(root) / ".autoresearch" / "debug" / "inflight.json"
    data = read_json(path, {}) or {}
    if data.get("started_at"):
        data["age_seconds"] = round(time.time() - float(data["started_at"]), 1)
    return data
