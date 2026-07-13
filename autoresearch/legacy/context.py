"""Artifact, command, and context helpers for the legacy AutoResearch loop."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from autoresearch.legacy.services import ProjectBoundary, _safe_slug, normalize_versioning_policy
from autoresearch.observability.debug import inflight_finish, inflight_start
from autoresearch.legacy.types import AutoResearchObservation, AutoResearchSettings, ContextBucket, DEFAULT_CONTEXT_BUCKETS

class AutoResearchArtifactStore:
    def __init__(self, settings: AutoResearchSettings):
        self.settings = settings
        self.root = settings.artifacts_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, *, kind: str, rationale: str, content: str, extension: str = "txt") -> str:
        import secrets

        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}" + f"-{secrets.token_hex(3)}"
        filename = "_".join([
            stamp,
            _safe_slug(self.settings.project_id),
            _safe_slug(rationale, "trial"),
            _safe_slug(kind),
        ]) + f".{_safe_slug(extension, 'txt', 12)}"
        path = self.root / filename
        path.write_text(content or "", encoding="utf-8")
        return str(path)


class ProjectConfinedCommandRunner:
    """Fast shell runner for project-local autoresearch commands.

    It does not call global run_command, so project-confined commands are not
    blocked by high-risk warnings.  Boundary checks reject obvious path escapes.
    """

    def __init__(self, project_dir: str | Path, timeout_seconds: int = 300):
        self.boundary = ProjectBoundary(project_dir)
        self.timeout_seconds = timeout_seconds

    def _command_env(self) -> dict[str, str]:
        shim_dir = self.boundary.project_dir / ".autoresearch" / "bin"
        shim_dir.mkdir(parents=True, exist_ok=True)
        python_shim = shim_dir / "python"
        if not python_shim.exists():
            python_shim.write_text(
                "#!/usr/bin/env sh\n"
                f"exec {shlex.quote(sys.executable)} \"$@\"\n",
                encoding="utf-8",
            )
            python_shim.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
        return env

    def run(self, command: str, cwd: str | Path = ".") -> dict:
        workdir = self.boundary.resolve(cwd)
        self.boundary.validate_command_surface(command)
        started = time.time()
        inflight_start(self.boundary.project_dir, "shell", detail=command[:300], cwd=str(workdir))
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._command_env(),
            )
            result = {
                "command": command,
                "cwd": str(workdir),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "duration_seconds": round(time.time() - started, 3),
            }
            inflight_finish(
                self.boundary.project_dir,
                "shell",
                detail=command[:300],
                returncode=completed.returncode,
                duration_seconds=result["duration_seconds"],
            )
            return result
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": command,
                "cwd": str(workdir),
                "returncode": None,
                "stdout": exc.stdout or "",
                "stderr": (exc.stderr or "") + f"\nCommand timed out after {self.timeout_seconds}s",
                "duration_seconds": round(time.time() - started, 3),
                "timeout": True,
            }
            inflight_finish(
                self.boundary.project_dir,
                "shell",
                detail=command[:300],
                returncode=None,
                timeout=True,
                duration_seconds=result["duration_seconds"],
            )
            return result


class AutoResearchContextManager:
    def __init__(self, settings: AutoResearchSettings):
        self.settings = settings
        self.boundary = ProjectBoundary(settings.project_dir)

    def read_program(self) -> str:
        path = self.boundary.ensure_inside(self.settings.program_file())
        if not path.exists():
            return ""
        return self._truncate(path.read_text(encoding="utf-8"), self.settings.program_char_budget)

    def default_state(self) -> dict:
        return {
            "summary": "",
            "observations": [],
            "buckets": {name: [] for name in DEFAULT_CONTEXT_BUCKETS},
            "experiments": [],
            "pareto_front": [],
            "best_experiment": None,
            "useful_failures": [],
            "last_finalized_experiment_count": 0,
            "versioning_policy": normalize_versioning_policy(self.settings.versioning_policy),
            "use_git_versioning": bool(self.settings.use_git_versioning),
        }

    def load_state(self) -> dict:
        path = self.boundary.ensure_inside(self.settings.state_file())
        if not path.exists():
            return self.default_state()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self.default_state()
        except Exception:
            return self.default_state()
        data.setdefault("summary", "")
        data.setdefault("observations", [])
        data.setdefault("experiments", [])
        data.setdefault("pareto_front", [])
        data.setdefault("best_experiment", None)
        data.setdefault("useful_failures", [])
        data.setdefault("last_finalized_experiment_count", 0)
        data["versioning_policy"] = normalize_versioning_policy(self.settings.versioning_policy)
        data["use_git_versioning"] = bool(self.settings.use_git_versioning)
        buckets = data.setdefault("buckets", {})
        for name in DEFAULT_CONTEXT_BUCKETS:
            buckets.setdefault(name, [])
        return data

    def add_to_bucket(self, state: dict, bucket_name: str, text: str) -> None:
        bucket_name = bucket_name if bucket_name in DEFAULT_CONTEXT_BUCKETS else "raw_observations"
        bucket = ContextBucket(
            bucket_name,
            list((state.setdefault("buckets", {}).get(bucket_name) or [])),
            max_items=self.settings.bucket_max_items,
            max_item_chars=self.settings.bucket_item_char_budget,
        )
        bucket.add(text)
        state["buckets"][bucket_name] = bucket.compact()

    def save_state(self, state: dict) -> None:
        import os

        state["versioning_policy"] = normalize_versioning_policy(self.settings.versioning_policy)
        state["use_git_versioning"] = bool(self.settings.use_git_versioning)
        state.setdefault("last_finalized_experiment_count", 0)
        path = self.boundary.ensure_inside(self.settings.state_file())
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def build_parent_context(self, observations: Iterable[AutoResearchObservation] = ()) -> str:
        state = self.load_state()
        recent = list(state.get("observations") or [])[-self.settings.recent_observation_limit :]
        recent.extend([obs.compact() for obs in observations])
        bucket_payload = {}
        for name in DEFAULT_CONTEXT_BUCKETS:
            items = list((state.get("buckets", {}) or {}).get(name) or [])[-self.settings.bucket_max_items :]
            bucket_payload[name] = [self._truncate(str(item), self.settings.bucket_item_char_budget) for item in items]
        payload = {
            "project_id": self.settings.project_id,
            "program_md": self.read_program(),
            "modular_context": bucket_payload,
            "state_summary": self._truncate(str(state.get("summary") or ""), self.settings.summary_char_budget),
            "recent_observations": recent[-self.settings.recent_observation_limit :],
            "context_policy": {
                "max_chars": self.settings.context_char_budget,
                "raw_outputs": "archived separately; parent sees summaries and artifact paths only",
            },
            "versioning": {
                "policy": normalize_versioning_policy(self.settings.versioning_policy),
                "use_git_versioning": bool(self.settings.use_git_versioning),
                "best_experiment": state.get("best_experiment"),
                "pareto_count": len(state.get("pareto_front") or []),
            },
        }
        return self._truncate_middle(json.dumps(payload, ensure_ascii=False, indent=2), self.settings.context_char_budget)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    @staticmethod
    def _truncate_middle(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        keep_head = max_chars // 2
        keep_tail = max_chars - keep_head - 40
        return text[:keep_head].rstrip() + "\n...<context clipped>...\n" + text[-keep_tail:].lstrip()


