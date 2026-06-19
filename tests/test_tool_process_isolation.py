import json
import os
import threading
import time

import pytest

from tools.registry import ToolExecutionInterrupted, ToolRegistry


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
