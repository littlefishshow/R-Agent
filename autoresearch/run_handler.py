"""Run/evidence phase for AutoResearch."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from autoresearch.diagnostics import (
    metric_payload,
    parse_higher_is_better,
    read_json,
    write_eval_contract_digest,
    write_failure_digest,
)
from autoresearch.state.memory import append_lesson, write_auto_note
from autoresearch.phases import PhaseContext, PhaseResult
from autoresearch.state.completion import is_metric_solved, parse_completion_criteria
from autoresearch.state.todo import load_todo_state, ready_tasks, repair_failed_run_tasks, save_todo_state, task_phase

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


def _command_from_run_spec(run_spec: dict, *, fallback_command: str, root: str | Path | None = None) -> tuple[str, int, float, str, str, float]:
    run_spec = dict(run_spec or {})
    commands = run_spec.get("commands") or []
    if isinstance(commands, str):
        commands = [commands]
    commands = [str(c).strip() for c in commands if str(c).strip()]
    commands = _complete_metric_commands(commands, root=root)
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


def _complete_metric_commands(commands: list[str], *, root: str | Path | None = None) -> list[str]:
    """Ensure run specs that train also refresh metrics when an eval entry exists.

    Planner-generated repair/validation tasks sometimes contain only
    ``bash train/train.sh``. Treating that as a metric-bearing run causes stale
    ``metrics.json`` to leak into experiment records. If the task runs training
    but does not mention evaluation, append the conventional eval command and a
    metrics readback so the Run phase records fresh project-owned evidence.
    """
    if not commands:
        return commands
    joined = "\n".join(commands).lower()
    runs_train = "train/train.sh" in joined or "train/train.py" in joined
    runs_eval = "eval.sh" in joined or "eval.py" in joined
    has_eval_sh = bool(root is not None and (Path(root) / "eval.sh").exists())
    if runs_train and not runs_eval and has_eval_sh:
        commands = list(commands) + ["bash eval.sh"]
    joined = "\n".join(commands).lower()
    if ("eval.sh" in joined or "eval.py" in joined) and "metrics.json" not in joined:
        commands = list(commands) + ["cat metrics.json"]
    return commands


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


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _metric_from_file(root: str | Path) -> tuple[Optional[float], bool]:
    data = read_json(Path(root) / "metrics.json")
    value = data.get("z", data.get("primary_metric"))
    try:
        metric = float(value)
    except Exception:
        return None, parse_higher_is_better(data.get("higher_is_better", True))
    return metric, parse_higher_is_better(data.get("higher_is_better", True))


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
        command, spec_max_evals, spec_max_seconds, mode, monitor_command, poll_interval = _command_from_run_spec(
            run_task.get("run_spec"),
            fallback_command=fallback_command,
            root=ctx.root,
        )
        max_evals_override = spec_max_evals
        max_seconds_override = spec_max_seconds
        rationale = f"v2 run task {run_task.get('task_id')} ({mode})"
    else:
        command = fallback_command
        monitor_command = ""
        poll_interval = 0.0
        max_evals_override = None
        max_seconds_override = None
    from autoresearch.legacy.loop import AutoResearchAction, git_snapshot

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
    state = repair_failed_run_tasks(state)
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
        write_eval_contract_digest(ctx.root)
        write_failure_digest(ctx.root)
        criteria = parse_completion_criteria(ctx.program_text)
        solved = is_metric_solved(metric, criteria)
        driver = result.get("search_driver") or "(single train+eval)"
        write_auto_note(ctx.root, "run_report",
                        f"# Run Report\n\nstatus={result.get('status')} returncode={result.get('returncode')} "
                        f"autofix_attempts={attempts} driver={driver} inner_evals={result.get('inner_evals', 1)} "
                        f"solved={solved} criteria={criteria}\n")
        if major:
            append_lesson(ctx.root, kind="operational_error",
                          summary=f"run failed after {attempts} autofix attempts",
                          detail=str(result.get("stderr") or result.get("stdout") or "")[:2000])
        signals_update = {"major_error": True} if major else ({"solved": True} if solved else {})
        summary = f"run: status={result.get('status')} autofix={attempts}" + (" solved" if solved else "") + (" (major_error)" if major else "")
        return PhaseResult(signals_update=signals_update, summary=summary)

    return handler


__all__ = ['make_run_handler', 'RunFn', 'AutofixFn', '_find_search_driver']
