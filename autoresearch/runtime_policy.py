"""AutoResearch step-level agent runtime contracts.

The three-step loop uses the same loop shape for plan/attempt/conclude: build a
step-specific context, expose a bounded tool surface, optionally delegate leaf
work, and stop when the step completion tag is reached.  This module keeps those
contracts explicit and testable before the controller swaps every handler to a
full R-Agent-style loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tools.delegate_tool import DELEGATE_CHILD_EXCLUDED_TOOLS


PLAN_ALLOWED_TOOLS = (
    "read_file",
    "search_files",
    "web_search",
    "web_extract",
    "archive_subtask",
    "artifact_inspect",
    "artifact_search",
    "artifact_slice",
    "skill_search",
    "skill_view",
    "todo_manage",
    "delegate_task",
)

ATTEMPT_ALLOWED_TOOLS = (
    "read_file",
    "search_files",
    "write_file",
    "run_command",
    "archive_subtask",
    "artifact_inspect",
    "artifact_search",
    "artifact_slice",
    "skill_search",
    "skill_view",
    "todo_manage",
    "delegate_task",
)

CONCLUDE_ALLOWED_TOOLS = (
    "read_file",
    "search_files",
    "write_file",
    "archive_subtask",
    "artifact_inspect",
    "artifact_search",
    "artifact_slice",
    "todo_manage",
)

READONLY_CHILD_ALLOWED_TOOLS = (
    "read_file",
    "search_files",
    "web_search",
    "web_extract",
    "archive_subtask",
    "artifact_inspect",
    "artifact_search",
    "artifact_slice",
    "skill_search",
    "skill_view",
    "todo_manage",
)

CODING_CHILD_ALLOWED_TOOLS = (
    "read_file",
    "search_files",
    "write_file",
    "run_command",
    "archive_subtask",
    "artifact_inspect",
    "artifact_search",
    "artifact_slice",
    "skill_search",
    "skill_view",
    "todo_manage",
)

CONCLUDE_CHILD_ALLOWED_TOOLS = (
    "read_file",
    "search_files",
    "write_file",
    "archive_subtask",
    "artifact_inspect",
    "artifact_search",
    "artifact_slice",
    "todo_manage",
)


@dataclass(frozen=True)
class StepRuntimePolicy:
    name: str
    done_tag: str
    goal: str
    focus_context: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    child_allowed_tools: tuple[str, ...]
    allowed_skills: tuple[str, ...] = ()
    child_excluded_tools: tuple[str, ...] = tuple(DELEGATE_CHILD_EXCLUDED_TOOLS)

    def tool_guard(self, *, child: bool = False):
        allowed = set(self.child_allowed_tools if child else self.allowed_tools)
        excluded = set(self.child_excluded_tools if child else ())
        allowed_skills = set(self.allowed_skills)

        def guard(name: str, args: str) -> str | None:
            if name in excluded:
                return f"tool {name} is disabled in autoresearch {self.name} child context"
            if allowed and name not in allowed:
                return f"tool {name} is not allowed in autoresearch {self.name} step"
            if name == "skill_view" and allowed_skills:
                try:
                    data = json.loads(args or "{}")
                except Exception:
                    return f"tool {name} arguments must be JSON in autoresearch {self.name} step"
                skill_name = str(data.get("skill_name") or "")
                if skill_name not in allowed_skills:
                    return (
                        f"skill {skill_name!r} is not allowed in autoresearch {self.name} step; "
                        f"allowed skills: {sorted(allowed_skills)}"
                    )
            if name == "delegate_task" and not child:
                try:
                    data = json.loads(args or "{}")
                except Exception:
                    return "delegate_task arguments must be JSON in autoresearch step"
                child_allowed = data.get("child_allowed_tools")
                if not child_allowed:
                    return (
                        f"delegate_task in autoresearch {self.name} step must include child_allowed_tools="
                        f"{list(self.child_allowed_tools)}"
                    )
            return None

        return guard


STEP_POLICIES = {
    "plan": StepRuntimePolicy(
        name="plan",
        done_tag="PLAN_DONE",
        goal=(
            "Understand the research goal and repository map, then produce or "
            "refresh a task DAG with explicit validation checkpoints."
        ),
        focus_context=(
            "program constitution/belief",
            "project summary",
            "codebase scout summary or survey",
            "todo digest",
            "recent best experiment",
        ),
        allowed_tools=PLAN_ALLOWED_TOOLS,
        child_allowed_tools=READONLY_CHILD_ALLOWED_TOOLS,
        allowed_skills=("codebase_scout",),
    ),
    "attempt": StepRuntimePolicy(
        name="attempt",
        done_tag="ATTEMPT_DONE",
        goal=(
            "Complete the next ready task by reading the required files, making "
            "bounded project-confined changes when needed, and running the "
            "appropriate cheap validation or run checkpoint."
        ),
        focus_context=(
            "one ready task",
            "task last_result",
            "program constraints",
            "editable/protected paths",
            "validation command map",
            "recent behavior artifacts",
        ),
        allowed_tools=ATTEMPT_ALLOWED_TOOLS,
        child_allowed_tools=CODING_CHILD_ALLOWED_TOOLS,
        allowed_skills=("codebase_scout",),
    ),
    "conclude": StepRuntimePolicy(
        name="conclude",
        done_tag="CONCLUDE_DONE",
        goal=(
            "Summarize evidence, update lessons and gate signals, compress "
            "working context, and decide whether to plan again, attempt more "
            "work, or pause."
        ),
        focus_context=(
            "state experiments",
            "pareto/best",
            "todo digest",
            "recent execute/run artifacts",
            "lessons",
            "budget/gate signals",
        ),
        allowed_tools=CONCLUDE_ALLOWED_TOOLS,
        child_allowed_tools=CONCLUDE_CHILD_ALLOWED_TOOLS,
    ),
}


def step_policy(name: str) -> StepRuntimePolicy:
    value = str(name or "").strip().lower()
    if value not in STEP_POLICIES:
        raise KeyError(f"unknown autoresearch step policy: {name}")
    return STEP_POLICIES[value]


def _read_text(path: Path, limit: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[: max(0, int(limit))]


def _read_json(path: Path) -> dict:
    if not path.exists() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _json_text(payload: dict, limit: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return text
    for string_limit, list_limit in ((240, 5), (160, 5), (120, 5), (80, 5), (40, 5), (40, 3), (40, 2)):
        shrunk = _shrink_json_value(payload, string_limit=string_limit, list_limit=list_limit)
        text = json.dumps(shrunk, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(text) <= limit:
            return text
    fallback = {
        "compacted": True,
        "truncated": True,
        "keys": list(payload.keys()),
        "best_experiment": payload.get("best_experiment") or payload.get("best") or {},
        "latest_experiments": payload.get("latest_experiments", [])[-2:],
        "latest_attempts": payload.get("latest_attempts", [])[-2:],
    }
    text = json.dumps(fallback, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return text
    minimal = {"compacted": True, "truncated": True, "keys": list(payload.keys())[:12]}
    return json.dumps(minimal, ensure_ascii=False, separators=(",", ":"), default=str)


def _shrink_json_value(value, *, string_limit: int, list_limit: int):
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[: max(0, string_limit - 3)] + "..."
    if isinstance(value, list):
        return [_shrink_json_value(item, string_limit=string_limit, list_limit=list_limit) for item in value[:list_limit]]
    if isinstance(value, dict):
        return {
            str(key): _shrink_json_value(item, string_limit=string_limit, list_limit=list_limit)
            for key, item in value.items()
        }
    return value


def _experiment_brief(exp: dict) -> dict:
    if not isinstance(exp, dict):
        return {}
    return {
        "experiment_id": exp.get("experiment_id", ""),
        "timestamp": exp.get("timestamp", ""),
        "hypothesis": str(exp.get("hypothesis") or "")[:120],
        "primary_metric_name": exp.get("primary_metric_name", ""),
        "metrics": exp.get("metrics") if isinstance(exp.get("metrics"), dict) else {},
        "decision": exp.get("decision", ""),
        "status": exp.get("status", ""),
        "summary": str(exp.get("summary") or "")[:160],
        "artifact_path": exp.get("artifact_path", ""),
        "source_snapshot_path": exp.get("source_snapshot_path", ""),
    }


def _compact_state_json(root: Path, limit: int = 3000) -> str:
    state = _read_json(root / ".autoresearch" / "state.json")
    if not state:
        return ""
    raw = json.dumps(state, ensure_ascii=False, default=str)
    if len(raw) <= limit:
        return raw[:limit]
    experiments = [exp for exp in state.get("experiments") or [] if isinstance(exp, dict)]
    payload = {
        "compacted": True,
        "reason": "state.json exceeded step context budget; keeping best, pareto, latest, and useful failures",
        "best_experiment": _experiment_brief(state.get("best_experiment") or {}),
        "pareto_front": [_experiment_brief(exp) for exp in (state.get("pareto_front") or [])[:5] if isinstance(exp, dict)],
        "latest_experiments": [_experiment_brief(exp) for exp in experiments[-5:]],
        "useful_failures": [
            {
                "experiment_id": item.get("experiment_id", ""),
                "summary": str(item.get("summary") or "")[:350],
                "artifact_path": item.get("artifact_path", ""),
            }
            for item in (state.get("useful_failures") or [])[-3:]
            if isinstance(item, dict)
        ],
        "last_finalized_experiment_count": state.get("last_finalized_experiment_count", len(experiments)),
        "versioning_policy": state.get("versioning_policy", ""),
    }
    return _json_text(payload, limit)


def _compact_experiment_memory_json(root: Path, limit: int = 3500) -> str:
    memory = _read_json(root / ".autoresearch" / "experiment_memory.json")
    if not memory:
        return ""
    raw = json.dumps(memory, ensure_ascii=False, default=str)
    if len(raw) <= limit:
        return raw[:limit]
    attempts = memory.get("attempts") if isinstance(memory.get("attempts"), list) else []
    payload = {
        "compacted": True,
        "reason": "experiment_memory.json exceeded step context budget; keeping current, best, guidance, and latest attempts",
        "current": memory.get("current", {}),
        "best": memory.get("best", {}),
        "guidance": list(memory.get("guidance") or [])[:8] if isinstance(memory.get("guidance"), list) else [],
        "latest_attempts": attempts[-5:],
        "remaining_failures": (memory.get("remaining_failures") or [])[:5]
        if isinstance(memory.get("remaining_failures"), list) else [],
        "regressions": (memory.get("regressions") or [])[-3:]
        if isinstance(memory.get("regressions"), list) else [],
    }
    return _json_text(payload, limit)


def _compact_json_text(text: str, limit: int) -> str:
    try:
        payload = json.loads(text or "{}")
    except Exception:
        return str(text or "")[: max(0, int(limit))]
    if not isinstance(payload, dict):
        return str(text or "")[: max(0, int(limit))]
    return _json_text(payload, limit)


def build_step_context(root: str | Path, step_name: str, *, task: dict | None = None, max_chars: int = 12000) -> dict:
    """Build a compact, serializable context payload for one autoresearch step."""
    root = Path(root)
    policy = step_policy(step_name)
    payload = {
        "step": policy.name,
        "done_tag": policy.done_tag,
        "goal": policy.goal,
        "focus_context": list(policy.focus_context),
        "task": task or {},
        "files": {
            "program_md": _read_text(root / "program.md", 4000),
            "project_md": _read_text(root / "project.md", 3000),
            "todo_state_json": _read_text(root / ".autoresearch" / "todo_state.json", 3000),
            "gate_signals_json": _read_text(root / ".autoresearch" / "gate_signals.json", 1200),
            "state_json": _compact_state_json(root, 3000),
            "experiment_memory_json": _compact_experiment_memory_json(root, 3500),
            "experiment_memory_md": _read_text(root / ".autoresearch" / "experiment_memory.md", 2500),
            "regression_cases_json": _read_text(root / ".autoresearch" / "regression_cases.json", 3500),
            "execute_validation_md": _read_text(root / ".auto" / "execute_validation.md", 1200),
        },
        "tool_policy": {
            "allowed_tools": list(policy.allowed_tools),
            "child_allowed_tools": list(policy.child_allowed_tools),
            "child_excluded_tools": list(policy.child_excluded_tools),
            "allowed_skills": list(policy.allowed_skills),
        },
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return payload
    # Drop heaviest optional fields in a deterministic order, preserving JSON
    # validity for structured state fields.
    for key in ("experiment_memory_json", "regression_cases_json", "todo_state_json", "project_md", "program_md", "state_json"):
        current = payload["files"].get(key, "")
        if key.endswith("_json"):
            payload["files"][key] = _compact_json_text(current, 1000)
        else:
            payload["files"][key] = current[:1000]
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) <= max_chars:
            return payload
    return payload


def allowed_tools_for_step(step_name: str, *, child: bool = False) -> tuple[str, ...]:
    policy = step_policy(step_name)
    return policy.child_allowed_tools if child else policy.allowed_tools


def excluded_tools_for_step(step_name: str, *, child: bool = False) -> tuple[str, ...]:
    policy = step_policy(step_name)
    return policy.child_excluded_tools if child else ()


__all__ = [
    "StepRuntimePolicy",
    "STEP_POLICIES",
    "allowed_tools_for_step",
    "build_step_context",
    "excluded_tools_for_step",
    "step_policy",
]
