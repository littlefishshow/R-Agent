"""AutoResearch v2 — 6-phase re-entrant state machine (AUTORESEARCH_DESIGN_v2.md §2).

The controller is deliberately split into two testable layers:

- **Pure transition functions** (`next_phase`, `phase_gate`, `budget_gate`) that
  take plain data and return the next phase + reason.  No IO, no LLM.
- **A thin runner** (`PhaseController`) that reads L0-L2 files, advances one
  phase, writes files back, and releases context.  This is what makes the loop
  able to run "forever": all state lives in files + git, nothing survives a
  phase boundary.

The concrete work of each phase (survey, debate, execute, run, evaluate,
compress) is pluggable via ``phase_handlers``; deterministic no-LLM handlers are
provided so the machine is fully testable without a model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.autoresearch_memory import (
    DEFAULT_PROJECT_TEMPLATE,
    ensure_program_scaffold,
    read_phase,
    write_phase,
    normalize_phase,
)
from core.autoresearch_debug import debug_event, ensure_debug_from_settings, inflight_finish, inflight_start
from core.autoresearch_gate_state import load_gate_state
from core.autoresearch_todo_state import has_failed_tasks, has_open_tasks, load_todo_state


# --------------------------------------------------------------------------- #
# Pure gate / transition logic
# --------------------------------------------------------------------------- #

@dataclass
class PhaseSignals:
    """Everything the pure transition functions need to decide the next phase."""

    phase: str
    pareto_changed: bool = False       # did the last Evaluate change the Pareto front?
    plateau_counter: int = 0           # consecutive rounds with no Pareto improvement
    plateau_patience: int = 3          # K
    major_error: bool = False          # Run/Execute hit an unrecoverable error
    solved: bool = False               # objective is good enough; stop cleanly
    plan_still_valid: bool = True      # is the current L2 plan still actionable?
    plan_has_open_tasks: bool = False  # current structured plan still has work
    execute_has_open_tasks: bool = False  # execute phase still has unlocked work
    budget_exhausted: bool = False
    budget_degrade: bool = False
    started: bool = True               # first Gate visit after Init?


def phase_gate(sig: PhaseSignals) -> tuple[str, str]:
    """Gate after Init/Compress: decide whether to re-Plan or go straight to Execute."""
    if sig.started:
        return "plan", "project start: initial planning"
    if not sig.plan_still_valid:
        return "plan", "current plan exhausted/invalid"
    if sig.plan_has_open_tasks:
        return "execute", "current plan still has open tasks"
    if sig.pareto_changed:
        return "plan", "pareto front changed since last evaluate"
    if sig.plateau_counter >= max(1, sig.plateau_patience):
        return "plan", f"plateau>={sig.plateau_patience}: replan (widen exploration)"
    return "execute", "current plan still valid: continue executing"


def budget_gate(sig: PhaseSignals) -> tuple[str, str]:
    """Gate after Compress: keep looping, or pause and notify the user."""
    if sig.budget_exhausted:
        return "pause", "budget exhausted: pausing for user"
    return "gate", "budget available and not converged: continue"


def next_phase(sig: PhaseSignals) -> tuple[str, str]:
    """Pure transition for the linear part of the machine.

    ``gate`` and ``budget_gate`` outcomes are resolved by the two functions
    above; this function handles the deterministic P->P edges plus the
    branch that lets Run jump to Evaluate on a major error.
    """
    phase = normalize_phase(sig.phase)
    if phase == "init":
        return "gate", "init complete"
    if phase == "gate":
        return phase_gate(sig)
    if phase == "plan":
        return "execute", "plan produced"
    if phase == "execute":
        if sig.major_error:
            return "evaluate", "major error during execute: jump to evaluate"
        if sig.execute_has_open_tasks:
            return "execute", "execute still has open plan tasks"
        if not sig.plan_still_valid:
            return "evaluate", "current plan exhausted/replan required before run"
        return "run", "changes applied: run project"
    if phase == "run":
        if sig.solved:
            return "pause", "objective solved: pause"
        if sig.major_error:
            return "evaluate", "major error during run: jump to evaluate"
        return "evaluate", "run finished"
    if phase == "evaluate":
        return "compress", "evaluation recorded"
    if phase == "compress":
        result, reason = budget_gate(sig)
        return result, reason
    if phase == "pause":
        return "pause", "paused: awaiting user"
    return "gate", "unknown phase: reset to gate"


# --------------------------------------------------------------------------- #
# Phase controller (thin file IO runner)
# --------------------------------------------------------------------------- #

PhaseHandler = Callable[["PhaseContext"], "PhaseResult"]


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


class PhaseController:
    """Advance the phase machine one step, persisting L0-L2 files each time.

    Idempotent + crash-recoverable: the next phase is always read from
    project.md, so restarting the process resumes where it left off.
    """

    def __init__(
        self,
        settings,
        *,
        handlers: Optional[dict] = None,
        loop=None,
        monitor=None,
    ):
        self.settings = settings
        self.loop = loop
        self.root = Path(settings.root())
        self.handlers = dict(handlers or {})
        self.monitor = monitor
        self._step_index = 0

    # ---- file helpers ----

    def _program_path(self) -> Path:
        return self.settings.program_file()

    def _project_path(self) -> Path:
        return self.settings.project_state_file()

    def read_program(self) -> str:
        p = self._program_path()
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def read_project(self) -> str:
        p = self._project_path()
        if p.exists():
            return p.read_text(encoding="utf-8")
        return DEFAULT_PROJECT_TEMPLATE

    def _atomic_write(self, path: Path, text: str) -> None:
        import os

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def ensure_scaffold(self) -> None:
        """Make sure program.md has L0/L1 markers and project.md exists."""
        prog_path = self._program_path()
        if prog_path.exists():
            scaffolded = ensure_program_scaffold(prog_path.read_text(encoding="utf-8"))
            if scaffolded != prog_path.read_text(encoding="utf-8"):
                self._atomic_write(prog_path, scaffolded)
        proj_path = self._project_path()
        if not proj_path.exists():
            self._atomic_write(proj_path, write_phase(DEFAULT_PROJECT_TEMPLATE, "init", "scaffolded"))

    def current_phase(self) -> tuple[str, str]:
        return read_phase(self.read_project())

    def _handler_for(self, phase: str) -> PhaseHandler:
        return self.handlers.get(phase, _noop_handler)

    def build_signals(self, phase: str, extra: Optional[dict] = None) -> PhaseSignals:
        budget = getattr(self.loop, "budget", None)
        gate = load_gate_state(self.root)
        todo_state = load_todo_state(self.root)
        todo_has_failed = has_failed_tasks(todo_state)
        sig = PhaseSignals(
            phase=phase,
            plateau_patience=int(getattr(self.settings, "plateau_patience", 3)),
            pareto_changed=bool(gate.get("pareto_changed")),
            plateau_counter=int(gate.get("plateau_counter") or 0),
            plan_still_valid=bool(gate.get("plan_still_valid", True)) and not todo_has_failed,
            plan_has_open_tasks=has_open_tasks(todo_state),
            budget_exhausted=bool(budget.is_exhausted()) if budget is not None else False,
            budget_degrade=bool(budget.should_degrade()) if budget is not None else False,
        )
        for k, v in (extra or {}).items():
            if hasattr(sig, k):
                setattr(sig, k, v)
        return sig

    def step(self, extra_signals: Optional[dict] = None) -> dict:
        """Run the current phase's handler, persist outputs, advance phase.

        Returns a report dict for observability/testing.
        """
        self.ensure_scaffold()
        phase, _reason = self.current_phase()
        # Tag the loop so the metered LLM client attributes token/time spend to
        # the running phase instead of the "unknown" bucket.
        if self.loop is not None:
            try:
                self.loop._current_phase = phase
            except Exception:
                pass
        signals = self.build_signals(phase, extra_signals)
        budget = getattr(self.loop, "budget", None)
        budget_snapshot = budget.snapshot() if budget is not None else None
        if self.monitor is not None:
            self.monitor.update_phase_start(
                step_index=self._step_index,
                current_phase=phase,
                summary=f"starting {phase}",
                budget_snapshot=budget_snapshot,
            )
        debug_event(self.root, "phase_start", step_index=self._step_index, phase=phase)
        inflight_start(self.root, "phase", phase=phase, detail=f"{phase} handler")
        ctx = PhaseContext(
            phase=phase,
            root=self.root,
            program_text=self.read_program(),
            project_text=self.read_project(),
            signals=signals,
            loop=self.loop,
        )
        handler = self._handler_for(phase)
        try:
            result = handler(ctx) or PhaseResult()
        finally:
            inflight_finish(self.root, "phase", phase=phase)

        # Persist handler outputs (belief-only for program.md).
        if result.program_text is not None:
            self._atomic_write(self._program_path(), result.program_text)
        project_text = result.project_text if result.project_text is not None else ctx.project_text

        # Merge signal overrides, then compute the next phase.
        for k, v in (result.signals_update or {}).items():
            if hasattr(signals, k):
                setattr(signals, k, v)
        # 'started' is only true the very first time we reach the gate.
        signals.started = (phase == "init")
        nxt, reason = next_phase(signals)
        # Resolve chained gate outcomes (init->gate->plan/execute, compress->gate).
        if nxt == "gate":
            gate_phase, gate_reason = phase_gate(signals)
            nxt, reason = gate_phase, gate_reason

        project_text = write_phase(project_text, nxt, reason)
        self._atomic_write(self._project_path(), project_text)

        self._step_index += 1
        budget_snapshot = budget.snapshot() if budget is not None else None
        if self.monitor is not None:
            self.monitor.update_step(
                step_index=self._step_index,
                current_phase=phase,
                next_phase=nxt,
                summary=result.summary,
                budget_snapshot=budget_snapshot,
            )
        debug_event(self.root, "phase_finish", step_index=self._step_index, phase=phase,
                    next_phase=nxt, reason=reason, summary=result.summary)

        return {
            "ran_phase": phase,
            "next_phase": nxt,
            "reason": reason,
            "summary": result.summary,
            "step_index": self._step_index,
            "budget_status": (budget.status() if budget is not None else "ok"),
            "timestamp": time.strftime("%F %T"),
        }

    def run(self, max_steps: int = 24, extra_signals: Optional[dict] = None) -> list[dict]:
        """Advance the machine up to ``max_steps`` phases or until pause."""
        if self.monitor is not None:
            self.monitor.set_max_steps(max_steps)
            self.monitor.start()
        reports = []
        error = ""
        try:
            for _ in range(max(0, int(max_steps))):
                # Cooperative interrupt: STOP sentinel ends the machine cleanly at
                # a phase boundary (state already persisted to project.md each step).
                stop_file = self.root / ".autoresearch" / "STOP"
                if stop_file.exists():
                    if self.monitor is not None:
                        self.monitor.finish(status="paused", error="stopped_by_request")
                    break
                report = self.step(extra_signals)
                reports.append(report)
                if report["next_phase"] == "pause":
                    # Run the pause phase's handler once, then stop.
                    break
                if getattr(self.loop, "budget", None) and self.loop.budget.is_exhausted():
                    break
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            if self.monitor is not None:
                budget = getattr(self.loop, "budget", None)
                paused = bool(reports) and reports[-1]["next_phase"] == "pause"
                status = "failed" if error else ("paused" if paused else "completed")
                self.monitor.finish(
                    status=status,
                    error=error,
                    budget_snapshot=(budget.snapshot() if budget is not None else None),
                )
        return reports


__all__ = [
    "PhaseSignals",
    "phase_gate",
    "budget_gate",
    "next_phase",
    "PhaseContext",
    "PhaseResult",
    "PhaseController",
    "PhaseHandler",
    "run_phase_loop",
]


def run_phase_loop(settings, *, max_steps: int = 24, handlers: Optional[dict] = None, loop=None,
                   run_id: str = "", monitor=None) -> dict:
    """Run the default v2 loop.

    The public v2 entrypoint now uses the simplified three-step controller
    (plan -> attempt -> conclude). The legacy 6-phase ``PhaseController`` is
    still available for tests and compatibility when custom handlers are passed.
    """
    from core.autoresearch_loop import AutoResearchLoop
    from core.autoresearch_phase_handlers import default_handlers
    from core.autoresearch_monitor import RunMonitor

    ensure_debug_from_settings(settings)
    loop = loop or AutoResearchLoop(settings)
    if monitor is None:
        monitor = RunMonitor(settings.monitor_file(), run_id=run_id, project_id=settings.project_id)
    if handlers is None:
        from core.autoresearch_three_step import run_three_step_loop

        return run_three_step_loop(settings, max_steps=max_steps, loop=loop, run_id=run_id, monitor=monitor)
    controller = PhaseController(settings, handlers=handlers or default_handlers(), loop=loop, monitor=monitor)
    reports = controller.run(max_steps=max_steps)
    budget = getattr(loop, "budget", None)
    return {
        "project_id": settings.project_id,
        "run_id": run_id,
        "steps": reports,
        "final_phase": (reports[-1]["next_phase"] if reports else "init"),
        "project_path": str(settings.project_state_file()),
        "program_path": str(settings.program_file()),
        "budget_path": str(settings.budget_file()),
        "monitor_path": str(settings.monitor_file()),
        "budget": (budget.snapshot() if budget is not None else {}),
    }
