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
import time
from pathlib import Path
from typing import Callable, Optional

from core.autoresearch_memory import write_auto_note, read_auto_notes, append_lesson
from core.autoresearch_phases import PhaseContext, PhaseResult
from core.autoresearch_todo_state import (
    has_open_tasks,
    load_todo_state,
    ready_execute_tasks,
    ready_tasks,
    save_todo_state,
    task_phase,
)


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

_IMPLEMENTATION_HINTS = (
    "implement", "update", "modify", "rewrite", "replace", "create", "add",
    "edit", "refactor", "fix", "write", "保存", "修改", "新增", "创建", "实现", "重写",
)
_NON_IMPLEMENTATION_HINTS = (
    "run ", "evaluate", "eval", "analyze", "record ", "inspect", "check whether",
    "identify", "repeat", "stop", "select", "verify", "compare", "运行", "评估",
    "分析", "记录", "检查", "选择", "验证",
)


def _is_implementation_todo(item: str) -> bool:
    text = str(item or "").lower()
    if any(h in text for h in _IMPLEMENTATION_HINTS):
        return True
    if any(h in text for h in _NON_IMPLEMENTATION_HINTS):
        return False
    # Ambiguous items are allowed through; the LLM can still decide note/read.
    return True


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
    old_tier = getattr(agent, "_tier", "plan")
    try:
        agent._tier = "exec"
    except Exception:
        pass
    from core.autoresearch_loop import AutoResearchAction, AutoResearchWorkflowStep

    step = AutoResearchWorkflowStep(
        name="apply_change",
        action_type="note",
        rationale=f"execute todo: {item}",
        content=item,
        allowed_tools=("write", "apply_patch"),
    )
    fallback = AutoResearchAction(type="note", rationale="execute_no_safe_change", content=item)
    max_chars = int(getattr(getattr(ctx.loop, "settings", None), "execute_context_chars", 12000) or 12000)
    parent_context = _execute_parent_context(ctx.root, ctx.project_text, item, max_chars=max_chars)
    try:
        result = agent.plan_step(step=step, fallback_action=fallback, parent_context=parent_context, round_index=0)
    finally:
        try:
            agent._tier = old_tier
        except Exception:
            pass
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


def _train_side_snippets(root: str | Path, max_files: int = 4, max_chars_per_file: int = 2200) -> list[str]:
    root = Path(root)
    preferred = [
        root / "train" / "train.py",
        root / "train" / "train.sh",
        root / "train.py",
        root / "run.sh",
    ]
    paths: list[Path] = []
    for path in preferred:
        if path.exists() and path.is_file() and path not in paths:
            paths.append(path)
    train_dir = root / "train"
    if train_dir.is_dir():
        for path in sorted(train_dir.glob("*")):
            if path.is_file() and path.suffix.lower() in _INVENTORY_SUFFIXES and path not in paths:
                paths.append(path)
            if len(paths) >= max_files:
                break
    snippets: list[str] = []
    for path in paths[:max_files]:
        try:
            rel = str(path.relative_to(root))
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        snippets.append(f"## {rel}\n{text[:max_chars_per_file]}")
    return snippets


def _execute_parent_context(root: str | Path, project_text: str, item: str, max_chars: int = 12000) -> str:
    notes = read_auto_notes(root, max_files=3)
    inventory = _train_side_inventory(root)
    inv_block = "\n".join(f"- {f}" for f in inventory) if inventory else "(no train-side files yet)"
    snippet_block = "\n\n".join(_train_side_snippets(root)) or "(no readable train-side snippets)"
    parts = [
        "V2 execute phase: implement exactly one safe project-confined change for this todo.",
        f"Todo: {item}",
        "Forbidden: do not edit eval harness/read-only evaluation files.",
        "You MUST return a mutating action: prefer type='write' with a complete file body. "
        "Do not return note/read for implementation tasks.",
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
        "## Current train-side file snippets:",
        snippet_block,
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
        parts.extend(["", f"# .auto/{name} (truncated)", (text or "")[:1200]])
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
        todo_state = load_todo_state(ctx.root)
        structured_tasks = bool(todo_state.get("tasks"))
        ready = ready_execute_tasks(todo_state)
        using_todo_state = structured_tasks
        items = [task["goal"] for task in ready] if using_todo_state else parse_todo_from_plan(ctx.root)
        if not items:
            if using_todo_state:
                execute_open = has_open_tasks(todo_state, phase="execute")
                run_open = has_open_tasks(todo_state, phase="run")
                run_ready = bool(ready_tasks(todo_state, phase="run", statuses={"pending", "in_progress"}))
                signals_update = {"execute_has_open_tasks": execute_open and not run_ready}
                if (execute_open and not ready and not run_ready) or (run_open and not run_ready and not execute_open):
                    signals_update["plan_still_valid"] = False
                summary = "execute: no ready execute tasks"
                if run_open:
                    summary += "; run tasks are ready or waiting"
                write_auto_note(ctx.root, "execute_report", "# Execute Report\n\n" + summary + "\n")
                return PhaseResult(signals_update=signals_update, summary=summary)
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
        for offset, item in enumerate(window):
            task = ready[start + offset] if using_todo_state and start + offset < len(ready) else None
            if task and task.get("type") == "analysis":
                res = _execute_analysis_task(ctx, task)
                results.append(res)
                _update_task_from_execute_result(todo_state, task, res)
                done += 1
                any_verified = True
                continue
            if not using_todo_state and not _is_implementation_todo(item):
                res = {
                    "item": item,
                    "status": "skipped",
                    "verification": True,
                    "note": "non-implementation todo; handled by run/evaluate phase",
                }
                results.append(res)
                _update_task_from_execute_result(todo_state, task, res)
                done += 1
                any_verified = True
                continue
            exec_item = _execution_attempt_item(task, item)
            res = fn(exec_item, ctx) or {}
            if task and exec_item != item:
                res.setdefault("full_item", item)
                res.setdefault("subgoal", exec_item)
            if task:
                res.setdefault(
                    "max_attempts",
                    int(getattr(getattr(ctx.loop, "settings", None), "execute_max_task_attempts", 2) or 2),
                )
            results.append(res)
            _update_task_from_execute_result(todo_state, task, res)
            # verification hard-constraint: only verified items count as done.
            if res.get("status") in {"ok", "done", "skipped"} and res.get("verification"):
                done += 1
                any_verified = True

        if using_todo_state:
            save_todo_state(ctx.root, todo_state)

        if cap > 0:
            _save_execute_cursor(ctx.root, plan_key, 0 if more_pending is False else end)

        # children write .auto/, parent is the single writer of project.md.
        write_auto_note(ctx.root, "execute_report", "# Execute Report\n\n" +
                        f"window items {start + 1}-{end} of {len(items)}\n\n" +
                        "\n".join(f"- {r.get('item')}: status={r.get('status')} verified={r.get('verification')} — {r.get('note','')}" for r in results))
        project_text = _append_change_record(ctx.project_text, f"executed {done}/{len(window)} todo items (verified); window {start + 1}-{end}/{len(items)}")

        attempted = any(r.get("status") in {"ok", "done", "failed", "tried"} for r in results)
        execute_open = bool(ready_execute_tasks(todo_state)) if using_todo_state else bool(more_pending)
        # If nothing verified and something was attempted, treat it as a major
        # error only when there are no runnable Execute retries left. Otherwise
        # stay in Execute and let the bounded attempt counter make progress.
        major = attempted and not any_verified and not execute_open and not more_pending
        signals_update = {"execute_has_open_tasks": execute_open}
        if major:
            signals_update["major_error"] = True
        if major:
            append_lesson(ctx.root, kind="operational_error",
                          summary="execute produced no verified change", detail=json.dumps(results, ensure_ascii=False)[:2000])
        pending_note = f" (+{len(items) - end} pending)" if more_pending else ""
        summary = f"execute: {done}/{len(window)} verified{pending_note}" + (" (major_error)" if major else "")
        return PhaseResult(project_text=project_text, signals_update=signals_update, summary=summary)

    return handler


def _execution_attempt_item(task: Optional[dict], item: str) -> str:
    """Select a small current subgoal from an oversized implementation task."""
    if not task:
        return item
    text = str(item or "").strip()
    if len(text) <= 700:
        return text
    last = task.get("last_result") if isinstance(task, dict) else {}
    attempts = 0
    if isinstance(last, dict):
        try:
            attempts = int(last.get("attempts") or 0)
        except Exception:
            attempts = 0
    pieces = _split_implementation_goal(text)
    if not pieces:
        return text[:700]
    selected = pieces[min(attempts, len(pieces) - 1)]
    return (
        "Implement this focused part of the larger task: "
        + selected[:650]
        + "\nKeep existing behavior working and prefer editing the most relevant existing train-side file."
    )


def _split_implementation_goal(text: str) -> list[str]:
    raw = str(text or "").strip()
    if "covering:" in raw:
        raw = raw.split("covering:", 1)[1]
    parts = []
    for chunk in raw.replace("\n", "; ").split(";"):
        cleaned = chunk.strip(" -.\t")
        if cleaned:
            parts.append(cleaned)
    return parts


def _execute_analysis_task(ctx: PhaseContext, task: dict) -> dict:
    root = Path(ctx.root)
    context_paths = task.get("context_paths") or []
    if not context_paths:
        context_paths = _default_analysis_paths(root)
    snippets = []
    for rel in context_paths[:8]:
        path = root / str(rel)
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
        except Exception:
            continue
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        head = "\n".join(text.splitlines()[:80])
        snippets.append(f"## {rel}\n{head}")
    summary = "\n\n".join(snippets)[:6000] if snippets else "no readable context files found"
    artifact = write_auto_note(ctx.root, f"analysis_{task.get('task_id', 'task')}", "# Analysis\n\n" + summary)
    return {
        "item": task.get("goal", ""),
        "status": "done",
        "verification": True,
        "note": f"analysis written to {artifact}",
    }


def _default_analysis_paths(root: Path) -> list[str]:
    candidates = []
    for rel in ("program.md", "project.md", "README.md", "train/train.py", "train/train.sh", "metrics.json"):
        if (root / rel).exists():
            candidates.append(rel)
    return candidates


def _update_task_from_execute_result(state: dict, task: Optional[dict], result: dict) -> None:
    if not task:
        return
    target = None
    for existing in state.get("tasks", []):
        if existing.get("task_id") == task.get("task_id"):
            target = existing
            break
    if target is None:
        return
    status = str(result.get("status") or "")
    verified = bool(result.get("verification"))
    last_result = dict(target.get("last_result") or {})
    attempts = int(last_result.get("attempts") or 0)
    if not verified and status not in {"skipped"}:
        attempts += 1
    max_attempts = 2
    try:
        max_attempts = max(1, int(result.get("max_attempts") or 2))
    except Exception:
        max_attempts = 2
    if status == "skipped":
        target["status"] = "skipped"
    elif status in {"ok", "done"} and verified:
        target["status"] = "verified"
    elif status == "failed":
        target["status"] = "failed"
    elif attempts >= max_attempts:
        target["status"] = "failed"
    else:
        target["status"] = "in_progress"
    target["last_result"] = {
        "status": status,
        "verification": verified,
        "note": str(result.get("note") or "")[:1000],
        "attempts": attempts,
        "max_attempts": max_attempts,
        "updated_at": time.time(),
    }


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
        matches = sorted(root.glob(str(rel)))
        for match in matches:
            if match.is_file():
                return str(match.relative_to(root))
    candidates = []
    train_dir = root / "train"
    if train_dir.exists():
        for path in sorted(train_dir.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            if path.name == "search.py" or "search" in path.stem or "driver" in path.stem or "exploration" in path.stem:
                candidates.append(path)
    if candidates:
        preferred = sorted(candidates, key=lambda p: (
            0 if p.name == "search.py" else 1 if p.name == "train.py" else 2,
            -p.stat().st_mtime,
            p.name,
        ))[0]
        return str(preferred.relative_to(root))
    return None


def _search_driver_command(rel: str) -> str:
    if rel.endswith(".sh"):
        return f"set -e; bash {rel}"
    # Python drivers are usually candidate generators invoked by train/train.sh.
    # Running both the driver and train.sh in the same iteration can emit the same
    # candidate twice before the eval result is logged, so drive the canonical
    # train->eval->log loop instead.
    return (
        "set -e; "
        "if [ -f train/train.sh ]; then bash train/train.sh; "
        f"else python3 {rel}; fi; "
        "if [ -f eval.sh ]; then bash eval.sh; fi; "
        "python3 .autoresearch/append_search_log.py autoresearch_run"
    )


def _fallback_eval_loop_command() -> str:
    return (
        "set -e; "
        "if [ -f train/train.sh ]; then bash train/train.sh; "
        "elif [ -f run.sh ]; then bash run.sh; "
        "elif [ -f eval.sh ]; then :; "
        "else echo 'no train/run/eval script found'; exit 1; fi; "
        "if [ -f eval.sh ]; then bash eval.sh; fi; "
        "python3 .autoresearch/append_search_log.py autoresearch_fallback_loop"
    )


def _select_run_task(root: str | Path) -> Optional[dict]:
    state = load_todo_state(root)
    candidates = [task for task in ready_tasks(state, phase="run", statuses={"pending", "in_progress"}) if task.get("run_spec")]
    candidates.sort(key=lambda t: (int(t.get("priority") or 0), t.get("task_id", "")))
    return candidates[0] if candidates else None


def _has_structured_run_work(root: str | Path) -> bool:
    state = load_todo_state(root)
    return any(
        task.get("run_spec")
        for task in state.get("tasks", [])
        if task.get("status") in {"pending", "in_progress"} and task_phase(task) == "run"
    )


def _command_from_run_spec(run_spec: dict, *, fallback_command: str) -> tuple[str, int, float, str, str, float]:
    run_spec = dict(run_spec or {})
    commands = run_spec.get("commands") or []
    if isinstance(commands, str):
        commands = [commands]
    commands = [str(c).strip() for c in commands if str(c).strip()]
    command = " && ".join(commands) if commands else fallback_command
    mode = str(run_spec.get("mode") or "single").strip().lower()
    if mode not in {"single", "loop", "long_job"}:
        mode = "single"
    max_iters = int(run_spec.get("max_iters") or (100 if mode == "loop" else 1))
    max_seconds = float(run_spec.get("max_seconds") or 0.0)
    monitor_commands = run_spec.get("monitor_commands") or []
    if isinstance(monitor_commands, str):
        monitor_commands = [monitor_commands]
    monitor_commands = [str(c).strip() for c in monitor_commands if str(c).strip()]
    monitor_command = " && ".join(monitor_commands)
    poll_interval = float(run_spec.get("poll_interval_seconds") or 0.0)
    return command, max(1, max_iters), max(0.0, max_seconds), mode, monitor_command, max(0.0, poll_interval)


def _ensure_search_log_helper(root: str | Path) -> None:
    helper = Path(root) / ".autoresearch" / "append_search_log.py"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "source = sys.argv[1] if len(sys.argv) > 1 else 'autoresearch_run'\n"
        "root = Path('.')\n"
        "submission = root.joinpath('outputs', 'submission.json')\n"
        "metrics = root.joinpath('metrics.json')\n"
        "log = root.joinpath('outputs', 'search_log.jsonl')\n"
        "if submission.exists() and metrics.exists():\n"
        "    s = json.loads(submission.read_text())\n"
        "    m = json.loads(metrics.read_text())\n"
        "    row = {'ts': time.time(), 'x': s.get('x'), 'y': s.get('y'), 'z': m.get('z', m.get('primary_metric')), 'source': source}\n"
        "    log.parent.mkdir(parents=True, exist_ok=True)\n"
        "    with log.open('a', encoding='utf-8') as f:\n"
        "        f.write(json.dumps(row, ensure_ascii=False) + '\\n')\n",
        encoding="utf-8",
    )


def _current_submission_key(root: str | Path) -> Optional[tuple[float, float]]:
    path = Path(root) / "outputs" / "submission.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (float(data.get("x")), float(data.get("y")))
    except Exception:
        return None


def _artifact_duration(obs) -> Optional[float]:
    try:
        if not getattr(obs, "artifact_path", ""):
            return None
        data = json.loads(Path(obs.artifact_path).read_text(encoding="utf-8"))
        return float(data.get("duration_seconds"))
    except Exception:
        return None


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _metric_from_file(root: str | Path) -> tuple[Optional[float], bool]:
    data = _read_json(Path(root) / "metrics.json")
    value = data.get("z", data.get("primary_metric"))
    try:
        metric = float(value)
    except Exception:
        return None, bool(data.get("higher_is_better", True))
    return metric, bool(data.get("higher_is_better", True))


def _search_log_rows(root: str | Path) -> list[dict]:
    rows = []
    path = Path(root) / "outputs" / "search_log.jsonl"
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        try:
            row["x"] = float(row["x"])
            row["y"] = float(row["y"])
            row["z"] = float(row["z"])
        except Exception:
            continue
        rows.append(row)
    return rows


def _default_run_fn(ctx: PhaseContext) -> dict:
    """Run the project/experiment via the loop's confined runner.

    Prefers a self-iterating search driver (many internal evals) when Execute
    produced one; otherwise falls back to a single train+eval pass.
    """
    loop = ctx.loop
    if loop is None:
        return {"status": "skipped", "returncode": None, "stdout": "no loop"}
    _ensure_search_log_helper(ctx.root)
    run_task = _select_run_task(ctx.root)
    if run_task is None and _has_structured_run_work(ctx.root):
        return {
            "status": "failed",
            "returncode": 1,
            "stdout": "",
            "stderr": "structured run tasks exist, but none are ready; dependencies are not satisfied",
            "search_driver": "",
            "inner_evals": 0,
        }
    driver = _find_search_driver(ctx)
    if driver:
        fallback_command = _search_driver_command(driver)
        rationale = f"v2 run search driver ({driver})"
        mode = "loop"
    else:
        fallback_command = _fallback_eval_loop_command()
        rationale = "v2 run experiment"
        mode = "single"
    if run_task:
        command, spec_max_evals, spec_max_seconds, mode, monitor_command, poll_interval = _command_from_run_spec(run_task.get("run_spec"), fallback_command=fallback_command)
        max_evals_override = spec_max_evals
        max_seconds_override = spec_max_seconds
        rationale = f"v2 run task {run_task.get('task_id')} ({mode})"
    else:
        command = fallback_command
        monitor_command = ""
        poll_interval = 0.0
        max_evals_override = None
        max_seconds_override = None
    from core.autoresearch_loop import AutoResearchAction, git_snapshot

    action = AutoResearchAction(type="run", rationale=rationale, command=command, role="trial")
    base_git = git_snapshot(loop.settings.root(), enabled=loop.settings.use_git_versioning)
    observations = []
    started = time.time()
    start_rows = len(_search_log_rows(ctx.root))
    max_seconds = max(0.0, float(max_seconds_override if max_seconds_override is not None else getattr(loop.settings, "run_max_inner_seconds", 20.0) or 0.0))
    max_evals = max(1, int(max_evals_override if max_evals_override is not None else getattr(loop.settings, "run_max_inner_evals", 100) or 1))
    cheap_threshold = max(0.0, float(getattr(loop.settings, "run_cheap_eval_threshold_seconds", 2.0) or 0.0))
    obs = None
    for index in range(max_evals):
        obs = loop.execute_action(action)
        observations.append(obs)
        last_duration = _artifact_duration(obs)
        if obs.status not in {"ok", "ok_metric_recovered"}:
            break
        if mode == "single":
            break
        if mode == "long_job":
            if monitor_command:
                monitor_action = AutoResearchAction(type="run", rationale=f"{rationale} monitor", command=monitor_command, role="trial")
                if poll_interval:
                    time.sleep(min(poll_interval, max(0.0, max_seconds - (time.time() - started))) if max_seconds else poll_interval)
                monitor_obs = loop.execute_action(monitor_action)
                observations.append(monitor_obs)
                obs = monitor_obs
            break
        if index == 0 and run_task is None and driver and (last_duration is None or last_duration > cheap_threshold):
            break
        if max_seconds and time.time() - started >= max_seconds:
            break
    obs = obs or observations[-1]
    recorder = getattr(loop, "_maybe_record_experiment", None)
    if callable(recorder):
        recorder(action, obs, base_git, "run_experiment")
    inner_evals = max(len(observations), len(_search_log_rows(ctx.root)) - start_rows)
    if run_task:
        _update_run_task_result(ctx.root, run_task, obs, inner_evals=inner_evals)
    status = "ok" if obs.status in {"ok", "ok_metric_recovered"} else "failed"
    stdout = obs.summary
    if len(observations) > 1:
        stdout = f"{stdout}\ninner_evals={inner_evals} elapsed_seconds={round(time.time() - started, 3)}"
    return {"status": status, "returncode": 0 if status == "ok" else 1,
            "stdout": stdout, "stderr": "", "artifact_path": obs.artifact_path,
            "search_driver": driver or "", "inner_evals": inner_evals}


def _update_run_task_result(root: str | Path, task: dict, obs, *, inner_evals: int) -> None:
    state = load_todo_state(root)
    metric, higher = _metric_from_file(root)
    for existing in state.get("tasks", []):
        if existing.get("task_id") != task.get("task_id"):
            continue
        command_ok = getattr(obs, "status", "") in {"ok", "ok_metric_recovered"}
        ok = command_ok and _verification_passed(existing.get("verification") or {}, metric, higher)
        existing["status"] = "verified" if ok else "failed"
        existing["last_result"] = {
            "status": getattr(obs, "status", ""),
            "artifact_path": getattr(obs, "artifact_path", ""),
            "summary": getattr(obs, "summary", "")[:1000],
            "inner_evals": inner_evals,
            "metric": metric,
            "higher_is_better": higher,
            "updated_at": time.time(),
        }
        break
    save_todo_state(root, state)


def _verification_passed(verification: dict, metric: Optional[float], higher: bool) -> bool:
    if not verification:
        return True
    if verification.get("metric_required") and metric is None:
        return False
    threshold = verification.get("metric_threshold")
    if threshold is not None and metric is not None:
        threshold = float(threshold)
        if higher and metric < threshold:
            return False
        if not higher and metric > threshold:
            return False
    return True


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
        metric, higher = _metric_from_file(ctx.root)
        threshold = getattr(getattr(ctx.loop, "settings", None), "solved_metric_threshold", None)
        solved = False
        if threshold is not None and metric is not None:
            threshold = float(threshold)
            solved = bool((not higher and metric <= threshold) or (higher and metric >= threshold))
        driver = result.get("search_driver") or "(single train+eval)"
        write_auto_note(ctx.root, "run_report",
                        f"# Run Report\n\nstatus={result.get('status')} returncode={result.get('returncode')} "
                        f"autofix_attempts={attempts} driver={driver} inner_evals={result.get('inner_evals', 1)} solved={solved}\n")
        if major:
            append_lesson(ctx.root, kind="operational_error",
                          summary=f"run failed after {attempts} autofix attempts",
                          detail=str(result.get("stderr") or result.get("stdout") or "")[:2000])
        signals_update = {"major_error": True} if major else ({"solved": True} if solved else {})
        summary = f"run: status={result.get('status')} autofix={attempts}" + (" solved" if solved else "") + (" (major_error)" if major else "")
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
