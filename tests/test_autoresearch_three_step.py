from pathlib import Path

from core.autoresearch_loop import AutoResearchLoop, AutoResearchSettings
from core.autoresearch_memory import read_phase
from core.autoresearch_three_step import ThreeStepController
from core.autoresearch_todo_state import load_todo_state, save_todo_state


def _settings(tmp_path):
    (tmp_path / "program.md").write_text("Goal: minimize z\n", encoding="utf-8")
    return AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)


def test_three_step_attempt_runs_execute_and_ready_run_checkpoint(tmp_path):
    settings = _settings(tmp_path)
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
    phase, _ = read_phase((tmp_path / "project.md").read_text(encoding="utf-8"))
    assert phase == "conclude"


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
