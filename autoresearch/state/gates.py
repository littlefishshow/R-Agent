from __future__ import annotations

import json
import os
import time
from pathlib import Path

from autoresearch.legacy.services import is_objective_metric


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
        "pareto_signature": [],
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
    state["pareto_signature"] = [str(v) for v in state.get("pareto_signature") or []]
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


def _member_signature(experiment: dict) -> str:
    """Objective-only fingerprint of one experiment.

    Two experiments with the same objective values collapse to the same string,
    so re-recording an identical result (same accuracy/f1/precision, only
    different runtime) does NOT look like progress. Telemetry keys are excluded
    via ``is_objective_metric``; experiments without any objective metric fall
    back to their id so distinct records still differ.
    """
    metrics = experiment.get("metrics") or {}
    objective = sorted(
        (str(k), round(float(v), 6))
        for k, v in metrics.items()
        if is_objective_metric(k)
        and isinstance(v, (int, float))
        and not isinstance(v, bool)
    )
    if objective:
        return json.dumps(objective, sort_keys=True)
    return "id:" + str(experiment.get("experiment_id") or "")


def _pareto_signature(pareto: list) -> list[str]:
    # Deduplicate: equal-objective points are mutually non-dominated and all
    # land on the front, so the raw list keeps growing while the metric is
    # frozen. Collapsing to the unique set of objective fingerprints keeps the
    # signature stable until a genuinely new objective value appears.
    return sorted({_member_signature(e) for e in pareto if isinstance(e, dict)})


def update_gate_state_from_experiment_state(root: str | Path, experiment_state: dict, *, major_error: bool = False) -> dict:
    prev = load_gate_state(root)
    experiments = experiment_state.get("experiments") or []
    pareto = experiment_state.get("pareto_front") or []
    best = experiment_state.get("best_experiment") or {}
    best_id = str(best.get("experiment_id") or "")
    exp_count = len(experiments)
    pareto_count = len(pareto)
    pareto_ids = [str(item.get("experiment_id") or "") for item in pareto if isinstance(item, dict)]
    # Progress is measured on the *objective-value* signature of the Pareto
    # front, not on experiment ids. Ids rotate whenever an equal-quality result
    # is recorded (all equal points are mutually non-dominated and pile onto the
    # front), which used to reset the plateau brake and let a converged task
    # spin to max_steps. The value signature is stable while the metric is
    # frozen, so the brake now accrues correctly.
    pareto_signature = _pareto_signature(pareto)
    prev_signature = list(prev.get("pareto_signature") or [])
    pareto_changed = pareto_signature != prev_signature
    plateau = 0 if pareto_changed else int(prev.get("plateau_counter") or 0) + (1 if exp_count else 0)
    state = {
        **prev,
        "best_experiment_id": best_id,
        "experiment_count": exp_count,
        "pareto_count": pareto_count,
        "pareto_ids": pareto_ids,
        "pareto_signature": pareto_signature,
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
