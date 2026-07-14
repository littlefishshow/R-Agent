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
    run_id = payload.pop("run_id", "") or os.environ.get("AUTORESEARCH_RUN_ID", "")
    row = {
        "ts": time.time(),
        "time": time.strftime("%F %T"),
        "event": event,
        "run_id": run_id,
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
    run_id = payload.pop("run_id", "") or os.environ.get("AUTORESEARCH_RUN_ID", "")
    row = {
        "pid": os.getpid(),
        "run_id": run_id,
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
    run_id = payload.pop("run_id", "") or os.environ.get("AUTORESEARCH_RUN_ID", "")
    p = debug_dir(root) / "inflight.json"
    old = {}
    try:
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        old = {}
    elapsed = None
    if old.get("started_at"):
        elapsed = round(time.time() - float(old["started_at"]), 3)
    debug_event(root, f"{kind}_finish", run_id=run_id, elapsed_seconds=elapsed, **payload)
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


def build_debug_summary(root: str | Path, *, tail: int = 12) -> str:
    root = Path(root)
    monitor = _read_json(root / ".autoresearch" / "monitor.json")
    budget = _read_json(root / ".autoresearch" / "budget.json") or (monitor.get("budget") if isinstance(monitor, dict) else {}) or {}
    state = _read_json(root / ".autoresearch" / "state.json")
    gate = _read_json(root / ".autoresearch" / "gate_signals.json")
    todo = _read_json(root / ".autoresearch" / "todo_state.json")
    inflight = read_inflight(root)
    events = _read_events(root / ".autoresearch" / "debug" / "debug.jsonl")

    lines = ["# AutoResearch Debug Summary", ""]
    lines.append(f"project: {root}")
    if monitor:
        lines.append(
            "monitor: "
            f"status={monitor.get('status')} phase={monitor.get('current_phase')}->{monitor.get('next_phase')} "
            f"step={monitor.get('step_index')}/{monitor.get('max_steps')} error={monitor.get('error') or ''}"
        )
        if monitor.get("updated_at"):
            lines.append(f"heartbeat_age_seconds: {round(time.time() - float(monitor.get('updated_at')), 1)}")
    else:
        lines.append("monitor: missing")

    if inflight:
        lines.append(
            "inflight: "
            f"kind={inflight.get('kind')} age={inflight.get('age_seconds')}s "
            f"phase={inflight.get('phase', '')} detail={inflight.get('detail', '')}"
        )
    else:
        lines.append("inflight: none")

    if budget:
        lines.append(
            "budget: "
            f"tokens={budget.get('total_tokens', 0)} calls={budget.get('calls', 0)} "
            f"usd={budget.get('estimated_usd', 0.0)} "
            f"think_total={budget.get('duration_seconds_total', 0.0)}s "
            f"think_max={budget.get('duration_seconds_max', 0.0)}s"
        )

    best = state.get("best_experiment") if isinstance(state, dict) else None
    if isinstance(best, dict) and best:
        lines.append(f"best: id={best.get('experiment_id')} decision={best.get('decision')} metrics={best.get('metrics')}")
    elif state:
        lines.append("best: none")

    if gate:
        lines.append(
            "gate: "
            f"pareto_changed={gate.get('pareto_changed')} plateau={gate.get('plateau_counter')} "
            f"plan_still_valid={gate.get('plan_still_valid')} needs_replan={gate.get('needs_replan')} "
            f"blocked={gate.get('blocked_reason') or ''}"
        )

    tasks = todo.get("tasks") if isinstance(todo, dict) else []
    if isinstance(tasks, list):
        counts = {}
        for task in tasks:
            status = str((task or {}).get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        lines.append(f"todo: total={len(tasks)} status_counts={counts}")
        for task in tasks[:5]:
            if isinstance(task, dict):
                lines.append(f"- {task.get('task_id')} [{task.get('status')}] {task.get('type')}: {task.get('goal')}")

    phase_events = [e for e in events if e.get("event") == "phase_finish"]
    if phase_events:
        lines.append("")
        lines.append("recent phases:")
        for event in phase_events[-5:]:
            lines.append(
                f"- {event.get('time')} {event.get('phase')} -> {event.get('next_phase')} "
                f"summary={event.get('summary', '')}"
            )

    shell_events = [e for e in events if e.get("event") == "shell_finish"]
    if shell_events:
        lines.append("")
        lines.append("recent shell:")
        for event in shell_events[-5:]:
            lines.append(
                f"- rc={event.get('returncode')} elapsed={event.get('elapsed_seconds')}s "
                f"detail={event.get('detail', '')[:160]}"
            )

    llm_events = [e for e in events if e.get("event") == "llm_finish"]
    if llm_events:
        lines.append("")
        lines.append("recent llm:")
        for event in llm_events[-5:]:
            lines.append(
                f"- elapsed={event.get('elapsed_seconds')}s phase={event.get('phase', '')} "
                f"detail={event.get('detail', '')}"
            )

    if events:
        lines.append("")
        lines.append("event tail:")
        for event in events[-max(1, int(tail)):]:
            lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    else:
        lines.append("events: none")

    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
