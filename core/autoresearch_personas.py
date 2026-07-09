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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.autoresearch_memory import update_belief, split_program, write_auto_note
from core.autoresearch_phases import PhaseContext, PhaseResult
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
        "Consolidate the personas into one concrete next plan. Return ONLY JSON: "
        '{"belief": "updated project belief (short, intuition-level)", '
        '"plan": "concrete next plan for project.md (can be coarse)", '
        '"detailed_plan": "step-by-step plan for .auto/plan.md", '
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

    def __init__(self, chat: ChatFn, *, personas=DEFAULT_PERSONAS, leader: Persona = LEADER, config: Optional[DebateConfig] = None):
        self.chat = chat
        self.personas = tuple(personas)
        self.leader = leader
        self.config = config or DebateConfig()

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
        for persona in self.personas[:n]:
            user = json.dumps({"task": "give your opinion on how to improve the project next",
                               "stable_context": context}, ensure_ascii=False)
            raw = _safe_chat(self.chat, persona.system, user)
            transcript.append({"persona": persona.name, "raw": raw})
            opinions.append(f"[{persona.name}] {_extract_opinion(raw)}")

        leader_user = json.dumps({
            "task": "consolidate the persona opinions into ONE concrete plan; you MUST decide",
            "stable_context": context,
            "persona_opinions": opinions,
        }, ensure_ascii=False)
        leader_raw = _safe_chat(self.chat, self.leader.system, leader_user)
        transcript.append({"persona": self.leader.name, "raw": leader_raw})
        decision = _extract_json(leader_raw)

        return {
            "belief": str(decision.get("belief") or "").strip(),
            "plan": str(decision.get("plan") or "").strip(),
            "detailed_plan": str(decision.get("detailed_plan") or "").strip(),
            "rationale": str(decision.get("rationale") or "").strip(),
            "transcript": transcript,
            "personas_used": [p.name for p in self.personas[:n]] + [self.leader.name],
        }


def _safe_chat(chat: ChatFn, system: str, user: str) -> str:
    try:
        return chat(system, user) or ""
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
        chat_fn = chat or build_loop_chat_fn(ctx.loop, tier="plan")
        degrade = bool(getattr(ctx.signals, "budget_degrade", False))

        if chat_fn is None:
            # No model available: keep belief, record that planning was skipped.
            note = "plan: no LLM client; kept existing belief and plan (deterministic)"
            write_auto_note(ctx.root, "plan", "# Plan\n(no LLM available; retained previous plan)\n")
            return PhaseResult(summary=note)

        debate = PlanDebate(chat_fn, config=config)
        result = debate.run(program_text=ctx.program_text, project_text=ctx.project_text, degrade=degrade)

        # 1) belief -> L1 (program.md), only if we got one and program is writable
        program_text = None
        if result["belief"]:
            try:
                program_text = update_belief(ctx.program_text, result["belief"])
            except ValueError:
                program_text = None  # read-only program: skip belief update

        # 2) coarse plan -> project.md "## 当前计划"
        project_text = _update_plan_section(ctx.project_text, result["plan"] or "(leader produced no plan)")

        # 3) detailed plan -> .auto/plan.md (L3)
        planned_todo_state = _plan_to_todo_state(result["detailed_plan"] or result["plan"] or "")
        todo_state = merge_todo_state(load_todo_state(ctx.root), planned_todo_state)
        save_todo_state(ctx.root, todo_state)
        write_auto_note(ctx.root, "plan", render_todo_markdown(todo_state))

        # 4) transcript -> L4 artifact (never into project.md)
        _archive_transcript(ctx, result)

        summary = f"plan: personas={result['personas_used']} plan_set={bool(result['plan'])}"
        return PhaseResult(program_text=program_text, project_text=project_text, summary=summary)

    return handler


def _plan_to_todo_state(plan_text: str) -> dict:
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
            items.append(m.group(1).strip())
    if not items and (plan_text or "").strip():
        items = [(plan_text or "").strip()]
    tasks = []
    for index, item in enumerate(items, start=1):
        lowered = item.lower()
        if any(token in lowered for token in ("run", "eval", "verify", "validate", "test", "运行", "评估", "验证", "测试")):
            task_type = "validation"
        elif any(token in lowered for token in ("analyze", "inspect", "compare", "检查", "分析", "对比")):
            task_type = "analysis"
        else:
            task_type = "implementation"
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
    return {"version": 1, "tasks": tasks}


def _default_run_spec_for_task(task_type: str, item: str) -> dict:
    if task_type == "validation":
        return {"mode": "single", "commands": ["bash train/train.sh", "bash eval.sh"]}
    return {}


def build_loop_chat_fn(loop, *, tier: str = "plan") -> Optional[ChatFn]:
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
        resp = client.chat.completions.create(**kwargs)
        return getattr(resp.choices[0].message, "content", "") or ""

    return chat


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
