"""Legacy fixed/evolutionary planners and bounded step agent."""

from __future__ import annotations

import json
from typing import Callable, Optional

from autoresearch.legacy.services import AutoResearchSafetyError, extract_json_object
from autoresearch.legacy.types import (
    AutoResearchAction,
    AutoResearchSettings,
    AutoResearchStepResult,
    AutoResearchWorkflowStep,
    DEFAULT_CONTEXT_BUCKETS,
)
from autoresearch.observability.debug import inflight_finish, inflight_start
from autoresearch.observability.timeout import call_with_deadline

Planner = Callable[[str, int], AutoResearchAction]
Summarizer = Callable[[AutoResearchAction, str], str]


class FixedAutoResearchPlanner:
    """Deterministic small-step workflow planner for autoresearch.

    Each step declares the only action/tool surface it may use.  R-Agent can run
    the whole loop as one isolated tool process, while this planner keeps the
    internal autoresearch workflow stable and low-variance.
    """

    DEFAULT_STEPS = (
        AutoResearchWorkflowStep(
            name="inspect_project",
            action_type="run",
            rationale="project_understanding_inspect",
            command="pwd && find . -maxdepth 2 -type f | sort | head -120",
            allowed_tools=("run", "read"),
        ),
        AutoResearchWorkflowStep(
            name="read_program",
            action_type="read",
            rationale="project_understanding_program",
            path="program.md",
            allowed_tools=("read",),
        ),
        AutoResearchWorkflowStep(
            name="plan_change",
            action_type="note",
            rationale="modification_plan_initial",
            content="Draft one minimal, reversible experiment hypothesis from program.md and current project understanding.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="baseline_eval",
            action_type="run",
            rationale="experiment_result_baseline",
            command="if [ -f eval.sh ]; then bash eval.sh; elif [ -f train/train.sh ]; then bash train/train.sh; else echo 'No eval.sh or train/train.sh found; baseline unavailable.'; fi",
            allowed_tools=("run",),
            role="baseline",
        ),
        AutoResearchWorkflowStep(
            name="summarize_baseline",
            action_type="note",
            rationale="conclusion_baseline_summary",
            content="Record baseline evidence, metrics if available, and whether a real experiment can proceed.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="propose_experiment",
            action_type="note",
            rationale="modification_plan_one_hypothesis",
            content="Propose exactly one minimal change hypothesis, expected metric direction, risk, and rollback condition. Do not edit yet.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="apply_change",
            action_type="note",
            rationale="current_change_apply_patch_or_skip",
            content="No safe patch has been produced by the step agent; record that apply-change was skipped.",
            allowed_tools=("apply_patch", "write", "note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="run_experiment_if_available",
            action_type="run",
            rationale="experiment_result_trial",
            command="if [ -f train/train.sh ]; then bash train/train.sh; elif [ -f eval.sh ]; then bash eval.sh; else echo 'No train/train.sh or eval.sh found; trial unavailable.'; fi",
            allowed_tools=("run",),
            role="trial",
        ),
        AutoResearchWorkflowStep(
            name="parse_metric_and_decide",
            action_type="note",
            rationale="conclusion_metric_decision",
            content="Parse latest metric evidence from observations/artifacts. Decide keep/discard/needs_metrics. Do not commit unless explicitly configured.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="record_decision",
            action_type="note",
            rationale="conclusion_record_decision",
            content="Record final decision, completed work, artifact paths, and next action. Commit is disabled by default; record what would be committed.",
            allowed_tools=("note", "read"),
        ),
    )

    def __init__(self, steps: Optional[list[AutoResearchWorkflowStep]] = None):
        self.steps = list(steps or self.DEFAULT_STEPS)

    def __call__(self, parent_context: str, round_index: int) -> AutoResearchAction:
        if round_index < len(self.steps):
            return self.steps[round_index].to_action()
        return AutoResearchAction(type="stop", rationale="fixed autoresearch workflow completed")

    def step_for_round(self, round_index: int) -> AutoResearchWorkflowStep | None:
        if round_index < len(self.steps):
            return self.steps[round_index]
        return None

    def allowed_tools_for_round(self, round_index: int) -> tuple[Decision, ...]:
        if round_index < len(self.steps):
            return self.steps[round_index].allowed_tools
        return ("stop",)


class EvolutionaryAutoResearchPlanner:
    """Wrap the fixed 10-step workflow with an inner propose/apply/run/decide loop.

    First runs the full fixed workflow once (baseline + first trial).  After that,
    while the loop has budget for more trials (measured by ``experiment_count``)
    and rounds remaining, keep replaying the four inner steps:

        propose_experiment -> apply_change -> run_experiment_if_available -> parse_metric_and_decide

    This is what makes ``max_experiments``, Pareto front, and per-trial versioning
    policies actually consume more than one trial per ``auto_research_run``.
    """

    INNER_STEP_NAMES = ("propose_experiment", "apply_change", "run_experiment_if_available", "parse_metric_and_decide")

    def __init__(self, base: Optional[FixedAutoResearchPlanner] = None):
        self._base = base or FixedAutoResearchPlanner()
        self._loop: "AutoResearchLoop | None" = None
        by_name = {step.name: step for step in self._base.steps}
        missing = [name for name in self.INNER_STEP_NAMES if name not in by_name]
        if missing:
            raise AutoResearchSafetyError(f"EvolutionaryAutoResearchPlanner base is missing steps: {missing}")
        self._inner_steps: tuple[AutoResearchWorkflowStep, ...] = tuple(by_name[name] for name in self.INNER_STEP_NAMES)
        self._final_step = by_name.get("record_decision")

    def bind_loop(self, loop: "AutoResearchLoop") -> None:
        self._loop = loop

    @property
    def base_step_count(self) -> int:
        return len(self._base.steps)

    def _step_for_round(self, round_index: int) -> AutoResearchWorkflowStep | None:
        base = self._base
        if round_index < len(base.steps):
            return base.steps[round_index]
        extra = round_index - len(base.steps)
        loop = self._loop
        experiments_done = int(getattr(loop, "_experiment_count", 0)) if loop is not None else 0
        max_experiments = int(getattr(loop.settings, "max_experiments", 0)) if loop is not None else 0
        if loop is not None and experiments_done >= max(0, max_experiments):
            if extra == 0 and self._final_step is not None:
                return self._final_step
            return None
        cycle_index = extra % len(self._inner_steps)
        return self._inner_steps[cycle_index]

    def step_for_round(self, round_index: int) -> AutoResearchWorkflowStep | None:
        return self._step_for_round(round_index)

    def allowed_tools_for_round(self, round_index: int) -> tuple[Decision, ...]:
        step = self._step_for_round(round_index)
        if step is None:
            return ("stop",)
        return step.allowed_tools

    def __call__(self, parent_context: str, round_index: int) -> AutoResearchAction:
        step = self._step_for_round(round_index)
        if step is None:
            return AutoResearchAction(type="stop", rationale="evolutionary autoresearch budget exhausted")
        return step.to_action()


class AutoResearchStepAgent:
    STEP_GUIDANCE = {
        "inspect_project": "Build concise project understanding: structure, likely entrypoints, existing eval/train files, and risks.",
        "read_program": "Extract research goal, success metric, allowed edits, fixed eval harness, budget, and stop conditions from program.md.",
        "plan_change": (
            "Propose one reversible experiment hypothesis. Prefer a plan that lets ONE edit do MANY evaluations: "
            "if the protocol allows editing files under train/, you MAY write a self-iterating search script "
            "(e.g. train/train.py runs a loop that itself calls the eval harness many times, reads the returned "
            "metric, and keeps the best candidate) instead of hand-editing a single constant per round. "
            "This is your choice; pick it when the task is a search/optimization loop, so you do not need to think "
            "once per evaluation. Specify target files, expected metric direction, risk, rollback."
        ),
        "baseline_eval": (
            "Run or prepare baseline evaluation; focus on machine-parseable metrics and failure diagnosis. "
            "Use python3 (never bare 'python') for any inline/summary script. Judge success by the parsed metric "
            "and the train/eval logs, NOT by the exit code of a summary wrapper — do not let a summary step's "
            "failure make you exit nonzero when train/eval actually produced a valid metric."
        ),
        "summarize_baseline": "Summarize baseline evidence and whether metrics are sufficient for comparison.",
        "propose_experiment": (
            "Produce a single minimal modification plan; do not combine unrelated ideas. For search/optimization "
            "tasks, a strong single plan is to (re)write an allowed train-side script that internally loops over "
            "many candidates and calls the eval harness each time, returning the best. That amortizes one LLM "
            "decision over many cheap evaluations instead of one candidate per round. "
            "IMPROVE ACROSS ROUNDS: read the previous round's best metric and search history from context; if the "
            "objective is not yet reached or still improving, REWRITE the search script to do better — widen the "
            "search range to cover the whole plausible domain from program.md, increase the sample budget, and/or "
            "switch algorithm (e.g. coarse global scan then local refinement). Do NOT anchor the search solely on "
            "the existing submission.json; always run an independent global search each round."
        ),
        "apply_change": (
            "Make the planned train-side change. Two safe options: (a) emit apply_patch with a unified diff when you "
            "know the exact current file contents; (b) if you do NOT have the exact contents, prefer a full-file "
            "'write' action (path + complete new content) rather than skipping — a self-contained search script is a "
            "good fit for 'write'. Only skip if no safe change can be expressed. Never touch forbidden eval files."
        ),
        "run_experiment_if_available": (
            "Run the configured experiment/eval command; prefer bounded commands and preserve logs. Use python3 for "
            "any inline summary; base the run's success on the parsed metric and logs, not on a summary script's exit "
            "code."
        ),
        "parse_metric_and_decide": (
            "Parse metrics, compare against baseline if present, and decide keep/discard/needs_metrics. If the target "
            "is not reached and the budget allows, prefer 'needs_metrics'/continue so the loop can propose an "
            "improved search script next round, rather than stopping at a mediocre local result."
        ),
        "record_decision": "Record final decision, completed parts, artifacts, next steps, and what would be committed.",
    }

    """One bounded LLM child agent for a single fixed autoresearch step.

    It receives only the step definition, allowed action surface, and the bounded
    parent context assembled from modular buckets.  It must return structured
    JSON.  The parent loop still validates and executes the selected action.
    """

    def __init__(self, settings: AutoResearchSettings, client=None, model: str | None = None, loop: "AutoResearchLoop | None" = None):
        self.settings = settings
        self.client = client
        self.model = model or settings.llm_model
        self.loop = loop
        self._tier = "plan"

    def _client(self):
        if self.client is None:
            from core import config

            inner = config.create_llm_client()
            ledger = getattr(self.loop, "budget", None)
            if ledger is not None:
                from autoresearch.observability.budget import MeteredLLMClient

                self.client = MeteredLLMClient(
                    inner,
                    ledger,
                    get_phase=lambda: getattr(self.loop, "_current_phase", "") or "",
                    get_model=lambda: self._resolved_model(),
                )
            else:
                self.client = inner
        return self.client

    def _resolved_model(self) -> str:
        if self.model:
            return self.model
        tiers = getattr(self.loop, "model_tiers", None)
        if tiers is not None:
            return tiers.resolve(self._tier)
        return __import__("core.config", fromlist=["get_model"]).get_model()

    def plan_step(
        self,
        *,
        step: AutoResearchWorkflowStep,
        fallback_action: AutoResearchAction,
        parent_context: str,
        round_index: int,
    ) -> AutoResearchStepResult:
        allowed = list(step.allowed_tools or (fallback_action.type,))
        system = (
            "You are an isolated auto_research step agent. "
            "Return ONLY valid JSON. Do not include markdown fences. "
            "Choose exactly one action within allowed_tools, and optionally write "
            "short bucket_updates for modular context. Do not claim experimental "
            "improvements without metrics in context/artifacts."
        )
        if step.name == "apply_change" and "write" in allowed:
            system += (
                " For apply_change implementation tasks, return a mutating action. "
                "Prefer action.type='write' with path and complete content. "
                "Do not return action.type='note' unless no safe mutation is possible."
            )
        user = {
            "round_index": round_index,
            "step": {
                "name": step.name,
                "fallback_action": fallback_action.__dict__,
                "allowed_tools": allowed,
                "guidance": self.STEP_GUIDANCE.get(step.name, "Perform this step conservatively and update relevant context buckets."),
            },
            "parent_context": parent_context,
            "output_schema": {
                "action": {
                    "type": "one of allowed_tools",
                    "rationale": "short reason",
                    "command": "for run",
                    "path": "for read/write",
                    "content": "for write/note",
                    "patch": "unified diff for apply_patch",
                    "query": "for web_search",
                    "urls": "for web_extract",
                    "max_results": "integer",
                },
                "bucket_updates": {name: ["short item"] for name in DEFAULT_CONTEXT_BUCKETS},
            },
        }
        try:
            response = self._chat_completion_with_retry(system, user)
            message = response.choices[0].message
            raw = getattr(message, "content", None) or ""
            data = extract_json_object(raw)
            action_data = data.get("action") or {}
            action_type = action_data.get("type") or fallback_action.type
            if action_type not in allowed:
                raise AutoResearchSafetyError(f"LLM step action {action_type!r} not in allowed_tools={allowed}")
        except Exception as exc:
            return AutoResearchStepResult(
                action=fallback_action,
                bucket_updates={"useful_failures": [f"{step.name} LLM fallback: {exc}"]},
                raw_response="",
                used_fallback=True,
                error=str(exc),
                system_prompt=system,
                user_payload=json.dumps(user, ensure_ascii=False),
            )
        action = AutoResearchAction(
            type=action_type,
            rationale=str(action_data.get("rationale") or fallback_action.rationale),
            command=str(action_data.get("command") or ""),
            path=str(action_data.get("path") or ""),
            content=str(action_data.get("content") or ""),
            patch=str(action_data.get("patch") or ""),
            query=str(action_data.get("query") or ""),
            urls=list(action_data.get("urls") or []),
            max_results=int(action_data.get("max_results") or 5),
            role=str(action_data.get("role") or fallback_action.role or ""),
        )
        # Preserve deterministic defaults for omitted action fields.
        for field_name in ("command", "path", "content", "patch", "query"):
            if not getattr(action, field_name):
                setattr(action, field_name, getattr(fallback_action, field_name))
        if not action.urls:
            action.urls = list(fallback_action.urls)
        bucket_updates = data.get("bucket_updates") or {}
        if not isinstance(bucket_updates, dict):
            bucket_updates = {}
        normalized = {}
        for key, values in bucket_updates.items():
            if key not in DEFAULT_CONTEXT_BUCKETS:
                key = "raw_observations"
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                normalized.setdefault(key, []).extend(str(v) for v in values if str(v).strip())
        return AutoResearchStepResult(
            action=action,
            bucket_updates=normalized,
            raw_response=raw,
            system_prompt=system,
            user_payload=json.dumps(user, ensure_ascii=False),
        )

    def _chat_completion_with_retry(self, system: str, user: dict):
        client = self._client()
        model = self._resolved_model()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]
        attempts = max(1, 1 + int(self.settings.llm_retry_attempts))
        timeout = float(self.settings.llm_request_timeout or 60)
        last_exc: Exception | None = None

        # Some OpenAI-compatible providers only accept the model default
        # temperature and reject explicit temperature=0. Avoid sending the
        # parameter for the default deterministic setting; callers that really
        # need sampling can still set a non-zero temperature.
        completion_kwargs = {"model": model, "messages": messages}
        if self.settings.llm_temperature not in (None, 0, 0.0):
            completion_kwargs["temperature"] = self.settings.llm_temperature

        for attempt in range(attempts):
            root = self.settings.root()
            phase = getattr(self.loop, "_current_phase", "") if self.loop is not None else ""
            step = (user.get("step") or {}).get("name") if isinstance(user, dict) else ""
            inflight_start(
                root,
                "llm",
                phase=phase,
                detail=f"{step or 'chat'} attempt {attempt + 1}/{attempts}",
                model=model,
                timeout_seconds=timeout,
                prompt_chars=sum(len(str(m.get("content", ""))) for m in messages),
            )
            try:
                def _call():
                    try:
                        return client.chat.completions.create(
                            **completion_kwargs,
                            timeout=timeout,
                        )
                    except TypeError:
                        # Older client shims may not accept timeout=
                        return client.chat.completions.create(**completion_kwargs)

                response = call_with_deadline(
                    _call,
                    timeout_seconds=timeout,
                    label=f"{step or 'chat'} attempt {attempt + 1}/{attempts}",
                )
                inflight_finish(root, "llm", phase=phase, detail=f"{step or 'chat'} attempt {attempt + 1}/{attempts}", model=model)
                return response
            except Exception as exc:
                inflight_finish(root, "llm", phase=phase, detail=f"{step or 'chat'} attempt {attempt + 1}/{attempts}", model=model, error=str(exc)[:500])
                last_exc = exc
        raise last_exc if last_exc else RuntimeError("LLM completion failed with no exception")


