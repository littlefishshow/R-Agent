"""Dataclasses and constants for the legacy AutoResearch loop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Decision = Literal["run", "read", "write", "apply_patch", "web_search", "web_extract", "note", "stop"]


@dataclass
class AutoResearchSettings:
    """Dedicated autoresearch loop settings.

    The loop is centered on program.md, keeps parent context bounded, and stores
    raw child/tool outputs outside the prompt as timestamped artifacts.
    """

    project_dir: str | Path
    project_id: str = "autoresearch"
    program_path: str | Path = "program.md"
    state_path: str | Path = ".autoresearch/state.json"
    artifact_dir: str | Path = ".autoresearch/artifacts"
    context_char_budget: int = 24_000
    program_char_budget: int = 12_000
    summary_char_budget: int = 6_000
    recent_observation_limit: int = 8
    command_timeout_seconds: int = 300
    max_rounds: int = 1
    trial_rationale: str = "manual"
    allowed_file_write_roots: tuple[str, ...] = (".",)
    bucket_item_char_budget: int = 900
    bucket_max_items: int = 3
    workflow: str = "default"
    use_llm_step_agents: bool = False
    llm_model: str | None = None
    llm_temperature: float = 0.0
    progress_path: str | Path = ".autoresearch/progress.md"
    auto_commit: bool = False
    max_experiments: int = 4
    max_active_context_chars: int = 8_000
    max_pareto_items: int = 8
    max_useful_failures: int = 3
    use_git_versioning: bool = True
    versioning_policy: str = "artifact_only"
    planner_kind: str = "fixed"
    llm_request_timeout: float = 300.0
    llm_retry_attempts: int = 0
    # --- v2: cost control + layered memory ---
    project_state_path: str | Path = "project.md"
    budget_path: str | Path = ".autoresearch/budget.json"
    monitor_path: str | Path = ".autoresearch/monitor.json"
    trace_rounds: bool = False
    trace_dir: str | Path = ".autoresearch/round_traces"
    max_usd: float = 0.0            # 0 => unlimited
    max_tokens: int = 0             # 0 => unlimited
    budget_degrade_ratio: float = 0.8
    model_tier_plan: str = ""
    model_tier_exec: str = ""
    model_tier_util: str = ""
    readonly_eval_globs: tuple[str, ...] = (
        "prepare.py", "eval.py", "eval.sh", "eval/**", "evaluation/**",
        "blackbox_oracle.py", "blackbox_oracle.*",
    )
    plateau_patience: int = 3
    debug_mode: bool = False
    # --- v2 plan tuning ---
    plan_max_personas: int = 2
    plan_degrade_personas: int = 1
    plan_max_implementation_tasks: int = 0
    planner_project_context_chars: int = 18_000
    execute_context_chars: int = 24_000
    execute_max_task_attempts: int = 3
    execute_behavior_check: bool = True
    execute_behavior_check_timeout_seconds: int = 300
    autoresearch_step_agent_loop: bool = False
    autoresearch_step_max_iterations: int = 12
    # --- v2 execute/run tuning ---
    # Cap LLM-backed actions per Execute phase visit so one step cannot burn the
    # whole time/token budget on a long todo list; remaining items advance on the
    # next Execute visit via a cursor.
    execute_max_actions_per_step: int = 1
    # V3 ownership boundary: Run records experiment observations; Conclude
    # finalizes best/Pareto, versioning, rollback, and lessons. Legacy
    # AutoResearchLoop.run keeps immediate finalization for compatibility.
    defer_experiment_finalization: bool = False
    # When Execute wrote a self-iterating search driver, let Run execute it so it
    # performs many internal evaluations from a single LLM decision.
    run_search_driver: bool = True
    run_max_inner_seconds: float = 20.0
    run_max_inner_evals: int = 100
    run_cheap_eval_threshold_seconds: float = 2.0
    search_driver_globs: tuple[str, ...] = (
        "train/search.py", "train/search_driver.py", "train/*search*.py",
        "train/*driver*.py", "train/*exploration*.py", "search.py",
        "train/search.sh", "search.sh",
    )

    def __post_init__(self) -> None:
        # Normalize early so tools, background payloads, state, progress, and
        # active_context all report the same supported lifecycle policy.
        from autoresearch.legacy.services import normalize_planner_kind, normalize_versioning_policy

        self.versioning_policy = normalize_versioning_policy(self.versioning_policy)
        self.planner_kind = normalize_planner_kind(self.planner_kind)

    def root(self) -> Path:
        return Path(self.project_dir).expanduser().resolve()

    def program_file(self) -> Path:
        p = Path(self.program_path)
        return p if p.is_absolute() else self.root() / p

    def project_state_file(self) -> Path:
        p = Path(self.project_state_path)
        return p if p.is_absolute() else self.root() / p

    def budget_file(self) -> Path:
        p = Path(self.budget_path)
        return p if p.is_absolute() else self.root() / p

    def monitor_file(self) -> Path:
        p = Path(self.monitor_path)
        return p if p.is_absolute() else self.root() / p

    def trace_root(self) -> Path:
        p = Path(self.trace_dir)
        return p if p.is_absolute() else self.root() / p

    def stop_file(self) -> Path:
        # Sentinel a watcher / esc handler can create to stop the loop cleanly.
        return self.root() / ".autoresearch" / "STOP"

    def debug_file(self) -> Path:
        return self.root() / ".autoresearch" / "DEBUG"

    def state_file(self) -> Path:
        p = Path(self.state_path)
        return p if p.is_absolute() else self.root() / p

    def artifacts_root(self) -> Path:
        p = Path(self.artifact_dir)
        return p if p.is_absolute() else self.root() / p

    def progress_file(self) -> Path:
        p = Path(self.progress_path)
        return p if p.is_absolute() else self.root() / p


@dataclass
class AutoResearchObservation:
    kind: str
    summary: str
    artifact_path: str = ""
    status: str = "ok"
    created_at: float = field(default_factory=time.time)

    def compact(self, max_chars: int = 900) -> dict:
        summary = self.summary
        if len(summary) > max_chars:
            summary = summary[: max_chars - 3].rstrip() + "..."
        return {
            "kind": self.kind,
            "status": self.status,
            "summary": summary,
            "artifact_path": self.artifact_path,
            "created_at": self.created_at,
        }


DEFAULT_CONTEXT_BUCKETS = (
    "project_understanding",
    "current_changes",
    "experiment_results",
    "conclusions",
    "modification_plans",
    "open_questions",
    "raw_observations",
)


@dataclass
class ContextBucket:
    name: str
    items: list[str] = field(default_factory=list)
    max_items: int = 3
    max_item_chars: int = 900

    def add(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        if len(text) > self.max_item_chars:
            text = text[: self.max_item_chars - 3].rstrip() + "..."
        self.items.append(text)
        self.items = self.items[-self.max_items :]

    def compact(self) -> list[str]:
        return list(self.items[-self.max_items :])


@dataclass
class AutoResearchWorkflowStep:
    name: str
    action_type: Decision
    rationale: str
    command: str = ""
    path: str = ""
    content: str = ""
    patch: str = ""
    query: str = ""
    urls: list[str] = field(default_factory=list)
    max_results: int = 5
    allowed_tools: tuple[Decision, ...] = field(default_factory=tuple)
    role: str = ""

    def to_action(self) -> "AutoResearchAction":
        return AutoResearchAction(
            type=self.action_type,
            rationale=self.rationale or self.name,
            command=self.command,
            path=self.path,
            content=self.content,
            patch=self.patch,
            query=self.query,
            urls=list(self.urls),
            max_results=self.max_results,
            role=self.role,
        )




@dataclass
class AutoResearchAction:
    type: Decision
    rationale: str
    command: str = ""
    path: str = ""
    content: str = ""
    patch: str = ""
    query: str = ""
    urls: list[str] = field(default_factory=list)
    max_results: int = 5
    role: str = ""


@dataclass
class AutoResearchStepResult:
    action: AutoResearchAction
    bucket_updates: dict[str, list[str]] = field(default_factory=dict)
    raw_response: str = ""
    used_fallback: bool = False
    error: str = ""
    # Full LLM I/O for post-hoc debugging (only populated when round tracing is on).
    system_prompt: str = ""
    user_payload: str = ""


from autoresearch.legacy.services import (
    AutoResearchSafetyError,
    ProjectBoundary,
    _contains_parent_escape,
    _extract_change_spec,
    _make_unified_diff,
    _matches_readonly,
    _safe_slug,
    apply_patch_with_git,
    apply_unified_patch_limited,
    decide_experiment,
    extract_json_object,
    extract_progress_percent,
    parse_primary_metric,
)

__all__ = [
    'AutoResearchAction',
    'AutoResearchObservation',
    'AutoResearchSettings',
    'AutoResearchStepResult',
    'AutoResearchWorkflowStep',
    'ContextBucket',
    'DEFAULT_CONTEXT_BUCKETS',
    'Decision',
]
