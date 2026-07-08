import json
from pathlib import Path

from core.autoresearch_loop import AutoResearchSettings, AutoResearchLoop, AutoResearchAction, AutoResearchStepResult
from core.autoresearch_execution import (
    parse_todo_from_plan,
    make_execute_handler,
    make_run_handler,
)
from core.autoresearch_phases import PhaseContext, PhaseSignals
from core.autoresearch_memory import write_auto_note


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
    # the escalation rule is present
    assert "TIER 1" in ctx and "TIER 2" in ctx and "TIER 3" in ctx
    assert "cap 3" in ctx or "hard cap 3" in ctx


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
    assert r1.signals_update == {}


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
