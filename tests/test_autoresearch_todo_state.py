import json

from autoresearch.state.todo import (
    dependencies_satisfied,
    empty_todo_state,
    has_blocking_failed_tasks,
    has_open_tasks,
    has_failed_tasks,
    load_todo_state,
    merge_todo_state,
    normalize_task,
    normalize_todo_state,
    open_tasks,
    ready_execute_tasks,
    ready_tasks,
    repair_failed_run_tasks,
    render_todo_markdown,
    save_todo_state,
    task_phase,
    todo_state_path,
    upsert_task,
)


def test_empty_state_and_round_trip(tmp_path):
    state = empty_todo_state()
    assert state["version"] == 1
    assert state["tasks"] == []

    state["tasks"].append({"task_id": "t1", "goal": "edit train", "status": "pending"})
    path = save_todo_state(tmp_path, state)
    assert path == todo_state_path(tmp_path)

    loaded = load_todo_state(tmp_path)
    assert loaded["tasks"][0]["task_id"] == "t1"
    assert loaded["tasks"][0]["goal"] == "edit train"


def test_normalize_task_sanitizes_defaults():
    task = normalize_task({"id": "bad id!", "status": "weird", "type": "unknown", "goal": "G"})
    assert task["task_id"] == "bad_id"
    assert task["status"] == "pending"
    assert task["type"] == "implementation"
    assert task["goal"] == "G"
    assert task["depends_on"] == []
    assert task["run_spec"] == {}


def test_duplicate_task_ids_are_deduped():
    state = normalize_todo_state({"tasks": [{"task_id": "t"}, {"task_id": "t"}]})
    assert [t["task_id"] for t in state["tasks"]] == ["t", "t_2"]


def test_ready_tasks_sorted_by_priority():
    state = normalize_todo_state({
        "tasks": [
            {"task_id": "b", "priority": 2, "status": "pending"},
            {"task_id": "a", "priority": 1, "status": "pending"},
            {"task_id": "done", "priority": 0, "status": "verified"},
        ]
    })
    assert [t["task_id"] for t in ready_tasks(state)] == ["a", "b"]
    assert [t["task_id"] for t in ready_tasks(state, limit=1)] == ["a"]


def test_ready_tasks_respects_dependencies_and_phase():
    state = normalize_todo_state({
        "tasks": [
            {"task_id": "impl", "type": "implementation", "status": "pending", "priority": 1},
            {"task_id": "val", "type": "validation", "status": "pending", "priority": 2, "depends_on": ["impl"], "run_spec": {"commands": "bash eval.sh"}},
        ]
    })
    assert task_phase(state["tasks"][0]) == "execute"
    assert task_phase(state["tasks"][1]) == "run"
    assert dependencies_satisfied(state, state["tasks"][1]) is False
    assert [t["task_id"] for t in ready_tasks(state, phase="execute")] == ["impl"]
    assert ready_tasks(state, phase="run") == []
    assert has_open_tasks(state, phase="run") is True

    state["tasks"][0]["status"] = "verified"
    assert dependencies_satisfied(state, state["tasks"][1]) is True
    assert [t["task_id"] for t in ready_tasks(state, phase="run")] == ["val"]
    assert [t["task_id"] for t in open_tasks(state, phase="run")] == ["val"]


def test_has_failed_tasks_can_filter_by_phase():
    state = normalize_todo_state({
        "tasks": [
            {"task_id": "impl", "type": "implementation", "status": "failed"},
            {"task_id": "val", "type": "validation", "status": "pending"},
        ]
    })
    assert has_failed_tasks(state) is True
    assert has_failed_tasks(state, phase="execute") is True
    assert has_failed_tasks(state, phase="run") is False


def test_failed_run_before_open_execute_does_not_block_repair():
    state = normalize_todo_state({
        "tasks": [
            {"task_id": "baseline", "type": "validation", "status": "failed", "priority": 1},
            {"task_id": "impl", "type": "implementation", "status": "pending", "priority": 2},
        ]
    })
    assert has_failed_tasks(state) is True
    assert has_blocking_failed_tasks(state) is False


def test_repair_failed_run_tasks_adds_implementation_and_redirects_deps():
    state = normalize_todo_state({
        "tasks": [
            {
                "task_id": "baseline",
                "goal": "run baseline",
                "type": "validation",
                "status": "failed",
                "priority": 1,
                "last_result": {"summary": "python3: can't open file train/optimizer.py"},
            },
            {
                "task_id": "final",
                "goal": "run final eval",
                "type": "validation",
                "status": "pending",
                "priority": 2,
                "depends_on": ["baseline"],
            },
        ]
    })

    repaired = repair_failed_run_tasks(state)
    ids = [task["task_id"] for task in repaired["tasks"]]

    assert ids[:3] == ["baseline", "repair_baseline", "final"]
    repair = next(task for task in repaired["tasks"] if task["task_id"] == "repair_baseline")
    assert repair["type"] == "implementation"
    assert repair["repairs_task_id"] == "baseline"
    assert "can't open file" not in repair["goal"]
    assert "can't open file" in repair["failure_evidence"]
    final = next(task for task in repaired["tasks"] if task["task_id"] == "final")
    assert final["depends_on"] == ["repair_baseline"]

    # Idempotent: repeated repair pass should not add another repair task.
    repaired_again = repair_failed_run_tasks(repaired)
    assert [task["task_id"] for task in repaired_again["tasks"]].count("repair_baseline") == 1


def test_ready_execute_tasks_stops_before_ready_run_checkpoint():
    state = normalize_todo_state({
        "tasks": [
            {"task_id": "a", "type": "analysis", "status": "verified", "priority": 1},
            {"task_id": "baseline", "type": "validation", "status": "pending", "priority": 2, "run_spec": {"commands": "bash eval.sh"}},
            {"task_id": "impl", "type": "implementation", "status": "pending", "priority": 3},
        ]
    })
    assert ready_execute_tasks(state) == []
    state["tasks"][1]["status"] = "verified"
    assert [t["task_id"] for t in ready_execute_tasks(state)] == ["impl"]


def test_upsert_task_updates_existing(tmp_path):
    upsert_task(tmp_path, {"task_id": "t1", "goal": "old"})
    upsert_task(tmp_path, {"task_id": "t1", "goal": "new", "status": "blocked"})
    state = load_todo_state(tmp_path)
    assert len(state["tasks"]) == 1
    assert state["tasks"][0]["goal"] == "new"
    assert state["tasks"][0]["status"] == "blocked"


def test_merge_todo_state_preserves_progress_by_goal():
    existing = normalize_todo_state({
        "tasks": [
            {"task_id": "old", "goal": "same goal", "status": "verified", "last_result": {"ok": True}},
        ]
    })
    planned = normalize_todo_state({
        "tasks": [
            {"task_id": "t1", "goal": "same goal", "status": "pending", "run_spec": {"mode": "single"}},
        ]
    })
    merged = merge_todo_state(existing, planned)
    assert merged["tasks"][0]["task_id"] == "t1"
    assert merged["tasks"][0]["status"] == "verified"
    assert merged["tasks"][0]["last_result"] == {"ok": True}
    assert merged["tasks"][0]["run_spec"] == {"mode": "single"}


def test_merge_todo_state_preserves_existing_depends_when_plan_omits_it():
    existing = normalize_todo_state({
        "tasks": [
            {"task_id": "v", "goal": "validate", "type": "validation", "depends_on": ["i"]},
        ]
    })
    planned = normalize_todo_state({
        "tasks": [
            {"task_id": "v", "goal": "validate", "type": "validation"},
        ]
    })
    merged = merge_todo_state(existing, planned)
    assert merged["tasks"][0]["depends_on"] == ["i"]


def test_merge_todo_state_does_not_match_generated_ids_across_different_goals():
    existing = normalize_todo_state({
        "tasks": [
            {"task_id": "t1", "goal": "old goal", "status": "failed"},
        ]
    })
    planned = normalize_todo_state({
        "tasks": [
            {"task_id": "t1", "goal": "new goal", "status": "pending"},
        ]
    })
    merged = merge_todo_state(existing, planned)
    assert merged["tasks"][0]["goal"] == "new goal"
    assert merged["tasks"][0]["status"] == "pending"


def test_render_markdown_includes_run_spec():
    state = normalize_todo_state({
        "tasks": [
            {
                "task_id": "t1",
                "goal": "run eval",
                "type": "validation",
                "status": "pending",
                "run_spec": {"mode": "single", "commands": ["bash eval.sh"]},
            }
        ]
    })
    text = render_todo_markdown(state)
    assert "[pending] t1 (validation): run eval" in text
    assert "run_spec" in text
    assert "bash eval.sh" in text


def test_render_markdown_includes_depends_on():
    state = normalize_todo_state({
        "tasks": [
            {"task_id": "v1", "goal": "run eval", "type": "validation", "depends_on": ["i1"]},
        ]
    })
    text = render_todo_markdown(state)
    assert "depends_on" in text
    assert "i1" in text


def test_load_corrupt_state_returns_empty(tmp_path):
    todo_state_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    todo_state_path(tmp_path).write_text("{bad", encoding="utf-8")
    assert load_todo_state(tmp_path)["tasks"] == []
