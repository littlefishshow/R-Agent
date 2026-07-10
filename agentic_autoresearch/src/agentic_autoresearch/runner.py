from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .agent import AgentLoop
from .context import build_step_context
from .debug import DebugLog
from .eval_interface import ensure_eval_interface, read_eval
from .monitor import RunMonitor
from .steps import DEFAULT_STEPS, STEP_ORDER, next_step
from .tools import ToolRegistry, build_default_tools
from .types import AutoResearchConfig, StepReport, StepSpec
from .utils import atomic_write_json, read_json


class ThreeStepAutoResearch:
    """Automated plan -> attempt -> conclude runner.

    Each step is a complete agent loop. The outer runner advances only when the
    loop's final answer contains that step's done tag set to true.
    """

    def __init__(
        self,
        config: AutoResearchConfig,
        *,
        client,
        tools: ToolRegistry | None = None,
        steps: dict[str, StepSpec] | None = None,
    ):
        self.config = config
        self.client = client
        self.root = config.root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config.state_dir().mkdir(parents=True, exist_ok=True)
        self.config.artifacts_dir().mkdir(parents=True, exist_ok=True)
        ensure_eval_interface(self.root)
        self.debug = DebugLog(self.root, enabled=config.debug)
        self.monitor = RunMonitor(config.monitor_file(), run_id=config.run_id)
        self.tools = tools or build_default_tools(
            self.root,
            command_timeout_seconds=config.command_timeout_seconds,
            client=client,
            model=config.model,
            debug=self.debug,
            enable_delegate=True,
        )
        self.steps = steps or dict(DEFAULT_STEPS)
        self.agent = AgentLoop(
            client=client,
            model=config.model,
            tools=self.tools,
            debug=self.debug,
            trace_dir=config.trace_dir() if config.trace else None,
        )

    def run(self) -> dict[str, Any]:
        state = self._load_state()
        self.monitor.start(max_cycles=self.config.max_cycles)
        reports: list[dict[str, Any]] = []
        status = "completed"
        error = ""
        try:
            while int(state.get("cycle") or 0) < self.config.max_cycles:
                if self.config.stop_file().exists():
                    status = "stopped"
                    break
                step_name = str(state.get("current_step") or "plan")
                if step_name not in self.steps:
                    step_name = "plan"
                cycle = int(state.get("cycle") or 0)
                report = self.run_one_step(step_name, cycle, state.get("last_report") or {})
                reports.append(report.__dict__)
                state["last_report"] = report.__dict__
                if not report.done:
                    status = "failed" if self.config.stop_on_step_failure else "paused"
                    error = report.error or f"{step_name} did not finish"
                    if self.config.stop_on_step_failure:
                        break
                    state["current_step"] = step_name
                    self._save_state(state)
                    break
                state["current_step"] = report.next_step
                if step_name == "conclude":
                    if _project_solved(self.root):
                        self.config.stop_file().write_text(f"solved at {time.time()}\n", encoding="utf-8")
                        state["current_step"] = "plan"
                        state["cycle"] = cycle + 1
                        self._save_state(state)
                        status = "stopped"
                        break
                    state["cycle"] = cycle + 1
                self._save_state(state)
            else:
                status = "completed"
        except Exception as exc:
            status = "failed"
            error = str(exc)
            raise
        finally:
            self.monitor.finish(status=status, error=error, usage=self.agent.usage)
        return {
            "run_id": self.config.run_id,
            "status": status,
            "error": error,
            "state_path": str(self.config.state_file()),
            "monitor_path": str(self.config.monitor_file()),
            "trace_dir": str(self.config.trace_dir()),
            "reports": reports,
            "usage": dict(self.agent.usage),
        }

    def run_one_step(self, step_name: str, cycle: int, previous_report: dict[str, Any] | None = None) -> StepReport:
        spec = self.steps[step_name]
        self.monitor.step_start(cycle=cycle, step=step_name)
        self.debug.event("step_start", cycle=cycle, step=step_name)
        started = time.time()
        context = build_step_context(self.config, spec, previous_report=previous_report)
        result = self.agent.run_step(
            spec=spec,
            context=context,
            max_iterations=self.config.max_iterations_per_step,
        )
        nxt = next_step(step_name) if result.done else step_name
        report = StepReport(
            step=step_name,
            done=result.done,
            next_step=nxt,
            iterations=result.iterations,
            summary=_summary(result.content, result.error),
            trace_path=result.trace_path,
            error=result.error,
            started_at=started,
            finished_at=time.time(),
            stats=result.stats,
        )
        self.monitor.step_finish(
            cycle=cycle,
            step=step_name,
            next_step=nxt,
            summary=report.summary,
            usage=self.agent.usage,
            step_stats=result.stats,
        )
        self.debug.event(
            "step_finish",
            cycle=cycle,
            step=step_name,
            next_step=nxt,
            done=result.done,
            iterations=result.iterations,
            error=result.error,
            trace_path=result.trace_path,
        )
        return report

    def _load_state(self) -> dict[str, Any]:
        state = read_json(self.config.state_file(), {}) or {}
        if not state or (state.get("run_id") and state.get("run_id") != self.config.run_id):
            state = {
                "version": 1,
                "run_id": self.config.run_id,
                "cycle": 0,
                "current_step": "plan",
                "step_order": list(STEP_ORDER),
                "last_report": {},
                "created_at": time.time(),
            }
            self._save_state(state)
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = time.time()
        atomic_write_json(self.config.state_file(), state)


def _summary(content: str, error: str = "", *, limit: int = 500) -> str:
    text = str(error or content or "").replace("\n", " ").strip()
    return text[:limit]


def _project_solved(root: Path) -> bool:
    try:
        return bool(read_eval(root).get("solved"))
    except Exception:
        return False
