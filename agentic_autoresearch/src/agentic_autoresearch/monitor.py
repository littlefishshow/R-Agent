from __future__ import annotations

import os
import time
from pathlib import Path

from .command_monitor import read_command_status
from .debug import read_inflight
from .utils import atomic_write_json, read_json


class RunMonitor:
    """Small heartbeat monitor for the automated runner."""

    def __init__(self, path: str | Path, *, run_id: str):
        self.path = Path(path)
        existing = read_json(self.path, {}) or {}
        self.data = {
            "run_id": run_id,
            "pid": os.getpid(),
            "status": existing.get("status", "queued"),
            "current_step": existing.get("current_step", "plan"),
            "next_step": existing.get("next_step", ""),
            "cycle": int(existing.get("cycle") or 0),
            "max_cycles": int(existing.get("max_cycles") or 0),
            "last_summary": existing.get("last_summary", ""),
            "last_error": "",
            "started_at": existing.get("started_at") or time.time(),
            "updated_at": time.time(),
            "finished_at": None,
            "usage": existing.get("usage", {}),
            "last_step_stats": existing.get("last_step_stats", {}),
            "totals": existing.get("totals", {"step_seconds": 0.0, "llm_seconds": 0.0, "tool_seconds": 0.0}),
        }
        self._write()

    def _write(self) -> None:
        self.data["updated_at"] = time.time()
        atomic_write_json(self.path, self.data)

    def start(self, *, max_cycles: int) -> None:
        self.data["status"] = "running"
        self.data["max_cycles"] = int(max_cycles)
        self._write()

    def step_start(self, *, cycle: int, step: str) -> None:
        self.data["status"] = "running"
        self.data["cycle"] = int(cycle)
        self.data["current_step"] = step
        self.data["next_step"] = "(running)"
        self.data["last_summary"] = f"starting {step}"
        self._write()

    def step_finish(self, *, cycle: int, step: str, next_step: str, summary: str, usage: dict | None = None,
                    step_stats: dict | None = None) -> None:
        self.data["status"] = "running"
        self.data["cycle"] = int(cycle)
        self.data["current_step"] = step
        self.data["next_step"] = next_step
        self.data["last_summary"] = str(summary or "")[:1000]
        if usage is not None:
            self.data["usage"] = usage
        if step_stats is not None:
            self.data["last_step_stats"] = step_stats
            totals = dict(self.data.get("totals") or {})
            totals["step_seconds"] = round(float(totals.get("step_seconds") or 0.0) + float(step_stats.get("duration_seconds") or 0.0), 3)
            totals["llm_seconds"] = round(float(totals.get("llm_seconds") or 0.0) + float(step_stats.get("llm_seconds") or 0.0), 3)
            totals["tool_seconds"] = round(float(totals.get("tool_seconds") or 0.0) + float(step_stats.get("tool_seconds") or 0.0), 3)
            self.data["totals"] = totals
        self._write()

    def finish(self, *, status: str = "completed", error: str = "", usage: dict | None = None) -> None:
        self.data["status"] = status
        self.data["finished_at"] = time.time()
        self.data["last_error"] = str(error or "")[:2000]
        if usage is not None:
            self.data["usage"] = usage
        self._write()


def read_monitor(path: str | Path) -> dict:
    data = read_json(path, {}) or {}
    if not data:
        return {"status": "unknown", "path": str(path)}
    if data.get("status") == "running":
        age = time.time() - float(data.get("updated_at") or 0)
        data["heartbeat_age_seconds"] = round(age, 1)
        data["stale"] = age > 300
    try:
        root = Path(path).parent.parent
        inflight = read_inflight(root)
        if inflight:
            data["inflight"] = inflight
        latest_command = read_command_status(root, latest=True)
        if latest_command.get("status") != "unknown":
            data["latest_command"] = latest_command
    except Exception:
        pass
    return data


def render_monitor_text(data: dict, *, bar_width: int = 20) -> str:
    cycle = int(data.get("cycle") or 0)
    total = int(data.get("max_cycles") or 0)
    pct = min(100, round(cycle * 100 / total)) if total else 0
    filled = round(bar_width * pct / 100)
    bar = "#" * filled + "." * (bar_width - filled)
    usage = data.get("usage") or {}
    last_stats = data.get("last_step_stats") or {}
    totals = data.get("totals") or {}
    lines = [
        f"run_id: {data.get('run_id', '')} status: {data.get('status', '?')}",
        f"step: {data.get('current_step', '?')} -> {data.get('next_step', '?')}",
        f"cycles: {cycle}/{total or '?'} [{bar}] {pct}%",
        f"llm_calls: {usage.get('llm_calls', 0)} tool_calls: {usage.get('tool_calls', 0)}",
        "time: "
        f"step_total={totals.get('step_seconds', 0.0)}s "
        f"llm={totals.get('llm_seconds', 0.0)}s "
        f"tools={totals.get('tool_seconds', 0.0)}s",
        "last_step: "
        f"duration={last_stats.get('duration_seconds', 0.0)}s "
        f"llm={last_stats.get('llm_seconds', 0.0)}s "
        f"tools={last_stats.get('tool_seconds', 0.0)}s "
        f"tokens={((last_stats.get('usage_delta') or {}).get('total_tokens', 0))}",
        f"last: {data.get('last_summary', '')}",
    ]
    inflight = data.get("inflight") or {}
    if inflight:
        lines.append(f"inflight: {inflight.get('kind')} age={inflight.get('age_seconds')}s detail={inflight.get('detail', '')}")
    latest_command = data.get("latest_command") or {}
    if latest_command:
        lines.append(
            "latest_command: "
            f"{latest_command.get('kind')} status={latest_command.get('status')} "
            f"duration={latest_command.get('duration_seconds')}s "
            f"heartbeat_age={latest_command.get('heartbeat_age_seconds', 0.0)}s "
            f"cmd={latest_command.get('command', '')}"
        )
    if data.get("last_error"):
        lines.append(f"error: {data.get('last_error')}")
    return "\n".join(lines)
