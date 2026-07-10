from __future__ import annotations

from .types import StepSpec


PLAN_TOOLS = (
    "read_file",
    "search_files",
    "skill_search",
    "skill_view",
    "detailed_plan",
    "delegate_task",
    "artifact_write",
    "write_file",
)

ATTEMPT_TOOLS = (
    "read_file",
    "search_files",
    "skill_search",
    "skill_view",
    "write_file",
    "run_command",
    "run_train",
    "run_eval",
    "run_pipeline",
    "command_status",
    "read_eval",
    "delegate_task",
    "artifact_write",
)

CONCLUDE_TOOLS = (
    "read_file",
    "search_files",
    "write_file",
    "command_status",
    "read_eval",
    "artifact_write",
)


DEFAULT_STEPS: dict[str, StepSpec] = {
    "plan": StepSpec(
        name="plan",
        done_tag="PLAN_DONE",
        allowed_tools=PLAN_TOOLS,
        system_prompt=(
            "You are the planning agent for an automated autoresearch loop. "
            "Understand program.md, project files, prior traces, and any local skills. "
            "For easy or already-clear projects, do NOT call detailed_plan; write a short, direct "
            ".autoresearch/plan.md with the next executable attempt. Call detailed_plan only when "
            "the project is genuinely complex, ambiguous, multi-module, or needs a long structured "
            "implementation strategy. You may use delegate_task for bounded side research such as "
            "reading a module or summarizing files, but do not delegate the immediate critical-path "
            "decision. The final plan must include concrete hypotheses, allowed files, commands to run, "
            "success metrics, and the next smallest executable attempt."
        ),
        user_goal=(
            "Produce a concrete plan for the next autoresearch attempt. Keep it bounded, "
            "metric-oriented, and directly executable by the attempt step."
        ),
    ),
    "attempt": StepSpec(
        name="attempt",
        done_tag="ATTEMPT_DONE",
        allowed_tools=ATTEMPT_TOOLS,
        system_prompt=(
            "You are the attempt agent for an automated autoresearch loop. "
            "Read the current plan, make only the smallest project-confined changes needed, "
            "run validation through run_pipeline after code changes, and store evidence. "
            "Prefer run_pipeline over separate run_train/run_eval/read_eval calls because it runs train, "
            "eval, and metric reading in one monitored framework action. Prefer run_train/run_eval over raw run_command when separate calls are needed because they write command heartbeat files "
            "under .autoresearch/commands for long-running jobs. Use command_status if you need to inspect "
            "the latest heartbeat. You may use delegate_task only for bounded side work that does not block "
            "your next local action. Do not edit protected evaluation files."
        ),
        user_goal=(
            "Execute one bounded research attempt and collect evidence. Write notes to "
            ".autoresearch/attempt.md or artifacts when needed."
        ),
    ),
    "conclude": StepSpec(
        name="conclude",
        done_tag="CONCLUDE_DONE",
        allowed_tools=CONCLUDE_TOOLS,
        system_prompt=(
            "You are the conclusion agent for an automated autoresearch loop. "
            "Use the project eval interface first: call read_eval to get structured metrics "
            "and solved status instead of manually inferring metric values from logs. "
            "Use command_status when train/eval may be long-running or recently timed out, so the "
            "summary can distinguish running, timeout, failure, and success. "
            "If read_eval reports solved, write concise notes/project state and request stop. "
            "Your LLM work should focus on lessons learned, context compression, and concise "
            "handoff for the next cycle; metric reading and good/bad evaluation should rely "
            "on the eval interface."
        ),
        user_goal=(
            "Record the result of the latest attempt and leave the project state ready "
            "for the next plan step."
        ),
    ),
}


STEP_ORDER = ("plan", "attempt", "conclude")


def next_step(name: str) -> str:
    idx = STEP_ORDER.index(name)
    return STEP_ORDER[(idx + 1) % len(STEP_ORDER)]
