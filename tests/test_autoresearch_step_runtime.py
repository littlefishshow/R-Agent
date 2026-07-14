import json

from autoresearch.runtime_policy import (
    allowed_tools_for_step,
    build_step_context,
    excluded_tools_for_step,
    step_policy,
)


def test_step_runtime_policies_have_distinct_tool_surfaces():
    plan = step_policy("plan")
    attempt = step_policy("attempt")
    conclude = step_policy("conclude")

    assert plan.done_tag == "PLAN_DONE"
    assert attempt.done_tag == "ATTEMPT_DONE"
    assert conclude.done_tag == "CONCLUDE_DONE"
    assert "delegate_task" in plan.allowed_tools
    assert "delegate_task" in attempt.allowed_tools
    assert "delegate_task" not in conclude.allowed_tools
    assert "archive_subtask" in plan.allowed_tools
    assert "archive_subtask" in attempt.allowed_tools
    assert "archive_subtask" in conclude.allowed_tools
    assert "archive_subtask" in plan.child_allowed_tools
    assert "archive_subtask" in attempt.child_allowed_tools
    assert "archive_subtask" in conclude.child_allowed_tools
    assert "web_search" in plan.allowed_tools
    assert "web_extract" in plan.allowed_tools
    assert "web_search" not in attempt.allowed_tools
    assert "write_file" not in plan.child_allowed_tools
    assert "web_search" in plan.child_allowed_tools
    assert "write_file" in attempt.child_allowed_tools
    assert "delegate_task" in excluded_tools_for_step("attempt", child=True)
    assert plan.allowed_skills == ("codebase_scout",)


def test_step_runtime_tool_guard_blocks_non_whitelisted_and_recursive_child():
    plan = step_policy("plan")
    guard = plan.tool_guard()
    child_guard = plan.tool_guard(child=True)

    assert guard("read_file", "{}") is None
    assert "not allowed" in guard("write_file", "{}")
    assert guard("skill_view", json.dumps({"skill_name": "codebase_scout"})) is None
    assert "not allowed" in guard("skill_view", json.dumps({"skill_name": "other_skill"}))
    assert "child_allowed_tools" in guard("delegate_task", json.dumps({"tasks": "[]"}))
    assert guard("delegate_task", json.dumps({"tasks": "[]", "child_allowed_tools": ["read_file"]})) is None
    assert "disabled" in child_guard("delegate_task", "{}")


def test_build_step_context_contains_policy_and_bounded_files(tmp_path):
    (tmp_path / "program.md").write_text("Goal\n" + "p" * 5000, encoding="utf-8")
    (tmp_path / "project.md").write_text("# Project\n" + "q" * 5000, encoding="utf-8")
    (tmp_path / ".autoresearch").mkdir()
    (tmp_path / ".autoresearch" / "todo_state.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")

    ctx = build_step_context(tmp_path, "attempt", task={"task_id": "t1"}, max_chars=5000)
    assert ctx["step"] == "attempt"
    assert ctx["done_tag"] == "ATTEMPT_DONE"
    assert ctx["task"]["task_id"] == "t1"
    assert "write_file" in ctx["tool_policy"]["allowed_tools"]
    assert "delegate_task" in ctx["tool_policy"]["child_excluded_tools"]
    assert ctx["tool_policy"]["allowed_skills"] == ["codebase_scout"]
    assert "program_md" in ctx["files"]


def test_build_step_context_compacts_large_state_and_experiment_memory(tmp_path):
    (tmp_path / "program.md").write_text("Goal", encoding="utf-8")
    (tmp_path / "project.md").write_text("# Project", encoding="utf-8")
    state_dir = tmp_path / ".autoresearch"
    state_dir.mkdir()
    experiments = [
        {
            "experiment_id": f"exp-{i}",
            "timestamp": f"t{i}",
            "hypothesis": "h" * 800,
            "primary_metric_name": "score",
            "metrics": {"score": float(i)},
            "decision": "keep" if i == 11 else "neutral",
            "status": "ok",
            "summary": "s" * 1200,
            "artifact_path": f"artifact-{i}",
        }
        for i in range(12)
    ]
    (state_dir / "state.json").write_text(json.dumps({
        "experiments": experiments,
        "best_experiment": experiments[-1],
        "pareto_front": experiments[-3:],
        "useful_failures": [{"experiment_id": "bad", "summary": "failure" * 200}],
    }), encoding="utf-8")
    (state_dir / "experiment_memory.json").write_text(json.dumps({
        "current": {"metric_name": "score", "metric": 11},
        "best": {"experiment_id": "exp-11", "metric": 11},
        "guidance": ["use best snapshot"] * 20,
        "attempts": [{"experiment_id": f"exp-{i}", "summary": "x" * 500} for i in range(12)],
    }), encoding="utf-8")

    ctx = build_step_context(tmp_path, "plan", max_chars=8000)
    state_payload = json.loads(ctx["files"]["state_json"])
    memory_payload = json.loads(ctx["files"]["experiment_memory_json"])

    assert state_payload["compacted"] is True
    assert state_payload["best_experiment"]["experiment_id"] == "exp-11"
    assert [item["experiment_id"] for item in state_payload["latest_experiments"]] == [f"exp-{i}" for i in range(7, 12)]
    assert memory_payload["compacted"] is True
    assert memory_payload["best"]["experiment_id"] == "exp-11"
    assert len(memory_payload["latest_attempts"]) == 5
