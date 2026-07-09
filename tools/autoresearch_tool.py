import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from core.autoresearch_loop import AutoResearchLoop, AutoResearchSettings, normalize_versioning_policy
from core.autoresearch_preflight import git_preflight
from tools.registry import registry


def _make_settings(
    project_dir: str,
    project_id: str = "autoresearch",
    rounds: int = 100,
    program_path: str = "program.md",
    context_char_budget: int = 24000,
    program_char_budget: int = 12000,
    summary_char_budget: int = 6000,
    bucket_item_char_budget: int = 900,
    bucket_max_items: int = 3,
    command_timeout_seconds: int = 300,
    use_llm_step_agents: bool = True,
    llm_model: str = "",
    max_experiments: int = 40,
    max_active_context_chars: int = 8000,
    max_pareto_items: int = 8,
    max_useful_failures: int = 3,
    use_git_versioning: bool = True,
    versioning_policy: str = "artifact_only",
    planner: str = "evolutionary",
    trace_rounds: bool = True,
) -> AutoResearchSettings:
    return AutoResearchSettings(
        project_dir=project_dir,
        project_id=project_id,
        program_path=program_path,
        max_rounds=rounds,
        context_char_budget=context_char_budget,
        program_char_budget=program_char_budget,
        summary_char_budget=summary_char_budget,
        bucket_item_char_budget=bucket_item_char_budget,
        bucket_max_items=bucket_max_items,
        command_timeout_seconds=command_timeout_seconds,
        use_llm_step_agents=use_llm_step_agents,
        llm_model=llm_model or None,
        max_experiments=max_experiments,
        max_active_context_chars=max_active_context_chars,
        max_pareto_items=max_pareto_items,
        max_useful_failures=max_useful_failures,
        use_git_versioning=use_git_versioning,
        versioning_policy=versioning_policy,
        planner_kind=planner,
        trace_rounds=trace_rounds,
    )


def _settings_payload(settings: AutoResearchSettings, progress_path: str) -> dict:
    return {
        "project_dir": str(settings.root()),
        "project_id": settings.project_id,
        "program_path": str(settings.program_path),
        "state_path": str(settings.state_path),
        "artifact_dir": str(settings.artifact_dir),
        "context_char_budget": settings.context_char_budget,
        "program_char_budget": settings.program_char_budget,
        "summary_char_budget": settings.summary_char_budget,
        "recent_observation_limit": settings.recent_observation_limit,
        "command_timeout_seconds": settings.command_timeout_seconds,
        "max_rounds": settings.max_rounds,
        "trial_rationale": settings.trial_rationale,
        "allowed_file_write_roots": list(settings.allowed_file_write_roots),
        "bucket_item_char_budget": settings.bucket_item_char_budget,
        "bucket_max_items": settings.bucket_max_items,
        "workflow": settings.workflow,
        "use_llm_step_agents": settings.use_llm_step_agents,
        "llm_model": settings.llm_model,
        "llm_temperature": settings.llm_temperature,
        "progress_path": progress_path,
        "auto_commit": settings.auto_commit,
        "max_experiments": settings.max_experiments,
        "max_active_context_chars": settings.max_active_context_chars,
        "max_pareto_items": settings.max_pareto_items,
        "max_useful_failures": settings.max_useful_failures,
        "use_git_versioning": settings.use_git_versioning,
        "versioning_policy": normalize_versioning_policy(settings.versioning_policy),
        "planner_kind": settings.planner_kind,
        "trace_rounds": settings.trace_rounds,
    }


_CHILD_CODE = r'''
import json
import sys
import time
from pathlib import Path
from core.autoresearch_loop import AutoResearchLoop, AutoResearchSettings

settings_data = json.loads(sys.argv[1])
run_id = sys.argv[2]
status_path = Path(sys.argv[3])
status = {
    "success": True,
    "background": True,
    "run_id": run_id,
    "status": "running",
    "started_at": time.time(),
    "project_dir": settings_data["project_dir"],
    "project_id": settings_data["project_id"],
    "progress_path": settings_data["progress_path"],
    "status_path": str(status_path),
    "versioning_policy": settings_data.get("versioning_policy", "artifact_only"),
}
status_path.parent.mkdir(parents=True, exist_ok=True)
status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
try:
    settings = AutoResearchSettings(**{k: v for k, v in settings_data.items() if k != "progress_path"})
    result = AutoResearchLoop(settings).run()
    status.update({"status": "completed", "finished_at": time.time(), "result": result})
except Exception as exc:
    status.update({"status": "failed", "finished_at": time.time(), "error": str(exc)})
status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
'''


def auto_research_run_tool(
    project_dir: str,
    project_id: str = "autoresearch",
    rounds: int = 100,
    program_path: str = "program.md",
    context_char_budget: int = 24000,
    program_char_budget: int = 12000,
    summary_char_budget: int = 6000,
    bucket_item_char_budget: int = 900,
    bucket_max_items: int = 3,
    command_timeout_seconds: int = 300,
    use_llm_step_agents: bool = True,
    llm_model: str = "",
    max_experiments: int = 40,
    max_active_context_chars: int = 8000,
    max_pareto_items: int = 8,
    max_useful_failures: int = 3,
    use_git_versioning: bool = True,
    versioning_policy: str = "artifact_only",
    planner: str = "evolutionary",
    background: bool = False,
    trace_rounds: bool = True,
) -> str:
    """Run the dedicated autoresearch loop for a project."""
    try:
        settings = _make_settings(
            project_dir=project_dir,
            project_id=project_id,
            rounds=rounds,
            program_path=program_path,
            context_char_budget=context_char_budget,
            program_char_budget=program_char_budget,
            summary_char_budget=summary_char_budget,
            bucket_item_char_budget=bucket_item_char_budget,
            bucket_max_items=bucket_max_items,
            command_timeout_seconds=command_timeout_seconds,
            use_llm_step_agents=use_llm_step_agents,
            llm_model=llm_model,
            max_experiments=max_experiments,
            max_active_context_chars=max_active_context_chars,
            max_pareto_items=max_pareto_items,
            max_useful_failures=max_useful_failures,
            use_git_versioning=use_git_versioning,
            versioning_policy=versioning_policy,
            planner=planner,
            trace_rounds=trace_rounds,
        )
        if background:
            run_id = f"ar-{uuid.uuid4().hex[:10]}"
            progress_path = str(settings.progress_file())
            status_path = str(settings.root() / ".autoresearch" / f"run_{run_id}.json")
            payload = {
                "success": True,
                "background": True,
                "run_id": run_id,
                "status": "queued",
                "project_dir": str(settings.root()),
                "project_id": project_id,
                "progress_path": progress_path,
                "status_path": status_path,
                "created_at": time.time(),
                "versioning_policy": normalize_versioning_policy(settings.versioning_policy),
            }
            Path(status_path).parent.mkdir(parents=True, exist_ok=True)
            Path(status_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            subprocess.Popen(
                [sys.executable, "-c", _CHILD_CODE, json.dumps(_settings_payload(settings, progress_path), ensure_ascii=False), run_id, status_path],
                cwd=str(Path.cwd()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return json.dumps(payload, ensure_ascii=False, indent=2)
        result = AutoResearchLoop(settings).run()
        return json.dumps({"success": True, "background": False, **result}, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def auto_research_status_tool(run_id: str = "", project_dir: str = ".") -> str:
    """Return background autoresearch status and text progress preview."""
    try:
        root = Path(project_dir).expanduser().resolve()
        record = None
        if run_id:
            status_path = root / ".autoresearch" / f"run_{run_id}.json"
            if status_path.exists():
                record = json.loads(status_path.read_text(encoding="utf-8"))
        if record is None:
            progress_path = str(root / ".autoresearch" / "progress.md")
            record = {"status": "unknown", "progress_path": progress_path, "project_dir": str(root)}
        progress_path = record.get("progress_path") or str(root / ".autoresearch" / "progress.md")
        preview = ""
        p = Path(progress_path)
        if p.exists():
            preview = p.read_text(encoding="utf-8")[:4000]
        payload = {"success": True, **record, "progress_preview": preview}
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


_V2_CHILD_CODE = r'''
import json
import sys
import time
from pathlib import Path
from core.autoresearch_loop import AutoResearchSettings
from core.autoresearch_phases import run_phase_loop

payload = json.loads(sys.argv[1])
run_id = sys.argv[2]
max_steps = int(sys.argv[3])
settings = AutoResearchSettings(**payload)
try:
    run_phase_loop(settings, max_steps=max_steps, run_id=run_id)
except Exception as exc:
    # run_phase_loop's monitor finally-block usually records failure; overwrite
    # the monitor too, because the queued seed file may already exist when
    # construction/import fails before the loop starts.
    mon = Path(settings.monitor_file())
    mon.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if mon.exists():
        try:
            data = json.loads(mon.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update({"run_id": run_id, "status": "failed", "error": str(exc), "finished_at": time.time()})
    mon.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
'''


def _v2_settings_kwargs(
    project_dir, project_id, program_path, project_state_path, use_llm_step_agents,
    llm_model, max_usd, max_tokens, model_tier_plan, model_tier_exec, model_tier_util,
    max_experiments, max_pareto_items, max_useful_failures, use_git_versioning,
    versioning_policy, plateau_patience, trace_rounds=False, debug_mode=False,
    solved_metric_threshold=None, llm_request_timeout=300.0, plan_max_personas=2,
    plan_degrade_personas=1, plan_max_implementation_tasks=0, execute_context_chars=24000,
    execute_max_task_attempts=3, execute_behavior_check=True,
    execute_behavior_check_timeout_seconds=300,
) -> dict:
    return dict(
        project_dir=project_dir,
        project_id=project_id,
        program_path=program_path,
        project_state_path=project_state_path,
        max_rounds=0,
        use_llm_step_agents=use_llm_step_agents,
        llm_model=llm_model or None,
        max_usd=max_usd,
        max_tokens=max_tokens,
        model_tier_plan=model_tier_plan,
        model_tier_exec=model_tier_exec,
        model_tier_util=model_tier_util,
        max_experiments=max_experiments,
        max_pareto_items=max_pareto_items,
        max_useful_failures=max_useful_failures,
        use_git_versioning=use_git_versioning,
        versioning_policy=versioning_policy,
        plateau_patience=plateau_patience,
        trace_rounds=trace_rounds,
        debug_mode=debug_mode,
        solved_metric_threshold=solved_metric_threshold,
        llm_request_timeout=llm_request_timeout,
        plan_max_personas=plan_max_personas,
        plan_degrade_personas=plan_degrade_personas,
        plan_max_implementation_tasks=plan_max_implementation_tasks,
        execute_context_chars=execute_context_chars,
        execute_max_task_attempts=execute_max_task_attempts,
        execute_behavior_check=execute_behavior_check,
        execute_behavior_check_timeout_seconds=execute_behavior_check_timeout_seconds,
    )


def _v2_state_snapshot(settings) -> dict:
    """Read experiments/best/pareto from state.json (pure file read)."""
    snap = {"experiments_recorded": 0, "best_experiment": None, "pareto_size": 0}
    try:
        state_path = settings.root() / ".autoresearch" / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            snap["experiments_recorded"] = len(state.get("experiments") or [])
            snap["best_experiment"] = state.get("best_experiment")
            snap["pareto_size"] = len(state.get("pareto_front") or [])
    except Exception:
        pass
    return snap


_TERMINAL_STATUSES = {"completed", "paused", "failed"}


def _wait_for_v2_completion(settings, run_id: str, wait_seconds: float, poll_interval: float = 2.0) -> dict:
    """Poll the monitor heartbeat until terminal or the bounded wait elapses.

    Returns the last monitor snapshot read. Never blocks longer than
    wait_seconds so the foreground tool call cannot be killed by the harness
    wall-clock timeout; the child subprocess keeps running regardless.
    """
    from core.autoresearch_monitor import read_monitor

    monitor_path = settings.monitor_file()
    deadline = time.time() + max(0.0, float(wait_seconds))
    data = read_monitor(monitor_path)
    while time.time() < deadline:
        data = read_monitor(monitor_path)
        if data.get("run_id") == run_id and data.get("status") in _TERMINAL_STATUSES:
            break
        time.sleep(max(0.2, float(poll_interval)))
    return data


def auto_research_run_v2_tool(
    project_dir: str,
    project_id: str = "autoresearch",
    max_steps: int = 100,
    program_path: str = "program.md",
    project_state_path: str = "project.md",
    use_llm_step_agents: bool = True,
    llm_model: str = "",
    max_usd: float = 0.0,
    max_tokens: int = 0,
    model_tier_plan: str = "",
    model_tier_exec: str = "",
    model_tier_util: str = "",
    max_experiments: int = 40,
    max_pareto_items: int = 8,
    max_useful_failures: int = 3,
    use_git_versioning: bool = True,
    versioning_policy: str = "artifact_only",
    plateau_patience: int = 3,
    background: bool = True,
    trace_rounds: bool = True,
    debug_mode: bool = False,
    solved_metric_threshold: Optional[float] = None,
    llm_request_timeout: float = 300.0,
    plan_max_personas: int = 2,
    plan_degrade_personas: int = 1,
    plan_max_implementation_tasks: int = 0,
    execute_context_chars: int = 24000,
    execute_max_task_attempts: int = 3,
    execute_behavior_check: bool = True,
    execute_behavior_check_timeout_seconds: int = 300,
    wait_seconds: float = 180.0,
    detach: bool = False,
) -> str:
    """Run the v2 phase-machine autoresearch loop (init/plan/execute/run/evaluate/compress).

    Runs the loop in a detached subprocess (survives long LLM phases) but, by
    default, the CALL BLOCKS and polls the heartbeat for up to ``wait_seconds``
    before returning, so the caller does not mistake a just-started run for a
    finished one:

    - if the loop reaches a terminal state (completed/paused/failed) within
      ``wait_seconds``, the tool returns ``completed=true`` with the real final
      results (experiments_recorded, best_experiment, budget);
    - if it is still running when ``wait_seconds`` elapses, the tool returns
      ``completed=false`` with ``status="running"`` and an explicit instruction
      to keep polling ``auto_research_v2_status`` — it MUST NOT be treated as done.

    ``wait_seconds`` is kept safely under a typical tool wall-clock timeout.
    Pass ``detach=true`` to return immediately after launch (fire-and-forget;
    still returns ``completed=false``). Pass ``background=false`` to run fully
    synchronously in-process (short deterministic runs/tests only).
    """
    try:
        from core.autoresearch_phases import run_phase_loop

        kwargs = _v2_settings_kwargs(
            project_dir, project_id, program_path, project_state_path, use_llm_step_agents,
            llm_model, max_usd, max_tokens, model_tier_plan, model_tier_exec, model_tier_util,
            max_experiments, max_pareto_items, max_useful_failures, use_git_versioning,
            versioning_policy, plateau_patience, trace_rounds, debug_mode, solved_metric_threshold,
            llm_request_timeout, plan_max_personas, plan_degrade_personas,
            plan_max_implementation_tasks, execute_context_chars, execute_max_task_attempts,
            execute_behavior_check, execute_behavior_check_timeout_seconds,
        )
        settings = AutoResearchSettings(**kwargs)
        preflight = git_preflight(settings.root()) if use_git_versioning else {"warnings": ["git versioning disabled"]}
        if debug_mode:
            from core.autoresearch_debug import set_debug

            set_debug(settings.root(), True)
        else:
            # A stale STOP from the previous interrupted run should not make the
            # next launch immediately pause. The user's explicit stop/kill still
            # wins for the currently running process.
            settings.stop_file().unlink(missing_ok=True)

        if background:
            from core.autoresearch_monitor import RunMonitor, read_monitor, render_monitor_text

            run_id = f"arv2-{uuid.uuid4().hex[:10]}"
            # Seed a queued monitor file synchronously so a watcher can find it
            # immediately, before the child process has started running.
            RunMonitor(settings.monitor_file(), run_id=run_id, project_id=project_id)
            # Serialize settings without the derived normalization fields the
            # dataclass sets in __post_init__ (they are accepted kwargs too).
            child_payload = json.dumps(kwargs, ensure_ascii=False)
            subprocess.Popen(
                [sys.executable, "-c", _V2_CHILD_CODE, child_payload, run_id, str(max_steps)],
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            base = {
                "success": True,
                "background": True,
                "run_id": run_id,
                "project_dir": str(settings.root()),
                "project_id": project_id,
                "monitor_path": str(settings.monitor_file()),
                "budget_path": str(settings.budget_file()),
                "project_path": str(settings.project_state_file()),
                "created_at": time.time(),
                "preflight": preflight,
            }

            # Fire-and-forget: return immediately, but still mark completed=false
            # so the caller knows the loop only just started.
            if detach:
                base.update({
                    "completed": False,
                    "status": "queued",
                    "note": ("autoresearch v2 launched in background (detach=true). It has NOT finished. "
                             "Do NOT treat this as an optimization result. Poll auto_research_v2_status "
                             f"(project_dir={project_dir!r}) until status is completed/paused/failed."),
                })
                return json.dumps(base, ensure_ascii=False, indent=2)

            # Bounded wait: block up to wait_seconds, polling the heartbeat, so a
            # just-started run is never mistaken for a finished one.
            data = _wait_for_v2_completion(settings, run_id, wait_seconds)
            status = data.get("status", "unknown")
            terminal = status in _TERMINAL_STATUSES
            snap = _v2_state_snapshot(settings)
            base.update({
                "completed": bool(terminal),
                "status": status,
                "step_index": data.get("step_index", 0),
                "max_steps": max_steps,
                "current_phase": data.get("current_phase", ""),
                "budget": data.get("budget", {}),
                "monitor_text": render_monitor_text(data) if status != "unknown" else "",
                "experiments_recorded": snap["experiments_recorded"],
                "best_experiment": snap["best_experiment"],
                "pareto_size": snap["pareto_size"],
                "waited_seconds": wait_seconds,
            })
            if not terminal:
                base["note"] = (
                    f"autoresearch v2 is STILL RUNNING after waiting {wait_seconds:.0f}s "
                    f"(status={status}, step {data.get('step_index',0)}/{max_steps}). "
                    "It has NOT finished — do NOT treat this as a final optimization result and do NOT "
                    "proceed as if the task is solved. The loop keeps running in its own process; poll "
                    f"auto_research_v2_status (project_dir={project_dir!r}) until status is "
                    "completed/paused/failed, or auto_research_stop to end it."
                )
            else:
                base["note"] = (
                    f"autoresearch v2 finished with status={status}, "
                    f"{snap['experiments_recorded']} experiment(s) recorded."
                )
            return json.dumps(base, ensure_ascii=False, indent=2)

        result = run_phase_loop(settings, max_steps=max_steps)
        return json.dumps({"success": True, "background": False, "completed": True, "preflight": preflight, **result}, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def auto_research_v2_status_tool(project_dir: str = ".", monitor_path: str = "") -> str:
    """Read the v2 run monitor heartbeat (rounds + token/usd + phase). Pure file read, no LLM."""
    try:
        from core.autoresearch_monitor import read_monitor, render_monitor_text

        if monitor_path:
            path = Path(monitor_path).expanduser()
        else:
            path = Path(project_dir).expanduser().resolve() / ".autoresearch" / "monitor.json"
        data = read_monitor(path)
        data["monitor_text"] = render_monitor_text(data) if data.get("status") not in {"unknown"} else ""
        return json.dumps({"success": True, "monitor_path": str(path), **data}, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def auto_research_stop_tool(project_dir: str = ".", resume: bool = False) -> str:
    """Request a graceful stop (or clear it) for an autoresearch run.

    Drops a STOP sentinel at <project_dir>/.autoresearch/STOP. Both the legacy
    loop and the v2 phase machine check it at each round/phase boundary and exit
    cleanly (all prior rounds are already persisted). Pass resume=true to remove
    the sentinel before starting a new run. Pure file IO, no LLM.
    """
    try:
        root = Path(project_dir).expanduser().resolve()
        stop_path = root / ".autoresearch" / "STOP"
        if resume:
            existed = stop_path.exists()
            if existed:
                stop_path.unlink()
            return json.dumps({"success": True, "action": "resume", "removed": existed, "stop_path": str(stop_path)}, ensure_ascii=False)
        stop_path.parent.mkdir(parents=True, exist_ok=True)
        stop_path.write_text(f"stop requested at {time.time()}\n", encoding="utf-8")
        return json.dumps({"success": True, "action": "stop", "stop_path": str(stop_path),
                           "note": "loop will exit cleanly at the next round/phase boundary"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


def _common_properties():
    return {
        "project_dir": {"type": "string", "description": "要运行 autoresearch 的项目目录，必须在当前工作区内。"},
        "project_id": {"type": "string", "description": "项目 ID，会写入 artifact 文件名。", "default": "autoresearch"},
        "rounds": {"type": "integer", "description": "最多执行多少个 workflow step；默认 100（激进，配合 evolutionary 多轮改进）。evolutionary 会在完成一遍固定 workflow 后循环 propose/apply/run/decide 直至 max_experiments 或 rounds 用尽；可随时用 auto_research_stop 或 esc 中断。", "default": 100},
        "program_path": {"type": "string", "description": "相对 project_dir 的 program.md 路径。", "default": "program.md"},
        "context_char_budget": {"type": "integer", "description": "父上下文总字符预算。", "default": 24000},
        "program_char_budget": {"type": "integer", "description": "program.md 注入字符预算。", "default": 12000},
        "summary_char_budget": {"type": "integer", "description": "状态摘要字符预算。", "default": 6000},
        "bucket_item_char_budget": {"type": "integer", "description": "每个模块化上下文条目的字符预算。", "default": 900},
        "bucket_max_items": {"type": "integer", "description": "每个模块化上下文 bucket 最多保留条目数。", "default": 3},
        "command_timeout_seconds": {"type": "integer", "description": "单个项目内命令超时时间。", "default": 300},
        "use_llm_step_agents": {"type": "boolean", "description": "是否启用每个 workflow step 的 LLM 子 Agent（默认 True，真正思考/写搜索脚本）；失败会降级 deterministic fallback。", "default": True},
        "llm_model": {"type": "string", "description": "LLM step agent 使用的模型；为空则使用默认模型。", "default": ""},
        "max_experiments": {"type": "integer", "description": "最多记录/执行多少个实际 trial/run_experiment 轮次；默认 40（激进）。", "default": 40},
        "max_active_context_chars": {"type": "integer", "description": "active_context.md 压缩上下文字符预算。", "default": 8000},
        "max_pareto_items": {"type": "integer", "description": "pareto_front.json 最多保留候选数。", "default": 8},
        "max_useful_failures": {"type": "integer", "description": "active context/state 中保留的失败/无用轮次摘要数。", "default": 3},
        "use_git_versioning": {"type": "boolean", "description": "在已有 git 仓库中记录 base commit/status/diff；非 git 安全降级且不会 git init。", "default": True},
        "versioning_policy": {"type": "string", "description": "中间版本生命周期策略：artifact_only(默认，仅保存 patch/manifest)、commit_pareto、commit_all_trials、branch_per_trial。", "enum": ["artifact_only", "commit_pareto", "commit_all_trials", "branch_per_trial"], "default": "artifact_only"},
        "planner": {"type": "string", "description": "workflow planner：evolutionary(默认，跑完固定 workflow 后按 propose/apply/run/decide 循环并跨轮改进搜索脚本，直到 max_experiments 或 rounds 用尽)；fixed(只跑一遍固定 10 步)。", "enum": ["fixed", "evolutionary"], "default": "evolutionary"},
        "trace_rounds": {"type": "boolean", "description": "是否把每轮完整上下文(parent_context+system/user prompt+LLM 原始返回+选中动作+观察)dump 到 .autoresearch/round_traces/round_NNN_*.json 供事后排查；默认开启。", "default": True},
        "background": {"type": "boolean", "description": "是否后台非阻塞运行；true 时立即返回 run_id 和 progress_path。", "default": False},
    }


registry.register(
    name="auto_research_run",
    description=(
        "运行专用于 autoresearch 的轻量 Agent Loop。可 background=true 后台非阻塞运行；"
        "内部按固定 workflow 分步读取 program.md、检查项目、规划修改、运行可用 eval/train、总结结论，"
        "并用模块化上下文 buckets 控制父上下文长度；可选启用每 step 的 LLM 子 Agent 生成结构化 action。"
    ),
    parameters={"type": "object", "properties": _common_properties(), "required": ["project_dir"]},
    handler=auto_research_run_tool,
)

registry.register(
    name="auto_research_status",
    description="查询后台 auto_research 运行状态，并返回 progress.md 的文字可视化预览。",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "auto_research_run(background=true) 返回的 run_id；为空则查最近一次。", "default": ""},
            "project_dir": {"type": "string", "description": "项目目录；当 run_id 不在当前进程内存中时用于读取 .autoresearch/progress.md。", "default": "."},
        },
    },
    handler=auto_research_status_tool,
)


def _v2_properties():
    return {
        "project_dir": {"type": "string", "description": "要运行 autoresearch v2 的项目目录，必须在当前工作区内。"},
        "project_id": {"type": "string", "description": "项目 ID。", "default": "autoresearch"},
        "max_steps": {"type": "integer", "description": "相位机最多推进多少个相位（init/plan/execute/run/evaluate/compress 循环）；默认 100（激进）。可随时用 auto_research_stop 或 esc 中断。", "default": 100},
        "program_path": {"type": "string", "description": "program.md 路径；含 CONSTITUTION(L0,只读)/BELIEF(L1,可演化) 标记。", "default": "program.md"},
        "project_state_path": {"type": "string", "description": "project.md 路径（L2 项目态，父进程单写，含 phase 标记）。", "default": "project.md"},
        "use_llm_step_agents": {"type": "boolean", "description": "是否启用 LLM（多性格 Plan 等），默认 True；关闭则走 deterministic handlers。", "default": True},
        "llm_model": {"type": "string", "description": "基础模型；为空用默认。", "default": ""},
        "max_usd": {"type": "number", "description": "预算硬上限(USD)，0=无限。触顶暂停并通知用户。", "default": 0.0},
        "max_tokens": {"type": "integer", "description": "预算硬上限(tokens)，0=无限。", "default": 0},
        "model_tier_plan": {"type": "string", "description": "Plan 相位(辩论/结论)使用的强模型；为空回落基础模型。", "default": ""},
        "model_tier_exec": {"type": "string", "description": "Execute/Run 相位使用的模型。", "default": ""},
        "model_tier_util": {"type": "string", "description": "Init/监控/压缩等使用的便宜模型。", "default": ""},
        "max_experiments": {"type": "integer", "description": "最多记录多少个 trial；默认 40（激进）。", "default": 40},
        "max_pareto_items": {"type": "integer", "description": "Pareto 候选上限。", "default": 8},
        "max_useful_failures": {"type": "integer", "description": "保留的失败摘要数。", "default": 3},
        "use_git_versioning": {"type": "boolean", "description": "已有 git 仓库中记录版本；非 git 安全降级。", "default": True},
        "versioning_policy": {"type": "string", "description": "中间版本策略。", "enum": ["artifact_only", "commit_pareto", "commit_all_trials", "branch_per_trial"], "default": "artifact_only"},
        "plateau_patience": {"type": "integer", "description": "连续多少轮 Pareto 无改进后触发重规划(收敛信号 K)。默认不会因为 plateau 暂停；autoresearch 会持续寻找更好解，除非预算耗尽、显式 solved 或用户停止。", "default": 3},
        "trace_rounds": {"type": "boolean", "description": "是否把每轮完整上下文(parent_context+system/user prompt+LLM 原始返回+选中动作+观察)dump 到 .autoresearch/round_traces/round_NNN_*.json 供事后排查；默认开启。", "default": True},
        "debug_mode": {"type": "boolean", "description": "是否开启 debug 模式：写 .autoresearch/debug/debug.jsonl 和 inflight.json，显示当前卡在 LLM/shell/phase 的哪一步。可删除 .autoresearch/DEBUG 或用 /autoresearch debug off 关闭。", "default": False},
        "solved_metric_threshold": {"type": "number", "description": "可选显式 solved 阈值。越小越好时 metric<=threshold 停止；越大越好时 metric>=threshold 停止。默认不设置，框架不会因固定阈值自动停止，需用户/预算/plateau 控制。", "default": None},
        "llm_request_timeout": {"type": "number", "description": "单次 LLM 请求超时秒数。默认 300s，让 Execute 子进程有足够时间读写文件；超时会记录到 debug/inflight，并由任务 attempt 计数决定是否重试或 replan。", "default": 300.0},
        "plan_max_personas": {"type": "integer", "description": "Plan 阶段最多使用多少个非 leader persona；降低可减少慢模型调用。", "default": 2},
        "plan_degrade_personas": {"type": "integer", "description": "预算降级时 Plan 阶段使用多少个非 leader persona。", "default": 1},
        "plan_max_implementation_tasks": {"type": "integer", "description": "把自然语言计划中的实现细节合并成最多多少个 Execute 任务；0=不强制合并，保留 DAG 粒度。", "default": 0},
        "execute_context_chars": {"type": "integer", "description": "Execute LLM parent_context 字符上限；默认 24000，保留上次失败、artifact 和文件上下文以便继续执行。", "default": 24000},
        "execute_max_task_attempts": {"type": "integer", "description": "同一个 Execute 任务最多允许多少次未验证尝试；默认 3，三次后才标记 failed 并进入 Evaluate/Replan。", "default": 3},
        "execute_behavior_check": {"type": "boolean", "description": "Execute 写入后是否运行一次训练侧入口进行行为 smoke check，并把 submission/metrics/train_verification 摘要和 artifact 写回 last_result；不调用最终 eval。", "default": True},
        "execute_behavior_check_timeout_seconds": {"type": "integer", "description": "Execute 行为 smoke check 的单次命令超时秒数；默认 300。", "default": 300},
        "background": {"type": "boolean", "description": "是否在独立子进程运行(存活于慢 LLM 相位)；默认 True。注意：调用默认仍会阻塞并轮询心跳最多 wait_seconds 秒后才返回，返回体带 completed 布尔标志——completed=false 表示仍在运行，禁止当作优化完成，必须继续用 auto_research_v2_status 轮询。", "default": True},
        "wait_seconds": {"type": "number", "description": "background=true 时，调用阻塞轮询心跳的最长秒数(默认 180，安全低于工具超时)。期间跑完则返回真实最终结果(experiments/best/budget)且 completed=true；超时未完成则返回 completed=false 且 status=running，需继续轮询。", "default": 180.0},
        "detach": {"type": "boolean", "description": "是否发射后不管：true 时启动子进程后立即返回(completed=false, status=queued)，不阻塞等待。默认 False(即有界等待)。", "default": False},
    }


registry.register(
    name="auto_research_run_v2",
    description=(
        "运行 autoresearch v2 相位状态机：init→plan(多性格辩论)→execute(Todo+验证)→run(事件驱动+有界autofix)"
        "→evaluate(Pareto+经验账本)→compress，成本可控(预算账本+模型分级)且可无限运行(状态全在 program.md/project.md/.auto/git)。"
        "background=true 可脱离主 agent 独立子进程运行，不阻塞主进程。"
    ),
    parameters={"type": "object", "properties": _v2_properties(), "required": ["project_dir"]},
    handler=auto_research_run_v2_tool,
)


registry.register(
    name="auto_research_v2_status",
    description=(
        "查询 v2 后台运行的进度与花费：迭代轮数(step_index)、当前/下一相位、token 与 USD 消耗、状态与心跳。"
        "纯读取 .autoresearch/monitor.json，不调用任何 LLM。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "project_dir": {"type": "string", "description": "项目目录；从中读取 .autoresearch/monitor.json。", "default": "."},
            "monitor_path": {"type": "string", "description": "直接指定 monitor.json 路径（可选，优先于 project_dir）。", "default": ""},
        },
    },
    handler=auto_research_v2_status_tool,
)

registry.register(
    name="auto_research_stop",
    description=(
        "优雅中断正在运行的 autoresearch（legacy 或 v2）：在 <project_dir>/.autoresearch/STOP 放置停止标记，"
        "loop 会在下一个 round/phase 边界干净退出（此前每轮状态均已持久化）。resume=true 则清除标记以便重新开始。"
        "纯文件操作，不调用 LLM。等价于 esc 主动退出。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "project_dir": {"type": "string", "description": "运行 autoresearch 的项目目录。", "default": "."},
            "resume": {"type": "boolean", "description": "true=移除 STOP 标记（准备重新运行）；false=请求停止。", "default": False},
        },
        "required": ["project_dir"],
    },
    handler=auto_research_stop_tool,
)
