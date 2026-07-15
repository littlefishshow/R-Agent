import json
from pathlib import Path

from autoresearch.legacy.loop import AutoResearchSettings, AutoResearchLoop, AutoResearchAction, AutoResearchStepResult
from autoresearch.execution import (
    parse_todo_from_plan,
    make_execute_handler,
    make_run_handler,
    _find_search_driver,
    _execution_attempt_item,
    _execute_parent_context,
    _execute_fallback_context,
    _is_train_side_write_path,
    _preferred_write_target,
)
from autoresearch.diagnostics import metric_payload, write_failure_digest
from autoresearch.phases import PhaseContext, PhaseSignals
from autoresearch.state.memory import write_auto_note
from autoresearch.state.todo import load_todo_state, save_todo_state


def _ctx(tmp_path, phase, loop=None, project_text="# Project State\n"):
    program_path = tmp_path / "program.md"
    return PhaseContext(
        phase=phase,
        root=tmp_path,
        program_text=program_path.read_text(encoding="utf-8") if program_path.exists() else "Goal\n",
        project_text=project_text,
        signals=PhaseSignals(phase=phase),
        loop=loop,
    )


# --------------------------------------------------------------------------- #
# Todo parsing
# --------------------------------------------------------------------------- #

def test_parse_todo_from_plan(tmp_path):
    write_auto_note(tmp_path, "plan", "# Detailed Plan\n1. edit config\n2. run training\n- extra bullet\n")
    items = parse_todo_from_plan(tmp_path)
    assert items == ["edit config", "run training", "extra bullet"]


def test_parse_todo_empty_when_no_plan(tmp_path):
    assert parse_todo_from_plan(tmp_path) == []


# --------------------------------------------------------------------------- #
# P3 Execute — verification hard-constraint
# --------------------------------------------------------------------------- #

def test_execute_counts_only_verified_items(tmp_path):
    write_auto_note(tmp_path, "plan", "1. task a\n2. task b\n3. task c\n")

    def fake_execute(item, ctx):
        # only "task a" verifies
        return {"item": item, "status": "ok", "verification": item == "task a"}

    handler = make_execute_handler(fake_execute)
    result = handler(_ctx(tmp_path, "execute"))
    assert "1/3 verified" in result.summary
    report = (tmp_path / ".auto" / "execute_report.md").read_text(encoding="utf-8")
    assert "task a: status=ok verified=True" in report
    assert "task b: status=ok verified=False" in report


def test_execute_all_unverified_flags_major_error(tmp_path):
    write_auto_note(tmp_path, "plan", "1. task a\n")

    def fake_execute(item, ctx):
        return {"item": item, "status": "failed", "verification": False}

    handler = make_execute_handler(fake_execute)
    result = handler(_ctx(tmp_path, "execute"))
    assert result.signals_update.get("major_error") is True
    lessons = (tmp_path / ".autoresearch" / "lessons.jsonl")
    assert lessons.exists()


def test_execute_parent_is_single_writer_of_project(tmp_path):
    write_auto_note(tmp_path, "plan", "1. task a\n")

    def fake_execute(item, ctx):
        return {"item": item, "status": "ok", "verification": True}

    handler = make_execute_handler(fake_execute)
    result = handler(_ctx(tmp_path, "execute"))
    # parent writes the change record into project.md
    assert "## 改动记录" in result.project_text
    assert "executed 1/1" in result.project_text


def test_execute_prefers_todo_state_and_updates_task_status(tmp_path):
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "t1", "goal": "edit config", "type": "implementation", "status": "pending", "priority": 1},
            {"task_id": "t2", "goal": "run eval", "type": "validation", "status": "pending", "priority": 2},
        ]
    })
    write_auto_note(tmp_path, "plan", "1. stale markdown task\n")
    seen = []

    def fake_execute(item, ctx):
        seen.append(item)
        return {"item": item, "status": "ok", "verification": True, "note": "done"}

    result = make_execute_handler(fake_execute)(_ctx(tmp_path, "execute"))
    assert seen == ["edit config"]
    assert "1/1 verified" in result.summary
    state = load_todo_state(tmp_path)
    assert state["tasks"][0]["status"] == "verified"
    assert state["tasks"][0]["last_result"]["note"] == "done"
    assert state["tasks"][1]["status"] == "pending"


def test_execute_analysis_task_writes_analysis_note(tmp_path):
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('train')\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "a1",
                "goal": "inspect train",
                "type": "analysis",
                "status": "pending",
                "context_paths": ["train/train.py"],
            }
        ]
    })
    result = make_execute_handler(lambda item, ctx: {"item": item, "status": "failed", "verification": False})(_ctx(tmp_path, "execute"))
    assert "1/1 verified" in result.summary
    state = load_todo_state(tmp_path)
    assert state["tasks"][0]["status"] == "verified"
    assert "analysis written" in state["tasks"][0]["last_result"]["note"]
    assert (tmp_path / ".auto" / "analysis_a1.md").exists()
    assert "print('train')" in (tmp_path / ".auto" / "analysis_a1.md").read_text(encoding="utf-8")


def test_execute_default_fn_verifies_via_py_compile(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    # queue a write spec that creates a valid python file
    spec = {"kind": "write", "path": "gen.py", "content": "x = 1\n"}
    (tmp_path / ".autoresearch").mkdir(exist_ok=True)
    (tmp_path / ".autoresearch" / "proposed_change.json").write_text(json.dumps(spec), encoding="utf-8")
    write_auto_note(tmp_path, "plan", "1. create gen.py\n")

    handler = make_execute_handler()
    result = handler(_ctx(tmp_path, "execute", loop=loop))
    assert (tmp_path / "gen.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "1/1 verified" in result.summary


def test_execute_apply_patch_false_success_downgraded_to_failed(tmp_path):
    """git apply reporting ok but writing nothing must NOT count as a real change."""
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            # apply_patch action whose "changed" file never lands on disk
            return AutoResearchStepResult(
                action=AutoResearchAction(type="apply_patch", rationale="phantom edit",
                                          patch="", content="")
            )

    # Fake execute_action: mimic a successful git-apply that lists a file which
    # was never actually written (the exact bug seen in nested/gitignored dirs).
    import autoresearch.execution as ex

    class Obs:
        status = "ok"
        summary = "git apply ok"
        artifact_path = str(tmp_path / "art.json")

    (tmp_path / "art.json").write_text(json.dumps({"changed_files": ["train/search.py"]}), encoding="utf-8")
    loop.step_agent = Agent()
    loop.execute_action = lambda action: Obs()
    write_auto_note(tmp_path, "plan", "1. create search.py\n")

    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    # nothing verified -> not counted as done, and (single item, none pending) -> major_error
    assert "0/1 verified" in result.summary
    assert result.signals_update.get("major_error") is True


def test_execute_prefers_write_action_surface(tmp_path):
    """The execute step must offer 'write' ahead of the fragile 'apply_patch'."""
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    captured = {}

    class Agent:
        def plan_step(self, *, step, fallback_action, parent_context, round_index):
            captured["allowed"] = list(step.allowed_tools)
            captured["ctx"] = parent_context
            return AutoResearchStepResult(
                action=AutoResearchAction(type="write", rationale="w", path="train/s.py", content="y = 2\n")
            )

    loop.step_agent = Agent()
    write_auto_note(tmp_path, "plan", "1. build search\n")
    make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert captured["allowed"][0] == "write"
    assert captured["allowed"].index("write") < captured["allowed"].index("apply_patch")
    assert "Prefer a small JSON change spec" in captured["ctx"]


def test_execute_context_lists_existing_files_and_tiers(tmp_path):
    """Execute context must surface existing train-side files + the 3-tier rule
    so the LLM edits what exists instead of spawning parallel helpers."""
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text("echo hi\n", encoding="utf-8")
    (tmp_path / "train" / "search.py").write_text("x = 1\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    captured = {}

    class Agent:
        def plan_step(self, *, step, fallback_action, parent_context, round_index):
            captured["ctx"] = parent_context
            return AutoResearchStepResult(
                action=AutoResearchAction(type="write", rationale="w", path="train/search.py", content="x = 2\n")
            )

    loop.step_agent = Agent()
    write_auto_note(tmp_path, "plan", "1. improve search\n")
    make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    ctx = captured["ctx"]
    # existing files listed for Tier-1 in-place editing
    assert "train/train.sh" in ctx
    assert "train/search.py" in ctx
    # the minimal-change rule is present
    assert "Fallback after direct-write" in ctx
    assert "Prefer a small JSON change spec" in ctx


def test_execute_direct_write_failure_falls_back_to_step_agent(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            raise RuntimeError("direct channel unavailable")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    class Agent:
        model = "test-model"

        def __init__(self):
            self._tier = "exec"

        def _client(self):
            return _Client()

        def _resolved_model(self):
            return "test-model"

        def plan_step(self, *, step, fallback_action, parent_context, round_index):
            captured["ctx"] = parent_context
            return AutoResearchStepResult(
                action=AutoResearchAction(type="write", rationale="fallback write", path="train/fallback.py", content="x = 1\n")
            )

    loop.step_agent = Agent()
    write_auto_note(tmp_path, "plan", "1. create helper\n")
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert (tmp_path / "train" / "fallback.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "1/1 verified" in result.summary
    assert "previous_direct_write_error" in captured["ctx"]
    assert "direct channel unavailable" in captured["ctx"]


def test_execute_direct_write_timeout_uses_compact_fallback_then_skips_direct_write(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "create helper", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_task_attempts=3)
    loop = AutoResearchLoop(settings)
    calls = {"plan_step": 0}

    class _Completions:
        def create(self, **kwargs):
            raise TimeoutError("execute direct write exceeded framework deadline of 45.0s")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    class Agent:
        model = "test-model"

        def __init__(self):
            self._tier = "exec"

        def _client(self):
            return _Client()

        def _resolved_model(self):
            return "test-model"

        def plan_step(self, **kwargs):
            calls["plan_step"] += 1
            assert "previous_direct_write_error" in kwargs["parent_context"]
            return AutoResearchStepResult(
                action=AutoResearchAction(type="write", rationale="should not run", path="train/x.py", content="x=1\n")
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert calls["plan_step"] == 1
    assert "1/1 verified" in result.summary
    assert (tmp_path / "train" / "x.py").exists()
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "verified"

    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "impl2",
                "goal": "create second helper",
                "type": "implementation",
                "status": "in_progress",
                "priority": 1,
                "last_result": {
                    "attempts": 1,
                    "note": "direct write failed: execute direct write exceeded framework deadline of 45.0s",
                },
            },
        ]
    })
    make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert calls["plan_step"] == 2


def test_execute_fallback_context_is_compact_and_traceable(tmp_path):
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('train')\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "impl",
                "goal": "edit train with trace",
                "type": "implementation",
                "status": "in_progress",
                "last_result": {
                    "note": "previous behavior stayed baseline",
                    "artifacts": ["/tmp/artifact.json"],
                    "behavior": {"metric": 10522.0, "command": "bash train/train.sh"},
                },
            }
        ]
    })
    task = load_todo_state(tmp_path)["tasks"][0]
    ctx = _ctx(tmp_path, "execute")
    setattr(ctx, "_autoresearch_current_task", task)
    text = _execute_fallback_context(ctx, "fix optimizer", "direct timeout", max_chars=2200)
    assert len(text) <= 2200
    assert "direct timeout" in text
    assert "/tmp/artifact.json" in text
    assert "previous behavior stayed baseline" in text
    assert "train/train.py" in text


def test_execute_read_action_retains_context_without_consuming_attempt(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('ctx')\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "inspect then edit", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    class _Completions:
        def create(self, **kwargs):
            raise RuntimeError("direct channel unavailable")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    class Agent:
        model = "test-model"

        def __init__(self):
            self._tier = "exec"

        def _client(self):
            return _Client()

        def _resolved_model(self):
            return "test-model"

        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(type="read", rationale="need file context", path="train/train.py")
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert "0/1 verified" in result.summary
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "in_progress"
    assert task["last_result"]["attempts"] == 0
    assert task["last_result"]["artifacts"]
    assert "read additional context" in task["last_result"]["note"]


def test_train_side_inventory_skips_noise(tmp_path):
    from autoresearch.execution import _train_side_inventory

    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text("x\n", encoding="utf-8")
    (tmp_path / "train" / "outputs").mkdir()
    (tmp_path / "train" / "outputs" / "junk.json").write_text("{}", encoding="utf-8")
    (tmp_path / "train" / "__pycache__").mkdir()
    (tmp_path / "train" / "__pycache__" / "c.py").write_text("x\n", encoding="utf-8")
    inv = _train_side_inventory(tmp_path)
    joined = "\n".join(inv)
    assert "train/train.sh" in joined
    assert "outputs" not in joined
    assert "__pycache__" not in joined


# --------------------------------------------------------------------------- #
# P4 Run — bounded autofix
# --------------------------------------------------------------------------- #

def test_run_success_no_autofix(tmp_path):
    def run_fn(ctx):
        return {"status": "ok", "returncode": 0}

    handler = make_run_handler(run_fn)
    result = handler(_ctx(tmp_path, "run"))
    assert result.signals_update == {}
    assert "status=ok" in result.summary
    assert (tmp_path / ".auto" / "run_report.md").exists()


def test_run_autofix_recovers_within_budget(tmp_path):
    state = {"calls": 0}

    def run_fn(ctx):
        state["calls"] += 1
        return {"status": "ok" if state["calls"] >= 2 else "failed", "returncode": 0 if state["calls"] >= 2 else 1}

    def autofix(ctx, last):
        return True  # claims to have fixed

    handler = make_run_handler(run_fn, autofix, max_autofix=2)
    result = handler(_ctx(tmp_path, "run"))
    assert result.signals_update == {}
    assert "autofix=1" in result.summary


def test_run_major_error_after_autofix_budget_exhausted(tmp_path):
    def run_fn(ctx):
        return {"status": "failed", "returncode": 1, "stderr": "boom"}

    def autofix(ctx, last):
        return True

    handler = make_run_handler(run_fn, autofix, max_autofix=2)
    result = handler(_ctx(tmp_path, "run"))
    assert result.signals_update.get("major_error") is True
    assert "autofix=2" in result.summary
    lessons = (tmp_path / ".autoresearch" / "lessons.jsonl")
    assert lessons.exists()


def test_run_stops_autofix_when_fix_not_attempted(tmp_path):
    calls = {"n": 0}

    def run_fn(ctx):
        calls["n"] += 1
        return {"status": "failed", "returncode": 1}

    def autofix(ctx, last):
        return False  # cannot fix -> stop immediately

    handler = make_run_handler(run_fn, autofix, max_autofix=5)
    result = handler(_ctx(tmp_path, "run"))
    assert result.signals_update.get("major_error") is True
    # run once, one autofix attempt that returns False -> no re-run
    assert calls["n"] == 1


def test_run_failure_writes_stderr_note_and_run_report(tmp_path):
    tb = "Traceback (most recent call last):\nAttributeError: 'list' object has no attribute 'get'"

    def run_fn(ctx):
        return {"status": "failed", "returncode": 1, "stderr": tb}

    handler = make_run_handler(run_fn)
    handler(_ctx(tmp_path, "run"))
    failure = (tmp_path / ".auto" / "run_failure.md")
    assert failure.exists()
    body = failure.read_text(encoding="utf-8")
    assert "has no attribute" in body
    # The run report also embeds the error output so it is impossible to miss.
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "## Material Passport" in report
    assert "FAILED" in report and "has no attribute" in report


def test_run_success_clears_stale_failure_note(tmp_path):
    from autoresearch.state.memory import write_auto_note
    write_auto_note(tmp_path, "run_failure", "# Run Failure\n\nold crash\n```\nboom\n```\n")

    def run_fn(ctx):
        return {"status": "ok", "returncode": 0}

    handler = make_run_handler(run_fn)
    handler(_ctx(tmp_path, "run"))
    body = (tmp_path / ".auto" / "run_failure.md").read_text(encoding="utf-8")
    assert "(resolved:" in body
    assert "boom" not in body


def test_failure_digest_headlines_active_run_failure(tmp_path):
    from autoresearch.state.memory import write_auto_note
    from autoresearch.diagnostics import failure_digest
    # A stale metrics.json plus an active run failure: the crash must headline
    # and the metric must be labeled stale.
    (tmp_path / "metrics.json").write_text(
        json.dumps({"primary_metric": 0.2, "primary_metric_name": "score", "higher_is_better": True}),
        encoding="utf-8",
    )
    write_auto_note(tmp_path, "run_failure",
                    "# Run Failure\n\nreturncode: 1\n\n## stderr / traceback (tail)\n```\nre.error: multiple repeat\n```\n")
    digest = failure_digest(tmp_path)
    assert "multiple repeat" in digest
    assert "STALE" in digest


def test_failure_digest_ignores_resolved_run_failure(tmp_path):
    from autoresearch.state.memory import write_auto_note
    from autoresearch.diagnostics import failure_digest
    (tmp_path / "metrics.json").write_text(
        json.dumps({"primary_metric": 0.9, "primary_metric_name": "score", "higher_is_better": True}),
        encoding="utf-8",
    )
    write_auto_note(tmp_path, "run_failure", "# Run Failure\n\n(resolved: the latest run exited 0)\n")
    digest = failure_digest(tmp_path)
    assert "STALE" not in digest
    assert "score=0.9" in digest


def test_execute_without_proposed_change_uses_step_agent_write(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(type="write", rationale="create train helper", path="train/helper.py", content="x = 1\n")
            )

    loop.step_agent = Agent()
    write_auto_note(tmp_path, "plan", "1. create helper\n")
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert (tmp_path / "train" / "helper.py").read_text(encoding="utf-8") == "x = 1\n"
    assert "1/1 verified" in result.summary


def test_execute_write_runs_train_side_behavior_check_and_records_trace(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('old')\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "update train behavior", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(
                    type="write",
                    rationale="write train",
                    path="train/train.py",
                    content=(
                        "import json\n"
                        "from pathlib import Path\n"
                        "Path('outputs').mkdir(exist_ok=True)\n"
                        "Path('outputs/submission.json').write_text(json.dumps({'x': 3, 'y': 4}) + '\\n')\n"
                        "Path('outputs/train_verification.json').write_text(json.dumps({'primary_metric': 7, 'z': 7, 'higher_is_better': False}) + '\\n')\n"
                        "print('train_primary_metric=7')\n"
                    ),
                )
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert "1/1 verified" in result.summary
    state = load_todo_state(tmp_path)
    task = state["tasks"][0]
    assert task["status"] == "verified"
    behavior = task["last_result"]["behavior"]
    assert behavior["status"] == "ok"
    assert behavior["command"] == "python3 train/train.py"
    assert behavior["metric"] == 7
    assert behavior["submission"] == {"x": 3, "y": 4}
    assert task["last_result"]["artifacts"]
    report = (tmp_path / ".auto" / "execute_validation.md").read_text(encoding="utf-8")
    assert "Execute Validation" in report
    assert "metric: 7" in report


def test_metric_payload_parses_string_false_direction_from_metrics(tmp_path):
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "submission.json").write_text(json.dumps({"x": 0, "y": 0}), encoding="utf-8")
    (tmp_path / "outputs" / "train_verification.json").write_text(json.dumps({"z": 10}), encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({"z": 10, "higher_is_better": "false"}), encoding="utf-8")
    payload = metric_payload(tmp_path)
    assert payload["metric"] == 10.0
    assert payload["higher_is_better"] is False


def test_execute_direct_write_bundle_writes_multiple_files_and_verifies_once(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('old')\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "create train optimizer bundle", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    payload = {
        "files": [
            {
                "path": "train/optimizer.py",
                "content": (
                    "import json\n"
                    "from pathlib import Path\n"
                    "def main():\n"
                    "    Path('outputs').mkdir(exist_ok=True)\n"
                    "    Path('outputs/submission.json').write_text(json.dumps({'x': 9, 'y': -2}) + '\\n')\n"
                    "    Path('outputs/train_verification.json').write_text(json.dumps({'primary_metric': 5, 'z': 5, 'higher_is_better': False}) + '\\n')\n"
                    "if __name__ == '__main__':\n"
                    "    main()\n"
                ),
            },
            {
                "path": "train/train.sh",
                "content": "#!/usr/bin/env bash\nset -e\npython3 train/optimizer.py\n",
            },
        ]
    }
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured["messages"] = kwargs.get("messages", [])
            class _Msg:
                content = json.dumps(payload)

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    class _Agent:
        model = "test-model"

        def __init__(self):
            self._tier = "exec"

        def _client(self):
            return _Client()

        def _resolved_model(self):
            return "test-model"

    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    loop.step_agent = _Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert "1/1 verified" in result.summary
    user_payload = json.loads(captured["messages"][1]["content"])
    assert user_payload["preferred_path"] == "train/optimizer.py"
    assert "train/train.sh" in user_payload["integration_hint"]
    assert (tmp_path / "train" / "optimizer.py").exists()
    traces = list((tmp_path / ".autoresearch" / "step_traces").glob("*execute_direct_write.json"))
    assert traces
    trace_payload = json.loads(traces[0].read_text(encoding="utf-8"))
    assert "raw_response" in trace_payload
    assert "train/optimizer.py" in trace_payload["raw_response"]
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "verified"
    behavior = task["last_result"]["behavior"]
    assert behavior["files_written"] == ["train/optimizer.py", "train/train.sh"]
    assert behavior["command"] == "bash train/train.sh"
    assert behavior["metric"] == 5
    assert behavior["submission"] == {"x": 9, "y": -2}


def test_execute_behavior_check_failure_prevents_task_verification(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('old')\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "break train behavior", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_task_attempts=1)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(
                    type="write",
                    rationale="write broken train",
                    path="train/train.py",
                    content="raise SystemExit(3)\n",
                )
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert "0/1 verified" in result.summary
    assert result.signals_update.get("major_error") is True
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "failed"
    assert task["last_result"]["behavior"]["status"] == "failed"
    assert task["last_result"]["behavior"]["command"] == "python3 train/train.py"


def test_execute_behavior_check_warns_when_existing_result_artifact_unchanged(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "submission").mkdir()
    (tmp_path / "submission" / "predictions.json").write_text(json.dumps({"old": True}) + "\n", encoding="utf-8")
    (tmp_path / "train" / "train.py").write_text(
        "from pathlib import Path\n"
        "Path('submission/cleaner.py').write_text('def clean_row(row):\\n    return row\\n')\n"
        "print('wrote helper only')\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "update train behavior", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_task_attempts=1)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(
                    type="write",
                    rationale="write train without predictions",
                    path="train/train.py",
                    content=(
                        "from pathlib import Path\n"
                        "Path('submission/cleaner.py').write_text('def clean_row(row):\\n    return row\\n')\n"
                        "print('wrote helper only')\n"
                    ),
                )
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert "1/1 verified" in result.summary
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "verified"
    assert task["last_result"]["behavior"]["status"] == "ok"
    assert task["last_result"]["behavior"]["changed_artifacts"] == []
    assert "warning: no tracked result artifact changed" in task["last_result"]["note"]


def test_default_run_records_metric_bearing_experiment(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "eval.sh").write_text("#!/usr/bin/env bash\necho 'primary_metric_name: score'\necho 'primary_metric: 0.5'\necho 'higher_is_better: true'\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update == {}
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert state["experiments"][0]["metrics"]["score"] == 0.5
    assert state["experiments"][0]["primary_metric_name"] == "score"


def test_run_spec_train_only_refreshes_eval_metrics(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text("#!/usr/bin/env bash\necho train-only\n", encoding="utf-8")
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\n"
        "cat > metrics.json <<'JSON'\n"
        "{\"primary_metric\": 0.75, \"primary_metric_name\": \"score\", \"higher_is_better\": true}\n"
        "JSON\n"
        "cat metrics.json\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "run-train",
                "type": "validation",
                "status": "pending",
                "priority": 1,
                "run_spec": {"mode": "single", "commands": ["bash train/train.sh"]},
            }
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))

    assert result.signals_update == {}
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    exp = state["experiments"][0]
    assert exp["metrics"]["score"] == 0.75
    assert exp["primary_metric_name"] == "score"
    assert "bash train/train.sh && bash eval.sh && cat metrics.json" in exp["summary"]


# --------------------------------------------------------------------------- #
# C: hardened todo parsing
# --------------------------------------------------------------------------- #

def test_parse_todo_handles_step_and_dash_forms(tmp_path):
    write_auto_note(tmp_path, "plan", "# Plan\nStep 1: do a\nStep 2. do b\n3 - do c\n• do d\n")
    items = parse_todo_from_plan(tmp_path)
    assert items == ["do a", "do b", "do c", "do d"]


def test_parse_todo_fallback_to_body_when_no_list(tmp_path):
    write_auto_note(tmp_path, "plan", "# Plan\nJust run a broad search then refine around the best point.\n")
    items = parse_todo_from_plan(tmp_path)
    assert len(items) == 1
    assert "broad search" in items[0]


def test_execute_without_plan_requests_replan_instead_of_llm_fallback(tmp_path):
    called = []

    def fake_execute(item, ctx):
        called.append(item)
        return {"item": item, "status": "ok", "verification": True}

    result = make_execute_handler(fake_execute)(_ctx(tmp_path, "execute"))
    assert called == []
    assert result.signals_update == {"plan_still_valid": False}
    assert "replan required" in result.summary


# --------------------------------------------------------------------------- #
# B: Execute phase bounds actions per visit with a plan-keyed cursor
# --------------------------------------------------------------------------- #

def test_execute_caps_actions_per_step_and_advances_cursor(tmp_path):
    write_auto_note(tmp_path, "plan", "1. a\n2. b\n3. c\n4. d\n5. e\n")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_actions_per_step=2)
    loop = AutoResearchLoop(settings)

    seen = []

    def fake_execute(item, ctx):
        seen.append(item)
        return {"item": item, "status": "ok", "verification": True}

    handler = make_execute_handler(fake_execute)
    r1 = handler(_ctx(tmp_path, "execute", loop=loop))
    assert seen == ["a", "b"]
    assert "+3 pending" in r1.summary
    # second visit picks up where the cursor left off
    r2 = handler(_ctx(tmp_path, "execute", loop=loop))
    assert seen == ["a", "b", "c", "d"]
    assert "+1 pending" in r2.summary
    r3 = handler(_ctx(tmp_path, "execute", loop=loop))
    assert seen[-1] == "e"
    assert "pending" not in r3.summary


def test_execute_no_major_error_while_items_pending(tmp_path):
    write_auto_note(tmp_path, "plan", "1. a\n2. b\n3. c\n")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_actions_per_step=1)
    loop = AutoResearchLoop(settings)

    def fake_execute(item, ctx):
        return {"item": item, "status": "failed", "verification": False}

    handler = make_execute_handler(fake_execute)
    r1 = handler(_ctx(tmp_path, "execute", loop=loop))
    # still items pending -> don't flag major error yet
    assert r1.signals_update == {"execute_has_open_tasks": True}


def test_execute_skips_non_implementation_todos(tmp_path):
    write_auto_note(tmp_path, "plan", "1. run bash train/train.sh then bash eval.sh\n2. analyze logged observations\n")
    called = []

    def fake_execute(item, ctx):
        called.append(item)
        return {"item": item, "status": "ok", "verification": True}

    result = make_execute_handler(fake_execute)(_ctx(tmp_path, "execute"))
    assert called == []
    assert "2/2 verified" in result.summary
    report = (tmp_path / ".auto" / "execute_report.md").read_text(encoding="utf-8")
    assert "non-implementation todo" in report


def test_execute_does_not_verify_structured_run_tasks(tmp_path):
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "edit config", "type": "implementation", "status": "verified", "priority": 1},
            {"task_id": "val", "goal": "run eval", "type": "validation", "status": "pending", "priority": 2,
             "depends_on": ["impl"], "run_spec": {"commands": ["bash eval.sh"]}},
        ]
    })
    seen = []

    def fake_execute(item, ctx):
        seen.append(item)
        return {"item": item, "status": "ok", "verification": True}

    result = make_execute_handler(fake_execute)(_ctx(tmp_path, "execute"))
    assert seen == []
    assert "no ready execute tasks" in result.summary
    state = load_todo_state(tmp_path)
    assert state["tasks"][1]["status"] == "pending"


def test_execute_yields_to_ready_run_checkpoint_before_later_implementation(tmp_path):
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "a", "goal": "inspect", "type": "analysis", "status": "verified", "priority": 1},
            {"task_id": "baseline", "goal": "run baseline", "type": "validation", "status": "pending", "priority": 2,
             "run_spec": {"commands": ["bash eval.sh"]}},
            {"task_id": "impl", "goal": "edit train", "type": "implementation", "status": "pending", "priority": 3},
        ]
    })
    seen = []

    def fake_execute(item, ctx):
        seen.append(item)
        return {"item": item, "status": "ok", "verification": True}

    result = make_execute_handler(fake_execute)(_ctx(tmp_path, "execute"))
    assert seen == []
    assert "no ready execute tasks" in result.summary
    assert result.signals_update["execute_has_open_tasks"] is False
    assert "plan_still_valid" not in result.signals_update


def test_execute_stays_in_execute_when_structured_execute_tasks_remain(tmp_path):
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "a", "goal": "edit a", "type": "implementation", "status": "pending", "priority": 1},
            {"task_id": "b", "goal": "edit b", "type": "implementation", "status": "pending", "priority": 2},
            {"task_id": "v", "goal": "run eval", "type": "validation", "status": "pending", "priority": 3,
             "depends_on": ["a", "b"], "run_spec": {"commands": ["bash eval.sh"]}},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_actions_per_step=1)
    loop = AutoResearchLoop(settings)

    def fake_execute(item, ctx):
        return {"item": item, "status": "ok", "verification": True}

    result = make_execute_handler(fake_execute)(_ctx(tmp_path, "execute", loop=loop))
    assert result.signals_update["execute_has_open_tasks"] is True
    state = load_todo_state(tmp_path)
    assert state["tasks"][0]["status"] == "verified"
    assert state["tasks"][1]["status"] == "pending"


def test_execute_failed_attempts_eventually_fail_task(tmp_path):
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "edit train", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_task_attempts=2)
    loop = AutoResearchLoop(settings)

    def fake_execute(item, ctx):
        return {"item": item, "status": "tried", "verification": False, "note": "no edit"}

    handler = make_execute_handler(fake_execute)
    first = handler(_ctx(tmp_path, "execute", loop=loop))
    assert first.signals_update["execute_has_open_tasks"] is True
    state = load_todo_state(tmp_path)
    assert state["tasks"][0]["status"] == "in_progress"
    assert state["tasks"][0]["last_result"]["attempts"] == 1

    second = handler(_ctx(tmp_path, "execute", loop=loop))
    state = load_todo_state(tmp_path)
    assert state["tasks"][0]["status"] == "failed"
    assert state["tasks"][0]["last_result"]["attempts"] == 2
    assert second.signals_update.get("major_error") is True


def test_execute_retries_same_failed_task_before_advancing_cursor(tmp_path):
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "a", "goal": "edit first", "type": "implementation", "status": "pending", "priority": 1},
            {"task_id": "b", "goal": "edit second", "type": "implementation", "status": "pending", "priority": 2},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_actions_per_step=1, execute_max_task_attempts=3)
    loop = AutoResearchLoop(settings)
    seen = []

    def fake_execute(item, ctx):
        seen.append(item)
        return {"item": item, "status": "tried", "verification": False, "note": "needs repair"}

    handler = make_execute_handler(fake_execute)
    handler(_ctx(tmp_path, "execute", loop=loop))
    handler(_ctx(tmp_path, "execute", loop=loop))
    assert seen == ["edit first", "edit first"]
    state = load_todo_state(tmp_path)
    assert state["tasks"][0]["status"] == "in_progress"
    assert state["tasks"][1]["status"] == "pending"


def test_execution_attempt_item_focuses_long_implementation_task():
    item = "Implement consolidated change covering: " + "; ".join([
        "Add optimizer " + ("x" * 300),
        "Add logging " + ("y" * 300),
        "Add restarts " + ("z" * 300),
    ])
    task = {"task_id": "t", "type": "implementation", "last_result": {"next_subgoal_index": 1}}
    focused = _execution_attempt_item(task, item)
    assert "Add logging" in focused
    assert "Add optimizer" not in focused


def test_execute_subgoal_success_keeps_long_task_in_progress(tmp_path):
    long_goal = "Implement consolidated change covering: " + "; ".join([
        "Add optimizer " + ("x" * 300),
        "Add logging " + ("y" * 300),
    ])
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": long_goal, "type": "implementation", "status": "pending", "priority": 1},
        ]
    })

    def fake_execute(item, ctx):
        return {"item": item, "status": "ok", "verification": True, "note": "wrote one file"}

    result = make_execute_handler(fake_execute)(_ctx(tmp_path, "execute"))
    state = load_todo_state(tmp_path)
    task = state["tasks"][0]
    assert task["status"] == "in_progress"
    assert task["last_result"]["next_subgoal_index"] == 1
    assert "0/1 verified" in result.summary


def test_execute_repair_success_reopens_failed_run_checkpoint(tmp_path):
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "baseline", "goal": "run baseline", "type": "validation", "status": "failed", "priority": 1},
            {
                "task_id": "repair_baseline",
                "goal": "fix train entrypoint",
                "type": "implementation",
                "status": "pending",
                "priority": 2,
                "repairs_task_id": "baseline",
            },
        ]
    })

    def fake_execute(item, ctx):
        return {"item": item, "status": "ok", "verification": True, "note": "fixed"}

    make_execute_handler(fake_execute)(_ctx(tmp_path, "execute"))
    state = load_todo_state(tmp_path)
    by_id = {task["task_id"]: task for task in state["tasks"]}
    assert by_id["repair_baseline"]["status"] == "verified"
    assert by_id["baseline"]["status"] == "pending"
    assert "repair_baseline" in by_id["baseline"]["depends_on"]
    assert by_id["baseline"]["last_result"]["reopened_by_repair"] == "repair_baseline"


def test_execute_repeats_current_task_until_subgoals_finish(tmp_path):
    long_goal = "Implement consolidated change covering: " + "; ".join([
        "Add optimizer " + ("x" * 300),
        "Add logging " + ("y" * 300),
    ])
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "a", "goal": long_goal, "type": "implementation", "status": "pending", "priority": 1},
            {"task_id": "b", "goal": "edit later", "type": "implementation", "status": "pending", "priority": 2},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_actions_per_step=1)
    loop = AutoResearchLoop(settings)
    seen = []

    def fake_execute(item, ctx):
        seen.append(item)
        return {"item": item, "status": "ok", "verification": True, "note": "wrote"}

    handler = make_execute_handler(fake_execute)
    handler(_ctx(tmp_path, "execute", loop=loop))
    handler(_ctx(tmp_path, "execute", loop=loop))
    assert "Add optimizer" in seen[0]
    assert "Add logging" in seen[1]
    assert "edit later" not in "\n".join(seen)


def test_execute_parent_context_truncation_keeps_todo_and_action_guidance(tmp_path):
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('train')\n", encoding="utf-8")
    text = _execute_parent_context(tmp_path, "P" * 10000, "edit train.py", max_chars=1200)
    assert "Todo: edit train.py" in text
    assert "MUST return a mutating action" in text
    assert "train/train.py" in text
    assert "execute context truncated" in text


def test_direct_write_path_guard():
    assert _is_train_side_write_path("train/train.py") is True
    assert _is_train_side_write_path("src/model.py") is True
    assert _is_train_side_write_path("README.md") is True
    assert _is_train_side_write_path("notes/plan.md") is True
    assert _is_train_side_write_path("eval.py") is False
    assert _is_train_side_write_path("train/../eval.py") is False
    assert _is_train_side_write_path("blackbox_oracle.py") is False


def test_preferred_write_target_uses_goal_keywords(tmp_path):
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print(1)\n", encoding="utf-8")
    assert _preferred_write_target(tmp_path, "Implement train optimizer with history") == "train/optimizer.py"
    assert _preferred_write_target(tmp_path, "Update train.sh entrypoint") == "train/train.sh"
    assert _preferred_write_target(tmp_path, "Add search driver") == "train/search.py"
    assert _preferred_write_target(tmp_path, "Improve the objective metric") == "train/train.py"
    assert _preferred_write_target(tmp_path, "Run candidate search with refinement") == "train/optimizer.py"


# --------------------------------------------------------------------------- #
# C: Run executes a self-iterating search driver when present
# --------------------------------------------------------------------------- #

def test_run_prefers_search_driver(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    # driver writes a metric line itself (stands in for many internal evals)
    (tmp_path / "train" / "search.py").write_text(
        "print('primary_metric_name: z'); print('primary_metric: 0.0'); print('higher_is_better: false')\n",
        encoding="utf-8",
    )
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update == {}
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "train/search.py" in report
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert state["experiments"][0]["metrics"]["z"] == 0.0


def test_find_search_driver_supports_driver_globs(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "search_driver.py").write_text("print('x')\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    assert _find_search_driver(_ctx(tmp_path, "run", loop=loop)) == "train/search_driver.py"


def test_run_fallback_loop_records_search_log(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nmkdir -p outputs\nprintf '{\"x\":1,\"y\":2}\\n' > outputs/submission.json\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\nprintf '{\"primary_metric\":3,\"z\":3,\"higher_is_better\":false}\\n' > metrics.json\necho primary_metric=3\n",
        encoding="utf-8",
    )
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update == {}
    lines = (tmp_path / "outputs" / "search_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 1
    row = json.loads(lines[-1])
    assert row["x"] == 1 and row["y"] == 2 and row["z"] == 3


def test_run_uses_todo_state_run_spec_and_updates_task(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "v1",
                "goal": "custom validation",
                "type": "validation",
                "status": "pending",
                "priority": 1,
                "run_spec": {
                    "mode": "single",
                    "commands": [
                        "mkdir -p outputs",
                        "printf '{\"x\":3,\"y\":4}\\n' > outputs/submission.json",
                        "printf '{\"primary_metric\":7,\"z\":7,\"higher_is_better\":false}\\n' > metrics.json",
                    ],
                },
            }
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update == {}
    assert json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))["z"] == 7
    state = load_todo_state(tmp_path)
    task = state["tasks"][0]
    assert task["status"] == "verified"
    assert task["last_result"]["inner_evals"] == 1
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "inner_evals=1" in report


def test_run_task_accepts_metric_recovered_nonzero_wrapper(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "v1",
                "goal": "custom validation",
                "type": "validation",
                "status": "pending",
                "priority": 1,
                "run_spec": {
                    "mode": "single",
                    "commands": [
                        "printf '{\"primary_metric\":1.0,\"metric_name\":\"score\",\"higher_is_better\":true}\n' > metrics.json",
                        "cat metrics.json",
                        "python3 -c 'raise SystemExit(1)'",
                    ],
                },
            }
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))

    assert result.signals_update == {}
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "verified"
    assert task["last_result"]["status"] == "ok_metric_recovered"


def test_run_does_not_fallback_when_structured_run_task_is_blocked(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nmkdir -p outputs\nprintf '{\"x\":0,\"y\":0}\\n' > outputs/submission.json\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\nprintf '{\"primary_metric\":999,\"z\":999,\"higher_is_better\":false}\\n' > metrics.json\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "edit train", "type": "implementation", "status": "pending"},
            {"task_id": "val", "goal": "run eval", "type": "validation", "status": "pending",
             "depends_on": ["impl"], "run_spec": {"commands": ["bash train/train.sh", "bash eval.sh"]}},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler(max_autofix=0)(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update.get("major_error") is True
    assert not (tmp_path / "metrics.json").exists()
    state = load_todo_state(tmp_path)
    assert state["tasks"][1]["status"] == "pending"


def test_run_task_verification_metric_threshold_can_fail(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "v1",
                "goal": "metric threshold",
                "type": "validation",
                "status": "pending",
                "run_spec": {
                    "mode": "single",
                    "commands": [
                        "mkdir -p outputs",
                        "printf '{\"x\":3,\"y\":4}\\n' > outputs/submission.json",
                        "printf '{\"primary_metric\":7,\"z\":7,\"higher_is_better\":false}\\n' > metrics.json",
                    ],
                },
                "verification": {"metric_required": True, "metric_threshold": 1.0},
            }
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "failed"
    assert task["last_result"]["metric"] == 7


def test_run_spec_loop_repeats_until_budget(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nmkdir -p outputs\n"
        "n=$(cat outputs/counter.txt 2>/dev/null || echo 0)\n"
        "printf '{\"x\":%s,\"y\":0}\\n' \"$n\" > outputs/submission.json\n"
        "printf '%s' \"$((n+1))\" > outputs/counter.txt\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\n"
        "python3 - <<'PY'\n"
        "import json\n"
        "s=json.load(open('outputs/submission.json'))\n"
        "z=10-int(s['x'])\n"
        "json.dump({'primary_metric':z,'z':z,'higher_is_better':False}, open('metrics.json','w'))\n"
        "print('primary_metric=%s' % z)\n"
        "PY\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "loop",
                "goal": "loop eval",
                "type": "experiment",
                "status": "pending",
                "run_spec": {
                    "mode": "loop",
                    "commands": ["bash train/train.sh", "bash eval.sh"],
                    "max_iters": 5,
                    "max_seconds": 5.0,
                },
            }
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update == {}
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "inner_evals=5" in report
    assert (tmp_path / "outputs" / "counter.txt").read_text(encoding="utf-8") == "5"


def test_run_does_not_mark_solved_without_program_completion_criteria(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nmkdir -p outputs\nprintf '{\"x\":1,\"y\":2}\\n' > outputs/submission.json\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\nprintf '{\"primary_metric\":0.0,\"z\":0.0,\"higher_is_better\":false}\\n' > metrics.json\n",
        encoding="utf-8",
    )
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update == {}
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "solved=False" in report


def test_run_marks_solved_with_program_completion_criteria(tmp_path):
    (tmp_path / "program.md").write_text(
        "Goal\n\n## Completion Criteria\n- metric_name: z\n- higher_is_better: false\n- z <= 0.1\n",
        encoding="utf-8",
    )
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nmkdir -p outputs\nprintf '{\"x\":1,\"y\":2}\\n' > outputs/submission.json\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\nprintf '{\"primary_metric\":0.01,\"z\":0.01,\"higher_is_better\":false}\\n' > metrics.json\n",
        encoding="utf-8",
    )
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update.get("solved") is True
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "solved=True" in report


def test_default_run_is_single_even_when_eval_is_cheap(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nmkdir -p outputs\nprintf '{\"x\":1,\"y\":2}\\n' > outputs/submission.json\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\nprintf '{\"primary_metric\":3,\"z\":3,\"higher_is_better\":false}\\n' > metrics.json\n",
        encoding="utf-8",
    )
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False, run_max_inner_evals=5,
                                    run_max_inner_seconds=10.0)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update == {}
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "inner_evals=1" in report


def test_run_spec_long_job_runs_submit_and_one_monitor(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "job",
                "goal": "submit training job",
                "type": "experiment",
                "status": "pending",
                "run_spec": {
                    "mode": "long_job",
                    "commands": [
                        "mkdir -p outputs",
                        "printf submitted > outputs/job_status.txt",
                        "printf '{\"primary_metric\":5,\"z\":5,\"higher_is_better\":false}\\n' > metrics.json",
                    ],
                    "monitor_commands": [
                        "printf monitored >> outputs/job_status.txt",
                        "printf '{\"primary_metric\":4,\"z\":4,\"higher_is_better\":false}\\n' > metrics.json",
                    ],
                    "max_iters": 10,
                },
            }
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False)
    loop = AutoResearchLoop(settings)
    make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    report = (tmp_path / ".auto" / "run_report.md").read_text(encoding="utf-8")
    assert "inner_evals=2" in report
    assert (tmp_path / "outputs" / "job_status.txt").read_text(encoding="utf-8") == "submittedmonitored"
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "verified"


def test_run_task_failure_does_not_verify_from_stale_metrics_json(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(
        json.dumps({"primary_metric": 1, "z": 1, "higher_is_better": False}) + "\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "bad",
                "goal": "bad validation",
                "type": "validation",
                "status": "pending",
                "run_spec": {"mode": "single", "commands": ["false"]},
            }
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False)
    loop = AutoResearchLoop(settings)
    result = make_run_handler()(_ctx(tmp_path, "run", loop=loop))
    assert result.signals_update.get("major_error") is True
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "failed"
    assert task["last_result"]["status"] == "failed"


def test_run_writes_eval_contract_and_failure_digest(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "solution.py").write_text("def solve(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "eval.py").write_text(
        "import importlib.util, json\n"
        "from pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "metrics={'primary_metric':0.5,'metric_name':'accuracy','higher_is_better':True,"
        "'failures':[{'input':'a','expected':'b','pred':'c'}]}\n"
        "Path('metrics.json').write_text(json.dumps(metrics))\n"
        "print('primary_metric_name: accuracy')\nprint('primary_metric: 0.5')\nprint('higher_is_better: true')\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text("#!/usr/bin/env bash\npython3 eval.py\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    run_search_driver=False)
    loop = AutoResearchLoop(settings)

    make_run_handler()(_ctx(tmp_path, "run", loop=loop))

    contract = (tmp_path / ".auto" / "eval_contract.md").read_text(encoding="utf-8")
    failure = (tmp_path / ".auto" / "failure_digest.md").read_text(encoding="utf-8")
    regression = json.loads((tmp_path / ".autoresearch" / "regression_cases.json").read_text(encoding="utf-8"))
    assert "solution.py" in contract
    assert "solve" in contract
    assert "accuracy=0.5" in failure
    assert "expected" in failure and "pred" in failure
    assert regression["closeout"] is True
    assert regression["must_fix"][0]["expected"] == "b"


def test_execute_direct_write_prompt_includes_eval_and_failure_digests(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "solution.py").write_text("def solve(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "eval.py").write_text(
        "import importlib.util\nfrom pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))\n"
        "def main(): pass\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(json.dumps({
        "primary_metric": 0.0,
        "metric_name": "accuracy",
        "higher_is_better": True,
        "failures": [{"input": "x", "expected": "y", "pred": "z"}],
    }), encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "improve implementation", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured["user"] = kwargs["messages"][1]["content"]
            class _Msg:
                content = json.dumps({"path": "train/train.py", "content": "print('ok')\n"})
            class _Choice:
                message = _Msg()
            class _Resp:
                choices = [_Choice()]
            return _Resp()

    class _Client:
        class chat:
            completions = _Completions()

    class _Agent:
        model = "test-model"
        _tier = "exec"
        def _client(self):
            return _Client()
        def _resolved_model(self):
            return "test-model"

    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('old')\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_behavior_check=False)
    loop = AutoResearchLoop(settings)
    loop.step_agent = _Agent()

    make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))

    user = captured["user"]
    assert "eval_contract" in user
    assert "failure_digest" in user
    assert "regression_cases" in user
    assert "Use the must_fix cases" in user
    assert "solution.py" in user
    assert "expected" in user and "pred" in user


def test_closeout_regression_context_does_not_hard_block_execution(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "Path('metrics.json').write_text(json.dumps({'primary_metric': 0.4, 'metric_name': 'score', 'higher_is_better': True}))\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(json.dumps({
        "primary_metric": 0.8,
        "metric_name": "score",
        "higher_is_better": True,
        "failures": [{"input": "x", "expected": "y", "pred": "z"}],
    }), encoding="utf-8")
    (tmp_path / ".autoresearch").mkdir()
    (tmp_path / ".autoresearch" / "regression_cases.json").write_text(json.dumps({
        "closeout": True,
        "best": {"metric_name": "score", "metric": 0.8},
        "current": {"metric_name": "score", "metric": 0.8},
        "must_fix": [{"input": "x", "expected": "y", "pred": "z"}],
        "instructions": ["Use the must_fix cases to focus the patch and verify with the official eval."],
    }), encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "small patch", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_max_task_attempts=1)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(
                    type="write",
                    rationale="exploratory patch",
                    path="train/train.py",
                    content=(tmp_path / "train" / "train.py").read_text(encoding="utf-8"),
                )
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    task = load_todo_state(tmp_path)["tasks"][0]

    assert "1/1 verified" in result.summary
    assert task["status"] == "verified"
    assert "regression metric floor failed" not in task["last_result"]["note"]


def test_direct_eval_solution_write_uses_import_smoke_not_result_artifact(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "eval.py").write_text(
        "import importlib.util\nfrom pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "print(mod.solve('x'))\n",
        encoding="utf-8",
    )
    (tmp_path / "solution.py").write_text("def solve(x):\n    return x\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "update solution implementation", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(
                    type="write",
                    rationale="write solution",
                    path="solution.py",
                    content="def solve(x):\n    return 'ok'\n",
                )
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    task = load_todo_state(tmp_path)["tasks"][0]
    assert "1/1 verified" in result.summary
    assert task["status"] == "verified"
    assert "direct eval target smoke" in task["last_result"]["note"]
    assert task["last_result"]["behavior"]["command"].startswith("python3 -m py_compile solution.py")


def test_fallback_context_lists_eval_import_targets(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "eval.py").write_text(
        "import importlib.util\nfrom pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))\n",
        encoding="utf-8",
    )
    (tmp_path / "solution.py").write_text("def solve(x):\n    return x\n", encoding="utf-8")
    ctx = _ctx(tmp_path, "execute")
    payload = json.loads(_execute_fallback_context(ctx, "improve implementation", max_chars=10000))
    assert payload["eval_import_targets"] == ["solution.py"]
    assert "solution.py" in payload["support_context"]


def test_preferred_write_target_respects_explicit_project_path(tmp_path):
    (tmp_path / "solution.py").write_text("def solve():\n    pass\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("print('train')\n", encoding="utf-8")

    assert _preferred_write_target(tmp_path, "Patch solution.py to fix failures") == "solution.py"
    assert _preferred_write_target(tmp_path, "Update submission/solver.py generator") == "submission/solver.py"
    assert _preferred_write_target(tmp_path, "Do not touch eval.py; improve implementation") == "train/train.py"


def test_execute_fallback_note_change_spec_is_applied(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.py").write_text("value = 'old'\n", encoding="utf-8")
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "patch train/train.py", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False,
                                    execute_behavior_check=False)
    loop = AutoResearchLoop(settings)

    class Agent:
        def plan_step(self, **kwargs):
            return AutoResearchStepResult(
                action=AutoResearchAction(
                    type="note",
                    rationale="small patch",
                    content=json.dumps({
                        "kind": "search_replace",
                        "path": "train/train.py",
                        "old": "value = 'old'",
                        "new": "value = 'new'",
                    }),
                )
            )

    loop.step_agent = Agent()
    result = make_execute_handler()(_ctx(tmp_path, "execute", loop=loop))
    assert "1/1 verified" in result.summary
    assert (tmp_path / "train" / "train.py").read_text(encoding="utf-8") == "value = 'new'\n"
    task = load_todo_state(tmp_path)["tasks"][0]
    assert task["status"] == "verified"


def test_failure_digest_derives_mapping_diffs_when_metrics_has_no_failures(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "submission").mkdir()
    (tmp_path / "metrics.json").write_text(
        json.dumps({
            "primary_metric": 0.8,
            "primary_metric_name": "score",
            "higher_is_better": True,
            "accuracy": 1.0,
            "runtime_sec": 0.42,
            "score": 0.8,
        }),
        encoding="utf-8",
    )
    (tmp_path / "data" / "truth.json").write_text(json.dumps({
        "r1": {"name": "Alice", "age": "34"},
        "r2": {"name": "Bob", "age": "40"},
    }), encoding="utf-8")
    (tmp_path / "submission" / "predictions.json").write_text(json.dumps({
        "r1": {"name": "alice", "age": "34"},
        "r2": {"name": "Bob", "age": "41"},
    }), encoding="utf-8")

    path = write_failure_digest(tmp_path)
    text = Path(path).read_text(encoding="utf-8")

    assert "performance optimization problem" in text
    assert "derived mismatch rows" in text
    assert '"id": "r1"' in text
    assert '"actual": "alice"' in text
