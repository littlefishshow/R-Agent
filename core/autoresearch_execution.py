"""AutoResearch v2 — Phase D: Execute (P3) and Run (P4) handlers.

Design ref: AUTORESEARCH_DESIGN_v2.md §5.

Both phases are built around injectable callables so they are testable without
spinning up real sub-agents or training jobs:

- P3 Execute: derive a Todo list from ``.auto/plan.md``, run an ``execute_fn``
  per item, and enforce the **verification hard-constraint** — an item is only
  "done" if it returns ``verification == True``.  The parent is the single
  writer of project.md (children only touch their own ``.auto/``).
- P4 Run: run the project/experiment via ``run_fn`` with **bounded autofix** —
  at most ``max_autofix`` repair attempts before flagging ``major_error`` and
  letting the state machine jump to Evaluate.

Deterministic defaults use the loop's project-confined runner so the phases do
real work out of the box.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Callable, Optional

from core.autoresearch_memory import write_auto_note, read_auto_notes, append_lesson
from core.autoresearch_phases import PhaseContext, PhaseResult


# --------------------------------------------------------------------------- #
# Todo parsing
# --------------------------------------------------------------------------- #

_TODO_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")


def parse_todo_from_plan(root: str | Path) -> list[str]:
    """Extract Todo items from .auto/plan.md (numbered or bulleted lines)."""
    notes = read_auto_notes(root, max_files=5)
    plan = notes.get("plan.md", "")
    items: list[str] = []
    for line in plan.splitlines():
        m = _TODO_LINE.match(line)
        if m:
            items.append(m.group(1).strip())
    return items


# --------------------------------------------------------------------------- #
# P3 Execute
# --------------------------------------------------------------------------- #

ExecuteFn = Callable[[str, PhaseContext], dict]  # (todo_item, ctx) -> result dict


def _default_execute_fn(item: str, ctx: PhaseContext) -> dict:
    """Deterministic executor: apply any queued change spec, then verify.

    Verification = py_compile on changed .py files (a cheap smoke check).  When
    there is nothing queued, the item is recorded as planned-only and marked
    unverified so it does not silently count as done.
    """
    loop = ctx.loop
    root = Path(ctx.root)
    spec_path = root / ".autoresearch" / "proposed_change.json"
    if loop is None:
        return {"item": item, "status": "planned", "verification": False,
                "note": "no loop available; recorded as plan-only"}
    try:
        if spec_path.exists():
            action = loop._maybe_hydrate_apply_change("apply_change",
                                                      _note_action(item))
            if action.type != "apply_patch":
                return {"item": item, "status": "planned", "verification": False,
                        "note": "spec did not synthesize a patch"}
        else:
            action = _llm_execute_action(item, ctx)
            if action is None:
                return {"item": item, "status": "planned", "verification": False,
                        "note": "no queued change spec and no execute step agent available"}
            if action.type not in {"apply_patch", "write"}:
                return {"item": item, "status": "tried", "verification": False,
                        "note": f"execute step agent chose non-mutating action {action.type}; no change applied"}
        obs = loop.execute_action(action)
        verified = _verify_action_effect(loop, obs, action)
        return {"item": item, "status": obs.status, "verification": verified,
                "note": obs.summary[:400]}
    except Exception as exc:
        return {"item": item, "status": "failed", "verification": False, "note": str(exc)}



def _llm_execute_action(item: str, ctx: PhaseContext):
    """Ask the v2 step agent to materialize a plan item when no queued spec exists.

    This bridges the v2 persona plan (natural-language todo items) to the legacy
    safe action surface.  The parent loop still validates and executes the action
    inside the project boundary, including read-only eval protections.
    """
    loop = ctx.loop
    agent = getattr(loop, "step_agent", None) if loop is not None else None
    if agent is None:
        return None
    from core.autoresearch_loop import AutoResearchAction, AutoResearchWorkflowStep

    step = AutoResearchWorkflowStep(
        name="apply_change",
        action_type="note",
        rationale=f"execute todo: {item}",
        content=item,
        allowed_tools=("apply_patch", "write", "note", "read"),
    )
    fallback = AutoResearchAction(type="note", rationale="execute_no_safe_change", content=item)
    parent_context = _execute_parent_context(ctx.root, ctx.project_text, item)
    result = agent.plan_step(step=step, fallback_action=fallback, parent_context=parent_context, round_index=0)
    apply_updates = getattr(loop, "_apply_bucket_updates", None)
    if callable(apply_updates):
        apply_updates(getattr(result, "bucket_updates", {}) or {})
    return result.action


def _execute_parent_context(root: str | Path, project_text: str, item: str, max_chars: int = 12000) -> str:
    notes = read_auto_notes(root, max_files=8)
    parts = [
        "V2 execute phase: implement exactly one safe project-confined change for this todo.",
        f"Todo: {item}",
        "Forbidden: do not edit eval harness/read-only evaluation files.",
        "Prefer a full-file write for train-side scripts when exact patch context is uncertain.",
        "",
        "# project.md",
        project_text or "",
    ]
    for name, text in notes.items():
        parts.extend(["", f"# .auto/{name}", text])
    data = "\n".join(parts)
    return data[-max_chars:]

def _note_action(item: str):
    from core.autoresearch_loop import AutoResearchAction

    return AutoResearchAction(type="note", rationale="execute_apply_change", content=item)


def _verify_action_effect(loop, obs, action=None) -> bool:
    """Cheap verification for mutating actions."""
    if getattr(obs, "status", "") not in {"ok", "ok_metric_recovered"}:
        return False
    if action is not None and getattr(action, "type", "") == "write":
        path = str(getattr(action, "path", "") or "")
        if path.endswith(".py"):
            result = loop.runner.run("python3 -m py_compile " + shlex.quote(path))
            return result.get("returncode") == 0
        return bool(path)
    return _verify_changed_python(loop, obs)


def _verify_changed_python(loop, obs) -> bool:
    """Cheap verification: py_compile any changed .py files."""
    try:
        raw = Path(obs.artifact_path).read_text(encoding="utf-8") if obs.artifact_path else "{}"
        data = json.loads(raw)
        changed = data.get("changed_files") or []
    except Exception:
        changed = []
    py_files = [str(f) for f in changed if str(f).endswith(".py")]
    if not py_files:
        return obs.status == "ok"
    result = loop.runner.run("python3 -m py_compile " + " ".join(shlex.quote(f) for f in py_files))
    return result.get("returncode") == 0


def make_execute_handler(execute_fn: Optional[ExecuteFn] = None):
    fn = execute_fn or _default_execute_fn

    def handler(ctx: PhaseContext) -> PhaseResult:
        items = parse_todo_from_plan(ctx.root)
        if not items:
            items = ["(no explicit todo; execute current plan)"]
        results = []
        done = 0
        any_verified = False
        for item in items:
            res = fn(item, ctx) or {}
            results.append(res)
            # verification hard-constraint: only verified items count as done.
            if res.get("status") in {"ok", "done"} and res.get("verification"):
                done += 1
                any_verified = True
        # children write .auto/, parent is the single writer of project.md.
        write_auto_note(ctx.root, "execute_report", "# Execute Report\n\n" +
                        "\n".join(f"- {r.get('item')}: status={r.get('status')} verified={r.get('verification')} — {r.get('note','')}" for r in results))
        project_text = _append_change_record(ctx.project_text, f"executed {done}/{len(items)} todo items (verified)")

        # If nothing verified and something was attempted, treat as an execute-side
        # major error so the machine routes to Evaluate rather than Run.
        attempted = any(r.get("status") in {"ok", "done", "failed"} for r in results)
        major = attempted and not any_verified
        signals_update = {"major_error": True} if major else {}
        if major:
            append_lesson(ctx.root, kind="operational_error",
                          summary="execute produced no verified change", detail=json.dumps(results, ensure_ascii=False)[:2000])
        summary = f"execute: {done}/{len(items)} verified" + (" (major_error)" if major else "")
        return PhaseResult(project_text=project_text, signals_update=signals_update, summary=summary)

    return handler


def _append_change_record(project_text: str, line: str) -> str:
    marker = "## 改动记录"
    entry = f"- {line}"
    if marker not in project_text:
        return project_text.rstrip() + f"\n\n{marker}\n{entry}\n"
    head, _, rest = project_text.partition(marker)
    after = rest.split("\n## ", 1)
    body = f"{marker}\n{entry}\n"
    if len(after) == 2:
        return head + body + "\n## " + after[1]
    return head + body


# --------------------------------------------------------------------------- #
# P4 Run
# --------------------------------------------------------------------------- #

RunFn = Callable[[PhaseContext], dict]     # (ctx) -> {status, returncode, stdout, ...}
AutofixFn = Callable[[PhaseContext, dict], bool]  # (ctx, last_result) -> attempted_fix?


def _default_run_fn(ctx: PhaseContext) -> dict:
    """Run the project/experiment via the loop's confined runner."""
    loop = ctx.loop
    if loop is None:
        return {"status": "skipped", "returncode": None, "stdout": "no loop"}
    command = (
        "set -e; "
        "if [ -f train/train.sh ]; then bash train/train.sh; "
        "elif [ -f run.sh ]; then bash run.sh; "
        "else echo 'no train/run script found'; fi; "
        "if [ -f eval.sh ]; then bash eval.sh; fi"
    )
    from core.autoresearch_loop import AutoResearchAction, git_snapshot

    action = AutoResearchAction(type="run", rationale="v2 run experiment", command=command, role="trial")
    base_git = git_snapshot(loop.settings.root(), enabled=loop.settings.use_git_versioning)
    obs = loop.execute_action(action)
    recorder = getattr(loop, "_maybe_record_experiment", None)
    if callable(recorder):
        recorder(action, obs, base_git, "run_experiment")
    status = "ok" if obs.status in {"ok", "ok_metric_recovered"} else "failed"
    return {"status": status, "returncode": 0 if status == "ok" else 1,
            "stdout": obs.summary, "stderr": "", "artifact_path": obs.artifact_path}


def make_run_handler(run_fn: Optional[RunFn] = None, autofix_fn: Optional[AutofixFn] = None, *, max_autofix: int = 2):
    run = run_fn or _default_run_fn

    def handler(ctx: PhaseContext) -> PhaseResult:
        result = run(ctx)
        attempts = 0
        while result.get("status") == "failed" and attempts < max(0, int(max_autofix)):
            attempts += 1
            fixed = bool(autofix_fn(ctx, result)) if autofix_fn else False
            if not fixed:
                break
            result = run(ctx)
        major = result.get("status") == "failed"
        write_auto_note(ctx.root, "run_report",
                        f"# Run Report\n\nstatus={result.get('status')} returncode={result.get('returncode')} autofix_attempts={attempts}\n")
        if major:
            append_lesson(ctx.root, kind="operational_error",
                          summary=f"run failed after {attempts} autofix attempts",
                          detail=str(result.get("stderr") or result.get("stdout") or "")[:2000])
        signals_update = {"major_error": True} if major else {}
        summary = f"run: status={result.get('status')} autofix={attempts}" + (" (major_error)" if major else "")
        return PhaseResult(signals_update=signals_update, summary=summary)

    return handler


__all__ = [
    "parse_todo_from_plan",
    "make_execute_handler",
    "make_run_handler",
    "ExecuteFn",
    "RunFn",
    "AutofixFn",
]
