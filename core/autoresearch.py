from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
from typing import Callable


class AutoresearchInterrupted(Exception):
    """用户主动中断 autoresearch 运行。"""


@dataclass
class AutoresearchPaths:
    project_root: Path
    state_dir: Path
    runs_dir: Path
    run_dir: Path
    state_json: Path
    plan_json: Path
    execute_result_json: Path
    conclude_result_json: Path
    memory_md: Path
    lessons_md: Path
    results_tsv: Path
    traces_dir: Path
    trace_jsonl: Path
    flow_md: Path
    contexts_dir: Path


BASE_SAFE_COMMANDS = [
    ["pwd"],
    ["find", ".", "-maxdepth", "2", "-type", "f"],
]


def _safe_inventory_commands(project_root: Path) -> list[list[str]]:
    commands = [[*cmd] for cmd in BASE_SAFE_COMMANDS]
    if (project_root / ".git").exists():
        commands.insert(1, ["git", "status", "--short"])
    return commands


def _now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _run_id() -> str:
    return time.strftime("exp_%Y%m%d_%H%M%S")


def _is_cancelled(cancel_event=None) -> bool:
    return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())


def _check_cancel(cancel_event=None):
    if _is_cancelled(cancel_event):
        raise AutoresearchInterrupted()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return dict(default or {})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def resolve_project_root(project_path: str | os.PathLike[str]) -> Path:
    raw = Path(project_path).expanduser()
    root = raw.resolve()
    if not root.exists():
        raise FileNotFoundError(f"项目路径不存在：{root}")
    if not root.is_dir():
        raise NotADirectoryError(f"项目路径不是目录：{root}")
    return root


def build_paths(project_path: str | os.PathLike[str], run_id: str | None = None) -> AutoresearchPaths:
    project_root = resolve_project_root(project_path)
    state_dir = project_root / ".autoresearch"
    runs_dir = state_dir / "runs"
    rid = run_id or _run_id()
    run_dir = runs_dir / rid
    return AutoresearchPaths(
        project_root=project_root,
        state_dir=state_dir,
        runs_dir=runs_dir,
        run_dir=run_dir,
        state_json=state_dir / "state.json",
        plan_json=state_dir / "plan.json",
        execute_result_json=state_dir / "execute_result.json",
        conclude_result_json=state_dir / "conclude_result.json",
        memory_md=state_dir / "memory.md",
        lessons_md=state_dir / "lessons.md",
        results_tsv=state_dir / "results.tsv",
        traces_dir=state_dir / "traces",
        trace_jsonl=state_dir / "traces" / "trace.jsonl",
        flow_md=state_dir / "traces" / "flow.md",
        contexts_dir=state_dir / "traces" / "contexts",
    )




class AutoresearchTracer:
    """把 Plan/Execute/Conclude 的调试事件、上下文快照和流程归档到 .autoresearch/traces。"""

    def __init__(self, paths: AutoresearchPaths, enabled: bool = True):
        self.paths = paths
        self.enabled = enabled
        self.sequence = 0
        self.run_trace_jsonl = paths.run_dir / "trace.jsonl"
        self.run_flow_md = paths.run_dir / "flow.md"

    def _ensure_dirs(self) -> None:
        self.paths.traces_dir.mkdir(parents=True, exist_ok=True)
        self.paths.contexts_dir.mkdir(parents=True, exist_ok=True)
        self.paths.run_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, worker: str, event: str, message: str = "", data: dict | None = None) -> dict:
        if not self.enabled:
            return {}
        self._ensure_dirs()
        self.sequence += 1
        record = {
            "seq": self.sequence,
            "run_id": self.paths.run_dir.name,
            "ts": _now_ts(),
            "worker": worker,
            "event": event,
            "message": message,
            "data": data or {},
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        for path in (
            self.paths.trace_jsonl,
            self.run_trace_jsonl,
            self.paths.traces_dir / f"{worker.lower()}.jsonl",
        ):
            _append_text(path, line)
        flow_line = f"- `{record['ts']}` **{worker}.{event}** {message}\n"
        _append_text(self.paths.flow_md, flow_line)
        _append_text(self.run_flow_md, flow_line)
        return record

    def snapshot_context(self, worker: str, context: dict, label: str = "latest") -> Path | None:
        if not self.enabled:
            return None
        self._ensure_dirs()
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label) or "latest"
        payload = {
            "run_id": self.paths.run_dir.name,
            "worker": worker,
            "label": safe_label,
            "captured_at": _now_ts(),
            "context": deepcopy(context),
        }
        latest_path = self.paths.contexts_dir / f"{worker.lower()}_latest.json"
        labelled_path = self.paths.contexts_dir / f"{worker.lower()}_{safe_label}.json"
        run_path = self.paths.run_dir / f"{worker.lower()}_{safe_label}_context.json"
        for path in (latest_path, labelled_path, run_path):
            _write_json(path, payload)
        self.emit(worker, "context_snapshot", f"已保存上下文快照：{label}", {"path": str(labelled_path)})
        return labelled_path


def _noop_tracer(paths: AutoresearchPaths) -> AutoresearchTracer:
    return AutoresearchTracer(paths, enabled=False)


def _update_state(paths: AutoresearchPaths, **updates) -> dict:
    state = _read_json(paths.state_json, default={"version": 1})
    state.update(updates)
    state["updated_at"] = _now_ts()
    _write_json(paths.state_json, state)
    return state


def init_state(paths: AutoresearchPaths, objective: str = "最小 autoresearch 闭环") -> dict:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.runs_dir.mkdir(parents=True, exist_ok=True)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.traces_dir.mkdir(parents=True, exist_ok=True)
    paths.contexts_dir.mkdir(parents=True, exist_ok=True)
    if not paths.memory_md.exists():
        paths.memory_md.write_text("# Autoresearch Memory\n\n这里保存本项目 autoresearch 的长期观察。\n", encoding="utf-8")
    if not paths.lessons_md.exists():
        paths.lessons_md.write_text("# Autoresearch Lessons\n\n这里保存每轮实验后学到的经验。\n", encoding="utf-8")
    if not paths.results_tsv.exists():
        paths.results_tsv.write_text("run_id\tstarted_at\tdecision\tstatus\tfailed_commands\tlog_dir\n", encoding="utf-8")
    if not paths.flow_md.exists():
        paths.flow_md.write_text("# Autoresearch Debug Flow\n\n这里按时间记录 Plan / Execute / Conclude 的具体流程。\n", encoding="utf-8")
    return _update_state(
        paths,
        version=1,
        mode="autoresearch",
        project_root=str(paths.project_root),
        run_id=paths.run_dir.name,
        objective=objective,
        phase="initialized",
        started_at=_now_ts(),
        interrupted=False,
    )


def plan_worker(paths: AutoresearchPaths, objective: str, cancel_event=None, on_status: Callable[[str], None] | None = None, tracer: AutoresearchTracer | None = None) -> dict:
    tracer = tracer or _noop_tracer(paths)
    _check_cancel(cancel_event)
    tracer.emit("Plan", "start", "开始制定只读调研计划", {"objective": objective, "project_root": str(paths.project_root)})
    if on_status:
        on_status("[bold cyan]🔎 Autoresearch Plan：正在制定只读调研计划...[/bold cyan]")
    _update_state(paths, phase="plan")
    plan = {
        "role": "Plan",
        "run_id": paths.run_dir.name,
        "created_at": _now_ts(),
        "objective": objective,
        "rules": [
            "Plan 只规划，不改代码。",
            "第一版只执行安全、只读、短时间命令。",
            "如果需要进一步拆分或高风险动作，写入 lessons.md，交给用户决定。",
        ],
        "experiments": [
            {
                "id": "baseline_inventory",
                "description": "读取项目的最小基线信息，确认路径、git 状态和浅层文件结构。",
                "commands": _safe_inventory_commands(paths.project_root),
                "success_criteria": "命令可运行，日志完整保存。",
            }
        ],
    }
    _write_json(paths.plan_json, plan)
    _write_json(paths.run_dir / "plan.json", plan)
    tracer.snapshot_context("Plan", {"objective": objective, "plan": plan, "safe_commands": plan["experiments"][0]["commands"]}, label="after_plan")
    tracer.emit("Plan", "finish", "计划已生成", {"experiments": len(plan.get("experiments", [])), "plan_json": str(paths.plan_json)})
    return plan


def _run_command_capture(command: list[str], cwd: Path, timeout: int = 20, cancel_event=None) -> dict:
    _check_cancel(cancel_event)
    started = _now_ts()
    deadline = time.monotonic() + max(1, int(timeout or 20))
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        timed_out = False
        while proc.poll() is None:
            if _is_cancelled(cancel_event):
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise AutoresearchInterrupted()
            if time.monotonic() >= deadline:
                timed_out = True
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        stdout, stderr = proc.communicate(timeout=1)
        returncode = proc.returncode if proc.returncode is not None else -1
        if timed_out:
            returncode = -1
            stderr = (stderr or "") + f"\nCommand timed out after {timeout}s."
        return {
            "command": command,
            "started_at": started,
            "finished_at": _now_ts(),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    except AutoresearchInterrupted:
        raise
    except Exception as exc:
        return {
            "command": command,
            "started_at": started,
            "finished_at": _now_ts(),
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }


def execute_worker(paths: AutoresearchPaths, plan: dict, cancel_event=None, on_status: Callable[[str], None] | None = None, tracer: AutoresearchTracer | None = None) -> dict:
    tracer = tracer or _noop_tracer(paths)
    _check_cancel(cancel_event)
    tracer.emit("Execute", "start", "开始执行安全只读实验", {"experiment_count": len(plan.get("experiments", []))})
    tracer.snapshot_context("Execute", {"plan": plan, "project_root": str(paths.project_root)}, label="before_execute")
    if on_status:
        on_status("[bold cyan]🧪 Autoresearch Execute：正在执行安全只读实验...[/bold cyan]")
    _update_state(paths, phase="execute")
    command_results = []
    for experiment in plan.get("experiments", []):
        for command in experiment.get("commands", []):
            _check_cancel(cancel_event)
            if on_status:
                on_status(f"[bold cyan]🧪 Autoresearch Execute：{' '.join(command)}[/bold cyan]")
            tracer.emit("Execute", "command_start", f"执行命令：{' '.join(command)}", {"experiment_id": experiment.get("id"), "command": command})
            result = _run_command_capture(command, paths.project_root, cancel_event=cancel_event)
            command_record = {"experiment_id": experiment.get("id"), **result}
            command_results.append(command_record)
            log_name = f"{len(command_results):02d}_{command[0]}.log"
            log_text = (
                f"$ {' '.join(command)}\n"
                f"returncode: {result['returncode']}\n\n"
                f"[stdout]\n{result['stdout']}\n\n"
                f"[stderr]\n{result['stderr']}\n"
            )
            log_path = paths.run_dir / log_name
            log_path.write_text(log_text, encoding="utf-8")
            tracer.emit(
                "Execute",
                "command_finish",
                f"命令结束：{' '.join(command)} -> {result['returncode']}",
                {
                    "experiment_id": experiment.get("id"),
                    "command": command,
                    "returncode": result["returncode"],
                    "stdout_chars": len(result.get("stdout") or ""),
                    "stderr_chars": len(result.get("stderr") or ""),
                    "log_path": str(log_path),
                },
            )
    execute_result = {
        "role": "Execute",
        "run_id": paths.run_dir.name,
        "created_at": _now_ts(),
        "status": "completed",
        "command_results": command_results,
        "log_dir": str(paths.run_dir),
    }
    _write_json(paths.execute_result_json, execute_result)
    _write_json(paths.run_dir / "execute_result.json", execute_result)
    tracer.snapshot_context("Execute", {"plan": plan, "execute_result": execute_result}, label="after_execute")
    tracer.emit("Execute", "finish", "执行阶段完成", {"command_count": len(command_results), "execute_result_json": str(paths.execute_result_json)})
    return execute_result


def conclude_worker(paths: AutoresearchPaths, plan: dict, execute_result: dict, cancel_event=None, on_status: Callable[[str], None] | None = None, tracer: AutoresearchTracer | None = None) -> dict:
    tracer = tracer or _noop_tracer(paths)
    _check_cancel(cancel_event)
    tracer.emit("Conclude", "start", "开始解析日志并写总结", {"command_count": len(execute_result.get("command_results", []))})
    tracer.snapshot_context("Conclude", {"plan": plan, "execute_result": execute_result}, label="before_conclude")
    if on_status:
        on_status("[bold cyan]📌 Autoresearch Conclude：正在解析日志并写总结...[/bold cyan]")
    _update_state(paths, phase="conclude")
    command_results = execute_result.get("command_results", [])
    failed = [r for r in command_results if int(r.get("returncode", -1)) != 0]
    decision = "keep" if not failed else "crash"
    status = "completed" if not failed else "completed_with_errors"
    conclude_result = {
        "role": "Conclude",
        "run_id": paths.run_dir.name,
        "created_at": _now_ts(),
        "decision": decision,
        "status": status,
        "summary": "第一版 autoresearch 已完成一次安全只读闭环。" if not failed else "第一版 autoresearch 完成，但有命令失败，请查看日志。",
        "failed_commands": [r.get("command") for r in failed],
        "kept_files": [
            str(paths.state_json),
            str(paths.plan_json),
            str(paths.execute_result_json),
            str(paths.conclude_result_json),
            str(paths.lessons_md),
            str(paths.results_tsv),
            str(paths.run_dir),
            str(paths.trace_jsonl),
            str(paths.flow_md),
            str(paths.contexts_dir),
        ],
        "notes": [
            "本轮没有自动修改项目代码。",
            "本轮没有执行 git reset --hard。",
            "后续可在用户授权后把 Execute 扩展为可控修改与评测。",
        ],
    }
    _write_json(paths.conclude_result_json, conclude_result)
    _write_json(paths.run_dir / "conclude_result.json", conclude_result)
    _append_text(
        paths.lessons_md,
        (
            f"\n## {paths.run_dir.name} - {_now_ts()}\n\n"
            f"- 目标：{plan.get('objective', '')}\n"
            f"- 决策：{decision}\n"
            f"- 状态：{status}\n"
            f"- 失败命令数：{len(failed)}\n"
            f"- 日志目录：`{paths.run_dir}`\n"
        ),
    )
    _append_text(
        paths.results_tsv,
        f"{paths.run_dir.name}\t{_now_ts()}\t{decision}\t{status}\t{len(failed)}\t{paths.run_dir}\n",
    )
    tracer.snapshot_context("Conclude", {"plan": plan, "execute_result": execute_result, "conclude_result": conclude_result}, label="after_conclude")
    tracer.emit("Conclude", "finish", "总结阶段完成", {"decision": decision, "status": status, "failed_commands": len(failed)})
    _update_state(paths, phase="completed", decision=decision, status=status, finished_at=_now_ts())
    return conclude_result


def run_autoresearch_cycle(
    project_path: str | os.PathLike[str],
    objective: str = "最小 autoresearch 闭环",
    cancel_event=None,
    on_status: Callable[[str], None] | None = None,
) -> dict:
    """运行第一版最小 autoresearch 闭环：Plan → Execute → Conclude。"""
    paths = build_paths(project_path)
    try:
        if on_status:
            on_status("[bold cyan]🚀 Autoresearch：正在初始化 .autoresearch 状态目录...[/bold cyan]")
        init_state(paths, objective=objective)
        tracer = AutoresearchTracer(paths)
        tracer.emit("Main", "init", "状态目录初始化完成", {"state_dir": str(paths.state_dir), "run_dir": str(paths.run_dir)})
        tracer.snapshot_context("Main", {"objective": objective, "project_root": str(paths.project_root), "run_id": paths.run_dir.name}, label="initialized")
        plan = plan_worker(paths, objective=objective, cancel_event=cancel_event, on_status=on_status, tracer=tracer)
        execute_result = execute_worker(paths, plan=plan, cancel_event=cancel_event, on_status=on_status, tracer=tracer)
        conclude_result = conclude_worker(paths, plan=plan, execute_result=execute_result, cancel_event=cancel_event, on_status=on_status, tracer=tracer)
        return {
            "success": conclude_result.get("decision") == "keep",
            "project_root": str(paths.project_root),
            "state_dir": str(paths.state_dir),
            "run_dir": str(paths.run_dir),
            "plan": plan,
            "execute_result": execute_result,
            "conclude_result": conclude_result,
            "trace": {
                "trace_jsonl": str(paths.trace_jsonl),
                "flow_md": str(paths.flow_md),
                "contexts_dir": str(paths.contexts_dir),
                "run_trace_jsonl": str(paths.run_dir / "trace.jsonl"),
                "run_flow_md": str(paths.run_dir / "flow.md"),
            },
        }
    except AutoresearchInterrupted:
        _update_state(paths, phase="interrupted", interrupted=True, finished_at=_now_ts())
        raise


__all__ = [
    "AutoresearchInterrupted",
    "AutoresearchPaths",
    "AutoresearchTracer",
    "build_paths",
    "init_state",
    "plan_worker",
    "execute_worker",
    "conclude_worker",
    "run_autoresearch_cycle",
]
