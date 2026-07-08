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
