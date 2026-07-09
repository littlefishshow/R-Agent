"""AutoResearch V3 simplified three-step loop.

The loop intentionally keeps the outer control structure small:

- plan: read stable project context and produce/refresh a DAG todo state.
- attempt: execute the next ready task and immediately run a ready metric
  checkpoint when one becomes available.
- conclude: record lessons, gate signals, and bounded memory before continuing.

The heavy services are reused from the existing autoresearch stack: project
boundary, command runner, artifacts, budget, monitor, todo_state, and the safe
action surface all remain owned by ``AutoResearchLoop``.
"""

from __future__ import annotations

import os
import json
import time
from pathlib import Path
from typing import Optional

from core.autoresearch_debug import debug_event, ensure_debug_from_settings, inflight_finish, inflight_start
from core.autoresearch_gate_state import load_gate_state
from core.autoresearch_memory import (
    DEFAULT_PROJECT_TEMPLATE,
    ensure_program_scaffold,
    read_phase,
    write_phase,
)
from core.autoresearch_phase_handlers import (
    make_compress_handler,
    make_evaluate_handler,
    make_init_handler,
)
from core.autoresearch_phases import PhaseContext, PhaseResult, PhaseSignals
from core.autoresearch_todo_state import (
    has_failed_tasks,
    has_open_tasks,
    load_todo_state,
    save_todo_state,
    ready_tasks,
)


class ThreeStepController:
    """File-backed controller for plan -> attempt -> conclude."""

    def __init__(self, settings, *, loop=None, monitor=None, run_id: str = ""):
        self.settings = settings
        self.loop = loop
        self.root = Path(settings.root())
        self.monitor = monitor
        self.run_id = run_id
        self._step_index = 0
        self._init_handler = make_init_handler()
        from core.autoresearch_personas import make_plan_handler
        from core.autoresearch_execution import make_execute_handler, make_run_handler

        self._plan_handler = make_plan_handler()
        self._execute_handler = make_execute_handler()
        self._run_handler = make_run_handler()
        self._evaluate_handler = make_evaluate_handler()
        self._compress_handler = make_compress_handler()

    def _program_path(self) -> Path:
        return self.settings.program_file()

    def _project_path(self) -> Path:
        return self.settings.project_state_file()

    def _atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def read_program(self) -> str:
        p = self._program_path()
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def read_project(self) -> str:
        p = self._project_path()
        return p.read_text(encoding="utf-8") if p.exists() else DEFAULT_PROJECT_TEMPLATE

    def ensure_scaffold(self) -> None:
        prog_path = self._program_path()
        if prog_path.exists():
            current = prog_path.read_text(encoding="utf-8")
            scaffolded = ensure_program_scaffold(current)
            if scaffolded != current:
                self._atomic_write(prog_path, scaffolded)
        proj_path = self._project_path()
        if not proj_path.exists():
            self._atomic_write(proj_path, write_phase(DEFAULT_PROJECT_TEMPLATE, "init", "scaffolded"))

    def current_phase(self) -> tuple[str, str]:
        return read_phase(self.read_project())

    def build_signals(self, phase: str, extra: Optional[dict] = None) -> PhaseSignals:
        budget = getattr(self.loop, "budget", None)
        gate = load_gate_state(self.root)
        todo_state = load_todo_state(self.root)
        sig = PhaseSignals(
            phase=phase,
            plateau_patience=int(getattr(self.settings, "plateau_patience", 3)),
            pareto_changed=bool(gate.get("pareto_changed")),
            plateau_counter=int(gate.get("plateau_counter") or 0),
            plan_still_valid=bool(gate.get("plan_still_valid", True)) and not has_failed_tasks(todo_state),
            plan_has_open_tasks=has_open_tasks(todo_state),
            budget_exhausted=bool(budget.is_exhausted()) if budget is not None else False,
            budget_degrade=bool(budget.should_degrade()) if budget is not None else False,
        )
        for key, value in (extra or {}).items():
            if hasattr(sig, key):
                setattr(sig, key, value)
        return sig

    def _ctx(self, phase: str, signals: PhaseSignals) -> PhaseContext:
        return PhaseContext(
            phase=phase,
            root=self.root,
            program_text=self.read_program(),
            project_text=self.read_project(),
            signals=signals,
            loop=self.loop,
        )

    def _persist_result(self, ctx: PhaseContext, result: PhaseResult) -> str:
        if result.program_text is not None:
            self._atomic_write(self._program_path(), result.program_text)
        project_text = result.project_text if result.project_text is not None else ctx.project_text
        return project_text

    def _next_after_conclude(self, signals: PhaseSignals) -> tuple[str, str]:
        if signals.budget_exhausted:
            return "pause", "budget exhausted: pausing for user"
        todo_state = load_todo_state(self.root)
        if has_failed_tasks(todo_state):
            return "plan", "failed task requires replanning"
        if has_open_tasks(todo_state):
            return "attempt", "current DAG still has open work"
        return "plan", "DAG exhausted: plan next research direction"

    def _run_init_if_needed(self) -> None:
        phase, _ = self.current_phase()
        if phase != "init":
            return
        signals = self.build_signals("init")
        ctx = self._ctx("init", signals)
        result = self._init_handler(ctx) or PhaseResult()
        project_text = self._persist_result(ctx, result)
        self._atomic_write(self._project_path(), write_phase(project_text, "plan", "initial survey complete"))

    def _run_plan(self, signals: PhaseSignals) -> tuple[PhaseResult, str, str]:
        ctx = self._ctx("plan", signals)
        result = self._plan_handler(ctx) or PhaseResult()
        project_text = self._persist_result(ctx, result)
        return result, project_text, "attempt"

    def _ready_task_digest(self) -> dict:
        state = load_todo_state(self.root)
        tasks = state.get("tasks") or []
        ready_execute = ready_tasks(state, phase="execute", statuses={"pending", "in_progress"})
        ready_run = ready_tasks(state, phase="run", statuses={"pending", "in_progress"})
        return {
            "total": len(tasks),
            "status_counts": {
                status: sum(1 for task in tasks if task.get("status") == status)
                for status in ("pending", "in_progress", "verified", "failed", "blocked", "skipped")
            },
            "ready_execute": [
                {
                    "task_id": task.get("task_id"),
                    "goal": task.get("goal"),
                    "type": task.get("type"),
                    "depends_on": task.get("depends_on", []),
                    "last_result": _compact_last_result(task.get("last_result") or {}),
                }
                for task in ready_execute[:5]
            ],
            "ready_run": [
                {
                    "task_id": task.get("task_id"),
                    "goal": task.get("goal"),
                    "type": task.get("type"),
                    "depends_on": task.get("depends_on", []),
                    "run_spec": task.get("run_spec", {}),
                    "last_result": _compact_last_result(task.get("last_result") or {}),
                }
                for task in ready_run[:5]
            ],
        }

    def _save_attempt_context_artifact(self) -> str:
        """Persist a child-task context artifact for R-Agent-style delegation.

        The V3 parent continues to schedule via todo_state, but it now exposes the
        exact self-contained context a child process would receive. This mirrors
        delegate_task's "parent sees digest, child history lives in artifacts"
        policy and gives us a clean seam for swapping in real child Agents.
        """
        state = load_todo_state(self.root)
        ready_execute = ready_tasks(state, phase="execute", statuses={"pending", "in_progress"})
        ready_run = ready_tasks(state, phase="run", statuses={"pending", "in_progress"})
        task = (ready_execute or ready_run or [None])[0]
        if not task:
            return ""
        task_id = str(task.get("task_id") or "task")
        payload = {
            "created_at": time.time(),
            "project_id": self.settings.project_id,
            "phase": "attempt",
            "task": task,
            "todo_digest": self._ready_task_digest(),
            "program_md": self.read_program()[:12000],
            "project_md": self.read_project()[:8000],
            "policy": {
                "parent_role": "schedule and summarize through todo digest",
                "child_role": "read needed files, edit allowed project files, run validation, update task state",
                "context_retention": "full child context is stored as artifact path, not inlined into parent state",
            },
        }
        d = self.root / ".autoresearch" / "delegate_contexts"
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in task_id)[:80] or "task"
        path = d / f"{int(time.time() * 1000)}_{safe}_attempt_context.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        _attach_context_artifact_to_task(self.root, task_id, str(path))
        return str(path)

    def _run_attempt(self, signals: PhaseSignals) -> tuple[PhaseResult, str, str]:
        context_artifact = self._save_attempt_context_artifact()
        ctx = self._ctx("attempt", signals)
        execute_result = self._execute_handler(ctx) or PhaseResult()
        project_text = self._persist_result(ctx, execute_result)
        combined_summary = execute_result.summary
        if context_artifact:
            combined_summary = f"{combined_summary} context={context_artifact}".strip()
        combined_signals = dict(execute_result.signals_update or {})

        # If the code/read part completed enough to unlock a metric checkpoint,
        # run it immediately in the same outer step. This is the core simplified
        # loop: modification and evidence collection are one attempt.
        todo_state = load_todo_state(self.root)
        run_ready = bool(ready_tasks(todo_state, phase="run", statuses={"pending", "in_progress"}))
        if run_ready and not combined_signals.get("major_error"):
            run_ctx = self._ctx("run", self.build_signals("run"))
            run_result = self._run_handler(run_ctx) or PhaseResult()
            if run_result.project_text is not None:
                project_text = run_result.project_text
            combined_summary = f"{combined_summary}; {run_result.summary}".strip("; ")
            combined_signals.update(run_result.signals_update or {})

        result = PhaseResult(
            project_text=project_text,
            signals_update=combined_signals,
            summary=combined_summary or "attempt: no work",
        )
        next_phase = "conclude"
        return result, project_text, next_phase

    def _run_conclude(self, signals: PhaseSignals) -> tuple[PhaseResult, str, str]:
        ctx = self._ctx("conclude", signals)
        eval_result = self._evaluate_handler(ctx) or PhaseResult()
        project_text = self._persist_result(ctx, eval_result)

        compress_ctx = PhaseContext(
            phase="conclude",
            root=self.root,
            program_text=self.read_program(),
            project_text=project_text,
            signals=signals,
            loop=self.loop,
        )
        compress_result = self._compress_handler(compress_ctx) or PhaseResult()
        if compress_result.program_text is not None:
            self._atomic_write(self._program_path(), compress_result.program_text)
        if compress_result.project_text is not None:
            project_text = compress_result.project_text

        next_phase, reason = self._next_after_conclude(signals)
        summary = "; ".join(s for s in (eval_result.summary, compress_result.summary) if s)
        return PhaseResult(project_text=project_text, summary=summary), project_text, next_phase

    def step(self, extra_signals: Optional[dict] = None) -> dict:
        self.ensure_scaffold()
        self._run_init_if_needed()
        phase, _reason = self.current_phase()
        if phase not in {"plan", "attempt", "conclude", "pause"}:
            phase = "plan"
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
        inflight_start(self.root, "phase", phase=phase, detail=f"{phase} step")
        try:
            if phase == "plan":
                result, project_text, nxt = self._run_plan(signals)
                reason = "plan produced DAG"
            elif phase == "attempt":
                result, project_text, nxt = self._run_attempt(signals)
                reason = "attempt completed"
            elif phase == "conclude":
                result, project_text, nxt = self._run_conclude(signals)
                reason = "conclusion recorded"
                if nxt == "pause":
                    reason = "budget exhausted: pausing for user"
                elif nxt == "plan":
                    reason = "plan next research direction"
                else:
                    reason = "continue current DAG"
            else:
                result = PhaseResult(summary="paused: awaiting user")
                project_text = self.read_project()
                nxt = "pause"
                reason = "paused"
        finally:
            inflight_finish(self.root, "phase", phase=phase)

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
        if self.monitor is not None:
            self.monitor.set_max_steps(max_steps)
            self.monitor.start()
        reports = []
        error = ""
        try:
            for _ in range(max(0, int(max_steps))):
                stop_file = self.root / ".autoresearch" / "STOP"
                if stop_file.exists():
                    if self.monitor is not None:
                        self.monitor.finish(status="paused", error="stopped_by_request")
                    break
                report = self.step(extra_signals)
                reports.append(report)
                if report["next_phase"] == "pause":
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


def run_three_step_loop(settings, *, max_steps: int = 24, loop=None, run_id: str = "", monitor=None) -> dict:
    from core.autoresearch_loop import AutoResearchLoop
    from core.autoresearch_monitor import RunMonitor

    ensure_debug_from_settings(settings)
    loop = loop or AutoResearchLoop(settings)
    if monitor is None:
        monitor = RunMonitor(settings.monitor_file(), run_id=run_id, project_id=settings.project_id)
    controller = ThreeStepController(settings, loop=loop, monitor=monitor, run_id=run_id)
    reports = controller.run(max_steps=max_steps)
    budget = getattr(loop, "budget", None)
    return {
        "project_id": settings.project_id,
        "run_id": run_id,
        "steps": reports,
        "final_phase": (reports[-1]["next_phase"] if reports else "plan"),
        "project_path": str(settings.project_state_file()),
        "program_path": str(settings.program_file()),
        "budget_path": str(settings.budget_file()),
        "monitor_path": str(settings.monitor_file()),
        "budget": (budget.snapshot() if budget is not None else {}),
    }


__all__ = ["ThreeStepController", "run_three_step_loop"]


def _compact_last_result(last: dict) -> dict:
    if not isinstance(last, dict):
        return {}
    return {
        "status": last.get("status", ""),
        "verification": last.get("verification"),
        "note": str(last.get("note", ""))[:600],
        "metric": (last.get("behavior") or {}).get("metric") if isinstance(last.get("behavior"), dict) else last.get("metric"),
        "artifacts": list(last.get("artifacts") or [])[:4] if isinstance(last.get("artifacts"), list) else [],
        "attempts": last.get("attempts"),
    }


def _attach_context_artifact_to_task(root: Path, task_id: str, artifact_path: str) -> None:
    state = load_todo_state(root)
    for task in state.get("tasks", []):
        if task.get("task_id") != task_id:
            continue
        artifacts = list(task.get("artifacts") or [])
        if artifact_path not in artifacts:
            artifacts.append(artifact_path)
        task["artifacts"] = artifacts[-12:]
        last = dict(task.get("last_result") or {})
        last["context_artifact_path"] = artifact_path
        task["last_result"] = last
        save_todo_state(root, state)
        break
