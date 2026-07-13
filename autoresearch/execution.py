"""AutoResearch V3 — attempt-step execution helpers.

Both phases are built around injectable callables so they are testable without
spinning up real sub-agents or training jobs:

- Execute: derive a Todo list from ``todo_state.json`` / ``.auto/plan.md``, run an ``execute_fn``
  per item, and enforce the **verification hard-constraint** — an item is only
  "done" if it returns ``verification == True``.  The parent is the single
  writer of project.md (children only touch their own ``.auto/``).
- Run: run the project/experiment via ``run_fn`` with **bounded autofix** —
  at most ``max_autofix`` repair attempts before flagging ``major_error`` and
  letting the state machine jump to Evaluate.

Deterministic defaults use the loop's project-confined runner so the phases do
real work out of the box.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

from autoresearch.state.memory import write_auto_note, read_auto_notes, append_lesson
from autoresearch.phases import PhaseContext, PhaseResult
from autoresearch.observability.debug import debug_event, inflight_finish, inflight_start
from autoresearch.observability.timeout import call_with_deadline
from autoresearch.state.completion import is_metric_solved, parse_completion_criteria
from autoresearch.diagnostics import (
    changed_result_artifacts,
    direct_eval_import_check,
    eval_import_targets,
    has_visible_result_artifacts,
    is_direct_eval_target,
    metric_payload,
    parse_higher_is_better,
    project_fingerprint,
    read_json,
    read_project_json,
    write_eval_contract_digest,
    write_failure_digest,
)
from autoresearch.state.todo import (
    has_open_tasks,
    load_todo_state,
    ready_execute_tasks,
    ready_tasks,
    repair_failed_run_tasks,
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
# Attempt code/read side
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
            note_spec_action = _note_change_spec_action(action)
            if note_spec_action is not None:
                spec_path.write_text(str(getattr(note_spec_action, "content", "")) + "\n", encoding="utf-8")
                action = loop._maybe_hydrate_apply_change("apply_change", note_spec_action)
            if action.type not in {"apply_patch", "write", "bundle", "read"}:
                status = "tried"
                result = {"item": item, "status": status, "verification": False,
                          "note": f"execute step agent chose non-mutating action {action.type}; no change applied"}
                if getattr(action, "content", ""):
                    result["note"] += "; " + str(getattr(action, "content", ""))[:500]
                return result
        actions = _normalize_execute_actions(action)
        if actions and all(getattr(a, "type", "") == "read" for a in actions):
            return _execute_read_actions(loop, item, actions)
        bad = [getattr(a, "type", "") for a in actions if getattr(a, "type", "") not in {"apply_patch", "write"}]
        if bad:
            note_action = next((a for a in actions if getattr(a, "type", "") == "note"), actions[0] if actions else action)
            content = str(getattr(note_action, "content", "") or "")[:500]
            extra = f"; note={content}" if content else ""
            return {"item": item, "status": "tried", "verification": False,
                    "note": f"execute step agent chose non-mutating action {bad[0]}; no change applied{extra}"}
        return _execute_mutating_actions(loop, ctx, item, actions)
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
    from autoresearch.legacy.loop import AutoResearchAction, AutoResearchWorkflowStep

    direct_failed = ""
    if not _skip_direct_write(ctx):
        direct = _direct_write_action(item, ctx)
        if direct is not None:
            return direct
        direct_failed = getattr(ctx, "_autoresearch_direct_write_failed", "")
    else:
        direct_failed = "skipped direct write after previous direct-write timeout; using compact fallback"
    if str(direct_failed).startswith("unsafe:"):
        return AutoResearchAction(type="note", rationale="execute_direct_write_failed", content=direct_failed)

    step = AutoResearchWorkflowStep(
        name="apply_change",
        action_type="note",
        rationale=f"execute todo: {item}",
        content=item,
        allowed_tools=("write", "apply_patch", "read"),
    )
    fallback = AutoResearchAction(type="note", rationale="execute_no_safe_change", content=item)
    max_chars = int(getattr(getattr(ctx.loop, "settings", None), "execute_context_chars", 12000) or 12000)
    parent_context = _execute_fallback_context(ctx, item, direct_failed, max_chars=min(max_chars, 4500))
    try:
        result = agent.plan_step(step=step, fallback_action=fallback, parent_context=parent_context, round_index=0)
        _save_execute_llm_trace(ctx, "execute_fallback_step_agent", {
            "system": getattr(result, "system_prompt", ""),
            "user": getattr(result, "user_payload", ""),
            "raw_response": getattr(result, "raw_response", ""),
            "used_fallback": getattr(result, "used_fallback", False),
            "error": getattr(result, "error", ""),
            "action": getattr(getattr(result, "action", None), "__dict__", {}),
            "bucket_updates": getattr(result, "bucket_updates", {}),
            "task": item,
        })
    finally:
        try:
            agent._tier = old_tier
        except Exception:
            pass
    apply_updates = getattr(loop, "_apply_bucket_updates", None)
    if callable(apply_updates):
        apply_updates(getattr(result, "bucket_updates", {}) or {})
    return result.action


def _direct_write_action(item: str, ctx: PhaseContext):
    """Ask the model for a minimal {path, content} write before full StepAgent."""
    loop = ctx.loop
    agent = getattr(loop, "step_agent", None) if loop is not None else None
    if agent is None:
        return None
    try:
        client = agent._client()
    except Exception:
        return None
    try:
        from autoresearch.legacy.loop import AutoResearchAction, extract_json_object
    except Exception:
        return None
    model = ""
    try:
        old_tier = getattr(agent, "_tier", "plan")
        agent._tier = "exec"
        model = agent._resolved_model()
        agent._tier = old_tier
    except Exception:
        model = getattr(agent, "model", "") or ""
    root = Path(ctx.root)
    target = _preferred_write_target(root, item)
    current = ""
    if target:
        try:
            current = (root / target).read_text(encoding="utf-8", errors="replace")
        except Exception:
            current = ""
    support_context = _direct_write_support_context(root, target)
    task_context = _current_execute_task_context(ctx)
    regression_contract = read_project_json(root, ".autoresearch/regression_cases.json")
    system = (
        "Return ONLY JSON. Preferred schema: {\"files\": [{\"path\": str, \"content\": str}, ...]}. "
        "You may also return legacy {\"path\": str, \"content\": str}. "
        "Rewrite up to 3 train-side files with complete content. Keep the first slice small and runnable; "
        "prefer a minimal correct implementation over a large all-in-one optimizer. "
        "If you create a new optimizer/search module, include the train entrypoint file that calls it. "
        "Do not edit eval.py, eval.sh, blackbox_oracle.py. No markdown."
    )
    user = json.dumps({
        "todo": str(item)[:900],
        "preferred_path": target,
        "integration_hint": (
            "If preferred_path is a new optimizer/search module, return files for both that module and train/train.sh "
            "so bash train/train.sh executes the new code and writes outputs/submission.json."
            if target in {"train/optimizer.py", "train/search.py"} else ""
        ),
        "current_file": current[:1200],
        "support_context": support_context,
        "task_context": task_context,
        "regression_contract": regression_contract,
        "closeout_instructions": regression_contract.get("instructions", []) if isinstance(regression_contract, dict) else [],
        "schema": {
            "files": [{"path": "relative train-side path", "content": "complete new file content"}],
            "path": "legacy single relative train-side path",
            "content": "legacy single complete new file content",
        },
    }, ensure_ascii=False)
    timeout = float(getattr(getattr(loop, "settings", None), "llm_request_timeout", 60.0) or 60.0)
    try:
        inflight_start(
            root,
            "llm",
            phase=getattr(loop, "_current_phase", "execute") if loop is not None else "execute",
            detail="execute direct write",
            model=model or "gpt-4o",
            timeout_seconds=timeout,
            prompt_chars=len(system) + len(user),
        )
        def _call():
            try:
                return client.chat.completions.create(
                    model=model or "gpt-4o",
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    timeout=timeout,
                )
            except TypeError:
                return client.chat.completions.create(
                    model=model or "gpt-4o",
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                )

        resp = call_with_deadline(_call, timeout_seconds=timeout, label="execute direct write")
        inflight_finish(
            root,
            "llm",
            phase=getattr(loop, "_current_phase", "execute") if loop is not None else "execute",
            detail="execute direct write",
            model=model or "gpt-4o",
        )
        raw = getattr(resp.choices[0].message, "content", "") or ""
        _save_execute_llm_trace(ctx, "execute_direct_write", {
            "system": system,
            "user": user,
            "raw_response": raw,
            "model": model or "gpt-4o",
            "task": item,
        })
        data = extract_json_object(raw)
        files = data.get("files")
        if isinstance(files, list):
            items = [f for f in files if isinstance(f, dict)]
        else:
            items = [{"path": data.get("path"), "content": data.get("content")}]
        actions = []
        for f in items[:3]:
            path = str(f.get("path") or "").strip()
            content = str(f.get("content") or "")
            if not path or not content:
                continue
            if not _is_train_side_write_path(path):
                setattr(ctx, "_autoresearch_direct_write_failed", f"unsafe: direct write returned unsafe path: {path}")
                return None
            actions.append(AutoResearchAction(type="write", rationale=f"direct write for execute todo: {item[:120]}", path=path, content=content))
        if not actions:
            return None
        return actions[0] if len(actions) == 1 else _ExecuteActionBundle(actions)
    except Exception as exc:
        _save_execute_llm_trace(ctx, "execute_direct_write_error", {
            "system": system,
            "user": user,
            "raw_response": "",
            "model": model or "gpt-4o",
            "task": item,
            "error": str(exc),
        })
        inflight_finish(
            root,
            "llm",
            phase=getattr(loop, "_current_phase", "execute") if loop is not None else "execute",
            detail="execute direct write",
            model=model or "gpt-4o",
            error=str(exc)[:500],
        )
        setattr(ctx, "_autoresearch_direct_write_failed", f"direct write failed: {exc}")
        return None


def _save_execute_llm_trace(ctx: PhaseContext, name: str, payload: dict) -> str:
    root = Path(ctx.root)
    d = root / ".autoresearch" / "step_traces"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "execute_llm")).strip("_")[:80] or "execute_llm"
    path = d / f"{int(time.time() * 1000)}_{safe}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return str(path)


def _skip_direct_write(ctx: PhaseContext) -> bool:
    """Avoid repeating the same expensive direct-write request after timeouts."""
    task = getattr(ctx, "_autoresearch_current_task", None)
    if not isinstance(task, dict):
        return False
    last = task.get("last_result") if isinstance(task.get("last_result"), dict) else {}
    note = str(last.get("note") or "").lower()
    attempts = int(last.get("attempts") or 0)
    return attempts > 0 and "direct write" in note and "exceeded framework deadline" in note


def _explicit_project_path_from_text(root: Path, text: str) -> str:
    for match in re.finditer(r"`?([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:py|sh|json|md|txt|yaml|yml|toml))`?", str(text or "")):
        rel = match.group(1).lstrip("./")
        parts = Path(rel).parts
        if not parts or ".." in parts:
            continue
        name = Path(rel).name
        if name in {"eval.py", "eval.sh", "blackbox_oracle.py"}:
            continue
        if (root / rel).exists() or (len(parts) > 1 and len(parts) <= 3):
            return rel
    return ""


def _preferred_write_target(root: Path, item: str = "") -> str:
    explicit = _explicit_project_path_from_text(root, item)
    if explicit:
        return explicit
    lowered = str(item or "").lower()
    if "train.sh" in lowered:
        return "train/train.sh"
    if "optimizer.py" in lowered or "optimizer" in lowered:
        return "train/optimizer.py"
    if "search.py" in lowered or "driver" in lowered:
        return "train/search.py"
    if any(token in lowered for token in (
        "black-box", "black box", "oracle", "incumbent", "candidate search",
        "optimizer", "search", "exploration", "refinement",
    )):
        return "train/optimizer.py"
    for rel in ("train/train.py", "train/train.sh", "train/search.py", "train/optimizer.py"):
        if (root / rel).exists():
            return rel
    return "train/train.py"


def _direct_write_support_context(root: Path, target: str, max_chars: int = 2200) -> dict:
    """Small, high-signal context for a file-level write request."""
    candidates = [
        "train/train.py", "train/train.sh",
        *(eval_import_targets(root) or ["solution.py"]),
        ".autoresearch/experiment_memory.md", ".autoresearch/experiment_memory.json",
        ".autoresearch/regression_cases.json",
        ".auto/eval_contract.md", ".auto/failure_digest.md", ".auto/execute_validation.md", "program.md",
    ]
    if target and target not in candidates:
        candidates.append(target)
    payload: dict[str, str] = {}
    remaining = max(0, int(max_chars))
    for rel in candidates:
        if remaining <= 0:
            break
        path = root / rel
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        snippet = _compact_support_snippet(rel, text, min(remaining, 700))
        payload[rel] = snippet
        remaining -= len(snippet)
    return payload


def _current_execute_task_context(ctx: PhaseContext) -> dict:
    task = getattr(ctx, "_autoresearch_current_task", None)
    subgoal = getattr(ctx, "_autoresearch_current_subgoal", None)
    if not isinstance(task, dict):
        return {}
    last = task.get("last_result") if isinstance(task.get("last_result"), dict) else {}
    subgoal = subgoal if isinstance(subgoal, dict) else {}
    return {
        "task_id": task.get("task_id", ""),
        "full_goal": str(task.get("goal") or "")[:1200],
        "last_status": last.get("status", ""),
        "last_note": str(last.get("note") or "")[:600],
        "last_behavior": last.get("behavior", {}),
        "last_artifacts": last.get("artifacts", []),
        "attempts": last.get("attempts", 0),
        "subgoal_index": subgoal.get("subgoal_index", last.get("subgoal_index", 0)),
        "subgoal_count": subgoal.get("subgoal_count", last.get("subgoal_count", 1)),
    }


def _compact_support_snippet(rel: str, text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if rel.endswith("train.py"):
        keep = []
        capture = False
        for line in text.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith(("ROOT", "OUTPUTS", "SUBMISSION", "VERIFICATION", "DEFAULT_CANDIDATE"))
                or stripped.startswith("def _verify_with_oracle")
                or "blackbox_oracle" in stripped
                or stripped.startswith("def main")
            ):
                capture = True
            if capture:
                keep.append(line)
            if capture and stripped.startswith("return ") and len(keep) > 8:
                capture = False
        compact = "\n".join(keep).strip()
        if compact:
            return compact[:limit]
    return text[:limit]


def _is_train_side_write_path(path: str) -> bool:
    parts = Path(path).parts
    if not parts or ".." in parts:
        return False
    name = Path(path).name
    return name not in {"eval.py", "eval.sh", "blackbox_oracle.py"}


class _ExecuteActionBundle:
    """Internal container for a small atomic-ish group of safe write actions."""

    type = "bundle"

    def __init__(self, actions: list):
        self.actions = list(actions or [])
        self.rationale = "; ".join(str(getattr(a, "rationale", "")) for a in self.actions)[:300]


def _normalize_execute_actions(action) -> list:
    if isinstance(action, _ExecuteActionBundle):
        return list(action.actions)
    return [action] if action is not None else []


def _combined_observation(observations: list) -> SimpleNamespace:
    status = "ok" if observations and all(getattr(o, "status", "") in {"ok", "ok_metric_recovered"} for o in observations) else "failed"
    summary = "\n".join(str(getattr(o, "summary", "")) for o in observations if getattr(o, "summary", "")).strip()
    artifact_path = ""
    for obs in reversed(observations):
        artifact_path = str(getattr(obs, "artifact_path", "") or "")
        if artifact_path:
            break
    return SimpleNamespace(status=status, summary=summary, artifact_path=artifact_path)


def _execute_read_actions(loop, item: str, actions: list) -> dict:
    """Let Execute gather missing context without consuming a failed write attempt."""
    observations = []
    for action in actions:
        obs = loop.execute_action(action)
        observations.append(obs)
        if getattr(obs, "status", "") not in {"ok", "ok_metric_recovered"}:
            break
    combined = _combined_observation(observations)
    artifacts = [str(getattr(o, "artifact_path", "") or "") for o in observations if getattr(o, "artifact_path", "")]
    return {
        "item": item,
        "status": "read_context" if combined.status in {"ok", "ok_metric_recovered"} else "tried",
        "verification": False,
        "note": ("read additional context; retry task with retained artifacts: " + combined.summary[:700]).strip(),
        "artifacts": artifacts,
        "attempt_delta": 0,
    }


def _execute_mutating_actions(loop, ctx: PhaseContext, item: str, actions: list) -> dict:
    """Execute one or more safe mutating actions, then verify once."""
    setattr(ctx, "_autoresearch_execute_before", project_fingerprint(ctx.root))
    observations = []
    static_ok = True
    for action in actions:
        obs = loop.execute_action(action)
        observations.append(obs)
        if not _verify_action_effect(loop, obs, action):
            static_ok = False
        if getattr(obs, "status", "") not in {"ok", "ok_metric_recovered"}:
            static_ok = False
            break
    combined_action = actions[0] if len(actions) == 1 else _ExecuteActionBundle(actions)
    combined_obs = _combined_observation(observations)
    behavior_result = {}
    if observations and combined_obs.status in {"ok", "ok_metric_recovered"}:
        behavior_result = _execute_behavior_check(ctx, item, combined_action, combined_obs, static_verified=static_ok)
    verified = bool(static_ok and (behavior_result.get("ok", True)))
    status = combined_obs.status
    if not verified and status in {"ok", "ok_metric_recovered"}:
        status = "failed"
    note = combined_obs.summary[:500]
    if status == "failed" and combined_obs.status in {"ok", "ok_metric_recovered"}:
        note = "unverified change (files not written or behavior check failed); " + note
    if behavior_result.get("note"):
        note = (note + "; " + str(behavior_result.get("note")))[:1000]
    artifacts = [str(getattr(o, "artifact_path", "") or "") for o in observations if getattr(o, "artifact_path", "")]
    if behavior_result.get("artifact_path"):
        artifacts.append(str(behavior_result["artifact_path"]))
    behavior = behavior_result.get("behavior", {})
    if len(actions) > 1:
        behavior = {**behavior, "files_written": [str(getattr(a, "path", "")) for a in actions]}
    return {
        "item": item,
        "status": status,
        "verification": verified,
        "note": note,
        "behavior": behavior,
        "artifacts": artifacts,
    }


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
    notes = read_auto_notes(root, max_files=6, max_chars_per_file=1400)
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
        "Keep the change surface minimal: prefer editing one existing train-side file; create new files only when necessary.",
        "",
        "## Current train-side file snippets:",
        snippet_block,
        "",
        "## Existing editable train-side files:",
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
        parts.extend(["", f"# .auto/{name} (truncated)", (text or "")[:1200]])
    data = "\n".join(parts)
    if len(data) <= max_chars:
        return data
    # Keep the front: it contains the todo, safety boundaries, editable-file
    # snippets, and action schema guidance. Tail truncation hid the actual task
    # and made Execute more likely to time out or return a non-mutating note.
    return data[: max(0, max_chars - 80)].rstrip() + "\n\n[execute context truncated]\n"


def _execute_fallback_context(ctx: PhaseContext, item: str, direct_failed: str = "", max_chars: int = 4500) -> str:
    """Compact fallback context for the full StepAgent action channel.

    Direct-write already saw the tight file context. If it times out, feeding a
    larger 10k+ parent_context into StepAgent tends to time out again. Keep this
    fallback task-focused and refer to artifacts instead of expanding them.
    """
    root = Path(ctx.root)
    task_context = _current_execute_task_context(ctx)
    target = _preferred_write_target(root, item)
    support = _direct_write_support_context(root, target, max_chars=1800)
    inventory = _train_side_inventory(root, max_files=30)
    notes = read_auto_notes(root, max_files=6, max_chars_per_file=1200)
    payload = {
        "purpose": "Fallback after direct-write could not produce a safe edit. Prefer a small search_replace/write JSON change spec in a note, or one mutating write/apply_patch action.",
        "todo": str(item)[:1000],
        "task_context": task_context,
        "previous_direct_write_error": str(direct_failed or "")[:800],
        "preferred_path": target,
        "eval_import_targets": eval_import_targets(root),
        "editable_train_side_files": inventory,
        "support_context": support,
        "recent_auto_notes": notes,
        "constraints": [
            "Do not edit eval.py, eval.sh, blackbox_oracle.py.",
            "Prefer a small JSON change spec when possible: {kind: search_replace, path, old, new} or {kind: write, path, content}.",
            "Use full-file write only when a small patch is not enough.",
            "Use artifact paths in task_context for trace; do not paste full old logs.",
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 80)].rstrip() + "\n\n[execute fallback context truncated]\n"


def _note_action(item: str):
    from autoresearch.legacy.loop import AutoResearchAction

    return AutoResearchAction(type="note", rationale="execute_apply_change", content=item)


def _note_change_spec_action(action):
    try:
        from autoresearch.legacy.loop import AutoResearchAction, _extract_change_spec
    except Exception:
        return None
    if getattr(action, "type", "") != "note":
        return None
    spec = _extract_change_spec(str(getattr(action, "content", "") or ""))
    if not spec:
        return None
    content = json.dumps(spec, ensure_ascii=False)
    return AutoResearchAction(type="note", rationale=getattr(action, "rationale", "execute_change_spec"), content=content)


def _execute_behavior_command(root: Path, action=None) -> Optional[str]:
    """Pick a project-owned train-side command for Execute smoke verification.

    This intentionally avoids final eval files. It is only a post-edit behavior
    check that proves the new training-side code can run and leave artifacts for
    the later Run/Evaluate phases.
    """
    action_paths = []
    if isinstance(action, _ExecuteActionBundle):
        action_paths = [str(getattr(a, "path", "") or "") for a in action.actions]
    else:
        action_paths = [str(getattr(action, "path", "") or "")]
    action_path = action_paths[0] if action_paths else ""
    if "train/train.sh" in action_paths and (root / "train" / "train.sh").exists():
        return "bash train/train.sh"
    if "train/train.py" in action_paths and (root / "train" / "train.py").exists():
        return "python3 train/train.py"
    if action_path and action_path not in {"train/train.py", "train/train.sh"} and not (root / "train" / "train.sh").exists():
        return None
    if (root / "train" / "train.sh").exists():
        return "bash train/train.sh"
    if (root / "train" / "train.py").exists():
        return "python3 train/train.py"
    return None


def _save_execute_behavior_artifact(loop, ctx: PhaseContext, payload: dict) -> str:
    artifact_path = ""
    artifacts = getattr(loop, "artifacts", None) if loop is not None else None
    if artifacts is not None:
        try:
            artifact_path = artifacts.save(
                kind="execute_behavior",
                rationale="execute_behavior_check",
                content=json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                extension="json",
            )
        except Exception:
            artifact_path = ""
    if not artifact_path:
        path = Path(ctx.root) / ".autoresearch" / "artifacts" / f"{int(time.time() * 1000)}_execute_behavior.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        artifact_path = str(path)
    return artifact_path


def _record_execute_behavior_note(ctx: PhaseContext, payload: dict, artifact_path: str) -> None:
    result = payload.get("result", {})
    command = payload.get("command") or "(none)"
    metric = result.get("metric")
    summary = [
        "# Execute Validation",
        "",
        f"- task: {payload.get('item', '')}",
        f"- command: `{command}`",
        f"- status: {payload.get('status', '')}",
        f"- static_verified: {payload.get('static_verified')}",
        f"- behavior_verified: {payload.get('behavior_verified')}",
        f"- artifact: {artifact_path}",
    ]
    if metric is not None:
        summary.append(f"- metric: {metric} higher_is_better={result.get('higher_is_better')}")
    submission = result.get("submission") or {}
    if submission:
        summary.append(f"- submission: `{json.dumps(submission, ensure_ascii=False, sort_keys=True)}`")
    note = payload.get("note") or ""
    if note:
        summary.extend(["", "## Note", note[:1600]])
    write_auto_note(ctx.root, "execute_validation", "\n".join(summary).rstrip() + "\n")


def _execute_behavior_check(ctx: PhaseContext, item: str, action, obs, *, static_verified: bool) -> dict:
    loop = ctx.loop
    root = Path(ctx.root)
    before = getattr(ctx, "_autoresearch_execute_before", None)
    before = before if isinstance(before, dict) else {}
    after_write = project_fingerprint(root)
    enabled = bool(getattr(getattr(loop, "settings", None), "execute_behavior_check", True)) if loop is not None else False
    action_paths = [str(getattr(a, "path", "") or "") for a in action.actions] if isinstance(action, _ExecuteActionBundle) else [str(getattr(action, "path", "") or "")]
    direct_eval_targets = [path for path in action_paths if is_direct_eval_target(root, path)]
    command = ""
    command_result = None
    behavior_verified = True
    note = ""
    if enabled and loop is not None and direct_eval_targets:
        command = "python3 -m py_compile " + " ".join(shlex.quote(path) for path in direct_eval_targets) + " && import smoke"
        compile_result = loop.runner.run("python3 -m py_compile " + " ".join(shlex.quote(path) for path in direct_eval_targets))
        if compile_result.get("returncode") == 0:
            import_result = direct_eval_import_check(loop, direct_eval_targets[0])
            command_result = {
                "returncode": import_result.get("returncode"),
                "duration_seconds": round(float(compile_result.get("duration_seconds") or 0) + float(import_result.get("duration_seconds") or 0), 3),
                "stdout": (compile_result.get("stdout") or "") + (import_result.get("stdout") or ""),
                "stderr": (compile_result.get("stderr") or "") + (import_result.get("stderr") or ""),
                "timeout": bool(compile_result.get("timeout") or import_result.get("timeout")),
            }
        else:
            command_result = compile_result
        behavior_verified = command_result.get("returncode") == 0
        stdout = (command_result.get("stdout") or "")[-1200:]
        stderr = (command_result.get("stderr") or "")[-1200:]
        note = f"direct eval target smoke rc={command_result.get('returncode')} duration={command_result.get('duration_seconds')}s"
        if stderr:
            note += f"; stderr={stderr[:400]}"
        elif stdout:
            note += f"; stdout={stdout[:400]}"
    else:
        command = _execute_behavior_command(root, action) if enabled else None
    if command and loop is not None and not direct_eval_targets:
        timeout = int(getattr(getattr(loop, "settings", None), "execute_behavior_check_timeout_seconds", 60) or 60)
        old_timeout = getattr(loop.runner, "timeout_seconds", None)
        try:
            if old_timeout is not None:
                loop.runner.timeout_seconds = min(int(old_timeout), max(1, timeout))
            command_result = loop.runner.run(command)
        finally:
            if old_timeout is not None:
                loop.runner.timeout_seconds = old_timeout
        behavior_verified = command_result.get("returncode") == 0
        stdout = (command_result.get("stdout") or "")[-1200:]
        stderr = (command_result.get("stderr") or "")[-1200:]
        note = f"behavior command rc={command_result.get('returncode')} duration={command_result.get('duration_seconds')}s"
        if stderr:
            note += f"; stderr={stderr[:400]}"
        elif stdout:
            note += f"; stdout={stdout[:400]}"
    after_behavior = project_fingerprint(root)
    metric_info = metric_payload(root)
    regression_payload = read_project_json(root, ".autoresearch/regression_cases.json")
    changed_artifacts = changed_result_artifacts(before, after_behavior)
    expects_result_artifact = bool(command and not direct_eval_targets and has_visible_result_artifacts(before, after_write, after_behavior))
    if expects_result_artifact and not changed_artifacts:
        note = (note + "; " if note else "") + "warning: no tracked result artifact changed during behavior check"
    payload = {
        "item": item,
        "action": {
            "type": getattr(action, "type", ""),
            "path": getattr(action, "path", ""),
            "rationale": getattr(action, "rationale", ""),
        },
        "observation": {
            "status": getattr(obs, "status", ""),
            "summary": getattr(obs, "summary", "")[:1000],
            "artifact_path": getattr(obs, "artifact_path", ""),
        },
        "static_verified": bool(static_verified),
        "behavior_verified": bool(behavior_verified),
        "status": "ok" if static_verified and behavior_verified else "failed",
        "command": command or "",
        "command_result": {
            "returncode": command_result.get("returncode") if isinstance(command_result, dict) else None,
            "duration_seconds": command_result.get("duration_seconds") if isinstance(command_result, dict) else None,
            "stdout_tail": (command_result.get("stdout") or "")[-2000:] if isinstance(command_result, dict) else "",
            "stderr_tail": (command_result.get("stderr") or "")[-2000:] if isinstance(command_result, dict) else "",
            "timeout": bool(command_result.get("timeout")) if isinstance(command_result, dict) else False,
        },
        "before": before,
        "after_write": after_write,
        "after_behavior": after_behavior,
        "changed_artifacts": changed_artifacts,
        "expects_result_artifact": expects_result_artifact,
        "result": metric_info,
        "regression": regression_payload,
        "note": note,
    }
    artifact = _save_execute_behavior_artifact(loop, ctx, payload)
    _record_execute_behavior_note(ctx, payload, artifact)
    debug_event(
        root,
        "execute_behavior_check",
        status=payload["status"],
        command=command or "",
        behavior_verified=payload["behavior_verified"],
        static_verified=payload["static_verified"],
        metric=metric_info.get("metric"),
        artifact_path=artifact,
    )
    return {
        "ok": bool(static_verified and behavior_verified),
        "behavior": {
            "status": payload["status"],
            "command": command or "",
            "metric": metric_info.get("metric"),
            "higher_is_better": metric_info.get("higher_is_better"),
            "changed_artifacts": changed_artifacts,
            "submission": metric_info.get("submission", {}),
        },
        "artifact_path": artifact,
        "note": note,
    }


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
        write_eval_contract_digest(ctx.root)
        write_failure_digest(ctx.root)
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
            summary = "execute: no plan/todo found; replan required"
            write_auto_note(ctx.root, "execute_report", "# Execute Report\n\n" + summary + "\n")
            return PhaseResult(signals_update={"plan_still_valid": False}, summary=summary)

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
        repeat_current_window = False
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
            exec_item, subgoal_meta = _execution_attempt(task, item)
            if task:
                setattr(ctx, "_autoresearch_current_task", task)
                setattr(ctx, "_autoresearch_current_subgoal", subgoal_meta)
            res = fn(exec_item, ctx) or {}
            res.update(subgoal_meta)
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
            if task and _task_should_continue_subgoals(todo_state, task):
                repeat_current_window = True
                # Stop this Execute visit after a successful focused subgoal.
                # The next visit will continue the same task with the updated
                # subgoal cursor. Continuing to unrelated ready tasks in the
                # same window makes broad implementation plans expensive and
                # hides the useful intermediate behavior check.
                if res.get("status") in {"ok", "done"} and res.get("verification"):
                    break
            if task and res.get("status") in {"failed", "tried"} and not res.get("verification"):
                repeat_current_window = True
            # verification hard-constraint: only verified items count as done.
            subgoal_done = int(res.get("subgoal_index") or 0) + 1 >= int(res.get("subgoal_count") or 1)
            if res.get("status") in {"ok", "done", "skipped"} and res.get("verification") and subgoal_done:
                done += 1
                any_verified = True

        if using_todo_state:
            save_todo_state(ctx.root, todo_state)

        if cap > 0:
            _save_execute_cursor(ctx.root, plan_key, start if repeat_current_window else (0 if more_pending is False else end))

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
    return _execution_attempt(task, item)[0]


def _task_should_continue_subgoals(state: dict, task: dict) -> bool:
    for existing in state.get("tasks", []):
        if existing.get("task_id") != task.get("task_id"):
            continue
        if existing.get("status") != "in_progress":
            return False
        last = existing.get("last_result") or {}
        try:
            return int(last.get("next_subgoal_index") or 0) > int(last.get("subgoal_index") or 0)
        except Exception:
            return False
    return False


def _execution_attempt(task: Optional[dict], item: str) -> tuple[str, dict]:
    """Select a small current subgoal from an oversized implementation task."""
    empty_meta = {"subgoal_index": 0, "subgoal_count": 1}
    if not task:
        return item, empty_meta
    if task.get("repairs_task_id"):
        return item, empty_meta
    text = str(item or "").strip()
    pieces = _split_implementation_goal(text)
    if len(text) <= 700 and len(pieces) <= 1:
        return text, empty_meta
    last = task.get("last_result") if isinstance(task, dict) else {}
    next_index = 0
    if isinstance(last, dict):
        try:
            next_index = int(last.get("next_subgoal_index") or 0)
        except Exception:
            next_index = 0
    if not pieces:
        return text[:700], empty_meta
    index = min(max(0, next_index), len(pieces) - 1)
    selected = pieces[index]
    focused = (
        "Implement this focused part of the larger task: "
        + selected[:650]
        + "\nKeep existing behavior working and prefer editing the most relevant existing train-side file."
    )
    return focused, {"subgoal_index": index, "subgoal_count": len(pieces)}


def _split_implementation_goal(text: str) -> list[str]:
    raw = str(text or "").strip()
    if "covering:" in raw:
        raw = raw.split("covering:", 1)[1]
    parts = []
    for chunk in raw.replace("\n", "; ").split(";"):
        cleaned = chunk.strip(" -.\t")
        if cleaned:
            parts.append(cleaned)
    return _coalesce_subgoals(parts)


def _coalesce_subgoals(parts: list[str], *, max_subgoals: int = 4) -> list[str]:
    if len(parts) <= max_subgoals:
        return parts
    buckets: list[tuple[str, list[str]]] = [
        ("Update train/train.sh or the existing train entrypoint so it calls the train-side optimizer and preserves submission JSON validation.", []),
        ("Implement or update the train-side optimizer/search module with persistent history, oracle verification, deduplication, global exploration, and local refinement.", []),
        ("Integrate optimizer output with train/train.py or candidate generation so outputs/submission.json always contains the best verified candidate.", []),
        ("Finalize robustness, rerun behavior, and project notes so repeated train/eval runs use the best verified incumbent.", []),
    ]
    for part in parts:
        low = part.lower()
        if "train.sh" in low or "entrypoint" in low:
            buckets[0][1].append(part)
        elif "optimizer" in low or "search" in low or "oracle" in low or "history" in low or "exploration" in low or "refinement" in low or "candidate" in low:
            buckets[1][1].append(part)
        elif "submission" in low or "train.py" in low or "write" in low or "output" in low:
            buckets[2][1].append(part)
        else:
            buckets[3][1].append(part)
    coalesced = []
    for summary, grouped in buckets:
        if grouped:
            coalesced.append(summary + " Details: " + "; ".join(grouped))
    return coalesced or parts[:max_subgoals]


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
        attempts += int(result.get("attempt_delta", 1))
    max_attempts = 3
    try:
        max_attempts = max(1, int(result.get("max_attempts") or 3))
    except Exception:
        max_attempts = 3
    subgoal_index = int(result.get("subgoal_index") or 0)
    subgoal_count = int(result.get("subgoal_count") or 1)
    subgoal_complete = bool(status in {"ok", "done"} and verified and subgoal_index + 1 < subgoal_count)
    if status == "skipped":
        target["status"] = "skipped"
    elif subgoal_complete:
        target["status"] = "in_progress"
    elif status in {"ok", "done"} and verified:
        target["status"] = "verified"
    elif status == "failed" and attempts >= max_attempts:
        target["status"] = "failed"
    elif status == "failed":
        target["status"] = "in_progress"
    elif attempts >= max_attempts:
        target["status"] = "failed"
    else:
        target["status"] = "in_progress"
    target["last_result"] = {
        "status": status,
        "verification": verified,
        "note": str(result.get("note") or "")[:1000],
        "behavior": result.get("behavior", {}) if isinstance(result.get("behavior"), dict) else {},
        "artifacts": [str(p) for p in (result.get("artifacts") or [])][:6] if isinstance(result.get("artifacts"), list) else [],
        "attempts": attempts,
        "max_attempts": max_attempts,
        "subgoal_index": subgoal_index,
        "subgoal_count": subgoal_count,
        "next_subgoal_index": subgoal_index + 1 if subgoal_complete else subgoal_index,
        "subgoal": str(result.get("subgoal") or "")[:1000],
        "updated_at": time.time(),
    }
    if last_result.get("context_artifact_path"):
        target["last_result"]["context_artifact_path"] = last_result.get("context_artifact_path")
    if isinstance(result.get("artifacts"), list):
        existing_artifacts = list(target.get("artifacts") or [])
        for path in result.get("artifacts") or []:
            if path and path not in existing_artifacts:
                existing_artifacts.append(str(path))
        target["artifacts"] = existing_artifacts[-12:]
    if target.get("repairs_task_id") and target.get("status") == "verified":
        repaired_id = str(target.get("repairs_task_id") or "")
        for existing in state.get("tasks", []):
            if existing.get("task_id") != repaired_id:
                continue
            if existing.get("status") == "failed":
                existing["status"] = "pending"
                deps = list(existing.get("depends_on") or [])
                if target["task_id"] not in deps:
                    deps.append(target["task_id"])
                existing["depends_on"] = deps
                last = dict(existing.get("last_result") or {})
                last["reopened_by_repair"] = target["task_id"]
                existing["last_result"] = last
            break


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
# Run/evidence side
# --------------------------------------------------------------------------- #

from autoresearch.run_handler import AutofixFn, RunFn, _find_search_driver, make_run_handler


__all__ = [
    "parse_todo_from_plan",
    "make_execute_handler",
    "make_run_handler",
    "ExecuteFn",
    "RunFn",
    "AutofixFn",
]
