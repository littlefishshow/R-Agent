from __future__ import annotations

import json

from agentic_autoresearch.steps import ATTEMPT_TOOLS, CONCLUDE_TOOLS, PLAN_TOOLS
from agentic_autoresearch.tools import build_default_tools


def test_detailed_plan_only_available_to_plan_step():
    assert "detailed_plan" in PLAN_TOOLS
    assert "detailed_plan" not in ATTEMPT_TOOLS
    assert "detailed_plan" not in CONCLUDE_TOOLS


def test_detailed_plan_tool_writes_structured_plan(tmp_path):
    tools = build_default_tools(tmp_path)
    result = json.loads(tools.execute("detailed_plan", {
        "problem": "multi-module optimizer",
        "context_summary": "several moving pieces",
        "complexity_reason": "needs staged validation",
        "milestones": ["map files", "patch train", "run eval"],
        "risks": ["overfitting"],
        "validation": ["bash train/train.sh", "bash eval.sh"],
        "next_attempt": "inspect entrypoint",
    }))

    assert result["success"] is True
    path = tmp_path / result["result"]["path"]
    text = path.read_text(encoding="utf-8")
    assert "multi-module optimizer" in text
    assert "needs staged validation" in text
    assert "bash eval.sh" in text
