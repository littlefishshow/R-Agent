import json

import core.agent
from tools import delegate_tool
from tools import todo_tool


class _FakeAgent:
    def __init__(self, max_iterations=None):
        self.max_iterations = max_iterations

    def run_conversation(self, **kwargs):
        task_id = kwargs["user_message"].split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = kwargs["user_message"].split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage(
            "claim",
            json.dumps(
                {
                    "id": task_id,
                    "worker_id": worker_id,
                    "max_iterations": self.max_iterations,
                },
                ensure_ascii=False,
            ),
        )
        return f"⚠️ **已达迭代上限 ({self.max_iterations} 轮)，以下为强制收尾结果。**"

    def is_truncated(self):
        return True


def test_delegate_task_prints_snapshots_and_blocks_truncated_in_progress_task(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(core.agent, "RAgent", _FakeAgent)

    todo_tool.todo_manage(
        "init",
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "t1",
                        "description": "需要被子 Agent 执行的测试任务",
                        "dependencies": [],
                    }
                ]
            },
            ensure_ascii=False,
        ),
    )

    result = delegate_tool.delegate_task(
        tasks=json.dumps([{"task_id": "t1", "goal": "触发截断", "max_iterations": 1}], ensure_ascii=False),
        max_workers=1,
        default_max_iterations=1,
    )

    payload = json.loads(result)
    tasks = payload["tasks"]
    assert tasks[0]["status"] == "truncated"
    assert tasks[0]["truncated"] is True
    assert "sub_agent_messages" not in tasks[0]

    state = todo_tool._load_state()
    task = todo_tool._find_task(state, "t1")
    assert task["status"] == "blocked"
    assert task["metadata"]["blocked_reason"] == "subagent_max_iterations_reached"
    assert "强制收尾结果" in task["result"]

    out = capsys.readouterr().out
    assert "Delegate 准备并发执行" in out
    assert "Delegate 子任务状态更新：t1 -> truncated" in out
    assert "Todo Progress" in out
    assert "🕓 未完成任务：" in out


class _CompletingAgent:
    def __init__(self, max_iterations=None):
        self.max_iterations = max_iterations

    def get_token_usage_summary(self, include_children=False):
        return {
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 5,
            "last_prompt_tokens": 2,
            "last_completion_tokens": 3,
            "last_total_tokens": 5,
            "available": True,
        }

    def run_conversation(self, **kwargs):
        task_id = kwargs["user_message"].split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = kwargs["user_message"].split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage(
            "claim",
            json.dumps({"id": task_id, "worker_id": worker_id}, ensure_ascii=False),
        )
        todo_tool.todo_manage(
            "update",
            json.dumps({"id": task_id, "status": "completed", "result": f"{task_id} done"}, ensure_ascii=False),
        )
        return f"{task_id} completed"

    def is_truncated(self):
        return False


def test_delegate_task_final_snapshot_reports_completed_progress(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(core.agent, "RAgent", _CompletingAgent)

    todo_tool.todo_manage(
        "init",
        json.dumps(
            {
                "tasks": [
                    {"id": "a", "description": "A"},
                    {"id": "b", "description": "B"},
                ]
            },
            ensure_ascii=False,
        ),
    )

    result = delegate_tool.delegate_task(
        tasks=json.dumps(
            [
                {"task_id": "a", "goal": "finish a"},
                {"task_id": "b", "goal": "finish b"},
            ],
            ensure_ascii=False,
        ),
        max_workers=2,
        default_max_iterations=3,
    )

    payload = json.loads(result)
    tasks = payload["tasks"]
    assert [item["status"] for item in tasks] == ["success", "success"]
    assert all("sub_agent_messages" not in item for item in tasks)
    assert tasks[0]["token_usage"]["total_tokens"] == 5
    assert tasks[0]["token_usage"]["available"] is True
    assert payload["delegated_token_usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 6,
        "total_tokens": 10,
        "available": True,
    }
    assert payload["todo_digest"]["status_counts"]["completed"] == 2
    state = todo_tool._load_state()
    assert all(task["status"] == "completed" for task in state["tasks"])
    out = capsys.readouterr().out
    assert "Delegate 子任务状态更新：" in out
    assert "完成进度：2/2 (100.0%)" in out
    assert "✅ 已完成任务：" in out


def test_todo_manage_update_prints_board_with_completed_and_unfinished_tasks(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    todo_tool.todo_manage(
        "init",
        json.dumps(
            {
                "tasks": [
                    {"id": "done", "description": "已经完成"},
                    {"id": "todo", "description": "还没完成"},
                ]
            },
            ensure_ascii=False,
        ),
    )
    capsys.readouterr()

    todo_tool.todo_manage(
        "update",
        json.dumps({"id": "done", "status": "completed", "result": "done"}, ensure_ascii=False),
    )

    out = capsys.readouterr().out
    assert "Todo Progress" in out
    assert "任务 done 状态更新为 completed" in out
    assert "✅ 已完成任务：" in out
    assert "done" in out
    assert "已经完成" in out
    assert "completed" in out
    assert "🕓 未完成任务：" in out
    assert "todo" in out
    assert "还没完成" in out
    assert "pending" in out


def test_todo_snapshot_shows_completed_and_unfinished_task_lists(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    todo_tool.todo_manage(
        "init",
        json.dumps(
            {
                "tasks": [
                    {"id": "done", "description": "已经完成", "status": "completed"},
                    {"id": "todo", "description": "还没完成", "status": "pending"},
                    {"id": "blocked", "description": "等待处理", "status": "blocked"},
                ]
            },
            ensure_ascii=False,
        ),
    )

    text = todo_tool._todo_snapshot_text(todo_tool._load_state(), "测试快照")

    assert "✅ 已完成任务：" in text
    assert "- done: 已经完成 [completed]" in text
    assert "🕓 未完成任务：" in text
    assert "- todo: 还没完成 [pending" in text
    assert "- blocked: 等待处理 [blocked" in text
    assert "🧭 需要父 Agent 处理：" in text


def test_print_after_status_stops_and_restarts_active_cli_status(monkeypatch, capsys):
    calls = []

    class _Status:
        def stop(self):
            calls.append("stop")

        def start(self):
            calls.append("start")

    monkeypatch.setattr(delegate_tool, "_current_cli_status", lambda: _Status())

    delegate_tool._print_after_status("hello status-safe print")

    assert calls == ["stop", "start"]
    assert "hello status-safe print" in capsys.readouterr().out


def test_consecutive_todo_boards_overwrite_previous_panel(monkeypatch, tmp_path):
    import io
    from rich.console import Console
    from tools import progress_render

    output = io.StringIO()
    monkeypatch.setattr(progress_render, "STATE_FILE", str(tmp_path / "terminal_progress_state.json"))
    monkeypatch.setattr(progress_render, "console", Console(file=output, force_terminal=True, color_system=None, width=80))

    progress_render.print_after_status("first board", output_kind="todo_board")
    progress_render.print_after_status("second board", output_kind="todo_board")

    text = output.getvalue()
    assert "first board" in text
    assert "second board" in text
    assert "\x1b[1A\x1b[2K" in text


def test_non_board_output_prevents_erasing_logs(monkeypatch, tmp_path):
    import io
    from rich.console import Console
    from tools import progress_render

    output = io.StringIO()
    monkeypatch.setattr(progress_render, "STATE_FILE", str(tmp_path / "terminal_progress_state.json"))
    monkeypatch.setattr(progress_render, "console", Console(file=output, force_terminal=True, color_system=None, width=80))

    progress_render.print_after_status("first board", output_kind="todo_board")
    progress_render.print_after_status("ordinary log")
    progress_render.print_after_status("second board", output_kind="todo_board")

    assert "\x1b[1A\x1b[2K" not in output.getvalue()


def test_stale_todo_board_state_does_not_erase_terminal(monkeypatch, tmp_path):
    import io
    import json
    import time
    from rich.console import Console
    from tools import progress_render

    output = io.StringIO()
    state_file = tmp_path / "terminal_progress_state.json"
    state_file.write_text(json.dumps({"kind": "todo_board", "line_count": 3, "updated_at": time.time() - 999}), encoding="utf-8")
    monkeypatch.setattr(progress_render, "STATE_FILE", str(state_file))
    monkeypatch.setattr(progress_render, "console", Console(file=output, force_terminal=True, color_system=None, width=80))

    progress_render.print_after_status("new board", output_kind="todo_board")

    assert "\x1b[1A\x1b[2K" not in output.getvalue()
