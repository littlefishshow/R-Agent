from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class AutoResearchConfig:
    """Configuration for an automated three-step autoresearch run."""

    project_dir: str | Path
    run_id: str = "autoresearch"
    model: str = ""
    max_cycles: int = 20
    max_iterations_per_step: int = 12
    command_timeout_seconds: int = 300
    context_char_budget: int = 24_000
    trace: bool = True
    debug: bool = False
    stop_on_step_failure: bool = True
    state_dir_name: str = ".autoresearch"

    def root(self) -> Path:
        return Path(self.project_dir).expanduser().resolve()

    def state_dir(self) -> Path:
        return self.root() / self.state_dir_name

    def state_file(self) -> Path:
        return self.state_dir() / "runner_state.json"

    def monitor_file(self) -> Path:
        return self.state_dir() / "monitor.json"

    def debug_dir(self) -> Path:
        return self.state_dir() / "debug"

    def trace_dir(self) -> Path:
        return self.state_dir() / "traces"

    def artifacts_dir(self) -> Path:
        return self.state_dir() / "artifacts"

    def stop_file(self) -> Path:
        return self.state_dir() / "STOP"


@dataclass(frozen=True)
class StepSpec:
    """One complete agent loop in the outer three-step runner."""

    name: str
    done_tag: str
    system_prompt: str
    user_goal: str
    allowed_tools: tuple[str, ...]
    context_files: tuple[str, ...] = (
        "program.md",
        "project.md",
        ".autoresearch/runner_state.json",
        ".autoresearch/eval_interface.json",
        ".autoresearch/notes.md",
    )


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentResult:
    content: str
    done: bool
    iterations: int
    tag: str
    trace_path: str = ""
    error: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepReport:
    step: str
    done: bool
    next_step: str
    iterations: int
    summary: str
    trace_path: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = field(default_factory=time.time)
    stats: dict[str, Any] = field(default_factory=dict)
