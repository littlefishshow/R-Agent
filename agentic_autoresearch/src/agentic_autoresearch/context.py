from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .types import AutoResearchConfig, StepSpec
from .utils import read_json, safe_relative_path, truncate_middle


def build_step_context(config: AutoResearchConfig, spec: StepSpec, *, previous_report: dict[str, Any] | None = None) -> dict[str, Any]:
    root = config.root()
    state = read_json(config.state_file(), {}) or {}
    files = {}
    for rel in spec.context_files:
        path = root / rel
        if path.exists() and path.is_file():
            files[rel] = truncate_middle(path.read_text(encoding="utf-8", errors="replace"), 5000)
    artifacts = _artifact_digest(root)
    tree = _project_tree(root)
    payload = {
        "project_root": str(root),
        "run_id": config.run_id,
        "step": spec.name,
        "state": state,
        "previous_report": previous_report or {},
        "project_tree": tree,
        "files": files,
        "artifacts": artifacts,
        "operating_rules": [
            "Each step is a complete agent loop with its own local message history.",
            "The outer runner switches steps only when the final answer contains the required done tag set to true.",
            "Keep durable state in project files or .autoresearch files, not in hidden conversation memory.",
            "Evaluation harness files such as eval.py/eval.sh are protected by default.",
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) <= config.context_char_budget:
        return payload
    payload["project_tree"] = payload["project_tree"][:80]
    payload["artifacts"] = payload["artifacts"][:20]
    for key in list(payload["files"]):
        payload["files"][key] = truncate_middle(payload["files"][key], 1600)
    return payload


def _project_tree(root: Path, *, limit: int = 180) -> list[str]:
    rows = []
    skip = {".git", ".autoresearch", "__pycache__", ".venv", "venv", "node_modules"}
    for path in sorted(root.rglob("*")):
        if any(part in skip for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            rows.append(str(path.relative_to(root)))
        if len(rows) >= limit:
            break
    return rows


def _artifact_digest(root: Path, *, limit: int = 40) -> list[dict[str, Any]]:
    base = root / ".autoresearch" / "artifacts"
    if not base.exists():
        return []
    rows = []
    for path in sorted(base.glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if not path.is_file():
            continue
        rows.append({
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "mtime": path.stat().st_mtime,
        })
        if len(rows) >= limit:
            break
    return rows

