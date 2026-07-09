import json

from core.autoresearch_todo_state import (
    empty_todo_state,
    load_todo_state,
    normalize_task,
    normalize_todo_state,
    ready_tasks,
    render_todo_markdown,
    save_todo_state,
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


def test_upsert_task_updates_existing(tmp_path):
    upsert_task(tmp_path, {"task_id": "t1", "goal": "old"})
    upsert_task(tmp_path, {"task_id": "t1", "goal": "new", "status": "blocked"})
    state = load_todo_state(tmp_path)
    assert len(state["tasks"]) == 1
    assert state["tasks"][0]["goal"] == "new"
    assert state["tasks"][0]["status"] == "blocked"


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


def test_load_corrupt_state_returns_empty(tmp_path):
    todo_state_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    todo_state_path(tmp_path).write_text("{bad", encoding="utf-8")
    assert load_todo_state(tmp_path)["tasks"] == []
