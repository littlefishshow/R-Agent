from pathlib import Path
import json

from autoresearch.legacy.loop import AutoResearchLoop, AutoResearchSettings
from autoresearch.state.memory import read_phase
from autoresearch.controller import ThreeStepController
from autoresearch.state.todo import load_todo_state, save_todo_state


def _settings(tmp_path):
    (tmp_path / "program.md").write_text("Goal: minimize z\n", encoding="utf-8")
    return AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)


def test_three_step_attempt_runs_execute_and_ready_run_checkpoint(tmp_path):
    settings = _settings(tmp_path)
    settings.trace_rounds = True
    (tmp_path / "project.md").write_text(
        "# Project State\n\n<!-- PHASE: attempt -->\n<!-- PHASE_REASON: test -->\n",
        encoding="utf-8",
    )
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nprintf '{\"primary_metric\":3,\"z\":3,\"higher_is_better\":false}\\n' > metrics.json\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "analysis", "type": "analysis", "status": "pending", "priority": 1},
            {
                "task_id": "val",
                "goal": "run eval",
                "type": "validation",
                "status": "pending",
                "priority": 2,
                "depends_on": ["impl"],
                "run_spec": {"mode": "single", "commands": ["bash train/train.sh"]},
            },
        ]
    })
    loop = AutoResearchLoop(settings)
    controller = ThreeStepController(settings, loop=loop)
    report = controller.step()

    assert report["ran_phase"] == "attempt"
    assert "run: status=ok" in report["summary"]
    state = load_todo_state(tmp_path)
    assert {task["task_id"]: task["status"] for task in state["tasks"]} == {"impl": "verified", "val": "verified"}
    impl = next(task for task in state["tasks"] if task["task_id"] == "impl")
    context_path = Path(impl["last_result"]["context_artifact_path"])
    assert context_path.exists()
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    assert payload["task"]["task_id"] == "impl"
    assert payload["todo_digest"]["ready_execute"][0]["task_id"] == "impl"
    assert payload["policy"]["parent_role"].startswith("schedule")
    assert payload["done_tag"] == "ATTEMPT_DONE"
    assert payload["step_context"]["tool_policy"]["child_allowed_tools"]
    assert "delegate_task" in payload["step_context"]["tool_policy"]["child_excluded_tools"]
    phase, _ = read_phase((tmp_path / "project.md").read_text(encoding="utf-8"))
    assert phase == "conclude"
    trace = tmp_path / ".autoresearch" / "step_traces" / "step_000_attempt.json"
    assert trace.exists()
    trace_payload = json.loads(trace.read_text(encoding="utf-8"))
    assert trace_payload["phase"] == "attempt"
    assert trace_payload["next_phase"] == "conclude"
    assert trace_payload["todo_digest"]["total"] == 2
    assert "program_excerpt" in trace_payload


def test_three_step_attempt_pauses_when_run_marks_solved(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "program.md").write_text(
        "Goal: minimize z\n\n## Completion Criteria\n- metric_name: z\n- higher_is_better: false\n- z <= 0.1\n",
        encoding="utf-8",
    )
    (tmp_path / "project.md").write_text(
        "# Project State\n\n<!-- PHASE: attempt -->\n<!-- PHASE_REASON: test -->\n",
        encoding="utf-8",
    )
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nprintf '{\"primary_metric\":0,\"z\":0,\"higher_is_better\":false}\\n' > metrics.json\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "impl", "goal": "analysis", "type": "analysis", "status": "pending", "priority": 1},
            {
                "task_id": "val",
                "goal": "run eval",
                "type": "validation",
                "status": "pending",
                "priority": 2,
                "depends_on": ["impl"],
                "run_spec": {"mode": "single", "commands": ["bash train/train.sh"]},
            },
        ]
    })
    loop = AutoResearchLoop(settings)
    controller = ThreeStepController(settings, loop=loop)

    report = controller.step()

    assert report["ran_phase"] == "attempt"
    assert report["next_phase"] == "pause"
    phase, reason = read_phase((tmp_path / "project.md").read_text(encoding="utf-8"))
    assert phase == "pause"
    assert "solved" in reason


def test_three_step_conclude_continues_current_dag_instead_of_replanning(tmp_path):
    settings = _settings(tmp_path)
    (tmp_path / "project.md").write_text(
        "# Project State\n\n## 短期结论\n(none)\n\n<!-- PHASE: conclude -->\n<!-- PHASE_REASON: test -->\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {"task_id": "next", "goal": "next edit", "type": "implementation", "status": "pending", "priority": 1},
        ]
    })
    loop = AutoResearchLoop(settings)
    controller = ThreeStepController(settings, loop=loop)
    report = controller.step()

    assert report["ran_phase"] == "conclude"
    assert report["next_phase"] == "attempt"
    phase, _ = read_phase((tmp_path / "project.md").read_text(encoding="utf-8"))
    assert phase == "attempt"


def test_three_step_run_finalizes_conclude_when_step_budget_ends_after_attempt(tmp_path):
    settings = _settings(tmp_path)
    settings.trace_rounds = True
    (tmp_path / "project.md").write_text(
        "# Project State\n\n<!-- PHASE: attempt -->\n<!-- PHASE_REASON: test -->\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\n"
        "cat > metrics.json <<'JSON'\n"
        "{\"primary_metric\": 0.5, \"primary_metric_name\": \"score\", \"higher_is_better\": true}\n"
        "JSON\n"
        "cat metrics.json\n",
        encoding="utf-8",
    )
    save_todo_state(tmp_path, {
        "tasks": [
            {
                "task_id": "val",
                "goal": "run eval",
                "type": "validation",
                "status": "pending",
                "priority": 1,
                "run_spec": {"mode": "single", "commands": ["bash eval.sh"]},
            },
        ]
    })
    loop = AutoResearchLoop(settings)
    controller = ThreeStepController(settings, loop=loop)

    reports = controller.run(max_steps=1)

    assert [report["ran_phase"] for report in reports] == ["attempt", "conclude"]
    assert reports[-1]["next_phase"] in {"plan", "attempt"}
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert state["best_experiment"]["primary_metric_name"] == "score"


class _CapturingStepAgent:
    calls = []

    def __init__(self, max_iterations=None, session_id=None):
        self.max_iterations = max_iterations
        self.session_id = session_id

    def run_conversation(self, **kwargs):
        type(self).calls.append(kwargs)
        data = json.loads(kwargs["user_message"])
        if data["step"] == "plan":
            return json.dumps({
                "summary": "agent plan",
                "tasks": [
                    {"task_id": "a1", "goal": "inspect", "type": "analysis", "status": "pending", "priority": 1}
                ],
                "done_tag": data["done_tag"],
            }, ensure_ascii=False)
        return f"finished {data['step']} {data['done_tag']}"


def test_three_step_optional_agent_loop_uses_step_tool_policy(tmp_path, monkeypatch):
    import core.agent

    _CapturingStepAgent.calls = []
    monkeypatch.setattr(core.agent, "RAgent", _CapturingStepAgent)
    settings = _settings(tmp_path)
    settings.autoresearch_step_agent_loop = True
    settings.autoresearch_step_max_iterations = 3
    (tmp_path / "project.md").write_text(
        "# Project State\n\n<!-- PHASE: plan -->\n<!-- PHASE_REASON: test -->\n",
        encoding="utf-8",
    )
    loop = AutoResearchLoop(settings)
    controller = ThreeStepController(settings, loop=loop)

    report = controller.step()

    assert report["ran_phase"] == "plan"
    assert report["next_phase"] == "attempt"
    call = _CapturingStepAgent.calls[0]
    assert "delegate_task" in call["allowed_tools"]
    assert call["exclude_tools"] == ()
    assert call["tool_call_guard"]("write_file", "{}").startswith("tool write_file is not allowed")
    assert "codebase_scout" in call["system_message"]
    assert "child_allowed_tools" in call["system_message"]
    user_payload = json.loads(call["user_message"])
    assert user_payload["done_tag"] == "PLAN_DONE"
    assert user_payload["context"]["tool_policy"]["child_excluded_tools"]
    assert user_payload["context"]["tool_policy"]["allowed_skills"] == ["codebase_scout"]
    state = load_todo_state(tmp_path)
    assert state["tasks"][0]["task_id"] == "a1"
    assert (tmp_path / ".auto" / "plan.md").exists()
