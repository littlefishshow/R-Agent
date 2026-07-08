import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from core.autoresearch_loop import AutoResearchLoop, AutoResearchSettings, normalize_versioning_policy
from tools.registry import registry


def _make_settings(
    project_dir: str,
    project_id: str = "autoresearch",
    rounds: int = 10,
    program_path: str = "program.md",
    context_char_budget: int = 24000,
    program_char_budget: int = 12000,
    summary_char_budget: int = 6000,
    bucket_item_char_budget: int = 900,
    bucket_max_items: int = 3,
    command_timeout_seconds: int = 300,
    use_llm_step_agents: bool = False,
    llm_model: str = "",
    max_experiments: int = 4,
    max_active_context_chars: int = 8000,
    max_pareto_items: int = 8,
    max_useful_failures: int = 3,
    use_git_versioning: bool = True,
    versioning_policy: str = "artifact_only",
    planner: str = "fixed",
    trace_rounds: bool = False,
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
    rounds: int = 10,
    program_path: str = "program.md",
    context_char_budget: int = 24000,
    program_char_budget: int = 12000,
    summary_char_budget: int = 6000,
    bucket_item_char_budget: int = 900,
    bucket_max_items: int = 3,
    command_timeout_seconds: int = 300,
    use_llm_step_agents: bool = False,
    llm_model: str = "",
    max_experiments: int = 4,
    max_active_context_chars: int = 8000,
    max_pareto_items: int = 8,
    max_useful_failures: int = 3,
    use_git_versioning: bool = True,
    versioning_policy: str = "artifact_only",
    planner: str = "fixed",
    background: bool = False,
    trace_rounds: bool = False,
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
    # run_phase_loop's monitor finally-block already records failure; make sure
    # a monitor file exists even if construction failed before the loop started.
    mon = Path(settings.monitor_file())
    if not mon.exists():
        mon.parent.mkdir(parents=True, exist_ok=True)
        mon.write_text(json.dumps({"run_id": run_id, "status": "failed", "error": str(exc)}) + "\n", encoding="utf-8")
'''


def _v2_settings_kwargs(
    project_dir, project_id, program_path, project_state_path, use_llm_step_agents,
    llm_model, max_usd, max_tokens, model_tier_plan, model_tier_exec, model_tier_util,
    max_experiments, max_pareto_items, max_useful_failures, use_git_versioning,
    versioning_policy, plateau_patience, trace_rounds=False,
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
    )


def auto_research_run_v2_tool(
    project_dir: str,
    project_id: str = "autoresearch",
    max_steps: int = 24,
    program_path: str = "program.md",
    project_state_path: str = "project.md",
    use_llm_step_agents: bool = False,
    llm_model: str = "",
    max_usd: float = 0.0,
    max_tokens: int = 0,
    model_tier_plan: str = "",
    model_tier_exec: str = "",
    model_tier_util: str = "",
    max_experiments: int = 4,
    max_pareto_items: int = 8,
    max_useful_failures: int = 3,
    use_git_versioning: bool = True,
    versioning_policy: str = "artifact_only",
    plateau_patience: int = 3,
    background: bool = False,
    trace_rounds: bool = False,
) -> str:
    """Run the v2 phase-machine autoresearch loop (init/plan/execute/run/evaluate/compress).

    background=true detaches the loop into its own process and returns immediately
    with a run_id + monitor_path; watch progress via auto_research_v2_status
    (pure file read, no LLM).
    """
    try:
        from core.autoresearch_phases import run_phase_loop

        kwargs = _v2_settings_kwargs(
            project_dir, project_id, program_path, project_state_path, use_llm_step_agents,
            llm_model, max_usd, max_tokens, model_tier_plan, model_tier_exec, model_tier_util,
            max_experiments, max_pareto_items, max_useful_failures, use_git_versioning,
            versioning_policy, plateau_patience, trace_rounds,
        )
        settings = AutoResearchSettings(**kwargs)

        if background:
            from core.autoresearch_monitor import RunMonitor

            run_id = f"arv2-{uuid.uuid4().hex[:10]}"
            # Seed a queued monitor file synchronously so a watcher can find it
            # immediately, before the child process has started running.
            RunMonitor(settings.monitor_file(), run_id=run_id, project_id=project_id)
            # Serialize settings without the derived normalization fields the
            # dataclass sets in __post_init__ (they are accepted kwargs too).
            child_payload = json.dumps(kwargs, ensure_ascii=False)
            subprocess.Popen(
                [sys.executable, "-c", _V2_CHILD_CODE, child_payload, run_id, str(max_steps)],
                cwd=str(Path.cwd()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return json.dumps({
                "success": True,
                "background": True,
                "run_id": run_id,
                "project_dir": str(settings.root()),
                "project_id": project_id,
                "monitor_path": str(settings.monitor_file()),
                "budget_path": str(settings.budget_file()),
                "project_path": str(settings.project_state_file()),
                "created_at": time.time(),
            }, ensure_ascii=False, indent=2)

        result = run_phase_loop(settings, max_steps=max_steps)
        return json.dumps({"success": True, "background": False, **result}, ensure_ascii=False, indent=2)
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


def _common_properties():
    return {
        "project_dir": {"type": "string", "description": "要运行 autoresearch 的项目目录，必须在当前工作区内。"},
        "project_id": {"type": "string", "description": "项目 ID，会写入 artifact 文件名。", "default": "autoresearch"},
        "rounds": {"type": "integer", "description": "最多执行多少个 workflow step；fixed 默认 10 步跑完，evolutionary 会在完成一遍后循环 propose/apply/run/decide 直至 max_experiments 或 rounds 用尽。", "default": 10},
        "program_path": {"type": "string", "description": "相对 project_dir 的 program.md 路径。", "default": "program.md"},
        "context_char_budget": {"type": "integer", "description": "父上下文总字符预算。", "default": 24000},
        "program_char_budget": {"type": "integer", "description": "program.md 注入字符预算。", "default": 12000},
        "summary_char_budget": {"type": "integer", "description": "状态摘要字符预算。", "default": 6000},
        "bucket_item_char_budget": {"type": "integer", "description": "每个模块化上下文条目的字符预算。", "default": 900},
        "bucket_max_items": {"type": "integer", "description": "每个模块化上下文 bucket 最多保留条目数。", "default": 3},
        "command_timeout_seconds": {"type": "integer", "description": "单个项目内命令超时时间。", "default": 300},
        "use_llm_step_agents": {"type": "boolean", "description": "是否启用每个固定 workflow step 的 LLM 子 Agent；失败会降级 deterministic fallback。", "default": False},
        "llm_model": {"type": "string", "description": "LLM step agent 使用的模型；为空则使用默认模型。", "default": ""},
        "max_experiments": {"type": "integer", "description": "最多记录/执行多少个实际 trial/run_experiment 轮次。", "default": 4},
        "max_active_context_chars": {"type": "integer", "description": "active_context.md 压缩上下文字符预算。", "default": 8000},
        "max_pareto_items": {"type": "integer", "description": "pareto_front.json 最多保留候选数。", "default": 8},
        "max_useful_failures": {"type": "integer", "description": "active context/state 中保留的失败/无用轮次摘要数。", "default": 3},
        "use_git_versioning": {"type": "boolean", "description": "在已有 git 仓库中记录 base commit/status/diff；非 git 安全降级且不会 git init。", "default": True},
        "versioning_policy": {"type": "string", "description": "中间版本生命周期策略：artifact_only(默认，仅保存 patch/manifest)、commit_pareto、commit_all_trials、branch_per_trial。", "enum": ["artifact_only", "commit_pareto", "commit_all_trials", "branch_per_trial"], "default": "artifact_only"},
        "planner": {"type": "string", "description": "workflow planner：fixed(默认，跑一遍固定 10 步)；evolutionary(跑完固定 workflow 后按 propose/apply/run/decide 循环，直到 max_experiments 或 rounds 用尽)。", "enum": ["fixed", "evolutionary"], "default": "fixed"},
        "trace_rounds": {"type": "boolean", "description": "是否把每轮完整上下文(parent_context+system/user prompt+LLM 原始返回+选中动作+观察)dump 到 .autoresearch/round_traces/round_NNN_*.json 供事后排查；默认关闭(内容较大)。", "default": False},
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
        "max_steps": {"type": "integer", "description": "相位机最多推进多少个相位（init/plan/execute/run/evaluate/compress 循环）。", "default": 24},
        "program_path": {"type": "string", "description": "program.md 路径；含 CONSTITUTION(L0,只读)/BELIEF(L1,可演化) 标记。", "default": "program.md"},
        "project_state_path": {"type": "string", "description": "project.md 路径（L2 项目态，父进程单写，含 phase 标记）。", "default": "project.md"},
        "use_llm_step_agents": {"type": "boolean", "description": "是否启用 LLM（多性格 Plan 等）；关闭则走 deterministic handlers。", "default": False},
        "llm_model": {"type": "string", "description": "基础模型；为空用默认。", "default": ""},
        "max_usd": {"type": "number", "description": "预算硬上限(USD)，0=无限。触顶暂停并通知用户。", "default": 0.0},
        "max_tokens": {"type": "integer", "description": "预算硬上限(tokens)，0=无限。", "default": 0},
        "model_tier_plan": {"type": "string", "description": "Plan 相位(辩论/结论)使用的强模型；为空回落基础模型。", "default": ""},
        "model_tier_exec": {"type": "string", "description": "Execute/Run 相位使用的模型。", "default": ""},
        "model_tier_util": {"type": "string", "description": "Init/监控/压缩等使用的便宜模型。", "default": ""},
        "max_experiments": {"type": "integer", "description": "最多记录多少个 trial。", "default": 4},
        "max_pareto_items": {"type": "integer", "description": "Pareto 候选上限。", "default": 8},
        "max_useful_failures": {"type": "integer", "description": "保留的失败摘要数。", "default": 3},
        "use_git_versioning": {"type": "boolean", "description": "已有 git 仓库中记录版本；非 git 安全降级。", "default": True},
        "versioning_policy": {"type": "string", "description": "中间版本策略。", "enum": ["artifact_only", "commit_pareto", "commit_all_trials", "branch_per_trial"], "default": "artifact_only"},
        "plateau_patience": {"type": "integer", "description": "连续多少轮 Pareto 无改进后触发重规划/暂停(收敛信号 K)。", "default": 3},
        "trace_rounds": {"type": "boolean", "description": "是否把每轮完整上下文(parent_context+system/user prompt+LLM 原始返回+选中动作+观察)dump 到 .autoresearch/round_traces/round_NNN_*.json 供事后排查；默认关闭(内容较大)。", "default": False},
        "background": {"type": "boolean", "description": "是否后台非阻塞运行；true 立即返回 run_id 和 monitor_path，用 auto_research_v2_status 轮询进度/花费(纯文件读，无 LLM)。", "default": False},
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
