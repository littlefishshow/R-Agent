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

from autoresearch.autoresearch_debug import debug_event, ensure_debug_from_settings, inflight_finish, inflight_start
from autoresearch.autoresearch_gate_state import load_gate_state
from autoresearch.autoresearch_memory import (
    DEFAULT_PROJECT_TEMPLATE,
    ensure_program_scaffold,
    read_phase,
    write_auto_note,
    write_phase,
)
from autoresearch.autoresearch_phase_handlers import (
    make_compress_handler,
    make_evaluate_handler,
    make_init_handler,
)
from autoresearch.autoresearch_phases import PhaseContext, PhaseResult, PhaseSignals
from autoresearch.autoresearch_step_runtime import allowed_tools_for_step, build_step_context, excluded_tools_for_step, step_policy
from autoresearch.autoresearch_todo_state import (
    has_blocking_failed_tasks,
    has_failed_tasks,
    has_open_tasks,
    load_todo_state,
    merge_todo_state,
    render_todo_markdown,
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
        from autoresearch.autoresearch_personas import make_plan_handler
        from autoresearch.autoresearch_execution import make_execute_handler, make_run_handler

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
            plan_still_valid=bool(gate.get("plan_still_valid", True)) and not has_blocking_failed_tasks(todo_state),
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
        if signals.solved:
            return "pause", "solved target reached"
        if signals.budget_exhausted:
            return "pause", "budget exhausted: pausing for user"
        todo_state = load_todo_state(self.root)
        if has_blocking_failed_tasks(todo_state):
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
        agent_result = self._run_step_agent_loop("plan", signals)
        if agent_result is not None and agent_result.signals_update.get("step_done"):
            ctx = self._ctx("plan", signals)
            project_text = agent_result.project_text or ctx.project_text
            return PhaseResult(project_text=project_text, summary=agent_result.summary), project_text, "attempt"
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
        policy = step_policy("attempt")
        payload = {
            "created_at": time.time(),
            "project_id": self.settings.project_id,
            "phase": "attempt",
            "done_tag": policy.done_tag,
            "step_goal": policy.goal,
            "task": task,
            "todo_digest": self._ready_task_digest(),
            "step_context": build_step_context(self.root, "attempt", task=task),
            "policy": {
                "parent_role": "schedule and summarize through todo digest",
                "child_role": "read needed files, edit allowed project files, run validation, update task state",
                "context_retention": "full child context is stored as artifact path, not inlined into parent state",
                "allowed_tools": list(policy.allowed_tools),
                "child_allowed_tools": list(policy.child_allowed_tools),
                "child_excluded_tools": list(policy.child_excluded_tools),
                "done_tag": policy.done_tag,
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
        agent_result = self._run_step_agent_loop("attempt", signals)
        if agent_result is not None and agent_result.signals_update.get("step_done"):
            ctx = self._ctx("attempt", signals)
            project_text = agent_result.project_text or ctx.project_text
            return agent_result, project_text, "conclude"
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
        next_phase = "pause" if combined_signals.get("solved") else "conclude"
        return result, project_text, next_phase

    def _run_conclude(self, signals: PhaseSignals) -> tuple[PhaseResult, str, str]:
        agent_result = self._run_step_agent_loop("conclude", signals)
        if agent_result is not None and agent_result.signals_update.get("step_done"):
            ctx = self._ctx("conclude", signals)
            project_text = agent_result.project_text or ctx.project_text
            next_phase, _reason = self._next_after_conclude(signals)
            return agent_result, project_text, next_phase
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

    def _run_step_agent_loop(self, step_name: str, signals: PhaseSignals) -> Optional[PhaseResult]:
        """Optional R-Agent-style loop for one autoresearch step.

        The phase handlers remain the deterministic fallback.  When
        settings.autoresearch_step_agent_loop is true, each step can run a full
        RAgent loop with a step-specific context and tool whitelist.
        """
        if not bool(getattr(self.settings, "autoresearch_step_agent_loop", False)):
            return None
        try:
            from core.agent import RAgent
        except Exception as exc:
            return PhaseResult(summary=f"{step_name}: step agent unavailable: {exc}")
        policy = step_policy(step_name)
        state = load_todo_state(self.root)
        ready_execute = ready_tasks(state, phase="execute", statuses={"pending", "in_progress"})
        ready_run = ready_tasks(state, phase="run", statuses={"pending", "in_progress"})
        task = (ready_execute or ready_run or [None])[0]
        context = build_step_context(self.root, step_name, task=task)
        system = _step_system_prompt(policy, context)
        user = json.dumps({
            "step": step_name,
            "done_tag": policy.done_tag,
            "instruction": (
                f"Work only on the {step_name} step. Use tools as needed. "
                f"When this step is complete, include the exact tag {policy.done_tag} in your final answer."
            ),
            "context": context,
        }, ensure_ascii=False, indent=2, default=str)
        agent = RAgent(
            max_iterations=int(getattr(self.settings, "autoresearch_step_max_iterations", 12) or 12),
            session_id=f"autoresearch-{self.settings.project_id}-{step_name}",
        )
        result = agent.run_conversation(
            user_message=user,
            system_message=system,
            allowed_tools=allowed_tools_for_step(step_name),
            exclude_tools=excluded_tools_for_step(step_name),
            tool_call_guard=policy.tool_guard(),
        )
        done = policy.done_tag in str(result)
        project_text = self._apply_step_agent_result(step_name, result) if done else None
        artifact = self._save_step_agent_result(step_name, result, done)
        return PhaseResult(
            project_text=project_text,
            summary=f"{step_name}: agent_loop done={done} artifact={artifact}",
            signals_update={"step_done": done},
        )

    def _write_step_trace(self, *, phase: str, signals: PhaseSignals, result: PhaseResult,
                          next_phase: str, reason: str) -> str:
        if not bool(getattr(self.settings, "trace_rounds", False)):
            return ""
        d = self.root / ".autoresearch" / "step_traces"
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "step_index": self._step_index,
            "phase": phase,
            "next_phase": next_phase,
            "reason": reason,
            "timestamp": time.strftime("%F %T"),
            "signals": dict(getattr(signals, "__dict__", {}) or {}),
            "summary": result.summary,
            "signals_update": result.signals_update,
            "program_excerpt": self.read_program()[:5000],
            "project_excerpt": self.read_project()[:5000],
            "todo_digest": self._ready_task_digest(),
            "monitor_path": str(self.settings.monitor_file()),
            "debug_paths": {
                "debug_jsonl": str(self.root / ".autoresearch" / "debug" / "debug.jsonl"),
                "inflight_json": str(self.root / ".autoresearch" / "debug" / "inflight.json"),
            },
        }
        path = d / f"step_{self._step_index:03d}_{phase}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return str(path)

    def _apply_step_agent_result(self, step_name: str, result: str) -> str | None:
        if step_name != "plan":
            return None
        try:
            from autoresearch.autoresearch_loop import extract_json_object

            data = extract_json_object(str(result))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        tasks = data.get("tasks") or data.get("task_dag") or []
        if not isinstance(tasks, list) or not tasks:
            return None
        planned = {"version": 1, "tasks": [task for task in tasks if isinstance(task, dict)]}
        merged = merge_todo_state(load_todo_state(self.root), planned)
        save_todo_state(self.root, merged)
        write_auto_note(self.root, "plan", render_todo_markdown(merged))
        plan_text = str(data.get("plan") or data.get("summary") or "Plan produced by autoresearch step agent.").strip()
        return _update_project_plan_section(self.read_project(), plan_text)

    def _save_step_agent_result(self, step_name: str, result: str, done: bool) -> str:
        d = self.root / ".autoresearch" / "step_agent_results"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{int(time.time() * 1000)}_{step_name}.json"
        path.write_text(json.dumps({
            "step": step_name,
            "done": bool(done),
            "result": str(result),
            "created_at": time.time(),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return str(path)

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
                reason = "solved target reached" if nxt == "pause" else "attempt completed"
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
        trace_path = self._write_step_trace(phase=phase, signals=signals, result=result, next_phase=nxt, reason=reason)
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
                    next_phase=nxt, reason=reason, summary=result.summary, step_trace_path=trace_path)
        return {
            "ran_phase": phase,
            "next_phase": nxt,
            "reason": reason,
            "summary": result.summary,
            "step_index": self._step_index,
            "step_trace_path": trace_path,
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
    from autoresearch.autoresearch_loop import AutoResearchLoop
    from autoresearch.autoresearch_monitor import RunMonitor

    ensure_debug_from_settings(settings)
    settings.defer_experiment_finalization = True
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


def _step_system_prompt(policy, context: dict) -> str:
    allowed_skills = ", ".join(policy.allowed_skills) if policy.allowed_skills else "(none)"
    return (
        "You are an isolated AutoResearch step agent.\n"
        "You are not the global controller. Stay inside this step's goal and tool policy.\n"
        f"Step: {policy.name}\n"
        f"Goal: {policy.goal}\n"
        f"Done tag: {policy.done_tag}\n"
        f"Allowed skills through skill_view: {allowed_skills}\n"
        "Use only the tools exposed to you. Do not assume access to the parent conversation.\n"
        "If you need child agents, use delegate_task only from the parent step context and pass child_allowed_tools from the step context; child agents cannot delegate again.\n"
        "When the step is complete, include the done tag exactly once in the final answer.\n"
        "If the step is not complete, summarize blockers and do not include the done tag.\n"
        "Context is JSON in the user message; artifact paths are trace handles, not text to paste back wholesale.\n"
    )


def _update_project_plan_section(project_text: str, plan_text: str) -> str:
    marker = "## 当前计划"
    if marker not in project_text:
        return project_text.rstrip() + f"\n\n{marker}\n{plan_text}\n"
    head, _, rest = project_text.partition(marker)
    after = rest.split("\n## ", 1)
    body = f"{marker}\n{plan_text}\n"
    if len(after) == 2:
        return head + body + "\n## " + after[1]
    return head + body
