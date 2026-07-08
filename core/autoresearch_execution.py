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
    if loop is None or not spec_path.exists():
        return {"item": item, "status": "planned", "verification": False,
                "note": "no queued change spec; recorded as plan-only"}
    try:
        action = loop._maybe_hydrate_apply_change("apply_change",
                                                  _note_action(item))
        if action.type != "apply_patch":
            return {"item": item, "status": "planned", "verification": False,
                    "note": "spec did not synthesize a patch"}
        obs = loop.execute_action(action)
        verified = _verify_changed_python(loop, obs)
        return {"item": item, "status": obs.status, "verification": verified,
                "note": obs.summary[:400]}
    except Exception as exc:
        return {"item": item, "status": "failed", "verification": False, "note": str(exc)}


def _note_action(item: str):
    from core.autoresearch_loop import AutoResearchAction

    return AutoResearchAction(type="note", rationale="execute_apply_change", content=item)


def _verify_changed_python(loop, obs) -> bool:
    """Cheap verification: py_compile any changed .py files."""
    try:
        raw = Path(obs.artifact_path).read_text(encoding="utf-8") if obs.artifact_path else "{}"
        data = json.loads(raw)
        changed = data.get("changed_files") or []
    except Exception:
        changed = []
    py_files = [f for f in changed if str(f).endswith(".py")]
    if not py_files:
        return obs.status == "ok"
    result = loop.runner.run("python -m py_compile " + " ".join(py_files))
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
        "if [ -f train/train.sh ]; then bash train/train.sh; "
        "elif [ -f run.sh ]; then bash run.sh; "
        "elif [ -f eval.sh ]; then bash eval.sh; "
        "else echo 'no runnable script found'; fi"
    )
    result = loop.runner.run(command)
    status = "ok" if result.get("returncode") == 0 else "failed"
    return {"status": status, "returncode": result.get("returncode"),
            "stdout": result.get("stdout", ""), "stderr": result.get("stderr", "")}


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
