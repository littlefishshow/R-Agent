from __future__ import annotations

import subprocess
from pathlib import Path


def git_preflight(project_dir: str | Path, *, require_standalone: bool = True) -> dict:
    root = Path(project_dir).expanduser().resolve()
    result = {
        "project_dir": str(root),
        "git_available": False,
        "repo_root": "",
        "has_head": False,
        "dirty": False,
        "standalone": False,
        "warnings": [],
    }
    probe = _git(root, ["rev-parse", "--show-toplevel"])
    if probe["returncode"] != 0:
        result["warnings"].append("target is not a git repository; versioning/rollback will be unavailable")
        return result
    repo_root = Path(probe["stdout"].strip()).resolve()
    result["git_available"] = True
    result["repo_root"] = str(repo_root)
    result["standalone"] = repo_root == root
    if require_standalone and not result["standalone"]:
        result["warnings"].append(f"target is inside another git repo ({repo_root}); use a standalone project repo to avoid versioning the wrong tree")
    head = _git(root, ["rev-parse", "--verify", "HEAD"])
    result["has_head"] = head["returncode"] == 0
    if not result["has_head"]:
        result["warnings"].append("target git repository has no baseline commit")
    status = _git(root, ["status", "--porcelain=v1"])
    if status["returncode"] == 0 and status["stdout"].strip():
        result["dirty"] = True
        result["warnings"].append("target git worktree is dirty before autoresearch starts")
    return result


def _git(cwd: Path, args: list[str]) -> dict:
    try:
        completed = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=20)
        return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    except Exception as exc:
        return {"returncode": None, "stdout": "", "stderr": str(exc)}


__all__ = ["git_preflight"]
