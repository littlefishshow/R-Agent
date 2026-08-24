import json
import os
import threading
import time

import pytest

from tools.registry import ToolExecutionInterrupted, ToolRegistry, _tool_process_start_method


def test_tool_process_start_method_avoids_fork_on_macos():
    assert _tool_process_start_method("darwin") == "spawn"
    assert _tool_process_start_method("win32") == "spawn"
    assert _tool_process_start_method("linux") == "fork"


def test_execute_tool_isolated_runs_in_child_process():
    registry = ToolRegistry()
    registry.register(
        "pid",
        "return child pid",
        {"type": "object", "properties": {}},
        lambda: os.getpid(),
    )

    result = json.loads(registry.execute_tool_isolated("pid", "{}"))

    assert result["success"] is True
    assert result["result"] != os.getpid()


def test_execute_tool_isolated_cancel_terminates_long_running_tool():
    registry = ToolRegistry()

    def slow_tool():
        time.sleep(5)
        return "done"

    registry.register(
        "slow",
        "slow tool",
        {"type": "object", "properties": {}},
        slow_tool,
    )
    cancel_event = threading.Event()

    def cancel_soon():
        time.sleep(0.1)
        cancel_event.set()

    threading.Thread(target=cancel_soon, daemon=True).start()

    with pytest.raises(ToolExecutionInterrupted):
        registry.execute_tool_isolated("slow", "{}", cancel_event=cancel_event)


def test_execute_tool_isolated_timeout_terminates_child():
    registry = ToolRegistry()

    def slow_tool():
        time.sleep(5)
        return "done"

    registry.register(
        "slow_timeout",
        "slow timeout tool",
        {"type": "object", "properties": {}},
        slow_tool,
    )

    result = json.loads(registry.execute_tool_isolated("slow_timeout", "{}", timeout=0.1))

    assert "timed out" in result["error"]


def test_execute_tool_isolated_returns_json_errors_for_exceptions_and_unserializable_results():
    registry = ToolRegistry()

    def boom():
        raise RuntimeError("boom")

    def unserializable():
        return {"bad": object()}

    registry.register("boom", "boom", {"type": "object", "properties": {}}, boom)
    registry.register("unserializable", "bad", {"type": "object", "properties": {}}, unserializable)

    boom_result = json.loads(registry.execute_tool_isolated("boom", "{}"))
    bad_result = json.loads(registry.execute_tool_isolated("unserializable", "{}"))

    assert boom_result["error"] == "boom"
    assert "not JSON serializable" in bad_result["error"]
