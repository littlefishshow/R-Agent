"""AutoResearch v2 — non-LLM run monitor + heartbeat.

Writes a small ``.autoresearch/monitor.json`` heartbeat every phase step so the
loop can run as a detached subprocess while anything else (a parent agent, a
shell, a dashboard) watches progress **without invoking any LLM**.  Everything
here is plain file IO + the budget ledger snapshot the loop already maintains.

Tracked signals:
- rounds: how many phase steps have executed (``step_index``) + current phase
- tokens/usd: pulled straight from the BudgetLedger snapshot (metered, no LLM)
- status: queued / running / paused / completed / failed
- liveness: pid + updated_at, so a stale heartbeat is detectable
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional


class RunMonitor:
    """Heartbeat writer for a single autoresearch run (atomic JSON writes)."""

    def __init__(self, path: str | Path, *, run_id: str = "", project_id: str = ""):
        self.path = Path(path)
        self.run_id = run_id
        self.project_id = project_id
        self._data = {
            "run_id": run_id,
            "project_id": project_id,
            "pid": os.getpid(),
            "status": "queued",
            "current_phase": "init",
            "next_phase": "",
            "step_index": 0,          # == rounds completed
            "max_steps": 0,
            "last_summary": "",
            "budget": {},
            "started_at": time.time(),
            "updated_at": time.time(),
            "finished_at": None,
            "error": "",
        }
        self._write()

    def _write(self) -> None:
        self._data["updated_at"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def set_max_steps(self, n: int) -> None:
        self._data["max_steps"] = int(n)
        self._write()

    def start(self) -> None:
        self._data["status"] = "running"
        self._data["started_at"] = time.time()
        self._write()

    def update_step(self, *, step_index: int, current_phase: str, next_phase: str,
                    summary: str, budget_snapshot: Optional[dict] = None) -> None:
        self._data["step_index"] = int(step_index)
        self._data["current_phase"] = current_phase
        self._data["next_phase"] = next_phase
        self._data["last_summary"] = str(summary or "")[:500]
        if budget_snapshot is not None:
            self._data["budget"] = _compact_budget(budget_snapshot)
        if next_phase == "pause":
            self._data["status"] = "paused"
        self._write()

    def finish(self, *, status: str = "completed", error: str = "", budget_snapshot: Optional[dict] = None) -> None:
        self._data["status"] = status
        self._data["finished_at"] = time.time()
        if error:
            self._data["error"] = str(error)[:2000]
        if budget_snapshot is not None:
            self._data["budget"] = _compact_budget(budget_snapshot)
        self._write()

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self._data))


def _compact_budget(snapshot: dict) -> dict:
    """Keep only the numbers a watcher cares about (tokens/usd/status)."""
    if not isinstance(snapshot, dict):
        return {}
    calls = snapshot.get("calls", 0) or 0
    dur_total = snapshot.get("duration_seconds_total", 0.0) or 0.0
    return {
        "total_tokens": snapshot.get("total_tokens", 0),
        "prompt_tokens": snapshot.get("prompt_tokens", 0),
        "completion_tokens": snapshot.get("completion_tokens", 0),
        "estimated_usd": snapshot.get("estimated_usd", 0.0),
        "calls": calls,
        "duration_seconds_total": round(dur_total, 3),
        "duration_seconds_last": snapshot.get("duration_seconds_last", 0.0),
        "duration_seconds_max": snapshot.get("duration_seconds_max", 0.0),
        "duration_seconds_avg": round(dur_total / calls, 3) if calls else 0.0,
        "status": snapshot.get("status", "ok"),
        "limits": snapshot.get("limits", {}),
    }


def read_monitor(path: str | Path) -> dict:
    """Pure file read of a monitor heartbeat (no LLM, safe for watchers)."""
    p = Path(path)
    if not p.exists():
        return {"status": "unknown", "reason": "no monitor file", "path": str(p)}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "unknown", "reason": f"unreadable: {exc}", "path": str(p)}
    # Liveness: flag a heartbeat that has not advanced in a while while "running".
    if data.get("status") == "running":
        age = time.time() - float(data.get("updated_at") or 0)
        data["heartbeat_age_seconds"] = round(age, 1)
        data["stale"] = age > 300  # 5 min without an update => suspicious
    return data


def render_monitor_text(data: dict, bar_width: int = 20) -> str:
    """Human-readable one-screen summary (text-only, no LLM)."""
    step = int(data.get("step_index") or 0)
    total = int(data.get("max_steps") or 0)
    pct = min(100, round(step * 100 / total)) if total else 0
    filled = round(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)
    budget = data.get("budget") or {}
    limits = budget.get("limits") or {}
    lines = [
        f"run_id: {data.get('run_id','')}  status: {data.get('status','?')}",
        f"phase: {data.get('current_phase','?')} -> {data.get('next_phase','?')}",
        f"rounds: {step}/{total or '?'}  [{bar}] {pct}%",
        f"tokens: {budget.get('total_tokens',0)}"
        + (f"/{limits.get('max_tokens')}" if limits.get('max_tokens') else "")
        + f"  usd: {budget.get('estimated_usd',0.0)}"
        + (f"/{limits.get('max_usd')}" if limits.get('max_usd') else "")
        + f"  calls: {budget.get('calls',0)}  budget:{budget.get('status','ok')}",
        f"think time: total={budget.get('duration_seconds_total',0.0)}s"
        + f" avg={budget.get('duration_seconds_avg',0.0)}s"
        + f" last={budget.get('duration_seconds_last',0.0)}s"
        + f" max={budget.get('duration_seconds_max',0.0)}s",
        f"last: {data.get('last_summary','')}",
    ]
    if data.get("stale"):
        lines.append(f"⚠️ heartbeat stale ({data.get('heartbeat_age_seconds')}s, pid {data.get('pid')})")
    if data.get("error"):
        lines.append(f"error: {data.get('error')}")
    return "\n".join(lines)


__all__ = ["RunMonitor", "read_monitor", "render_monitor_text"]
