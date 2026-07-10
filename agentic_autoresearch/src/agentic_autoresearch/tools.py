from __future__ import annotations

import fnmatch
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from .command_monitor import read_command_status, run_monitored_command
from .debug import DebugLog
from .delegate import delegate_task as run_child_delegate
from .eval_interface import read_eval as read_eval_state
from .types import ToolSpec
from .utils import safe_relative_path


class ToolRegistry:
    """Small in-process tool registry.

    This intentionally mirrors the useful part of R-Agent's registry without
    multiprocessing, CLI rendering, or global workspace assumptions.
    """

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def schemas(self, allowed: tuple[str, ...] | list[str] | set[str] | None = None) -> list[dict[str, Any]]:
        allowed_set = set(allowed or [])
        specs = [
            spec for name, spec in sorted(self._tools.items())
            if not allowed_set or name in allowed_set
        ]
        return [spec.schema() for spec in specs]

    def execute(self, name: str, args_json: str | dict[str, Any] | None = None) -> str:
        if name not in self._tools:
            return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)
        try:
            args = args_json
            if isinstance(args_json, str):
                args = json.loads(args_json or "{}")
            if args is None:
                args = {}
            if not isinstance(args, dict):
                return json.dumps({"error": "tool arguments must be a JSON object"}, ensure_ascii=False)
            result = self._tools[name].handler(**args)
            return json.dumps({"success": True, "result": result}, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)


def build_default_tools(
    root: str | Path,
    *,
    command_timeout_seconds: int = 300,
    client=None,
    model: str = "",
    debug: DebugLog | None = None,
    enable_delegate: bool = False,
) -> ToolRegistry:
    root = Path(root).expanduser().resolve()
    registry = ToolRegistry()

    def read_file(path: str, offset: int = 1, limit: int = 240) -> dict:
        target = safe_relative_path(root, path)
        if not target.exists() or not target.is_file():
            return {"error": f"file not found: {path}"}
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, int(offset or 1) - 1)
        end = min(len(lines), start + max(1, int(limit or 240)))
        content = "\n".join(f"{i + 1}|{lines[i]}" for i in range(start, end))
        return {
            "path": str(target.relative_to(root)),
            "offset": start + 1,
            "limit": int(limit or 240),
            "total_lines": len(lines),
            "content": content,
        }

    def write_file(path: str, content: str, mode: str = "replace") -> dict:
        target = safe_relative_path(root, path)
        rel_parts = target.relative_to(root).parts
        if target.name in {"eval.py", "eval.sh"} or (
            rel_parts and rel_parts[0] in {"eval", "evaluation"}
        ):
            return {"error": f"refusing to write protected evaluation path: {path}"}
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = str(mode or "replace")
        old = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        if mode == "append":
            new = old + str(content)
        else:
            new = str(content)
        target.write_text(new, encoding="utf-8")
        return {
            "path": str(target.relative_to(root)),
            "bytes": len(new.encode("utf-8")),
            "changed": old != new,
        }

    def search_files(pattern: str, path: str = ".", target: str = "content", limit: int = 100) -> dict:
        base = safe_relative_path(root, path)
        results: list[str] = []
        max_results = max(1, int(limit or 100))
        if not base.exists():
            return {"error": f"path not found: {path}", "results": []}
        for current, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {".git", ".autoresearch", "__pycache__", ".venv", "venv", "node_modules"}]
            for name in files:
                file_path = Path(current) / name
                rel = str(file_path.relative_to(root))
                if target == "files":
                    if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
                        results.append(rel)
                else:
                    try:
                        for idx, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                            if pattern in line:
                                results.append(f"{rel}:{idx}:{line[:240]}")
                                break
                    except Exception:
                        continue
                if len(results) >= max_results:
                    return {"results": results, "truncated": True}
        return {"results": results, "truncated": False}

    def run_command(command: str, timeout_seconds: int | None = None) -> dict:
        command = str(command or "").strip()
        if not command:
            return {"error": "command is required"}
        _validate_command_surface(root, command)
        timeout = int(timeout_seconds or command_timeout_seconds)
        return run_monitored_command(root, command, timeout_seconds=timeout, kind="command")

    def run_train(timeout_seconds: int | None = None) -> dict:
        command = "bash train/train.sh"
        _validate_command_surface(root, command)
        return run_monitored_command(
            root,
            command,
            timeout_seconds=int(timeout_seconds or command_timeout_seconds),
            kind="train",
        )

    def run_eval(timeout_seconds: int | None = None) -> dict:
        command = "bash eval.sh"
        _validate_command_surface(root, command)
        return run_monitored_command(
            root,
            command,
            timeout_seconds=int(timeout_seconds or command_timeout_seconds),
            kind="eval",
        )

    def run_pipeline(train_timeout_seconds: int | None = None, eval_timeout_seconds: int | None = None) -> dict:
        """Run train, then eval, then read structured metric status."""
        train_result = run_train(timeout_seconds=train_timeout_seconds)
        if train_result.get("status") != "ok":
            return {
                "status": "train_failed",
                "train": train_result,
                "eval": None,
                "eval_state": read_eval_state(root),
            }
        eval_result = run_eval(timeout_seconds=eval_timeout_seconds)
        eval_state = read_eval_state(root)
        if eval_result.get("status") != "ok":
            return {
                "status": "eval_failed",
                "train": train_result,
                "eval": eval_result,
                "eval_state": eval_state,
            }
        return {
            "status": "ok",
            "train": train_result,
            "eval": eval_result,
            "eval_state": eval_state,
            "solved": bool(eval_state.get("solved")),
            "metric_name": eval_state.get("metric_name"),
            "metric_value": eval_state.get("metric_value"),
        }

    def command_status(command_id: str = "", latest: bool = True) -> dict:
        return read_command_status(root, command_id=command_id, latest=bool(latest))

    def skill_search(query: str = "", limit: int = 20) -> dict:
        skills_root = root / "skills"
        matches = []
        if skills_root.exists():
            for skill_md in sorted(skills_root.rglob("SKILL.md")):
                rel = str(skill_md.relative_to(root))
                text = skill_md.read_text(encoding="utf-8", errors="replace")
                haystack = f"{rel}\n{text[:1200]}"
                if not query or str(query).lower() in haystack.lower():
                    matches.append({"name": skill_md.parent.name, "path": rel, "preview": text[:500]})
                if len(matches) >= int(limit or 20):
                    break
        return {"skills": matches}

    def skill_view(skill_name: str, file_path: str = "SKILL.md") -> dict:
        skills_root = root / "skills"
        if not skills_root.exists():
            return {"error": "no skills directory"}
        candidates = [p.parent for p in skills_root.rglob("SKILL.md") if p.parent.name == skill_name]
        if not candidates:
            return {"error": f"skill not found: {skill_name}"}
        if len(candidates) > 1:
            return {"error": f"ambiguous skill name: {skill_name}", "matches": [str(p.relative_to(root)) for p in candidates]}
        target = safe_relative_path(candidates[0], file_path or "SKILL.md")
        if not target.exists() or not target.is_file():
            return {"error": f"skill file not found: {file_path}"}
        return {"skill": skill_name, "file_path": str(target.relative_to(root)), "content": target.read_text(encoding="utf-8", errors="replace")}

    def artifact_write(name: str, content: str) -> dict:
        safe = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in str(name or "artifact"))[:100] or "artifact"
        path = root / ".autoresearch" / "artifacts" / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content or ""), encoding="utf-8")
        return {"path": str(path.relative_to(root)), "bytes": path.stat().st_size}

    def read_eval() -> dict:
        return read_eval_state(root)

    def delegate_task(goal: str, context: dict | None = None, max_iterations: int = 6,
                      child_allowed_tools: list | None = None, parent_step: str = "") -> dict:
        if not enable_delegate or client is None:
            return {
                "error": "delegate_task is unavailable because no child client was configured",
                "hint": "Construct tools with enable_delegate=True and a client.",
            }
        return run_child_delegate(
            root=root,
            client=client,
            model=model,
            parent_step=parent_step or "unknown",
            goal=goal,
            context=context if isinstance(context, dict) else {},
            max_iterations=max_iterations,
            child_allowed_tools=child_allowed_tools,
            tools_factory=lambda child_root: build_default_tools(
                child_root,
                command_timeout_seconds=command_timeout_seconds,
                client=client,
                model=model,
                debug=debug,
                enable_delegate=False,
            ),
            debug=debug,
            trace_root=root / ".autoresearch" / "child_traces",
        )

    def detailed_plan(
        problem: str,
        context_summary: str = "",
        complexity_reason: str = "",
        milestones: list | None = None,
        risks: list | None = None,
        validation: list | None = None,
        next_attempt: str = "",
        output_path: str = ".autoresearch/detailed_plan.md",
    ) -> dict:
        """Persist a structured long plan for genuinely complex projects.

        The tool is deliberately deterministic. The model decides whether the
        project is complex enough to call it; the tool just normalizes and
        stores the plan so the main plan response can stay short.
        """
        target = safe_relative_path(root, output_path)
        milestones = milestones if isinstance(milestones, list) else []
        risks = risks if isinstance(risks, list) else []
        validation = validation if isinstance(validation, list) else []
        lines = [
            "# Detailed Plan",
            "",
            "## Problem",
            str(problem or "").strip() or "(not provided)",
            "",
            "## Context Summary",
            str(context_summary or "").strip() or "(not provided)",
            "",
            "## Why This Needs A Detailed Plan",
            str(complexity_reason or "").strip() or "(not provided)",
            "",
            "## Milestones",
        ]
        if milestones:
            for i, item in enumerate(milestones, start=1):
                lines.append(f"{i}. {str(item).strip()}")
        else:
            lines.append("(none)")
        lines.extend(["", "## Risks"])
        if risks:
            for item in risks:
                lines.append(f"- {str(item).strip()}")
        else:
            lines.append("(none)")
        lines.extend(["", "## Validation"])
        if validation:
            for item in validation:
                lines.append(f"- {str(item).strip()}")
        else:
            lines.append("(none)")
        lines.extend(["", "## Next Smallest Attempt", str(next_attempt or "").strip() or "(not provided)", ""])
        target.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(lines)
        target.write_text(text, encoding="utf-8")
        return {
            "path": str(target.relative_to(root)),
            "bytes": len(text.encode("utf-8")),
            "milestone_count": len(milestones),
            "validation_count": len(validation),
        }

    registry.register(ToolSpec(
        "read_file", "Read a project file with line numbers.",
        {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, "required": ["path"]},
        read_file,
    ))
    registry.register(ToolSpec(
        "write_file", "Write or append a project-confined file. Evaluation files are protected.",
        {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "mode": {"type": "string", "enum": ["replace", "append"]}}, "required": ["path", "content"]},
        write_file,
    ))
    registry.register(ToolSpec(
        "search_files", "Search file names or file contents under the project.",
        {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "target": {"type": "string", "enum": ["content", "files"]}, "limit": {"type": "integer"}}, "required": ["pattern"]},
        search_files,
    ))
    registry.register(ToolSpec(
        "run_command", "Run a project-confined shell command.",
        {"type": "object", "properties": {"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, "required": ["command"]},
        run_command,
    ))
    registry.register(ToolSpec(
        "run_train", "Run bash train/train.sh with heartbeat status under .autoresearch/commands.",
        {"type": "object", "properties": {"timeout_seconds": {"type": "integer"}}},
        run_train,
    ))
    registry.register(ToolSpec(
        "run_eval", "Run bash eval.sh with heartbeat status under .autoresearch/commands.",
        {"type": "object", "properties": {"timeout_seconds": {"type": "integer"}}},
        run_eval,
    ))
    registry.register(ToolSpec(
        "run_pipeline",
        "Run train, then eval, then read structured eval status in one monitored framework action.",
        {
            "type": "object",
            "properties": {
                "train_timeout_seconds": {"type": "integer"},
                "eval_timeout_seconds": {"type": "integer"},
            },
        },
        run_pipeline,
    ))
    registry.register(ToolSpec(
        "command_status", "Read the latest or specified monitored command heartbeat/status.",
        {"type": "object", "properties": {"command_id": {"type": "string"}, "latest": {"type": "boolean"}}},
        command_status,
    ))
    registry.register(ToolSpec(
        "skill_search", "Search project-local skills.",
        {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}},
        skill_search,
    ))
    registry.register(ToolSpec(
        "skill_view", "View a project-local skill file.",
        {"type": "object", "properties": {"skill_name": {"type": "string"}, "file_path": {"type": "string"}}, "required": ["skill_name"]},
        skill_view,
    ))
    registry.register(ToolSpec(
        "artifact_write", "Write a long note or raw evidence into .autoresearch/artifacts.",
        {"type": "object", "properties": {"name": {"type": "string"}, "content": {"type": "string"}}, "required": ["name", "content"]},
        artifact_write,
    ))
    registry.register(ToolSpec(
        "read_eval",
        "Read the current eval interface, metrics.json, metric value, and solved status.",
        {"type": "object", "properties": {}},
        read_eval,
    ))
    registry.register(ToolSpec(
        "delegate_task",
        (
            "Run a self-contained child autoresearch agent for bounded side work. "
            "Child agents cannot delegate again and full child context is stored in traces."
        ),
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "context": {"type": "object"},
                "max_iterations": {"type": "integer", "default": 6},
                "child_allowed_tools": {"type": "array", "items": {"type": "string"}},
                "parent_step": {"type": "string"},
            },
            "required": ["goal"],
        },
        delegate_task,
    ))
    registry.register(ToolSpec(
        "detailed_plan",
        (
            "Create a structured long-form plan for a genuinely complex project. "
            "Use this only when a short direct plan is insufficient."
        ),
        {
            "type": "object",
            "properties": {
                "problem": {"type": "string"},
                "context_summary": {"type": "string"},
                "complexity_reason": {"type": "string"},
                "milestones": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "validation": {"type": "array", "items": {"type": "string"}},
                "next_attempt": {"type": "string"},
                "output_path": {"type": "string", "default": ".autoresearch/detailed_plan.md"},
            },
            "required": ["problem", "complexity_reason", "next_attempt"],
        },
        detailed_plan,
    ))
    return registry


def _validate_command_surface(root: Path, command: str) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"invalid command: {exc}") from exc
    for token in tokens:
        if token.startswith("~"):
            raise ValueError(f"command token escapes project root: {token}")
        if token.startswith("/") and Path(token).exists():
            safe_relative_path(root, token)
        if ".." in Path(token).parts:
            candidate = (root / token).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"command token escapes project root: {token}") from exc
