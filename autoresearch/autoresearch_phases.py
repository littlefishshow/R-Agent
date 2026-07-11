"""AutoResearch V3 shared types and public loop entrypoint.

V3 keeps the control loop in ``autoresearch.autoresearch_three_step``:
``plan -> attempt -> conclude``.  This module remains as the stable import
surface used by tools and handlers: shared phase context/result dataclasses plus
``run_phase_loop`` for the public `/autoresearch` path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from autoresearch.autoresearch_debug import ensure_debug_from_settings

@dataclass
class PhaseSignals:
    """Signals shared by V3 planner/attempt/conclude handlers."""

    phase: str
    pareto_changed: bool = False
    plateau_counter: int = 0
    plateau_patience: int = 3
    major_error: bool = False
    solved: bool = False
    plan_still_valid: bool = True
    plan_has_open_tasks: bool = False
    execute_has_open_tasks: bool = False
    budget_exhausted: bool = False
    budget_degrade: bool = False
    started: bool = True


@dataclass
class PhaseContext:
    """Read-only-ish view handed to a phase handler."""

    phase: str
    root: Path
    program_text: str
    project_text: str
    signals: PhaseSignals
    loop: object = None  # AutoResearchLoop, optional (for budget/tiers/step agent)


@dataclass
class PhaseResult:
    """What a phase handler produced."""

    program_text: Optional[str] = None      # updated program.md (belief only), or None
    project_text: Optional[str] = None       # updated project.md, or None
    signals_update: dict = field(default_factory=dict)  # override fields on PhaseSignals
    summary: str = ""


def _noop_handler(ctx: "PhaseContext") -> "PhaseResult":
    return PhaseResult(summary=f"{ctx.phase}: deterministic no-op")


__all__ = [
    "PhaseSignals",
    "PhaseContext",
    "PhaseResult",
    "run_phase_loop",
]


def run_phase_loop(settings, *, max_steps: int = 24, handlers: Optional[dict] = None, loop=None,
                   run_id: str = "", monitor=None) -> dict:
    """Run the V3 plan -> attempt -> conclude loop."""
    import os
    from autoresearch.autoresearch_loop import AutoResearchLoop
    from autoresearch.autoresearch_monitor import RunMonitor
    from autoresearch.autoresearch_three_step import run_three_step_loop

    if run_id:
        os.environ["AUTORESEARCH_RUN_ID"] = run_id
    ensure_debug_from_settings(settings)
    loop = loop or AutoResearchLoop(settings)
    if monitor is None:
        monitor = RunMonitor(settings.monitor_file(), run_id=run_id, project_id=settings.project_id)
    return run_three_step_loop(settings, max_steps=max_steps, loop=loop, run_id=run_id, monitor=monitor)
