from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agentic_autoresearch import AutoResearchConfig, ThreeStepAutoResearch, read_monitor
from agentic_autoresearch.agent import _tag_is_true


class FakeResponse:
    def __init__(self, message):
        self.choices = [SimpleNamespace(message=message)]
        self.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)


class FakeMessage:
    role = "assistant"

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeToolCall:
    type = "function"

    def __init__(self, name, arguments, call_id="call_1"):
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=arguments)


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **kwargs):
        if not self.responses:
            raise AssertionError("no fake responses left")
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


def _done(tag):
    return FakeResponse(FakeMessage(json.dumps({tag: True, "summary": "done"})))


def test_runner_rotates_three_steps_only_on_done_tag(tmp_path):
    (tmp_path / "program.md").write_text("Goal: improve metric\n", encoding="utf-8")
    responses = [
        FakeResponse(FakeMessage(tool_calls=[
            FakeToolCall("write_file", json.dumps({"path": ".autoresearch/plan.md", "content": "try baseline\n"}))
        ])),
        _done("PLAN_DONE"),
        _done("ATTEMPT_DONE"),
        _done("CONCLUDE_DONE"),
    ]
    runner = ThreeStepAutoResearch(
        AutoResearchConfig(project_dir=tmp_path, run_id="t", max_cycles=1, model="fake"),
        client=FakeClient(responses),
    )

    result = runner.run()

    assert result["status"] == "completed"
    assert [r["step"] for r in result["reports"]] == ["plan", "attempt", "conclude"]
    state = json.loads((tmp_path / ".autoresearch" / "runner_state.json").read_text(encoding="utf-8"))
    assert state["cycle"] == 1
    assert state["current_step"] == "plan"
    assert (tmp_path / ".autoresearch" / "plan.md").read_text(encoding="utf-8") == "try baseline\n"
    monitor = read_monitor(tmp_path / ".autoresearch" / "monitor.json")
    assert monitor["status"] == "completed"
    assert result["usage"]["llm_calls"] == 4
    assert result["usage"]["tool_calls"] == 1
    attempt_trace = json.loads(Path(result["reports"][1]["trace_path"]).read_text(encoding="utf-8"))
    assert attempt_trace["context_manifest"]["context_chars"] > 0
    assert attempt_trace["llm_events"]
    assert "duration_seconds" in attempt_trace["llm_events"][0]
    assert "usage_delta" in attempt_trace["llm_events"][0]
    assert attempt_trace["step_stats"]["usage_delta"]["llm_calls"] == 1
    assert attempt_trace["step_stats"]["duration_seconds"] >= 0
    context_traces = list((tmp_path / ".autoresearch" / "traces").glob("*_attempt_context.json"))
    assert context_traces


def test_runner_does_not_advance_without_done_tag(tmp_path):
    responses = [FakeResponse(FakeMessage("I am not done"))]
    runner = ThreeStepAutoResearch(
        AutoResearchConfig(project_dir=tmp_path, run_id="missing-tag", max_cycles=1, model="fake"),
        client=FakeClient(responses),
    )

    result = runner.run()

    assert result["status"] == "failed"
    assert result["reports"][0]["step"] == "plan"
    assert result["reports"][0]["next_step"] == "plan"
    assert "missing_done_tag" in result["reports"][0]["error"]


def test_attempt_machine_done_when_read_eval_solved(tmp_path):
    (tmp_path / "program.md").write_text("metric_name: score\nhigher_is_better: true\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({
        "primary_metric": 1.0,
        "primary_metric_name": "score",
        "higher_is_better": True,
        "score": 1.0,
    }), encoding="utf-8")
    (tmp_path / ".autoresearch").mkdir()
    (tmp_path / ".autoresearch" / "runner_state.json").write_text(
        json.dumps({"version": 1, "run_id": "m", "cycle": 0, "current_step": "attempt", "last_report": {}}),
        encoding="utf-8",
    )
    responses = [
        FakeResponse(FakeMessage(tool_calls=[
            FakeToolCall("read_eval", "{}", call_id="read_eval_1")
        ])),
        _done("CONCLUDE_DONE"),
    ]
    runner = ThreeStepAutoResearch(
        AutoResearchConfig(project_dir=tmp_path, run_id="m", max_cycles=1, model="fake"),
        client=FakeClient(responses),
    )

    result = runner.run()

    assert result["status"] == "stopped"
    assert result["reports"][0]["step"] == "attempt"
    assert result["reports"][0]["done"] is True
    assert result["reports"][0]["iterations"] == 1
    assert "machine completion gate" in result["reports"][0]["summary"]


def test_runner_stops_after_conclude_when_project_solved(tmp_path):
    (tmp_path / "program.md").write_text("metric_name: score\nhigher_is_better: true\n", encoding="utf-8")
    (tmp_path / "metrics.json").write_text(json.dumps({
        "primary_metric": 1.0,
        "primary_metric_name": "score",
        "higher_is_better": True,
        "score": 1.0,
    }), encoding="utf-8")
    responses = [
        _done("PLAN_DONE"),
        _done("ATTEMPT_DONE"),
        _done("CONCLUDE_DONE"),
        # If the runner incorrectly continues to the next cycle, this would be consumed.
        FakeResponse(FakeMessage("unexpected")),
    ]
    runner = ThreeStepAutoResearch(
        AutoResearchConfig(project_dir=tmp_path, run_id="solved-stop", max_cycles=3, model="fake"),
        client=FakeClient(responses),
    )

    result = runner.run()

    assert result["status"] == "stopped"
    assert [r["step"] for r in result["reports"]] == ["plan", "attempt", "conclude"]
    assert (tmp_path / ".autoresearch" / "STOP").exists()


def test_runner_resumes_from_persisted_step(tmp_path):
    state_dir = tmp_path / ".autoresearch"
    state_dir.mkdir()
    (state_dir / "runner_state.json").write_text(
        json.dumps({"version": 1, "run_id": "resume", "cycle": 0, "current_step": "attempt", "last_report": {}}),
        encoding="utf-8",
    )
    responses = [_done("ATTEMPT_DONE"), _done("CONCLUDE_DONE")]
    runner = ThreeStepAutoResearch(
        AutoResearchConfig(project_dir=tmp_path, run_id="resume", max_cycles=1, model="fake"),
        client=FakeClient(responses),
    )

    result = runner.run()

    assert [r["step"] for r in result["reports"]] == ["attempt", "conclude"]
    state = json.loads((state_dir / "runner_state.json").read_text(encoding="utf-8"))
    assert state["cycle"] == 1
    assert state["current_step"] == "plan"


def test_done_tag_detected_after_other_json_objects():
    content = (
        'Verified submission: {"x": 51.0, "y": -89.0}\n'
        'Metrics: {"z": 0.0}\n'
        '{"ATTEMPT_DONE": true}'
    )
    assert _tag_is_true(content, "ATTEMPT_DONE")


def test_runner_resets_state_for_new_run_id(tmp_path):
    state_dir = tmp_path / ".autoresearch"
    state_dir.mkdir()
    (state_dir / "runner_state.json").write_text(
        json.dumps({"version": 1, "run_id": "old", "cycle": 7, "current_step": "attempt", "last_report": {}}),
        encoding="utf-8",
    )
    runner = ThreeStepAutoResearch(
        AutoResearchConfig(project_dir=tmp_path, run_id="new", max_cycles=0, model="fake"),
        client=FakeClient([]),
    )

    result = runner.run()

    assert result["status"] == "completed"
    state = json.loads((state_dir / "runner_state.json").read_text(encoding="utf-8"))
    assert state["run_id"] == "new"
    assert state["cycle"] == 0
    assert state["current_step"] == "plan"
