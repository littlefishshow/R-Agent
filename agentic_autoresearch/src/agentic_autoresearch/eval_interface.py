from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .utils import atomic_write_json, read_json


def ensure_eval_interface(root: str | Path) -> dict[str, Any]:
    """Discover and persist a generic project eval interface.

    This is intentionally code-first. The LLM should not have to infer how to
    read the current metric from free-form logs on every conclude step.
    """
    root = Path(root).expanduser().resolve()
    program = _read_text(root / "program.md")
    criteria = parse_completion_criteria(program)
    interface = {
        "version": 1,
        "created_at": time.time(),
        "metric_file": "metrics.json",
        "eval_command": "bash eval.sh" if (root / "eval.sh").exists() else "",
        "train_command": "bash train/train.sh" if (root / "train" / "train.sh").exists() else "",
        "submission_file": _first_existing_or_default(root, ["outputs/submission.json", "train/outputs/submission.json"]),
        "train_verification_file": _first_existing_or_default(root, ["outputs/train_verification.json", "train/outputs/train_verification.json"]),
        "criteria": criteria,
        "notes": [
            "read_eval reads metrics.json and compares it to criteria.",
            "eval_command can regenerate metrics.json when a project has eval.sh.",
        ],
    }
    path = root / ".autoresearch" / "eval_interface.json"
    atomic_write_json(path, interface)
    return interface


def load_eval_interface(root: str | Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    path = root / ".autoresearch" / "eval_interface.json"
    data = read_json(path, {}) or {}
    if not data:
        data = ensure_eval_interface(root)
    return data


def read_eval(root: str | Path) -> dict[str, Any]:
    """Read the current eval metric and solved status from project files."""
    root = Path(root).expanduser().resolve()
    interface = load_eval_interface(root)
    metric_file = root / str(interface.get("metric_file") or "metrics.json")
    metrics = read_json(metric_file, {}) or {}
    criteria = interface.get("criteria") or {}
    metric_name = str(criteria.get("metric_name") or metrics.get("metric_name") or "primary_metric")
    value = _metric_value(metrics, metric_name)
    higher = _parse_bool(metrics.get("higher_is_better"), default=bool(criteria.get("higher_is_better", False)))
    solved = is_solved(value, criteria, higher_is_better=higher)
    if not solved:
        solved = infer_solved_without_threshold(value, metrics, higher_is_better=higher)
    return {
        "interface": interface,
        "metrics_path": str(metric_file.relative_to(root)) if metric_file.exists() else str(metric_file),
        "metrics_exists": metric_file.exists(),
        "metrics": metrics,
        "metric_name": metric_name,
        "metric_value": value,
        "higher_is_better": higher,
        "criteria": criteria,
        "solved": solved,
        "read_at": time.time(),
    }


def parse_completion_criteria(program_text: str) -> dict[str, Any]:
    text = str(program_text or "")
    metric_name = ""
    higher = None
    threshold = None
    op = ""

    metric_match = re.search(r"metric_name[`*\\s:-]+([A-Za-z0-9_.-]+)", text, flags=re.I)
    if metric_match:
        metric_name = metric_match.group(1).strip("`* ")
    if not metric_name:
        primary_match = re.search(r"primary_metric[`*\\s:-]+([A-Za-z0-9_.-]+)", text, flags=re.I)
        if primary_match:
            metric_name = primary_match.group(1).strip("`* ")
    if not metric_name:
        z_match = re.search(r"\bz\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)", text, flags=re.I)
        if z_match:
            metric_name = "z"

    hib_match = re.search(r"higher_is_better[`*\\s:-]+(true|false)", text, flags=re.I)
    if hib_match:
        higher = hib_match.group(1).lower() == "true"
    elif re.search(r"\blower (?:is )?better\b|\bminimi[sz]e\b", text, flags=re.I):
        higher = False
    elif re.search(r"\bhigher (?:is )?better\b|\bmaximi[sz]e\b", text, flags=re.I):
        higher = True

    threshold_patterns = [
        r"([A-Za-z0-9_.-]+)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)",
        r"official evaluation reports:\s*.*?([A-Za-z0-9_.-]+)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)",
    ]
    for pattern in threshold_patterns:
        for match in re.finditer(pattern, text, flags=re.I | re.S):
            name, candidate_op, raw = match.group(1), match.group(2), match.group(3)
            if metric_name and name != metric_name:
                continue
            metric_name = metric_name or name
            op = candidate_op
            try:
                threshold = float(raw)
            except Exception:
                threshold = None
            break
        if threshold is not None:
            break

    return {
        "metric_name": metric_name or "primary_metric",
        "higher_is_better": bool(higher) if higher is not None else False,
        "threshold": threshold,
        "op": op,
    }


def is_solved(value: float | None, criteria: dict[str, Any], *, higher_is_better: bool = False) -> bool:
    if value is None:
        return False
    threshold = criteria.get("threshold")
    if threshold is None:
        return False
    op = str(criteria.get("op") or "").strip()
    if op == "<=":
        return value <= float(threshold)
    if op == "<":
        return value < float(threshold)
    if op == ">=":
        return value >= float(threshold)
    if op == ">":
        return value > float(threshold)
    return value >= float(threshold) if higher_is_better else value <= float(threshold)


def infer_solved_without_threshold(value: float | None, metrics: dict[str, Any], *, higher_is_better: bool) -> bool:
    """Best-effort solved inference when program.md has no explicit threshold.

    Many small benchmark metrics are normalized: 1.0 is perfect for higher-is-
    better scores and 0.0 is perfect for lower-is-better losses. This avoids
    wasting cycles after a project has already reached a mathematically saturated
    score.
    """
    if value is None:
        return False
    eps = 1e-12
    if higher_is_better and value >= 1.0 - eps:
        return True
    if (not higher_is_better) and value <= eps:
        return True
    # Common auxiliary exactness fields.
    for key in ("accuracy", "exact_accuracy", "row_accuracy", "top1_accuracy"):
        try:
            if float(metrics.get(key)) >= 1.0 - eps:
                primary = metrics.get("primary_metric", metrics.get("score", value))
                return (not higher_is_better) or float(primary) >= 1.0 - eps
        except Exception:
            continue
    try:
        correct = int(metrics.get("correct"))
        total = int(metrics.get("total"))
        if total > 0 and correct == total and higher_is_better and value >= 0.999:
            return True
    except Exception:
        pass
    return False


def _metric_value(metrics: dict[str, Any], metric_name: str) -> float | None:
    candidates = [metric_name, "primary_metric", "z", "score", "metric", "loss"]
    for key in candidates:
        if key in metrics:
            try:
                return float(metrics[key])
            except Exception:
                continue
    return None


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return default


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _first_existing_or_default(root: Path, rels: list[str]) -> str:
    for rel in rels:
        if (root / rel).exists():
            return rel
    return rels[0]
