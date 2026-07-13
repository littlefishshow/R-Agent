from __future__ import annotations

import json
import os
import time
from pathlib import Path


def gate_state_path(root: str | Path) -> Path:
    return Path(root) / ".autoresearch" / "gate_signals.json"


def default_gate_state() -> dict:
    return {
        "version": 1,
        "updated_at": time.time(),
        "best_experiment_id": "",
        "experiment_count": 0,
        "pareto_count": 0,
        "pareto_ids": [],
        "pareto_changed": False,
        "plateau_counter": 0,
        "plan_still_valid": True,
        "needs_replan": False,
        "blocked_reason": "",
    }


def load_gate_state(root: str | Path) -> dict:
    path = gate_state_path(root)
    if not path.exists():
        return default_gate_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_gate_state()
    return normalize_gate_state(data if isinstance(data, dict) else {})


def normalize_gate_state(data: dict) -> dict:
    state = default_gate_state()
    state.update({k: v for k, v in dict(data or {}).items() if k in state})
    state["experiment_count"] = int(state.get("experiment_count") or 0)
    state["pareto_count"] = int(state.get("pareto_count") or 0)
    state["pareto_ids"] = [str(v) for v in state.get("pareto_ids") or []]
    state["plateau_counter"] = int(state.get("plateau_counter") or 0)
    state["pareto_changed"] = bool(state.get("pareto_changed"))
    state["plan_still_valid"] = bool(state.get("plan_still_valid"))
    state["needs_replan"] = bool(state.get("needs_replan"))
    state["best_experiment_id"] = str(state.get("best_experiment_id") or "")
    state["blocked_reason"] = str(state.get("blocked_reason") or "")
    return state


def save_gate_state(root: str | Path, state: dict) -> Path:
    path = gate_state_path(root)
    normalized = normalize_gate_state(state)
    normalized["updated_at"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def update_gate_state_from_experiment_state(root: str | Path, experiment_state: dict, *, major_error: bool = False) -> dict:
    prev = load_gate_state(root)
    experiments = experiment_state.get("experiments") or []
    pareto = experiment_state.get("pareto_front") or []
    best = experiment_state.get("best_experiment") or {}
    best_id = str(best.get("experiment_id") or "")
    exp_count = len(experiments)
    pareto_count = len(pareto)
    pareto_ids = [str(item.get("experiment_id") or "") for item in pareto if isinstance(item, dict)]
    pareto_changed = (
        best_id != prev.get("best_experiment_id")
        or pareto_ids != list(prev.get("pareto_ids") or [])
    )
    plateau = 0 if pareto_changed else int(prev.get("plateau_counter") or 0) + (1 if exp_count else 0)
    state = {
        **prev,
        "best_experiment_id": best_id,
        "experiment_count": exp_count,
        "pareto_count": pareto_count,
        "pareto_ids": pareto_ids,
        "pareto_changed": pareto_changed,
        "plateau_counter": plateau,
        "plan_still_valid": not major_error,
        "needs_replan": bool(major_error or (not pareto_changed and plateau > 0)),
        "blocked_reason": "major_error" if major_error else "",
    }
    save_gate_state(root, state)
    return state


__all__ = [
    "default_gate_state",
    "gate_state_path",
    "load_gate_state",
    "normalize_gate_state",
    "save_gate_state",
    "update_gate_state_from_experiment_state",
]
