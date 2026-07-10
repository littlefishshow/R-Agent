from __future__ import annotations

import json

from agentic_autoresearch.eval_interface import ensure_eval_interface, parse_completion_criteria, read_eval
from agentic_autoresearch.steps import ATTEMPT_TOOLS, CONCLUDE_TOOLS
from agentic_autoresearch.tools import build_default_tools


def test_parse_completion_criteria_from_program():
    text = """
    ## Completion Criteria
    - `metric_name`: `z`
    - `higher_is_better`: `false`
    - `z <= 0.001`
    """
    criteria = parse_completion_criteria(text)
    assert criteria["metric_name"] == "z"
    assert criteria["higher_is_better"] is False
    assert criteria["op"] == "<="
    assert criteria["threshold"] == 0.001


def test_eval_interface_reads_metric_and_solved(tmp_path):
    (tmp_path / "program.md").write_text(
        "metric_name: z\nhigher_is_better: false\nz <= 0.001\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({
        "metric_name": "z",
        "higher_is_better": False,
        "z": 0.0,
        "primary_metric": 0.0,
    }), encoding="utf-8")

    interface = ensure_eval_interface(tmp_path)
    result = read_eval(tmp_path)

    assert interface["eval_command"] == "bash eval.sh"
    assert result["metric_name"] == "z"
    assert result["metric_value"] == 0.0
    assert result["solved"] is True


def test_eval_interface_infers_solved_for_perfect_score_without_threshold(tmp_path):
    (tmp_path / "program.md").write_text(
        "Maximize primary_metric=score. higher_is_better=true\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(json.dumps({
        "primary_metric": 1.0,
        "primary_metric_name": "score",
        "higher_is_better": True,
        "accuracy": 1.0,
        "correct": 10,
        "total": 10,
        "score": 1.0,
    }), encoding="utf-8")

    ensure_eval_interface(tmp_path)
    result = read_eval(tmp_path)

    assert result["metric_value"] == 1.0
    assert result["solved"] is True


def test_read_eval_tool_available_to_attempt_and_conclude(tmp_path):
    assert "read_eval" in ATTEMPT_TOOLS
    assert "read_eval" in CONCLUDE_TOOLS
    tools = build_default_tools(tmp_path)
    names = {schema["function"]["name"] for schema in tools.schemas()}
    assert "read_eval" in names
