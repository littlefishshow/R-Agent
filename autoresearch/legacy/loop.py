from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from autoresearch.observability.debug import inflight_finish, inflight_start
from autoresearch.state.memory import append_lesson
from autoresearch.state.experiment_memory import write_experiment_memory
from autoresearch.observability.timeout import call_with_deadline

from autoresearch.legacy.context import AutoResearchArtifactStore, AutoResearchContextManager, ProjectConfinedCommandRunner
from autoresearch.legacy.types import (
    AutoResearchAction,
    AutoResearchObservation,
    AutoResearchSettings,
    AutoResearchStepResult,
    AutoResearchWorkflowStep,
    ContextBucket,
    DEFAULT_CONTEXT_BUCKETS,
    Decision,
)

from autoresearch.legacy.planners import (
    AutoResearchStepAgent,
    EvolutionaryAutoResearchPlanner,
    FixedAutoResearchPlanner,
    Planner,
    Summarizer,
)

from autoresearch.legacy.progress import AutoResearchProgressView
from autoresearch.legacy.services import (
    AutoResearchSafetyError,
    ProjectBoundary,
    _contains_parent_escape,
    _extract_change_spec,
    _git_worktree_clean,
    _make_unified_diff,
    _matches_readonly,
    _safe_slug,
    apply_patch_with_git,
    apply_unified_patch_limited,
    decide_experiment,
    extract_json_object,
    extract_metrics_from_text,
    extract_progress_percent,
    git_branch_trial,
    git_changed_files,
    git_commit_trial,
    git_safe_rollback_to_base,
    git_snapshot,
    normalize_planner_kind,
    normalize_versioning_policy,
    pareto_front,
    parse_primary_metric,
    save_project_diff,
    choose_best_experiment,
)

class AutoResearchLoop:
    """Lightweight loop for autoresearch projects.

    Each round: build bounded parent context -> choose one action -> execute in a
    project-confined child/tool surface -> archive raw output -> persist a compact
    observation into .autoresearch/state.json.
    """

    def __init__(self, settings: AutoResearchSettings, planner: Optional[Planner] = None, summarizer: Optional[Summarizer] = None, step_agent: AutoResearchStepAgent | None = None):
        self.settings = settings
        self.boundary = ProjectBoundary(settings.project_dir)
        self.context = AutoResearchContextManager(settings)
        self.artifacts = AutoResearchArtifactStore(settings)
        self.runner = ProjectConfinedCommandRunner(settings.project_dir, settings.command_timeout_seconds)
        self.budget = self._build_budget_ledger()
        self.model_tiers = self._build_model_tiers()
        self._current_phase = ""
        self.planner = planner or self._build_default_planner()
        self.summarizer = summarizer or self.default_summarizer
        self.step_agent = step_agent or (AutoResearchStepAgent(settings, loop=self) if settings.use_llm_step_agents else None)
        self._observations: list[AutoResearchObservation] = []
        self._step_agent_errors: list[str] = []
        self.progress = AutoResearchProgressView(settings)
        self._experiment_count = 0
        bind_loop = getattr(self.planner, "bind_loop", None)
        if callable(bind_loop):
            bind_loop(self)

    def _build_budget_ledger(self):
        from autoresearch.observability.budget import BudgetLedger, BudgetLimits

        limits = BudgetLimits(
            max_usd=float(self.settings.max_usd or 0.0),
            max_tokens=int(self.settings.max_tokens or 0),
            degrade_ratio=float(self.settings.budget_degrade_ratio or 0.8),
        )
        return BudgetLedger(self.settings.budget_file(), limits)

    def _build_model_tiers(self):
        from autoresearch.observability.budget import ModelTiers

        base = self.settings.llm_model or ""
        tiers = ModelTiers.from_env(base=base)
        # Explicit settings override env.
        if self.settings.model_tier_plan:
            tiers.plan = self.settings.model_tier_plan
        if self.settings.model_tier_exec:
            tiers.exec = self.settings.model_tier_exec
        if self.settings.model_tier_util:
            tiers.util = self.settings.model_tier_util
        return tiers

    def _build_default_planner(self) -> "Planner":
        if normalize_planner_kind(self.settings.planner_kind) == "evolutionary":
            return EvolutionaryAutoResearchPlanner()
        return FixedAutoResearchPlanner()

    def run(self, rounds: Optional[int] = None) -> dict:
        max_rounds = max(0, int(rounds if rounds is not None else self.settings.max_rounds))
        self._write_progress("running", "starting", 0, max_rounds)
        stopped_early = False
        for round_index in range(max_rounds):
            # Cooperative interrupt: a watcher (or the user's esc handler) can
            # drop a STOP sentinel in .autoresearch/ to end the loop cleanly at a
            # round boundary. All prior rounds are already persisted, so this
            # loses at most the not-yet-started round.
            if self._stop_requested():
                self._write_progress("stopped", "stopped_by_request", round_index, max_rounds)
                stopped_early = True
                break
            step = getattr(self.planner, "step_for_round", lambda _i: None)(round_index)
            step_name = getattr(step, "name", f"round_{round_index}")
            self._write_progress("running", step_name, round_index, max_rounds)
            parent_context = self.context.build_parent_context(self._observations)
            step_result = self._plan_step(parent_context, round_index)
            action = step_result.action
            self._capture_proposed_change_spec(step_name, action)
            action = self._maybe_hydrate_apply_change(step_name, action)
            step_result.action = action
            self._validate_step_tool_scope(action, round_index)
            self._apply_bucket_updates(step_result.bucket_updates)
            if self._is_experiment_action(action, step_name) and self._experiment_count >= max(0, int(self.settings.max_experiments)):
                observation = AutoResearchObservation(
                    "experiment_budget",
                    f"Skipped trial because max_experiments={self.settings.max_experiments} was reached",
                    "",
                    "skipped",
                )
                self._archive_useful_failure({"summary": observation.summary, "status": "skipped_budget"})
                base_git = {}
            else:
                base_git = git_snapshot(self.settings.root(), enabled=self.settings.use_git_versioning)
                observation = self.execute_action(action)
            self._observations.append(observation)
            self._persist_observation(observation)
            self._maybe_record_experiment(action, observation, base_git, step_name)
            self._write_round_trace(round_index, step_name, parent_context, step_result, observation)
            self._write_progress("running", step_name, round_index + 1, max_rounds)
            if action.type == "stop":
                break
        self._write_evolution_artifacts(self.context.load_state())
        final_status = "stopped" if stopped_early else "completed"
        self._write_progress(final_status, "stopped_by_request" if stopped_early else "done", len(self._observations) if stopped_early else max_rounds, max_rounds)
        return {
            "project_id": self.settings.project_id,
            "rounds_completed": len(self._observations),
            "stopped_early": stopped_early,
            "observations": [obs.compact(max_chars=1200) for obs in self._observations],
            "state_path": str(self.settings.state_file()),
            "artifact_dir": str(self.settings.artifacts_root()),
            "progress_path": str(self.settings.progress_file()),
            "best_path": str(self.settings.root() / ".autoresearch" / "best.json"),
            "pareto_front_path": str(self.settings.root() / ".autoresearch" / "pareto_front.json"),
            "active_context_path": str(self.settings.root() / ".autoresearch" / "active_context.md"),
            "versioning_policy": normalize_versioning_policy(self.settings.versioning_policy),
            "use_git_versioning": bool(self.settings.use_git_versioning),
            "step_agent_errors": list(self._step_agent_errors),
        }

    def _stop_requested(self) -> bool:
        """True if a STOP sentinel exists (cooperative interrupt / esc)."""
        try:
            return self.settings.stop_file().exists()
        except Exception:
            return False

    def _write_progress(self, status: str, current_step: str, round_index: int, total_rounds: int) -> None:
        try:
            self.progress.write(
                status=status,
                current_step=current_step,
                round_index=round_index,
                total_rounds=total_rounds,
                observations=self._observations,
                state=self.context.load_state(),
                artifact_dir=str(self.settings.artifacts_root()),
                step_agent_errors=self._step_agent_errors,
            )
        except Exception:
            pass

    def _plan_step(self, parent_context: str, round_index: int) -> AutoResearchStepResult:
        fallback_action = self.planner(parent_context, round_index)
        if not self.step_agent:
            return AutoResearchStepResult(action=fallback_action, used_fallback=True)
        step_getter = getattr(self.planner, "step_for_round", None)
        step = step_getter(round_index) if callable(step_getter) else None
        if step is None:
            return AutoResearchStepResult(action=fallback_action, used_fallback=True)
        try:
            result = self.step_agent.plan_step(
                step=step,
                fallback_action=fallback_action,
                parent_context=parent_context,
                round_index=round_index,
            )
            self._validate_step_tool_scope(result.action, round_index)
            return result
        except Exception as exc:
            msg = f"step_agent fallback at round {round_index} ({getattr(step, 'name', 'unknown')}): {exc}"
            self._step_agent_errors.append(msg)
            return AutoResearchStepResult(
                action=fallback_action,
                bucket_updates={"raw_observations": [msg]},
                used_fallback=True,
                error=str(exc),
            )

    def _write_round_trace(self, round_index, step_name, parent_context, step_result, observation) -> None:
        """Dump the full per-round LLM I/O + outcome for post-hoc debugging.

        Gated by settings.trace_rounds (default off) because it writes the entire
        parent context and prompt/response each round, which is verbose. When on,
        every round produces .autoresearch/round_traces/round_<NNN>_<step>.json so
        it is possible to see exactly what the LLM saw and replied, and why an
        action was chosen or fell back.
        """
        if not getattr(self.settings, "trace_rounds", False):
            return
        try:
            root = self.settings.trace_root()
            root.mkdir(parents=True, exist_ok=True)
            action = step_result.action
            trace = {
                "round_index": round_index,
                "step_name": step_name,
                "timestamp": time.strftime("%F %T"),
                "used_fallback": bool(step_result.used_fallback),
                "step_agent_error": step_result.error or "",
                "llm": {
                    "system_prompt": step_result.system_prompt or "",
                    "user_payload": step_result.user_payload or "",
                    "raw_response": step_result.raw_response or "",
                },
                "parent_context": parent_context,
                "chosen_action": {
                    "type": action.type,
                    "role": getattr(action, "role", ""),
                    "rationale": action.rationale,
                    "command": action.command,
                    "path": action.path,
                    "patch": action.patch,
                    "content_preview": (action.content or "")[:2000],
                },
                "bucket_updates": step_result.bucket_updates,
                "observation": {
                    "kind": observation.kind,
                    "status": observation.status,
                    "summary": observation.summary[:2000],
                    "artifact_path": observation.artifact_path,
                },
            }
            fname = f"round_{round_index:03d}_{_safe_slug(step_name)}.json"
            (root / fname).write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            # Tracing must never break the loop.
            pass

    def _apply_bucket_updates(self, bucket_updates: dict[str, list[str]]) -> None:
        if not bucket_updates:
            return
        state = self.context.load_state()
        for bucket_name, values in bucket_updates.items():
            if isinstance(values, str):
                values = [values]
            for value in values or []:
                self.context.add_to_bucket(state, bucket_name, str(value))
        self.context.save_state(state)

    _CHANGE_SPEC_STEPS = {"plan_change", "propose_experiment"}
    _APPLY_STEP_NAMES = {"apply_change"}

    def _proposed_change_path(self) -> Path:
        return self.settings.root() / ".autoresearch" / "proposed_change.json"

    def _capture_proposed_change_spec(self, step_name: str, action: AutoResearchAction) -> None:
        """If plan/propose step emitted a JSON change spec in note content, persist it.

        The spec is a lightweight escape hatch so deterministic and LLM step
        agents can queue a code change without producing a unified diff. Two
        forms are supported and validated in `_maybe_hydrate_apply_change`.
        """
        if step_name not in self._CHANGE_SPEC_STEPS or action.type != "note":
            return
        spec = _extract_change_spec(action.content or "")
        if not spec:
            return
        try:
            target_label = str(spec.get("path") or "")
            if not target_label:
                return
            # Reject obvious escapes before persisting; final resolution still
            # goes through ProjectBoundary in `_maybe_hydrate_apply_change`.
            self.boundary.resolve(target_label)
        except Exception:
            return
        path = self._proposed_change_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _maybe_hydrate_apply_change(self, step_name: str, action: AutoResearchAction) -> AutoResearchAction:
        """Upgrade apply_change note fallback into an apply_patch when a change spec is queued.

        Precedence:
        1. LLM emitted apply_patch with a real patch: pass through.
        2. LLM emitted note but proposed_change.json exists: synthesize a unified
           diff and switch to apply_patch.
        3. No spec available: keep the note (documents why no change was applied).
        """
        if step_name not in self._APPLY_STEP_NAMES:
            return action
        if action.type == "apply_patch" and (action.patch or action.content).strip():
            return action
        spec_path = self._proposed_change_path()
        if not spec_path.exists():
            return action
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            patch = self._change_spec_to_patch(spec)
        except Exception as exc:
            note_content = (action.content or "") + f"\napply_change: failed to synthesize patch from proposed_change.json: {exc}"
            return AutoResearchAction(
                type="note",
                rationale=action.rationale or "apply_change_synthesis_failed",
                content=note_content,
                role=action.role,
            )
        if not patch.strip():
            return action
        return AutoResearchAction(
            type="apply_patch",
            rationale=action.rationale or "apply_change_from_proposed_spec",
            patch=patch,
            role=action.role,
        )

    def _change_spec_to_patch(self, spec: dict) -> str:
        kind = str(spec.get("kind") or "").strip().lower()
        target_label = str(spec.get("path") or "").strip()
        if not target_label:
            raise AutoResearchSafetyError("proposed_change.json missing 'path'")
        target = self.boundary.resolve(target_label)
        if kind == "write":
            new_content = str(spec.get("content") or "")
            old_lines = target.read_text(encoding="utf-8").splitlines(keepends=True) if target.exists() else []
            new_lines = new_content.splitlines(keepends=True)
            return _make_unified_diff(target_label, old_lines, new_lines, is_new=not target.exists())
        if kind == "search_replace":
            if not target.exists():
                raise AutoResearchSafetyError(f"search_replace target does not exist: {target_label}")
            original = target.read_text(encoding="utf-8")
            old_snippet = str(spec.get("old") or "")
            new_snippet = str(spec.get("new") or "")
            if not old_snippet:
                raise AutoResearchSafetyError("search_replace requires non-empty 'old'")
            if old_snippet not in original:
                raise AutoResearchSafetyError(f"search_replace 'old' snippet not found in {target_label}")
            occurrences = original.count(old_snippet)
            if occurrences > 1:
                raise AutoResearchSafetyError(f"search_replace 'old' snippet is not unique in {target_label} ({occurrences} matches)")
            replaced = original.replace(old_snippet, new_snippet, 1)
            return _make_unified_diff(target_label, original.splitlines(keepends=True), replaced.splitlines(keepends=True), is_new=False)
        raise AutoResearchSafetyError(f"unsupported change spec kind: {kind!r}")

    def _validate_step_tool_scope(self, action: AutoResearchAction, round_index: int) -> None:
        allowed_getter = getattr(self.planner, "allowed_tools_for_round", None)
        if not callable(allowed_getter):
            return
        allowed = tuple(allowed_getter(round_index) or ())
        if allowed and action.type not in allowed:
            raise AutoResearchSafetyError(
                f"Workflow step {round_index} attempted action {action.type!r}, allowed={allowed}"
            )

    def execute_action(self, action: AutoResearchAction) -> AutoResearchObservation:
        try:
            if action.type == "run":
                result = self.runner.run(action.command)
                raw = json.dumps(result, ensure_ascii=False, indent=2)
                artifact = self.artifacts.save(kind="shell", rationale=action.rationale, content=raw, extension="json")
                status = "ok" if result.get("returncode") == 0 else "failed"
                # Robustness: a baseline/trial wrapper (often LLM-generated) may exit
                # nonzero because of a broken *summary* step even though train/eval
                # produced a valid metric. Do not let that mask a good experiment:
                # if a primary metric is parseable from the output, recover to ok.
                if status == "failed" and self._run_has_valid_metric(result, action):
                    status = "ok_metric_recovered"
                self._record_metric(action, raw, artifact, status)
                return AutoResearchObservation("shell", self.summarizer(action, raw), artifact, status)
            if action.type == "read":
                path = self.boundary.resolve(action.path)
                raw = path.read_text(encoding="utf-8")
                artifact = self.artifacts.save(kind="read", rationale=action.rationale, content=raw, extension="txt")
                self._record_metric(action, raw, artifact, "ok")
                return AutoResearchObservation("read", self.summarizer(action, raw), artifact, "ok")
            if action.type == "write":
                path = self.boundary.resolve(action.path)
                self._ensure_write_allowed(path)
                self._ensure_not_readonly_eval(path)
                old = path.read_text(encoding="utf-8") if path.exists() else ""
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(action.content, encoding="utf-8")
                raw = json.dumps({"path": str(path), "old_chars": len(old), "new_chars": len(action.content)}, ensure_ascii=False)
                artifact = self.artifacts.save(kind="write", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("write", self.summarizer(action, raw), artifact, "ok")
            if action.type == "apply_patch":
                result = apply_patch_with_git(self.settings.root(), action.patch or action.content, readonly_globs=self.settings.readonly_eval_globs)
                raw = json.dumps(result, ensure_ascii=False, indent=2)
                artifact = self.artifacts.save(kind="apply_patch", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("apply_patch", self.summarizer(action, raw), artifact, "ok")
            if action.type == "web_search":
                from tools.web_tools import web_search_tool

                raw = web_search_tool(action.query, limit=action.max_results)
                artifact = self.artifacts.save(kind="web_search", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("web_search", self.summarizer(action, raw), artifact, "ok")
            if action.type == "web_extract":
                from tools.web_tools import web_extract_tool

                raw = web_extract_tool(action.urls)
                artifact = self.artifacts.save(kind="web_extract", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("web_extract", self.summarizer(action, raw), artifact, "ok")
            if action.type == "note":
                artifact = self.artifacts.save(kind="note", rationale=action.rationale, content=action.content, extension="md")
                self._record_metric(action, action.content, artifact, "ok")
                return AutoResearchObservation("note", self.summarizer(action, action.content), artifact, "ok")
            if action.type == "stop":
                return AutoResearchObservation("stop", action.rationale or "Stopped by planner", "", "ok")
            raise AutoResearchSafetyError(f"Unsupported action type: {action.type}")
        except Exception as exc:
            raw = json.dumps({"error": str(exc), "action": action.__dict__}, ensure_ascii=False, indent=2, default=str)
            artifact = self.artifacts.save(kind="error", rationale=action.rationale or action.type, content=raw, extension="json")
            return AutoResearchObservation(action.type, f"Action failed: {exc}", artifact, "failed")

    def _run_has_valid_metric(self, result: dict, action: AutoResearchAction) -> bool:
        """True if a baseline/trial run yielded a parseable primary metric.

        Used to recover a nonzero-exit run only when the current command itself
        printed a parseable metric before a trailing summary/check failed.
        Deliberately do not recover from metrics.json here: that file may be a
        stale metric from an earlier run and can make a failed validation look
        successful.
        """
        role = getattr(action, "role", "")
        if role not in {"baseline", "trial"}:
            return False
        text = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
        return parse_primary_metric(text).get("metric") is not None

    def _record_metric(self, action: AutoResearchAction, raw: str, artifact_path: str, status: str) -> None:
        info = parse_primary_metric(raw)
        metric = info.get("metric")
        if metric is None:
            return
        state = self.context.load_state()
        baseline = state.get("baseline_metric")
        is_baseline = self._is_baseline_action(action)
        decision = decide_experiment(metric, None if is_baseline else baseline, bool(info.get("higher_is_better", True)))
        record = {
            "timestamp": time.strftime("%F %T"),
            "rationale": action.rationale,
            "metric_name": info.get("metric_name"),
            "metric": metric,
            "higher_is_better": info.get("higher_is_better"),
            "decision": decision,
            "artifact_path": artifact_path,
            "status": status,
        }
        state.setdefault("metrics", []).append(record)
        if is_baseline or baseline is None:
            state["baseline_metric"] = metric
        self.context.save_state(state)
        results_path = self.settings.root() / "results.tsv"
        header = "timestamp\trationale\tmetric_name\tmetric\thigher_is_better\tdecision\tartifact_path\tstatus\n"
        line = "\t".join(str(record[k]) for k in ["timestamp", "rationale", "metric_name", "metric", "higher_is_better", "decision", "artifact_path", "status"]) + "\n"
        if not results_path.exists():
            results_path.write_text(header + line, encoding="utf-8")
        else:
            with results_path.open("a", encoding="utf-8") as f:
                f.write(line)

    def _ensure_write_allowed(self, path: Path) -> None:
        for root in self.settings.allowed_file_write_roots or (".",):
            allowed = self.boundary.resolve(root)
            try:
                path.resolve().relative_to(allowed)
                return
            except ValueError:
                continue
        raise AutoResearchSafetyError(f"Write path is outside allowed roots: {path}")

    def _ensure_not_readonly_eval(self, path: Path) -> None:
        try:
            rel = str(Path(path).resolve().relative_to(self.settings.root()))
        except ValueError:
            return
        if _matches_readonly(rel, self.settings.readonly_eval_globs):
            raise AutoResearchSafetyError(
                f"write target is a read-only evaluation file: {rel} (requires user approval)"
            )

    def _persist_observation(self, obs: AutoResearchObservation) -> None:
        state = self.context.load_state()
        observations = list(state.get("observations") or [])
        observations.append(obs.compact(max_chars=1200))
        state["observations"] = observations[-100:]
        addition = f"- {time.strftime('%F %T')}: [{obs.status}/{obs.kind}] {obs.summary}"
        self.context.add_to_bucket(state, self._bucket_for_observation(obs), addition)
        existing = str(state.get("summary") or "").rstrip()
        state["summary"] = self.context._truncate((existing + "\n" + addition).strip(), self.settings.summary_char_budget)
        state["updated_at"] = time.time()
        self.context.save_state(state)

    @staticmethod
    def _is_experiment_action(action: AutoResearchAction, step_name: str = "") -> bool:
        if action.type != "run":
            return False
        if getattr(action, "role", "") == "trial":
            return True
        if getattr(action, "role", "") == "baseline":
            return False
        text = f"{step_name} {action.rationale} {action.command}".lower()
        return any(token in text for token in ("trial", "experiment", "run_experiment"))

    @staticmethod
    def _is_baseline_action(action: AutoResearchAction) -> bool:
        role = getattr(action, "role", "")
        if role == "baseline":
            return True
        if role == "trial":
            return False
        return "baseline" in (action.rationale or "").lower()

    _SOURCE_SNAPSHOT_SUFFIXES = (".py", ".sh", ".json", ".md", ".txt", ".yaml", ".yml", ".toml")
    _SOURCE_SNAPSHOT_SKIP_DIRS = {".git", ".autoresearch", ".auto", "__pycache__", "outputs", ".pytest_cache"}

    def _source_snapshot_files(self) -> list[str]:
        root = self.settings.root()
        candidates: list[str] = []
        for rel in ("solution.py", "train/train.py", "train/train.sh", "submission/solver.py", "submission/matcher.py", "submission/cleaner.py"):
            if (root / rel).is_file():
                candidates.append(rel)
        for base_rel in ("train", "submission"):
            base = root / base_rel
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                try:
                    rel = str(path.relative_to(root))
                except ValueError:
                    continue
                if any(part in self._SOURCE_SNAPSHOT_SKIP_DIRS for part in Path(rel).parts):
                    continue
                if path.suffix.lower() in self._SOURCE_SNAPSHOT_SUFFIXES and rel not in candidates:
                    candidates.append(rel)
                if len(candidates) >= 60:
                    break
        return candidates[:60]

    def _save_source_snapshot(self, experiment_id: str) -> str:
        root = self.settings.root()
        files = {}
        for rel in self._source_snapshot_files():
            path = root / rel
            try:
                files[rel] = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
        if not files:
            return ""
        return self.artifacts.save(
            kind="source_snapshot",
            rationale=experiment_id,
            content=json.dumps({"files": files}, ensure_ascii=False, indent=2) + "\n",
            extension="json",
        )

    def _restore_source_snapshot(self, snapshot_path: str) -> dict:
        if not snapshot_path:
            return {"restored": [], "error": "no snapshot"}
        path = Path(snapshot_path)
        if not path.exists():
            return {"restored": [], "error": "snapshot missing"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"restored": [], "error": str(exc)}
        files = data.get("files") if isinstance(data, dict) else {}
        if not isinstance(files, dict):
            return {"restored": [], "error": "invalid snapshot"}
        restored = []
        for rel, content in files.items():
            try:
                target = self.boundary.resolve(str(rel))
                self._ensure_not_readonly_eval(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")
                restored.append(str(rel))
            except Exception:
                continue
        return {"restored": restored, "error": ""}

    def _restore_best_source_snapshot(self, state: dict) -> None:
        best = state.get("best_experiment") if isinstance(state, dict) else None
        if not isinstance(best, dict) or not best.get("source_snapshot_path"):
            return
        # Guard: never revert a workspace that is actually as good as (or better
        # than) the recorded best. If the current on-disk solution has just been
        # improved but not yet promoted to best (e.g. its validation checkpoint
        # has not scored it yet), a blind restore would destroy real progress and
        # trap the loop at baseline forever. Only revert when the current official
        # metric is *known* and *strictly worse* than best.
        guard = self._best_restore_guard(state, best)
        if guard.get("skip"):
            state["best_restore"] = {
                "experiment_id": best.get("experiment_id", ""),
                "source_snapshot_path": best.get("source_snapshot_path", ""),
                "restored": [],
                "skipped_reason": guard.get("reason", "current workspace not worse than best"),
                "current_metric": guard.get("current_metric"),
                "best_metric": guard.get("best_metric"),
                "updated_at": time.time(),
            }
            return
        current = self._save_source_snapshot("current-before-best-restore")
        if current:
            state["pre_restore_source_snapshot_path"] = current
        result = self._restore_source_snapshot(best.get("source_snapshot_path", ""))
        state["best_restore"] = {
            "experiment_id": best.get("experiment_id", ""),
            "source_snapshot_path": best.get("source_snapshot_path", ""),
            "current_metric": guard.get("current_metric"),
            "best_metric": guard.get("best_metric"),
            **result,
            "updated_at": time.time(),
        }

    def _best_restore_guard(self, state: dict, best: dict) -> dict:
        """Decide whether the best-source restore should be skipped.

        Returns {"skip": bool, "reason", "current_metric", "best_metric"}.

        The restore is skipped when the current workspace's official metric is
        unavailable (we must not clobber unknown/possibly-better work) or is not
        strictly worse than best. It proceeds only when the current solution is
        demonstrably worse than the recorded best.
        """
        primary_name = str(best.get("primary_metric_name") or "").strip()
        best_metrics = best.get("metrics") if isinstance(best.get("metrics"), dict) else {}
        if not primary_name:
            primary_name = next(iter(best_metrics or {}), "")
        best_metric = best_metrics.get(primary_name) if primary_name else None
        try:
            best_metric = float(best_metric)
        except Exception:
            best_metric = None
        # Refresh the current workspace's official metric before deciding.
        current_metric, higher = self._current_official_metric(primary_name)
        if best_metric is None or current_metric is None:
            # Missing information on either side: default to preserving current
            # work rather than reverting blindly.
            return {"skip": True, "reason": "metric unavailable; preserving current workspace",
                    "current_metric": current_metric, "best_metric": best_metric}
        worse = (current_metric < best_metric) if higher else (current_metric > best_metric)
        if not worse:
            return {"skip": True, "reason": "current workspace not worse than best",
                    "current_metric": current_metric, "best_metric": best_metric}
        return {"skip": False, "reason": "current worse than best",
                "current_metric": current_metric, "best_metric": best_metric}

    def _current_official_metric(self, primary_name: str) -> tuple[float | None, bool]:
        """Run the official eval (if present) and read the current primary metric.

        Returns (metric, higher_is_better). Reads metrics.json after eval so the
        decision reflects the solution currently on disk, not a stale file.
        """
        root = self.settings.root()
        if (root / "eval.sh").exists():
            try:
                self.execute_action(AutoResearchAction(
                    type="run",
                    rationale="eval_current_before_best_restore",
                    command="bash eval.sh && cat metrics.json",
                ))
            except Exception:
                pass
        metrics_path = root / "metrics.json"
        higher = True
        if not metrics_path.exists():
            return None, higher
        try:
            data = json.loads(metrics_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None, higher
        if not isinstance(data, dict):
            return None, higher
        hib = data.get("higher_is_better", True)
        higher = bool(hib) if isinstance(hib, bool) else str(hib).strip().lower() in {"true", "1", "yes"}
        value = data.get(primary_name) if primary_name else None
        if value is None:
            value = data.get("primary_metric", data.get("z"))
        try:
            return float(value), higher
        except Exception:
            return None, higher

    def _refresh_metrics_after_best_restore(self, state: dict) -> None:
        """Refresh official metrics after restoring the best source snapshot."""
        restore = state.get("best_restore") if isinstance(state, dict) else None
        if not isinstance(restore, dict) or not restore.get("restored"):
            return
        if not (self.settings.root() / "eval.sh").exists():
            return
        try:
            obs = self.execute_action(AutoResearchAction(
                type="run",
                rationale="refresh_metrics_after_best_restore",
                command="bash eval.sh && cat metrics.json",
            ))
            restore["metric_refresh"] = {
                "status": obs.status,
                "summary": obs.summary[:1000],
                "artifact_path": obs.artifact_path,
                "updated_at": time.time(),
            }
        except Exception as exc:
            restore["metric_refresh"] = {
                "status": "failed",
                "error": str(exc)[:1000],
                "updated_at": time.time(),
            }

    def _maybe_record_experiment(self, action: AutoResearchAction, obs: AutoResearchObservation, base_git: dict, step_name: str) -> None:
        if not self._is_experiment_action(action, step_name):
            return
        if self._experiment_count >= max(0, int(self.settings.max_experiments)):
            self._archive_useful_failure({
                "summary": f"Skipped experiment record because max_experiments={self.settings.max_experiments} was reached",
                "action": action.__dict__,
                "status": "skipped_budget",
            })
            return
        self._experiment_count += 1
        state = self.context.load_state()
        existing = list(state.get("experiments") or [])
        experiment_id = f"exp-{len(existing) + 1:04d}-{int(time.time())}"
        raw = ""
        if obs.artifact_path and Path(obs.artifact_path).exists():
            raw = Path(obs.artifact_path).read_text(encoding="utf-8", errors="replace")
        program_text = self.context.read_program()
        metrics, directions = extract_metrics_from_text(raw, program_text)
        file_metrics, file_directions = self._collect_metric_files()
        metrics.update(file_metrics)
        directions.update(file_directions)
        primary = parse_primary_metric(raw)
        file_primary_name = self._primary_metric_name_from_file_metrics()
        primary_name = file_primary_name or (
            primary.get("metric_name") if primary.get("metric") is not None else next(iter(metrics), None)
        )
        primary_metric = metrics.get(primary_name) if primary_name else None
        baseline = state.get("baseline_metric")
        decision = decide_experiment(primary_metric, baseline, directions.get(str(primary_name), True)) if primary_name else ("failed" if obs.status == "failed" else "needs_metrics")
        policy = normalize_versioning_policy(self.settings.versioning_policy)
        git_after = git_snapshot(self.settings.root(), enabled=self.settings.use_git_versioning)
        git_available = bool(git_after.get("git_available"))
        changed_files = git_changed_files(self.settings.root()) if git_available else []
        diff_path = save_project_diff(self.settings.root(), self.artifacts, experiment_id, git_available=git_available)
        source_snapshot_path = self._save_source_snapshot(experiment_id)
        base_commit = base_git.get("head", "") if isinstance(base_git, dict) else ""
        base_clean = _git_worktree_clean(base_git)
        record = {
            "experiment_id": experiment_id,
            "created_at": time.time(),
            "timestamp": time.strftime("%F %T"),
            "hypothesis": action.rationale,
            "summary": obs.summary[:1200],
            "metrics": metrics,
            "metric_directions": directions,
            "primary_metric_name": primary_name,
            "status": obs.status,
            "decision": decision,
            "changed_files": changed_files,
            "diff_path": diff_path,
            "source_snapshot_path": source_snapshot_path,
            "artifact_path": obs.artifact_path,
            "git_commit": "",
            "commit_sha": "",
            "branch": "",
            "base_commit": base_commit,
            "git_available": git_available,
            "git_status_before": base_git.get("status", "") if isinstance(base_git, dict) else "",
            "git_status_after": git_after.get("status", "") if isinstance(git_after, dict) else "",
            "version_policy": policy,
            "version_action": "artifact_only",
            "rollback_status": "not_needed",
            "version_error": "",
        }
        existing.append(record)
        state["experiments"] = existing[-max(1, int(self.settings.max_experiments)) :]
        self.context.save_state(state)
        if not getattr(self.settings, "defer_experiment_finalization", False):
            self.finalize_experiments()

    def finalize_experiments(self) -> dict:
        """Finalize recorded experiments: best/Pareto, versioning, rollback, lessons.

        V3 calls this from Conclude. Legacy AutoResearchLoop.run calls it
        immediately after recording to preserve existing behavior.
        """
        state = self.context.load_state()
        experiments = list(state.get("experiments") or [])
        if not experiments:
            state["pareto_front"] = []
            state["best_experiment"] = None
            state["last_finalized_experiment_count"] = 0
            self.context.save_state(state)
            self._write_evolution_artifacts(state)
            return {"finalized": 0, "best_experiment": None, "pareto_count": 0}

        all_directions = {}
        primary_name = ""
        for exp in experiments:
            all_directions.update(exp.get("metric_directions") or {})
            if exp.get("primary_metric_name"):
                primary_name = exp.get("primary_metric_name")
        front = pareto_front(experiments, all_directions, self.settings.max_pareto_items)
        best = choose_best_experiment(experiments, all_directions, primary_name)
        state["pareto_front"] = front
        state["best_experiment"] = best

        finalized = 0
        for record in experiments:
            if record.get("version_finalized"):
                continue
            self._finalize_experiment_record(record, state, front, best)
            finalized += 1
        state["experiments"] = experiments[-max(1, int(self.settings.max_experiments)) :]
        state["last_finalized_experiment_count"] = len(state["experiments"])
        feedback = self._search_feedback_digest()
        if feedback:
            self.context.add_to_bucket(state, "experiment_results", feedback)
        if normalize_versioning_policy(self.settings.versioning_policy) == "artifact_only" and not bool(self.settings.use_git_versioning):
            self._restore_best_source_snapshot(state)
            self._refresh_metrics_after_best_restore(state)
        write_experiment_memory(self.settings.root(), state=state)
        self.context.save_state(state)
        self._write_evolution_artifacts(state)
        return {"finalized": finalized, "best_experiment": best, "pareto_count": len(front)}

    def _finalize_experiment_record(self, record: dict, state: dict, front: list[dict], best: dict | None) -> None:
        policy = normalize_versioning_policy(record.get("version_policy") or self.settings.versioning_policy)
        git_available = bool(record.get("git_available"))
        base_clean = not str(record.get("git_status_before") or "").strip() and git_available
        base_commit = str(record.get("base_commit") or "")
        experiment_id = str(record.get("experiment_id") or "")
        front_ids = {str(item.get("experiment_id")) for item in front or []}
        best_id = str((best or {}).get("experiment_id") or "")
        has_metrics = bool(record.get("metrics"))
        invalid = record.get("status") == "failed" or record.get("decision") in {"needs_metrics", "failed"} or not has_metrics
        pareto_kept = experiment_id in front_ids or experiment_id == best_id
        should_commit = False
        should_branch = False
        should_rollback = False
        if not git_available or not self.settings.use_git_versioning:
            record["version_action"] = "artifact_only_disabled" if not self.settings.use_git_versioning else "artifact_only_no_git"
            record["rollback_status"] = "skipped_no_git"
        elif policy == "artifact_only":
            record["version_action"] = "artifact_only"
            record["rollback_status"] = "skipped_artifact_only"
        elif not base_clean:
            record["version_action"] = "artifact_only_dirty_base"
            record["rollback_status"] = "skipped_dirty_base"
        else:
            if policy == "commit_all_trials":
                should_commit = not invalid
                should_rollback = invalid
            elif policy == "commit_pareto":
                should_commit = (not invalid) and pareto_kept
                should_rollback = not should_commit
            elif policy == "branch_per_trial":
                should_branch = not invalid
                should_rollback = invalid
            if should_commit:
                result = git_commit_trial(self.settings.root(), experiment_id, record.get("hypothesis", ""))
                record["version_action"] = result.get("action", "commit_attempted")
                record["commit_sha"] = result.get("commit_sha", "")
                record["git_commit"] = record["commit_sha"]
                record["branch"] = result.get("branch", "")
                record["version_error"] = result.get("error", "")
                if result.get("action") in {"commit_failed", "committed_branch_failed"}:
                    should_rollback = True
            elif should_branch:
                result = git_branch_trial(self.settings.root(), experiment_id, record.get("hypothesis", ""), base_commit)
                record["version_action"] = result.get("action", "branch_attempted")
                record["commit_sha"] = result.get("commit_sha", "")
                record["git_commit"] = record["commit_sha"]
                record["branch"] = result.get("branch", "")
                record["rollback_status"] = result.get("rollback_status", "not_needed")
                record["version_error"] = result.get("error", "")
                should_rollback = False
            else:
                record["version_action"] = "artifact_only_not_selected"
            if should_rollback:
                rollback = git_safe_rollback_to_base(self.settings.root(), base_commit)
                record["rollback_status"] = rollback.get("status", "unknown")
                record["version_error"] = record.get("version_error", "") or rollback.get("error", "")
        git_final = git_snapshot(self.settings.root(), enabled=self.settings.use_git_versioning)
        record["git_status_final"] = git_final.get("status", "") if isinstance(git_final, dict) else ""
        record["version_summary"] = f"policy={policy} action={record.get('version_action')} rollback={record.get('rollback_status')} commit={record.get('commit_sha','')} branch={record.get('branch','')}"
        record["version_finalized"] = True
        if invalid or record.get("decision") in {"discard", "needs_metrics", "failed"}:
            self._archive_useful_failure(record, state=state)
        self.context.add_to_bucket(state, "current_changes", f"Versioning: {record['version_summary']} diff={record.get('diff_path', '')}")
        if not record.get("lesson_recorded"):
            lesson_kind = "operational_error" if record.get("status") == "failed" else (
                "insight" if record.get("experiment_id") in {str(item.get("experiment_id")) for item in front or []} else "dead_end"
            )
            append_lesson(
                self.settings.root(),
                kind=lesson_kind,
                summary=(
                    f"{record.get('experiment_id')}: decision={record.get('decision')} "
                    f"metrics={record.get('metrics')} version={record.get('version_action')} "
                    f"rollback={record.get('rollback_status')}"
                ),
                detail=str(record.get("summary") or "")[:2000],
                experiment_id=str(record.get("experiment_id") or ""),
            )
            record["lesson_recorded"] = True

    def _search_feedback_digest(self) -> str:
        """Compact digest of a train-side search's own summary/history, if present.

        A self-iterating search script (the pattern we now encourage) tends to
        write outputs/train_search_summary.json + train_search_history.jsonl.
        Surfacing best_z, best point, eval_count and the sampled range back into
        the experiment_results bucket is what lets the NEXT round's planner see
        "range too small / stuck at boundary" and rewrite a better script.
        """
        root = self.settings.root()
        summary_path = root / "outputs" / "train_search_summary.json"
        if not summary_path.exists():
            return ""
        try:
            s = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        parts = []
        for k in ("best_z", "best_x", "best_y", "eval_count", "status"):
            if k in s:
                parts.append(f"{k}={s[k]}")
        # Add sampled x/y range from history so the planner can judge coverage.
        hist_path = root / "outputs" / "train_search_history.jsonl"
        if hist_path.exists():
            try:
                xs, ys = [], []
                for line in hist_path.read_text(encoding="utf-8").splitlines()[-2000:]:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if isinstance(r.get("x"), (int, float)):
                        xs.append(float(r["x"]))
                    if isinstance(r.get("y"), (int, float)):
                        ys.append(float(r["y"]))
                if xs and ys:
                    parts.append(f"sampled_x_range=[{min(xs):g},{max(xs):g}]")
                    parts.append(f"sampled_y_range=[{min(ys):g},{max(ys):g}]")
            except Exception:
                pass
        if not parts:
            return ""
        return "Search feedback: " + " ".join(parts) + " (if not converged, widen range/increase budget/refine locally next round)"

    def _collect_metric_files(self) -> tuple[dict[str, float], dict[str, bool]]:
        root = self.settings.root()
        metrics: dict[str, float] = {}
        directions: dict[str, bool] = {}
        candidates = [root / "metrics.json", root / "results.json", root / ".autoresearch" / "metrics.json"]
        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
            except Exception:
                continue
            parsed, dirs = extract_metrics_from_text(text, self.context.read_program())
            metrics.update(parsed)
            directions.update(dirs)
        results_tsv = root / "results.tsv"
        if results_tsv.exists():
            try:
                parsed, dirs = extract_metrics_from_text(results_tsv.read_text(encoding="utf-8", errors="replace")[-100_000:], self.context.read_program())
                metrics.update(parsed); directions.update(dirs)
            except Exception:
                pass
        return metrics, directions

    def _primary_metric_name_from_file_metrics(self) -> str:
        root = self.settings.root()
        for rel in ("metrics.json", "results.json", ".autoresearch/metrics.json"):
            path = root / rel
            if not path.exists() or not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace")[:200_000])
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            name = str(data.get("primary_metric_name") or data.get("metric_name") or "").strip()
            if name:
                return name
        return ""

    def _archive_useful_failure(self, record: dict, state: dict | None = None) -> None:
        own_state = state if state is not None else self.context.load_state()
        failure = {
            "experiment_id": record.get("experiment_id", ""),
            "timestamp": record.get("timestamp", time.strftime("%F %T")),
            "summary": str(record.get("summary") or record.get("hypothesis") or record.get("status") or "")[:800],
            "decision": record.get("decision", record.get("status", "failed")),
            "artifact_path": record.get("artifact_path", ""),
            "diff_path": record.get("diff_path", ""),
            "version_policy": record.get("version_policy", ""),
            "version_action": record.get("version_action", ""),
            "commit_sha": record.get("commit_sha", record.get("git_commit", "")),
            "branch": record.get("branch", ""),
            "rollback_status": record.get("rollback_status", ""),
        }
        useful = list(own_state.get("useful_failures") or [])
        useful.append(failure)
        own_state["useful_failures"] = useful[-max(0, int(self.settings.max_useful_failures)) :]
        if state is None:
            self.context.save_state(own_state)

    def _write_evolution_artifacts(self, state: dict) -> None:
        root = self.settings.root() / ".autoresearch"
        root.mkdir(parents=True, exist_ok=True)
        best = state.get("best_experiment")
        front = state.get("pareto_front") or []
        (root / "best.json").write_text(json.dumps(best or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "pareto_front.json").write_text(json.dumps(front, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._write_active_context(state)

    def _write_active_context(self, state: dict) -> None:
        root = self.settings.root() / ".autoresearch"
        budget = max(1000, int(self.settings.max_active_context_chars or 8000))
        recent_experiments = state.get("experiments") or []
        last_version = recent_experiments[-1].get("version_summary", "") if recent_experiments else ""
        lines = [
            f"# Active autoresearch context — {self.settings.project_id}",
            "",
            "This is a compressed working context. Raw logs and full history stay in artifacts/state.",
            "",
            "## Versioning",
            f"- policy: `{normalize_versioning_policy(self.settings.versioning_policy)}`",
            f"- last action: {last_version or '(none yet)'}",
            "",
        ]
        best = state.get("best_experiment") or {}
        if best:
            lines.extend([
                "## Best experiment",
                f"- id: {best.get('experiment_id')}",
                f"- decision/status: {best.get('decision')} / {best.get('status')}",
                f"- metrics: {json.dumps(best.get('metrics') or {}, ensure_ascii=False)}",
                f"- diff: {best.get('diff_path', '')}",
                "",
            ])
        lines.append("## Pareto front")
        for item in (state.get("pareto_front") or [])[: self.settings.max_pareto_items]:
            lines.append(f"- {item.get('experiment_id')}: decision={item.get('decision')} metrics={json.dumps(item.get('metrics') or {}, ensure_ascii=False)} diff={item.get('diff_path','')} version={item.get('version_summary') or item.get('version_action','')}")
        if not (state.get("pareto_front") or []):
            lines.append("- (no metric-bearing Pareto candidates yet)")
        lines.extend(["", "## Useful failures / discarded rounds"])
        for item in (state.get("useful_failures") or [])[-self.settings.max_useful_failures :]:
            lines.append(f"- {item.get('experiment_id','')}: {item.get('decision')} — {item.get('summary','')[:240]} artifact={item.get('artifact_path','')} version={item.get('version_action','')} rollback={item.get('rollback_status','')}")
        lines.extend(["", "## Recent conclusions"])
        for item in ((state.get("buckets") or {}).get("conclusions") or [])[-3:]:
            lines.append(f"- {item}")
        text = "\n".join(lines).strip() + "\n"
        if len(text) > budget:
            text = text[: budget - 40].rstrip() + "\n...<active context clipped>...\n"
        (root / "active_context.md").write_text(text, encoding="utf-8")

    @staticmethod
    def _bucket_for_observation(obs: AutoResearchObservation) -> str:
        text = f"{obs.kind} {obs.summary}".lower()
        if "conclusion" in text or "summary" in text:
            return "conclusions"
        if "modification_plan" in text or "plan" in text:
            return "modification_plans"
        if "experiment_result" in text or "eval" in text or "metric" in text or "train" in text:
            return "experiment_results"
        if "change" in text or "write" in text or "diff" in text:
            return "current_changes"
        if "question" in text or "unknown" in text:
            return "open_questions"
        if "project_understanding" in text or "inspect" in text or "program" in text:
            return "project_understanding"
        return "raw_observations"

    @staticmethod
    def default_summarizer(action: AutoResearchAction, raw: str) -> str:
        preview = raw.strip().replace("\r", "")
        metric_info = parse_primary_metric(raw)
        decision = decide_experiment(metric_info.get("metric"), None, bool(metric_info.get("higher_is_better", True)))
        progress = extract_progress_percent(raw)
        extras = []
        if metric_info.get("metric") is not None:
            extras.append(f"metric={metric_info['metric']} {metric_info['metric_name']} decision={decision}")
        if progress is not None:
            extras.append(f"progress={progress}%")
        if len(preview) > 900:
            preview = preview[:897].rstrip() + "..."
        suffix = ("; " + "; ".join(extras)) if extras else ""
        return f"{action.type} rationale={action.rationale!r}{suffix}; raw_preview={preview}"

    @staticmethod
    def default_planner(parent_context: str, round_index: int) -> AutoResearchAction:
        if round_index == 0:
            return AutoResearchAction(
                type="run",
                rationale="bootstrap_inspect_project",
                command="pwd && find . -maxdepth 2 -type f | sort | head -80",
            )
        return AutoResearchAction(type="stop", rationale="default planner completed bootstrap")


__all__ = [
    "AutoResearchAction",
    "AutoResearchArtifactStore",
    "AutoResearchContextManager",
    "AutoResearchLoop",
    "AutoResearchObservation",
    "AutoResearchStepAgent",
    "AutoResearchStepResult",
    "AutoResearchWorkflowStep",
    "ContextBucket",
    "DEFAULT_CONTEXT_BUCKETS",
    "AutoResearchProgressView",
    "FixedAutoResearchPlanner",
    "decide_experiment",
    "extract_json_object",
    "extract_progress_percent",
    "parse_primary_metric",
    "apply_unified_patch_limited",
    "apply_patch_with_git",
    "AutoResearchSafetyError",
    "AutoResearchSettings",
    "ProjectBoundary",
    "ProjectConfinedCommandRunner",
    "normalize_planner_kind",
    "normalize_versioning_policy",
    "git_snapshot",
    "save_project_diff",
]
