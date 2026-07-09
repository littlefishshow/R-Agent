import json
from pathlib import Path

from core.autoresearch_loop import AutoResearchSettings, AutoResearchLoop, AutoResearchAction, AutoResearchStepResult
from core.autoresearch_execution import (
    parse_todo_from_plan,
    make_execute_handler,
    make_run_handler,
    _find_search_driver,
    _execution_attempt_item,
    _execute_parent_context,
    _is_train_side_write_path,
)
from core.autoresearch_phases import PhaseContext, PhaseSignals
from core.autoresearch_memory import write_auto_note
from core.autoresearch_todo_state import load_todo_state, save_todo_state


def _ctx(tmp_path, phase, loop=None, project_text="# Project State\n"):
    return PhaseContext(
        phase=phase,
        root=tmp_path,
        program_text="Goal\n",
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
    import core.autoresearch_execution as ex

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
    assert "PREFER a full-file 'write'" in captured["ctx"]


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
    assert "change surface minimal" in ctx
    assert "prefer editing one existing train-side file" in ctx


def test_train_side_inventory_skips_noise(tmp_path):
    from core.autoresearch_execution import _train_side_inventory

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


def test_execution_attempt_item_focuses_long_implementation_task():
    item = "Implement consolidated change covering: " + "; ".join([
        "Add optimizer " + ("x" * 300),
        "Add logging " + ("y" * 300),
        "Add restarts " + ("z" * 300),
    ])
    task = {"task_id": "t", "type": "implementation", "last_result": {"attempts": 1}}
    focused = _execution_attempt_item(task, item)
    assert "Add logging" in focused
    assert "Add optimizer" not in focused


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
    assert _is_train_side_write_path("eval.py") is False
    assert _is_train_side_write_path("train/../eval.py") is False
    assert _is_train_side_write_path("blackbox_oracle.py") is False


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


def test_run_does_not_mark_solved_without_explicit_threshold(tmp_path):
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


def test_run_marks_solved_with_explicit_threshold(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n", encoding="utf-8")
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
                                    run_search_driver=False, solved_metric_threshold=0.1)
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
