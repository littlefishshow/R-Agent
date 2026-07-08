import json
import re
from pathlib import Path

from core.context.budget_config import BudgetConfig
from core.context.tool_result_storage import PERSISTED_OUTPUT_TAG, enforce_turn_budget, maybe_persist_tool_result


def _extract_path(message: str) -> Path:
    match = re.search(r"Full output saved to: (.+)", message)
    assert match, message
    return Path(match.group(1).strip())


def test_maybe_persist_tool_result_keeps_small_output():
    config = BudgetConfig(default_result_size=100, preview_size=20)
    assert maybe_persist_tool_result("small", "demo", "call1", config=config) == "small"


def test_maybe_persist_tool_result_persists_large_output():
    content = "line\n" * 100
    config = BudgetConfig(default_result_size=20, preview_size=30)

    result = maybe_persist_tool_result(content, "demo_tool", "call_large", config=config)

    assert PERSISTED_OUTPUT_TAG in result
    assert "artifact_search" in result
    path = _extract_path(result)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == content
    assert "line\n" in result


def test_read_file_threshold_is_pinned_to_avoid_persist_loop():
    content = "x" * 1000
    config = BudgetConfig(default_result_size=1, preview_size=10)

    result = maybe_persist_tool_result(content, "read_file", "call_read", config=config)

    assert result == content
    assert PERSISTED_OUTPUT_TAG not in result



def test_enforce_turn_budget_persists_largest_non_persisted_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BudgetConfig(default_result_size=10_000, turn_budget=120_000, preview_size=200)
    messages = [
        {"role": "tool", "tool_call_id": "call_small", "name": "small_tool", "content": "S" * 40_000},
        {"role": "tool", "tool_call_id": "call_large", "name": "large_tool", "content": "L" * 90_000},
        {"role": "tool", "tool_call_id": "call_medium", "name": "medium_tool", "content": "M" * 50_000},
    ]

    result = enforce_turn_budget(messages, config=config)

    contents = {msg["name"]: msg["content"] for msg in result}
    assert PERSISTED_OUTPUT_TAG in contents["large_tool"]
    assert contents["small_tool"] == "S" * 40_000
    assert contents["medium_tool"] == "M" * 50_000
    path = _extract_path(contents["large_tool"])
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "L" * 90_000
