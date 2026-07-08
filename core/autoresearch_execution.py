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

# Numbered ("1." / "1)" / "1 -"), bulleted ("-" / "*" / "•"), or "Step N:" items.
_TODO_LINE = re.compile(
    r"^\s*(?:"
    r"\d+\s*[.)\-:]"          # 1.  1)  1-  1:
    r"|[-*•]"                  # -  *  •
    r"|[Ss]tep\s+\d+\s*[:.)-]"  # Step 1:  step 2.
    r")\s+(.*\S)\s*$"
)


def parse_todo_from_plan(root: str | Path) -> list[str]:
    """Extract Todo items from .auto/plan.md (numbered, bulleted, or 'Step N:').

    Falls back to the whole non-header body as a single item when no structured
    list is found, so a differently formatted plan still gives Execute something
    concrete to act on instead of a vague "execute current plan".
    """
    notes = read_auto_notes(root, max_files=5)
    plan = notes.get("plan.md", "")
    items: list[str] = []
    for line in plan.splitlines():
        m = _TODO_LINE.match(line)
        if m:
            text = m.group(1).strip()
            if text:
                items.append(text)
    if items:
        return items
    # Fallback: treat the plan body (minus markdown headers/blank lines) as one
    # actionable block rather than losing the plan entirely.
    body = [ln.strip() for ln in plan.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if body:
        return [" ".join(body)]
    return []


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
        # A code-writing action that cannot be verified did not really land
        # (e.g. git apply reported success but wrote nothing in a gitignored
        # nested dir). Do not let it pass as ok — downgrade to failed so the
        # handler counts it as not-done and the loop stops spinning on baseline.
        status = obs.status
        if action.type in {"apply_patch", "write"} and not verified and status in {"ok", "ok_metric_recovered"}:
            status = "failed"
        note = obs.summary[:400]
        if status == "failed" and obs.status in {"ok", "ok_metric_recovered"}:
            note = "unverified change (files not written on disk); " + note
        return {"item": item, "status": status, "verification": verified, "note": note}
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
        allowed_tools=("write", "apply_patch", "note", "read"),
    )
    fallback = AutoResearchAction(type="note", rationale="execute_no_safe_change", content=item)
    parent_context = _execute_parent_context(ctx.root, ctx.project_text, item)
    result = agent.plan_step(step=step, fallback_action=fallback, parent_context=parent_context, round_index=0)
    apply_updates = getattr(loop, "_apply_bucket_updates", None)
    if callable(apply_updates):
        apply_updates(getattr(result, "bucket_updates", {}) or {})
    return result.action


_TRAIN_SIDE_DIRS = ("train", "src", "scripts")
_INVENTORY_SUFFIXES = (".py", ".sh", ".json", ".yaml", ".yml", ".toml", ".cfg")
_INVENTORY_SKIP_DIRS = {".git", ".autoresearch", ".auto", "__pycache__", "outputs", "logs", ".venv", "venv", "node_modules"}


def _train_side_inventory(root: str | Path, max_files: int = 40) -> list[str]:
    """List existing editable train-side files (relative paths) so the executor
    edits what already exists instead of spawning parallel helpers each round."""
    root = Path(root)
    found: list[str] = []
    for d in _TRAIN_SIDE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _INVENTORY_SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in _INVENTORY_SUFFIXES:
                continue
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                continue
            size = path.stat().st_size
            found.append(f"{rel} ({size}B)")
            if len(found) >= max_files:
                return found
    return found


def _execute_parent_context(root: str | Path, project_text: str, item: str, max_chars: int = 12000) -> str:
    notes = read_auto_notes(root, max_files=8)
    inventory = _train_side_inventory(root)
    inv_block = "\n".join(f"- {f}" for f in inventory) if inventory else "(no train-side files yet)"
    parts = [
        "V2 execute phase: implement exactly one safe project-confined change for this todo.",
        f"Todo: {item}",
        "Forbidden: do not edit eval harness/read-only evaluation files.",
        "",
        "## Keep the change surface MINIMAL — follow this 3-tier escalation:",
        "TIER 1 (default): edit the MOST RELEVANT EXISTING file listed below in-place. Do not create a "
        "new file if an existing one can hold the change.",
        "TIER 2 (only if Tier 1 truly cannot fit): create at most a FEW NEW files (hard cap 3 total across "
        "the whole run) and keep all work inside those 3 plus the existing files. Do not spawn a new helper "
        "every round — reuse and rewrite the same files.",
        "TIER 3 (only if Tier 2 is insufficient): create a single subdirectory under train/ and put new files "
        "there. If you add or grow files in that subdirectory, first re-read the whole subdirectory, then "
        "consolidate: merge overlapping logic and DELETE now-unused files so it stays minimal.",
        "Never leave dead/parallel scripts behind. Prefer rewriting one driver over adding another.",
        "",
        "## Existing editable train-side files (prefer editing these — Tier 1):",
        inv_block,
        "",
        "## Action choice:",
        "STRONGLY PREFER a full-file 'write' action (path + complete new file content) over 'apply_patch'. "
        "A unified diff is fragile (wrong hunk counts / stale context can no-op) and slower to generate. "
        "Use apply_patch only for a tiny edit to a file whose exact current contents you already know; "
        "for any new file or substantial change, emit 'write' with the entire file content.",
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
    """Verify an apply_patch actually took effect on disk, then smoke-compile.

    git apply can report success (returncode 0, changed_files listed) yet leave
    nothing on disk — e.g. a new-file diff applied inside a gitignored/nested
    directory. Trusting the return code let a no-op "succeed" and the loop spun
    on the baseline forever. So we require every claimed changed file to exist
    and be non-empty before believing the patch, then py_compile any .py files.
    """
    try:
        raw = Path(obs.artifact_path).read_text(encoding="utf-8") if obs.artifact_path else "{}"
        data = json.loads(raw)
        changed = data.get("changed_files") or []
    except Exception:
        changed = []
    if not changed:
        # Nothing was reported as changed: an apply_patch that changed nothing is
        # not a real edit, regardless of exit code.
        return False
    root = Path(loop.settings.root())
    for rel in changed:
        target = root / str(rel)
        try:
            if not target.exists() or target.stat().st_size == 0:
                return False
        except Exception:
            return False
    py_files = [str(f) for f in changed if str(f).endswith(".py")]
    if not py_files:
        return obs.status in {"ok", "ok_metric_recovered"}
    result = loop.runner.run("python3 -m py_compile " + " ".join(shlex.quote(f) for f in py_files))
    return result.get("returncode") == 0


def _execute_cursor_path(root: str | Path) -> Path:
    return Path(root) / ".autoresearch" / "execute_cursor.json"


def _load_execute_cursor(root: str | Path, plan_key: str) -> int:
    """Return the next todo index to run, resetting when the plan changed."""
    path = _execute_cursor_path(root)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if str(data.get("plan_key")) != plan_key:
        return 0
    try:
        return max(0, int(data.get("index", 0)))
    except Exception:
        return 0


def _save_execute_cursor(root: str | Path, plan_key: str, index: int) -> None:
    path = _execute_cursor_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"plan_key": plan_key, "index": int(index)}, ensure_ascii=False), encoding="utf-8")


def _plan_key(items: list[str]) -> str:
    import hashlib

    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()[:16]


def make_execute_handler(execute_fn: Optional[ExecuteFn] = None):
    fn = execute_fn or _default_execute_fn

    def handler(ctx: PhaseContext) -> PhaseResult:
        items = parse_todo_from_plan(ctx.root)
        if not items:
            items = ["(no explicit todo; execute current plan)"]

        # Bound the number of (possibly slow, LLM-backed) actions per Execute visit
        # so one step cannot exhaust the whole time/token budget on a long todo
        # list. A plan-keyed cursor carries remaining items to the next visit.
        cap = int(getattr(getattr(ctx.loop, "settings", None), "execute_max_actions_per_step", 0) or 0)
        plan_key = _plan_key(items)
        start = _load_execute_cursor(ctx.root, plan_key) if cap > 0 else 0
        if start >= len(items):
            start = 0  # plan fully executed before; re-run from the top on re-entry
        window = items[start:start + cap] if cap > 0 else items
        end = start + len(window)
        more_pending = cap > 0 and end < len(items)

        results = []
        done = 0
        any_verified = False
        for item in window:
            res = fn(item, ctx) or {}
            results.append(res)
            # verification hard-constraint: only verified items count as done.
            if res.get("status") in {"ok", "done"} and res.get("verification"):
                done += 1
                any_verified = True

        if cap > 0:
            _save_execute_cursor(ctx.root, plan_key, 0 if more_pending is False else end)

        # children write .auto/, parent is the single writer of project.md.
        write_auto_note(ctx.root, "execute_report", "# Execute Report\n\n" +
                        f"window items {start + 1}-{end} of {len(items)}\n\n" +
                        "\n".join(f"- {r.get('item')}: status={r.get('status')} verified={r.get('verification')} — {r.get('note','')}" for r in results))
        project_text = _append_change_record(ctx.project_text, f"executed {done}/{len(window)} todo items (verified); window {start + 1}-{end}/{len(items)}")

        # If nothing verified and something was attempted, treat as an execute-side
        # major error so the machine routes to Evaluate rather than Run — but only
        # when there is nothing left to try (otherwise let the next visit continue).
        attempted = any(r.get("status") in {"ok", "done", "failed"} for r in results)
        major = attempted and not any_verified and not more_pending
        signals_update = {"major_error": True} if major else {}
        if major:
            append_lesson(ctx.root, kind="operational_error",
                          summary="execute produced no verified change", detail=json.dumps(results, ensure_ascii=False)[:2000])
        pending_note = f" (+{len(items) - end} pending)" if more_pending else ""
        summary = f"execute: {done}/{len(window)} verified{pending_note}" + (" (major_error)" if major else "")
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


def _find_search_driver(ctx: PhaseContext) -> Optional[str]:
    """Return a relative path to a self-iterating search driver, if enabled/present.

    Execute may write a driver (e.g. train/search.py) that internally loops over
    many candidates and calls the eval harness each time. Running it amortizes one
    LLM decision over many cheap evaluations, which is the whole point of the
    search-script pattern.
    """
    settings = getattr(ctx.loop, "settings", None)
    if settings is None or not bool(getattr(settings, "run_search_driver", False)):
        return None
    root = Path(ctx.root)
    for rel in getattr(settings, "search_driver_globs", ()) or ():
        if (root / rel).exists():
            return rel
    return None


def _search_driver_command(rel: str) -> str:
    if rel.endswith(".sh"):
        return f"set -e; bash {rel}"
    # Python driver: run it, then materialize + evaluate the best candidate it found.
    return (
        "set -e; "
        f"python3 {rel}; "
        "if [ -f train/train.sh ]; then bash train/train.sh; fi; "
        "if [ -f eval.sh ]; then bash eval.sh; fi"
    )


def _default_run_fn(ctx: PhaseContext) -> dict:
    """Run the project/experiment via the loop's confined runner.

    Prefers a self-iterating search driver (many internal evals) when Execute
    produced one; otherwise falls back to a single train+eval pass.
    """
    loop = ctx.loop
    if loop is None:
        return {"status": "skipped", "returncode": None, "stdout": "no loop"}
    driver = _find_search_driver(ctx)
    if driver:
        command = _search_driver_command(driver)
        rationale = f"v2 run search driver ({driver})"
    else:
        command = (
            "set -e; "
            "if [ -f train/train.sh ]; then bash train/train.sh; "
            "elif [ -f run.sh ]; then bash run.sh; "
            "else echo 'no train/run script found'; fi; "
            "if [ -f eval.sh ]; then bash eval.sh; fi"
        )
        rationale = "v2 run experiment"
    from core.autoresearch_loop import AutoResearchAction, git_snapshot

    action = AutoResearchAction(type="run", rationale=rationale, command=command, role="trial")
    base_git = git_snapshot(loop.settings.root(), enabled=loop.settings.use_git_versioning)
    obs = loop.execute_action(action)
    recorder = getattr(loop, "_maybe_record_experiment", None)
    if callable(recorder):
        recorder(action, obs, base_git, "run_experiment")
    status = "ok" if obs.status in {"ok", "ok_metric_recovered"} else "failed"
    return {"status": status, "returncode": 0 if status == "ok" else 1,
            "stdout": obs.summary, "stderr": "", "artifact_path": obs.artifact_path,
            "search_driver": driver or ""}


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
        driver = result.get("search_driver") or "(single train+eval)"
        write_auto_note(ctx.root, "run_report",
                        f"# Run Report\n\nstatus={result.get('status')} returncode={result.get('returncode')} autofix_attempts={attempts} driver={driver}\n")
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
