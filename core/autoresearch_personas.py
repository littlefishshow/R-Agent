"""AutoResearch v2 — Phase C: multi-persona Plan phase (AUTORESEARCH_DESIGN_v2.md §5).

This is the most expensive phase, so it is bounded on every axis:

- persona count is capped and further reduced when the budget says "degrade";
- each persona gets one bounded turn (scoped context in, one opinion out);
- the leader gets exactly one turn to consolidate into (belief diff, L2 plan,
  .auto/plan.md);
- the full debate transcript goes to an L4 artifact, never into project.md.

The orchestrator accepts an injectable ``chat`` callable so it is fully testable
without a live model.  In the real loop the callable wraps the metered client
resolved at the ``plan`` model tier.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.autoresearch_debug import inflight_finish, inflight_start
from core.autoresearch_memory import update_belief, split_program, write_auto_note
from core.autoresearch_phases import PhaseContext, PhaseResult
from core.autoresearch_timeout import call_with_deadline
from core.autoresearch_todo_state import load_todo_state, merge_todo_state, render_todo_markdown, save_todo_state


# --------------------------------------------------------------------------- #
# Persona definitions
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Persona:
    name: str
    system: str
    # A rough priority: personas are dropped from the tail when the budget is
    # tight, so keep the most load-bearing ones first.
    priority: int = 0


DIVERGENT = Persona(
    name="divergent",
    system=(
        "You are the DIVERGENT researcher. Propose novel, non-obvious ideas to improve "
        "the project. Favor breadth and creativity over feasibility. Return ONLY JSON: "
        '{"opinion": "...", "ideas": ["..."], "risks": ["..."]}'
    ),
    priority=1,
)
PRAGMATIC = Persona(
    name="pragmatic",
    system=(
        "You are the PRAGMATIC planner. Judge ideas by feasibility, cost, and likelihood "
        "of moving the metric. Prune anything speculative. Return ONLY JSON: "
        '{"opinion": "...", "feasible": ["..."], "reject": ["..."]}'
    ),
    priority=2,
)
LEADER = Persona(
    name="leader",
    system=(
        "You are the LEADER. You MUST produce a single decision even if opinions conflict. "
        "Consolidate the personas into one concrete next plan. Prefer a DAG-shaped task plan: "
        "tasks should declare only real dependencies, not every earlier item. A validation/run "
        "checkpoint should depend on the specific implementation it needs, so feedback can happen "
        "as soon as a runnable slice exists. Use python3, never bare python, in all run_spec commands. "
        "Return ONLY JSON: "
        '{"belief": "updated project belief (short, intuition-level)", '
        '"plan": "concrete next plan for project.md (can be coarse)", '
        '"detailed_plan": "step-by-step plan for .auto/plan.md", '
        '"tasks": [{"task_id": "stable id", "goal": "concrete task", '
        '"type": "analysis|implementation|validation|experiment|maintenance", '
        '"depends_on": ["task_id"], "run_spec": {"mode": "single", "commands": ["bash train/train.sh"]}, '
        '"success_condition": "what proves this task is done"}], '
        '"rationale": "why this over alternatives"}'
    ),
    priority=99,
)

DEFAULT_PERSONAS = (DIVERGENT, PRAGMATIC)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

ChatFn = Callable[[str, str], str]  # (system, user) -> raw text


@dataclass
class DebateConfig:
    max_personas: int = 2          # excluding the leader
    degrade_personas: int = 1      # persona count when budget says degrade
    max_context_chars: int = 8000  # bound on the stable context per persona


class PlanDebate:
    """Run a bounded persona debate and let the leader produce the plan."""

    def __init__(self, chat: ChatFn, *, personas=DEFAULT_PERSONAS, leader: Persona = LEADER, config: Optional[DebateConfig] = None, chat_timeout_seconds: float = 0.0):
        self.chat = chat
        self.personas = tuple(personas)
        self.leader = leader
        self.config = config or DebateConfig()
        self.chat_timeout_seconds = float(chat_timeout_seconds or 0.0)

    def _persona_count(self, degrade: bool) -> int:
        cap = self.config.degrade_personas if degrade else self.config.max_personas
        return max(1, min(len(self.personas), cap))

    def _stable_context(self, program_text: str, project_text: str) -> str:
        # Personas see L0+L1 (program) and L2 (project) as stable context, bounded.
        sections = split_program(program_text)
        payload = {
            "constitution": sections.constitution,
            "belief": sections.belief,
            "project": project_text,
        }
        blob = json.dumps(payload, ensure_ascii=False)
        if len(blob) > self.config.max_context_chars:
            blob = blob[: self.config.max_context_chars - 3] + "..."
        return blob

    def run(self, *, program_text: str, project_text: str, degrade: bool = False) -> dict:
        """Return {belief, plan, detailed_plan, transcript, personas_used}."""
        context = self._stable_context(program_text, project_text)
        n = self._persona_count(degrade)
        transcript: list[dict] = []
        opinions: list[str] = []
        selected_personas = list(self.personas[:n])

        def _ask_persona(persona: Persona) -> tuple[Persona, str]:
            user = json.dumps({"task": "give your opinion on how to improve the project next",
                               "stable_context": context}, ensure_ascii=False)
            raw = _safe_chat(self.chat, persona.system, user, timeout_seconds=self.chat_timeout_seconds, label=f"plan persona {persona.name}")
            return persona, raw

        by_name: dict[str, str] = {}
        if len(selected_personas) <= 1:
            for persona in selected_personas:
                _, raw = _ask_persona(persona)
                by_name[persona.name] = raw
        else:
            with ThreadPoolExecutor(max_workers=len(selected_personas)) as executor:
                futures = [executor.submit(_ask_persona, persona) for persona in selected_personas]
                for future in as_completed(futures):
                    persona, raw = future.result()
                    by_name[persona.name] = raw
        for persona in selected_personas:
            raw = by_name.get(persona.name, "")
            transcript.append({"persona": persona.name, "raw": raw})
            opinions.append(f"[{persona.name}] {_extract_opinion(raw)}")

        leader_user = json.dumps({
            "task": "consolidate the persona opinions into ONE concrete plan; you MUST decide",
            "stable_context": context,
            "persona_opinions": opinions,
        }, ensure_ascii=False)
        leader_raw = _safe_chat(self.chat, self.leader.system, leader_user, timeout_seconds=self.chat_timeout_seconds, label="plan leader")
        transcript.append({"persona": self.leader.name, "raw": leader_raw})
        decision = _extract_json(leader_raw)

        return {
            "belief": str(decision.get("belief") or "").strip(),
            "plan": str(decision.get("plan") or "").strip(),
            "detailed_plan": str(decision.get("detailed_plan") or "").strip(),
            "tasks": decision.get("tasks") if isinstance(decision.get("tasks"), list) else [],
            "rationale": str(decision.get("rationale") or "").strip(),
            "transcript": transcript,
            "personas_used": [p.name for p in selected_personas] + [self.leader.name],
        }


def _safe_chat(chat: ChatFn, system: str, user: str, *, timeout_seconds: float = 0.0, label: str = "plan chat") -> str:
    try:
        return call_with_deadline(lambda: chat(system, user) or "", timeout_seconds=timeout_seconds, label=label)
    except Exception as exc:
        return json.dumps({"opinion": f"(persona failed: {exc})", "error": str(exc)})


def _extract_opinion(raw: str) -> str:
    data = _extract_json(raw)
    return str(data.get("opinion") or raw)[:600]


def _extract_json(raw: str) -> dict:
    try:
        from core.autoresearch_loop import extract_json_object

        return extract_json_object(raw)
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Phase handler factory
# --------------------------------------------------------------------------- #

def make_plan_handler(chat: Optional[ChatFn] = None, *, config: Optional[DebateConfig] = None):
    """Build a P2 plan handler.

    If ``chat`` is None, the handler resolves a metered ``plan``-tier client from
    the loop on the context.  A deterministic fallback plan is used when no
    client is available, so the phase never hard-fails.
    """

    def handler(ctx: PhaseContext) -> PhaseResult:
        chat_fn = chat or build_loop_chat_fn(ctx.loop, tier="plan", root=ctx.root, phase=ctx.phase)
        degrade = bool(getattr(ctx.signals, "budget_degrade", False))

        if chat_fn is None:
            # No model available: keep belief, record that planning was skipped.
            note = "plan: no LLM client; kept existing belief and plan (deterministic)"
            write_auto_note(ctx.root, "plan", "# Plan\n(no LLM available; retained previous plan)\n")
            return PhaseResult(summary=note)

        debate_config = config or _debate_config_from_settings(getattr(ctx.loop, "settings", None))
        debate = PlanDebate(
            chat_fn,
            config=debate_config,
            chat_timeout_seconds=float(getattr(getattr(ctx.loop, "settings", None), "llm_request_timeout", 60.0) or 60.0),
        )
        result = debate.run(program_text=ctx.program_text, project_text=ctx.project_text, degrade=degrade)

        # 1) belief -> L1 (program.md), only if we got one and program is writable
        program_text = None
        if result["belief"]:
            try:
                program_text = update_belief(ctx.program_text, result["belief"])
            except ValueError:
                program_text = None  # read-only program: skip belief update

        plan_text = result["plan"] or _fallback_plan_text(ctx.program_text, ctx.project_text)
        detailed_plan = result["detailed_plan"] or plan_text

        # 2) coarse plan -> project.md "## 当前计划"
        project_text = _update_plan_section(ctx.project_text, plan_text or "(leader produced no plan)")

        # 3) detailed plan -> .auto/plan.md (L3)
        max_implementation_tasks = int(getattr(getattr(ctx.loop, "settings", None), "plan_max_implementation_tasks", 0) or 0)
        planned_todo_state = (
            _leader_tasks_to_todo_state(result.get("tasks") or [])
            if result.get("tasks")
            else _plan_to_todo_state(detailed_plan, max_implementation_tasks=max_implementation_tasks)
        )
        if not _has_metric_experiment(ctx.root):
            planned_todo_state = _ensure_baseline_checkpoint(planned_todo_state)
        todo_state = merge_todo_state(load_todo_state(ctx.root), planned_todo_state)
        save_todo_state(ctx.root, todo_state)
        write_auto_note(ctx.root, "plan", render_todo_markdown(todo_state))

        # 4) transcript -> L4 artifact (never into project.md)
        _archive_transcript(ctx, result)

        summary = f"plan: personas={result['personas_used']} plan_set={bool(plan_text)}"
        return PhaseResult(program_text=program_text, project_text=project_text, summary=summary)

    return handler


def _fallback_plan_text(program_text: str, project_text: str) -> str:
    return (
        "1. Inspect the allowed project files and current train-side behavior.\n"
        "2. Run bash train/train.sh and bash eval.sh to establish a metric-bearing baseline.\n"
        "3. Implement the smallest train-side change that can improve the objective while respecting program.md.\n"
        "4. Run bash train/train.sh and bash eval.sh again, then record the metric and next action."
    )


def _has_metric_experiment(root: str | Path) -> bool:
    path = Path(root) / ".autoresearch" / "state.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("experiments"))


def _ensure_baseline_checkpoint(state: dict) -> dict:
    tasks = list(state.get("tasks") or [])
    if any((task or {}).get("task_id") == "baseline" for task in tasks):
        return state
    first_run_index = next((i for i, task in enumerate(tasks) if _task_runs_in_phase(task) == "run"), None)
    first_impl_index = next((i for i, task in enumerate(tasks) if (task or {}).get("type") == "implementation"), None)
    if first_run_index is not None and (first_impl_index is None or first_run_index < first_impl_index):
        return state
    insert_at = first_impl_index if first_impl_index is not None else (first_run_index if first_run_index is not None else len(tasks))
    prior_execute_ids = [
        str(task.get("task_id"))
        for task in tasks[:insert_at]
        if _task_runs_in_phase(task) == "execute" and str(task.get("task_id") or "").strip()
    ]
    baseline = {
        "task_id": "baseline",
        "goal": "Run the existing train/eval pipeline once to establish a metric-bearing baseline before modifications.",
        "type": "validation",
        "status": "pending",
        "priority": insert_at + 1,
        "depends_on": prior_execute_ids,
        "plan_summary": "Run existing train/eval baseline before modifications.",
        "run_spec": {"mode": "single", "commands": ["bash train/train.sh", "bash eval.sh"]},
    }
    updated = tasks[:insert_at] + [baseline] + tasks[insert_at:]
    for index, task in enumerate(updated, start=1):
        task["priority"] = index
    return {"version": 1, "tasks": updated}


def _debate_config_from_settings(settings) -> DebateConfig:
    if settings is None:
        return DebateConfig()
    return DebateConfig(
        max_personas=max(1, int(getattr(settings, "plan_max_personas", 2) or 2)),
        degrade_personas=max(1, int(getattr(settings, "plan_degrade_personas", 1) or 1)),
    )


def _plan_to_todo_state(plan_text: str, *, max_implementation_tasks: int = 0) -> dict:
    """Best-effort bridge from current leader prose to structured task state.

    This is an interim compatibility layer: the next iteration should ask the
    leader to emit task JSON directly. Until then, keep the structure explicit so
    Execute/Run/Evaluate no longer depend only on Markdown task text.
    """
    import re

    items = []
    for line in (plan_text or "").splitlines():
        m = re.match(r"^\s*(?:\d+\s*[.)\-:]|[-*•]|[Ss]tep\s+\d+\s*[:.)-])\s+(.*\S)\s*$", line)
        if m:
            items.extend(_split_inline_plan_items(m.group(1).strip()))
    if not items and (plan_text or "").strip():
        items = _split_inline_plan_items((plan_text or "").strip())
    items = _coalesce_plan_items(items, max_implementation_tasks=max_implementation_tasks)
    tasks = []
    for index, item in enumerate(items, start=1):
        task_type = _classify_plan_item(item)
        tasks.append({
            "task_id": f"t{index}",
            "goal": item,
            "type": task_type,
            "status": "pending",
            "priority": index,
            "plan_summary": item,
            "allowed_paths": ["train/**"] if task_type == "implementation" else [],
            "run_spec": _default_run_spec_for_task(task_type, item),
        })
    _assign_default_dag_dependencies(tasks)
    return {"version": 1, "tasks": tasks}


def _leader_tasks_to_todo_state(raw_tasks: list) -> dict:
    """Convert leader-emitted typed DAG tasks to todo_state.

    This is the preferred path for v2 planning. The fallback prose parser still
    exists for older leaders, but typed tasks preserve real dependency edges
    instead of inferring a linear checklist from markdown.
    """
    tasks = []
    for index, raw in enumerate(raw_tasks or [], start=1):
        if not isinstance(raw, dict):
            continue
        goal = str(raw.get("goal") or raw.get("title") or raw.get("description") or "").strip()
        if not goal:
            continue
        task_type = _normalize_task_type(raw.get("type"))
        task_id = _normalize_task_id(raw.get("task_id") or raw.get("id") or f"t{index}", fallback_index=index)
        plan_summary = str(raw.get("plan_summary") or raw.get("success_condition") or goal).strip()
        run_spec = raw.get("run_spec") if isinstance(raw.get("run_spec"), dict) else {}
        if not run_spec:
            run_spec = _default_run_spec_for_task(task_type, goal)
        else:
            run_spec = _normalize_run_spec(run_spec)
        task = {
            "task_id": task_id,
            "goal": goal,
            "type": task_type,
            "status": "pending",
            "priority": int(raw.get("priority") or index),
            "plan_summary": plan_summary,
            "allowed_paths": _string_list(raw.get("allowed_paths")),
            "context_paths": _string_list(raw.get("context_paths")),
            "depends_on": _string_list(raw.get("depends_on") or raw.get("dependencies")),
            "run_spec": run_spec,
            "verification": dict(raw.get("verification")) if isinstance(raw.get("verification"), dict) else {},
        }
        tasks.append(task)
    _assign_default_dag_dependencies(tasks)
    return {"version": 1, "tasks": tasks}


def _split_inline_plan_items(text: str) -> list[str]:
    """Split prose that contains multiple inline numbered steps."""
    raw = str(text or "").strip()
    if not raw:
        return []
    matches = list(re.finditer(r"(?:^|\s)(\d{1,2})\s*[.)]\s+", raw))
    if not matches:
        return [raw]
    items: list[str] = []
    prefix = raw[:matches[0].start()].strip(" ;.-\t")
    if prefix:
        items.append(prefix)
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        item = raw[start:end].strip(" ;.-\t")
        if item:
            items.append(item)
    return items or [raw]


def _coalesce_plan_items(items: list[str], *, max_implementation_tasks: int = 3) -> list[str]:
    """Keep the structured task list coarse enough for phase-level execution.

    Persona plans often contain many tactical bullets. Sending each bullet to a
    separate Execute LLM call is slow and encourages half-finished edits. Merge
    adjacent implementation bullets into a few coherent work packets, keep early
    analysis explicit, and keep validation checkpoints as Run-owned tasks.
    """
    if max_implementation_tasks <= 0:
        return list(items or [])
    implementation: list[str] = []
    output: list[str] = []
    for item in items:
        task_type = _classify_plan_item(item)
        if task_type == "implementation":
            implementation.append(item)
            continue
        if implementation:
            output.extend(_chunk_implementation_items(implementation, max_tasks=max_implementation_tasks))
            implementation = []
        output.append(item)
    if implementation:
        output.extend(_chunk_implementation_items(implementation, max_tasks=max_implementation_tasks))
    return output


def _chunk_implementation_items(items: list[str], *, max_tasks: int) -> list[str]:
    if max_tasks <= 0:
        return list(items or [])
    if len(items) <= max(1, max_tasks):
        return items
    chunks: list[list[str]] = [[] for _ in range(max(1, max_tasks))]
    for index, item in enumerate(items):
        chunks[index % len(chunks)].append(item)
    merged = []
    for chunk in chunks:
        if len(chunk) == 1:
            merged.append(chunk[0])
        else:
            merged.append("Implement a consolidated train-side change covering: " + "; ".join(chunk))
    return merged


_IMPLEMENTATION_PLAN_RE = r"\b(implement|update|modify|rewrite|replace|create|add|edit|refactor|fix|write|build|integrate|ensure)\b"
_ANALYSIS_PLAN_RE = r"^\s*(analyze|inspect|compare|检查|分析|对比)\b"
_VALIDATION_PLAN_RE = r"^\s*(run|evaluate|eval|verify|validate|test|运行|评估|验证|测试)\b"
_RUN_LIKE_IMPLEMENTATION_RE = r"^\s*run\s+(?:a\s+|an\s+|the\s+)?(?:(?:broad|bounded|deterministic|global|local|adaptive|coordinate|pattern|random|seeded|low-discrepancy|grid|latin)\s+)*(?:design|refinement|exploration|search)\b"
_EVAL_LIKE_IMPLEMENTATION_RE = r"^\s*(evaluate|verify)\s+(candidates?|points?|proposals?|incumbents?)\s+(with|using|through)\s+(the\s+)?(oracle|train-side|blackbox)"


def _classify_plan_item(item: str) -> str:
    """Classify prose plan items without treating every "run/eval" substring as validation.

    The bridge still consumes natural-language leader plans, so classification
    must be conservative. A sentence like "update train.sh so it runs ..." is an
    implementation task, not a validation task. Likewise "evaluated points" is
    just a noun phrase. Validation is reserved for explicit run/eval/verify/test
    actions or concrete eval commands when no implementation verb is present.
    """
    import re

    text = str(item or "").strip().lower()
    if not text:
        return "implementation"
    has_impl = bool(re.search(_IMPLEMENTATION_PLAN_RE, text)) or any(
        token in text for token in ("保存", "修改", "新增", "创建", "实现", "重写")
    )
    if has_impl:
        return "implementation"
    if re.search(_ANALYSIS_PLAN_RE, text):
        return "analysis"
    explicit_eval_command = any(token in text for token in ("bash eval.sh", "bash train/train.sh", "pytest", "python -m pytest"))
    natural_language_run_design = bool(re.match(r"^\s*run\b", text) and re.search(r"\b(design|refinement|exploration|search)\b", text))
    command_like_run = bool(re.search(r"\b(bash|sh|python3?|pytest)\b", text))
    if (
        re.search(_RUN_LIKE_IMPLEMENTATION_RE, text)
        or (natural_language_run_design and not command_like_run)
        or re.search(_EVAL_LIKE_IMPLEMENTATION_RE, text)
    ) and not explicit_eval_command:
        return "implementation"
    if explicit_eval_command or re.search(_VALIDATION_PLAN_RE, text):
        return "validation"
    return "implementation"


def _normalize_task_type(value) -> str:
    raw = str(value or "implementation").strip().lower()
    return raw if raw in {"implementation", "experiment", "validation", "analysis", "maintenance"} else "implementation"


def _normalize_task_id(value, *, fallback_index: int) -> str:
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return raw or f"t{fallback_index}"


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


def _assign_default_dag_dependencies(tasks: list[dict]) -> None:
    """Fill missing dependencies without turning the plan into a linear chain.

    The default DAG policy is feedback-oriented:
    - an implementation may depend on the immediately previous analysis, because
      the analysis is its context-gathering step;
    - a run/validation checkpoint depends only on the most recent execute task
      since the previous checkpoint, so the loop can run as soon as a runnable
      slice exists;
    - existing leader-emitted dependencies are preserved.
    """
    current_slice_execute_ids: list[str] = []
    last_analysis_id = ""
    for task in tasks:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        if task.get("depends_on"):
            if _task_runs_in_phase(task) == "run":
                current_slice_execute_ids = []
            elif _task_runs_in_phase(task) == "execute":
                current_slice_execute_ids.append(task_id)
            if task.get("type") == "analysis":
                last_analysis_id = task_id
            continue
        if _task_runs_in_phase(task) == "run":
            if current_slice_execute_ids:
                task["depends_on"] = [current_slice_execute_ids[-1]]
            current_slice_execute_ids = []
            continue
        if task.get("type") == "implementation" and last_analysis_id:
            task["depends_on"] = [last_analysis_id]
        current_slice_execute_ids.append(task_id)
        if task.get("type") == "analysis":
            last_analysis_id = task_id


def _normalize_run_spec(run_spec: dict) -> dict:
    spec = dict(run_spec or {})
    commands = spec.get("commands")
    if isinstance(commands, str):
        commands = [commands]
    if isinstance(commands, list):
        spec["commands"] = [_prefer_python3_command(str(command)) for command in commands]
    monitor_commands = spec.get("monitor_commands")
    if isinstance(monitor_commands, str):
        monitor_commands = [monitor_commands]
    if isinstance(monitor_commands, list):
        spec["monitor_commands"] = [_prefer_python3_command(str(command)) for command in monitor_commands]
    return spec


def _prefer_python3_command(command: str) -> str:
    text = str(command or "")
    # Common leader output uses inline heredocs. Keep command semantics, but
    # avoid bare python because this environment may map it to Python 2.
    text = re.sub(r"(^|&&\s*|;\s*)python(\s+-m\b)", r"\1python3\2", text)
    text = re.sub(r"(^|&&\s*|;\s*)python(\s+-)(?=\s|$)", r"\1python3\2", text)
    return text


def _default_run_spec_for_task(task_type: str, item: str) -> dict:
    if task_type == "validation":
        return {"mode": "single", "commands": ["bash train/train.sh", "bash eval.sh"]}
    return {}


def _task_runs_in_phase(task: dict) -> str:
    if task.get("type") in {"validation", "experiment"}:
        return "run"
    if task.get("type") == "maintenance" and task.get("run_spec"):
        return "run"
    return "execute"


def build_loop_chat_fn(loop, *, tier: str = "plan", root: str | Path | None = None, phase: str = "") -> Optional[ChatFn]:
    """Build a (system, user)->text callable backed by the loop's step agent client.

    Returns None (deterministic, no LLM) unless the loop was explicitly configured
    with use_llm_step_agents=True. This prevents phases from silently reaching for a
    live client when the caller asked for the deterministic path. ``tier`` picks the
    model cost tier (plan for debate, exec for code execution).
    """
    if loop is None:
        return None
    if not bool(getattr(getattr(loop, "settings", None), "use_llm_step_agents", False)):
        return None
    step_agent = getattr(loop, "step_agent", None)
    if step_agent is None:
        # Build a transient step agent if the loop supports it.
        try:
            from core.autoresearch_loop import AutoResearchStepAgent

            step_agent = AutoResearchStepAgent(loop.settings, loop=loop)
        except Exception:
            return None
    try:
        client = step_agent._client()
    except Exception:
        return None
    if client is None:
        return None

    def chat(system: str, user: str) -> str:
        model = ""
        tiers = getattr(loop, "model_tiers", None)
        if tiers is not None:
            model = tiers.resolve(tier)
        kwargs = {
            "model": model or "gpt-4o",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        temperature = getattr(loop.settings, "llm_temperature", 0.0)
        if temperature not in (None, 0, 0.0):
            kwargs["temperature"] = temperature
        timeout = float(getattr(loop.settings, "llm_request_timeout", 60.0) or 60.0)
        debug_root = Path(root) if root is not None else loop.settings.root()
        label = _plan_chat_label(system)
        inflight_start(
            debug_root,
            "llm",
            phase=phase or getattr(loop, "_current_phase", ""),
            detail=label,
            model=model or "gpt-4o",
            timeout_seconds=timeout,
            prompt_chars=len(system) + len(user),
        )
        try:
            def _call():
                try:
                    return client.chat.completions.create(**kwargs, timeout=timeout)
                except TypeError:
                    return client.chat.completions.create(**kwargs)

            resp = call_with_deadline(_call, timeout_seconds=timeout, label=label)
        except Exception as exc:
            inflight_finish(
                debug_root,
                "llm",
                phase=phase or getattr(loop, "_current_phase", ""),
                detail=label,
                model=model or "gpt-4o",
                error=str(exc)[:500],
            )
            raise
        inflight_finish(
            debug_root,
            "llm",
            phase=phase or getattr(loop, "_current_phase", ""),
            detail=label,
            model=model or "gpt-4o",
        )
        return getattr(resp.choices[0].message, "content", "") or ""

    return chat


def _plan_chat_label(system: str) -> str:
    if "DIVERGENT" in system:
        return "plan persona divergent"
    if "PRAGMATIC" in system:
        return "plan persona pragmatic"
    if "LEADER" in system:
        return "plan leader"
    return "plan chat"


# Backwards-compatible alias used by the plan handler.
def _loop_chat_fn(loop) -> Optional[ChatFn]:
    return build_loop_chat_fn(loop, tier="plan")


def _update_plan_section(project_text: str, plan: str) -> str:
    marker = "## 当前计划"
    if marker not in project_text:
        return project_text.rstrip() + f"\n\n{marker}\n{plan}\n"
    head, _, rest = project_text.partition(marker)
    after = rest.split("\n## ", 1)
    body = f"{marker}\n{plan}\n"
    if len(after) == 2:
        return head + body + "\n## " + after[1]
    return head + body


def _archive_transcript(ctx: PhaseContext, result: dict) -> None:
    loop = ctx.loop
    payload = json.dumps({
        "timestamp": time.strftime("%F %T"),
        "personas_used": result.get("personas_used"),
        "rationale": result.get("rationale"),
        "transcript": result.get("transcript"),
    }, ensure_ascii=False, indent=2)
    artifacts = getattr(loop, "artifacts", None)
    if artifacts is not None:
        try:
            artifacts.save(kind="plan_debate", rationale="plan_phase", content=payload, extension="json")
            return
        except Exception:
            pass
    # Fallback: write under .autoresearch/artifacts directly.
    d = Path(ctx.root) / ".autoresearch" / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{int(time.time())}_plan_debate.json").write_text(payload, encoding="utf-8")


__all__ = [
    "Persona",
    "DIVERGENT",
    "PRAGMATIC",
    "LEADER",
    "DEFAULT_PERSONAS",
    "DebateConfig",
    "PlanDebate",
    "make_plan_handler",
    "build_loop_chat_fn",
]
