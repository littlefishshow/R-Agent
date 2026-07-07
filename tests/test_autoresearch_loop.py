import json
from pathlib import Path

from core.autoresearch_loop import (
    AutoResearchAction,
    AutoResearchContextManager,
    AutoResearchLoop,
    AutoResearchSettings,
    AutoResearchSafetyError,
    ProjectConfinedCommandRunner,
)


def test_autoresearch_loop_archives_raw_output_and_keeps_bounded_context(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n" + "goal\n" * 200, encoding="utf-8")
    settings = AutoResearchSettings(
        project_dir=tmp_path,
        project_id="proj-x",
        context_char_budget=900,
        program_char_budget=300,
        summary_char_budget=500,
        max_rounds=1,
    )

    def planner(parent_context, round_index):
        assert len(parent_context) <= settings.context_char_budget
        assert "program_md" in parent_context
        return AutoResearchAction(type="run", rationale="list_files", command="pwd && printf hello")

    result = AutoResearchLoop(settings, planner=planner).run()

    assert result["rounds_completed"] == 1
    observation = result["observations"][0]
    assert observation["kind"] == "shell"
    assert observation["status"] == "ok"
    artifact = Path(observation["artifact_path"])
    assert artifact.exists()
    assert "proj-x" in artifact.name
    assert "list_files" in artifact.name
    raw = json.loads(artifact.read_text(encoding="utf-8"))
    assert raw["returncode"] == 0
    assert "hello" in raw["stdout"]

    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert state["observations"]
    context = AutoResearchContextManager(settings).build_parent_context()
    assert len(context) <= settings.context_char_budget


def test_project_confined_runner_allows_project_local_rm_without_global_approval(tmp_path):
    target = tmp_path / "scratch.txt"
    target.write_text("x", encoding="utf-8")
    runner = ProjectConfinedCommandRunner(tmp_path, timeout_seconds=10)

    result = runner.run("rm scratch.txt")

    assert result["returncode"] == 0
    assert not target.exists()


def test_project_confined_runner_rejects_workspace_escape(tmp_path):
    runner = ProjectConfinedCommandRunner(tmp_path, timeout_seconds=10)

    try:
        runner.run("cat ../outside.txt")
    except AutoResearchSafetyError as exc:
        assert "escape" in str(exc)
    else:
        raise AssertionError("expected escape to be rejected")


def test_autoresearch_write_action_is_project_confined(tmp_path):
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=1)

    def planner(parent_context, round_index):
        return AutoResearchAction(type="write", rationale="write_note", path="notes/out.md", content="ok")

    result = AutoResearchLoop(settings, planner=planner).run()

    assert result["observations"][0]["status"] == "ok"
    assert (tmp_path / "notes" / "out.md").read_text(encoding="utf-8") == "ok"
