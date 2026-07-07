import json
from pathlib import Path

from core.autoresearch_loop import AutoResearchLoop, AutoResearchSettings, DEFAULT_CONTEXT_BUCKETS
from tools.autoresearch_tool import auto_research_run_tool
from tools.registry import registry


def test_fixed_workflow_populates_modular_context_buckets(tmp_path):
    (tmp_path / "program.md").write_text("# Program\nRun a tiny baseline.\n", encoding="utf-8")
    (tmp_path / "eval.sh").write_text("#!/usr/bin/env bash\necho primary_metric: 1.0\n", encoding="utf-8")

    settings = AutoResearchSettings(
        project_dir=tmp_path,
        project_id="wf",
        max_rounds=5,
        bucket_max_items=2,
        bucket_item_char_budget=200,
        context_char_budget=4000,
    )
    result = AutoResearchLoop(settings).run()

    assert result["rounds_completed"] == 5
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert set(DEFAULT_CONTEXT_BUCKETS).issubset(state["buckets"].keys())
    assert state["buckets"]["project_understanding"]
    assert state["buckets"]["modification_plans"]
    assert state["buckets"]["experiment_results"]
    assert state["buckets"]["conclusions"]
    assert all(len(items) <= 2 for items in state["buckets"].values())


def test_auto_research_run_tool_registered_and_runs(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")

    registry.reload_all()
    assert "auto_research_run" in registry._tools

    payload = json.loads(auto_research_run_tool(str(tmp_path), project_id="tool", rounds=2))

    assert payload["success"] is True
    assert payload["project_id"] == "tool"
    assert payload["rounds_completed"] == 2
    assert Path(payload["state_path"]).exists()
    assert Path(payload["artifact_dir"]).exists()


def test_autoresearch_parent_context_contains_modular_context(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=3, context_char_budget=2000)
    loop = AutoResearchLoop(settings)
    loop.run()

    context = loop.context.build_parent_context()

    assert len(context) <= settings.context_char_budget
    assert "modular_context" in context
    assert "project_understanding" in context
    assert "modification_plans" in context


class _FakeStepAgent:
    def __init__(self):
        self.calls = []

    def plan_step(self, *, step, fallback_action, parent_context, round_index):
        from core.autoresearch_loop import AutoResearchAction, AutoResearchStepResult

        self.calls.append((step.name, tuple(step.allowed_tools), parent_context))
        return AutoResearchStepResult(
            action=AutoResearchAction(
                type="note",
                rationale="llm_plan_note",
                content="LLM child step produced a bounded research plan.",
            ),
            bucket_updates={"modification_plans": ["LLM child proposed a bounded next modification plan."]},
            raw_response='{"ok": true}',
        )


def test_llm_step_agent_can_override_allowed_action_and_update_buckets(tmp_path):
    from core.autoresearch_loop import AutoResearchWorkflowStep, FixedAutoResearchPlanner

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=1, use_llm_step_agents=True)
    planner = FixedAutoResearchPlanner([
        AutoResearchWorkflowStep(
            name="llm_plan",
            action_type="note",
            rationale="fallback_plan",
            content="fallback",
            allowed_tools=("note",),
        )
    ])
    fake = _FakeStepAgent()

    result = AutoResearchLoop(settings, planner=planner, step_agent=fake).run()

    assert result["rounds_completed"] == 1
    assert result["step_agent_errors"] == []
    assert fake.calls and fake.calls[0][0] == "llm_plan"
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert any("LLM child proposed" in item for item in state["buckets"]["modification_plans"])
    artifacts = list((tmp_path / ".autoresearch" / "artifacts").glob("*llm_plan_note_note.md"))
    assert artifacts


class _BadStepAgent:
    def plan_step(self, *, step, fallback_action, parent_context, round_index):
        from core.autoresearch_loop import AutoResearchAction, AutoResearchStepResult

        return AutoResearchStepResult(
            action=AutoResearchAction(type="run", rationale="not_allowed", command="echo bad"),
            bucket_updates={"raw_observations": ["should fallback"]},
        )


def test_llm_step_agent_disallowed_action_falls_back(tmp_path):
    from core.autoresearch_loop import AutoResearchWorkflowStep, FixedAutoResearchPlanner

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=1, use_llm_step_agents=True)
    planner = FixedAutoResearchPlanner([
        AutoResearchWorkflowStep(
            name="safe_note",
            action_type="note",
            rationale="fallback_note",
            content="fallback note",
            allowed_tools=("note",),
        )
    ])

    result = AutoResearchLoop(settings, planner=planner, step_agent=_BadStepAgent()).run()

    assert result["rounds_completed"] == 1
    assert result["step_agent_errors"]
    assert result["observations"][0]["kind"] == "note"
    assert "fallback_note" in result["observations"][0]["summary"]



def test_extract_json_object_handles_markdown_fence_and_embedded_text():
    from core.autoresearch_loop import extract_json_object

    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('prefix {"b": {"c": 2}} suffix') == {"b": {"c": 2}}


def test_metric_and_progress_helpers():
    from core.autoresearch_loop import parse_primary_metric, decide_experiment, extract_progress_percent

    info = parse_primary_metric('primary_metric_name: accuracy\nprimary_metric: 0.82\nhigher_is_better: true')
    assert info == {"metric": 0.82, "metric_name": "accuracy", "higher_is_better": True}
    assert decide_experiment(0.83, baseline=0.82, higher_is_better=True) == "keep"
    assert decide_experiment(0.81, baseline=0.82, higher_is_better=True) == "discard"
    assert extract_progress_percent('epoch 1 20% ... epoch 4 80%') == 80


def test_progress_markdown_is_written(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="viz", max_rounds=2)

    result = AutoResearchLoop(settings).run()

    progress = Path(result["progress_path"])
    assert progress.exists()
    text = progress.read_text(encoding="utf-8")
    assert "# auto_research Progress" in text
    assert "Overall:" in text
    assert "Experiment/Train progress:" in text
    assert "## 当前修改计划" in text
    assert "## 已完成部分" in text


def test_auto_research_background_run_and_status(tmp_path):
    import time
    from tools.autoresearch_tool import auto_research_status_tool

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    payload = json.loads(auto_research_run_tool(str(tmp_path), project_id="bg", rounds=2, background=True))
    assert payload["success"] is True
    assert payload["background"] is True
    assert payload["run_id"].startswith("ar-")
    assert Path(payload["status_path"]).exists()

    status = {}
    for _ in range(30):
        status = json.loads(auto_research_status_tool(payload["run_id"], project_dir=str(tmp_path)))
        if status.get("status") in {"completed", "failed"} and status.get("progress_preview"):
            break
        time.sleep(0.1)

    assert status["success"] is True
    assert status["status"] in {"queued", "running", "completed"}
    assert "progress_preview" in status
    if status["progress_preview"]:
        assert "auto_research Progress" in status["progress_preview"]



def test_apply_patch_action_changes_project_file(tmp_path):
    from core.autoresearch_loop import AutoResearchAction

    target = tmp_path / "hello.txt"
    target.write_text("old\n", encoding="utf-8")
    patch = """--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-old
+new
"""
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0)
    loop = AutoResearchLoop(settings)

    obs = loop.execute_action(AutoResearchAction(type="apply_patch", rationale="patch_test", patch=patch))

    assert obs.status == "ok"
    assert obs.kind == "apply_patch"
    assert target.read_text(encoding="utf-8") == "new\n"


def test_metrics_are_written_to_state_and_results_tsv(tmp_path):
    from core.autoresearch_loop import AutoResearchAction

    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0)
    loop = AutoResearchLoop(settings)
    raw = "primary_metric_name: accuracy\nprimary_metric: 0.91\nhigher_is_better: true\n"

    obs = loop.execute_action(AutoResearchAction(type="note", rationale="experiment_result_baseline", content=raw))

    assert obs.status == "ok"
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert state["baseline_metric"] == 0.91
    assert state["metrics"][0]["metric_name"] == "accuracy"
    results = (tmp_path / "results.tsv").read_text(encoding="utf-8")
    assert "accuracy" in results
    assert "baseline_recorded" in results


def test_progress_markdown_includes_log_tail_and_eta(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    (tmp_path / "eval.sh").write_text("#!/usr/bin/env bash\necho 'train 80%'\necho 'primary_metric: 0.5'\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="tail", max_rounds=4)

    result = AutoResearchLoop(settings).run()

    text = Path(result["progress_path"]).read_text(encoding="utf-8")
    assert "ETA:" in text
    assert "## 最近日志 Tail" in text
    assert "train 80%" in text or "primary_metric" in text
