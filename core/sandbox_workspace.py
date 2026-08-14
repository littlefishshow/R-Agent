"""Per-session sandbox compatibility layer.

This module provides deer-flow-style workspace semantics for session-scoped
tools and GUI resources. It is deliberately opt-in and lazy:

* no directory is created until ``ensure()`` or ``resolve_virtual()`` is called;
* each session gets an isolated root under ``sandbox/sessions/<session_id>``;
* virtual paths map to local directories:
  ``/mnt/user-data/workspace``, ``uploads``, ``outputs``, and ``/mnt/skills``;
* path traversal and unknown virtual roots are rejected.
"""

from __future__ import annotations

import re
from pathlib import Path


VIRTUAL_WORKSPACE = "/mnt/user-data/workspace"
VIRTUAL_UPLOADS = "/mnt/user-data/uploads"
VIRTUAL_OUTPUTS = "/mnt/user-data/outputs"
VIRTUAL_SKILLS = "/mnt/skills"


def safe_sandbox_id(session_id: str) -> str:
    raw = str(session_id or "default").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe[:100] or "default"


class SandboxWorkspace:
    """Lazy, path-confined per-session workspace."""

    def __init__(
        self,
        session_id: str,
        root: str | Path = "sandbox/sessions",
        skills_root: str | Path = "skills",
    ):
        self.sandbox_id = safe_sandbox_id(session_id)
        self.base_root = Path(root).resolve()
        self.root = (self.base_root / self.sandbox_id).resolve()
        self.skills_root = Path(skills_root).resolve()

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def todo_lists(self) -> Path:
        return self.root / "todo_lists"

    def ensure(self) -> "SandboxWorkspace":
        for directory in (self.workspace, self.uploads, self.outputs):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def describe(self) -> dict:
        return {
            "sandbox_id": self.sandbox_id,
            "root": str(self.root),
            "workspace": str(self.workspace),
            "uploads": str(self.uploads),
            "outputs": str(self.outputs),
            "todo_lists": str(self.todo_lists),
            "skills": str(self.skills_root),
            "virtual_paths": {
                VIRTUAL_WORKSPACE: str(self.workspace),
                VIRTUAL_UPLOADS: str(self.uploads),
                VIRTUAL_OUTPUTS: str(self.outputs),
                VIRTUAL_SKILLS: str(self.skills_root),
            },
        }

    def resolve_virtual(self, virtual_path: str) -> Path:
        """Resolve a supported virtual path and reject escapes."""
        raw = str(virtual_path or "").strip().replace("\\", "/")
        mappings = (
            (VIRTUAL_WORKSPACE, self.workspace),
            (VIRTUAL_UPLOADS, self.uploads),
            (VIRTUAL_OUTPUTS, self.outputs),
            (VIRTUAL_SKILLS, self.skills_root),
        )
        for prefix, local_root in mappings:
            if raw == prefix or raw.startswith(prefix + "/"):
                if prefix != VIRTUAL_SKILLS:
                    self.ensure()
                suffix = raw[len(prefix):].lstrip("/")
                candidate = (local_root / suffix).resolve()
                resolved_root = local_root.resolve()
                if candidate != resolved_root and resolved_root not in candidate.parents:
                    raise ValueError("virtual path escapes sandbox root")
                return candidate
        raise ValueError(f"unsupported virtual path: {virtual_path}")
