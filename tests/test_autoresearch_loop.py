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


def test_evolution_artifacts_budget_and_non_git_degradation(tmp_path):
    (tmp_path / "program.md").write_text("# Program\nmaximize accuracy while reducing latency\n", encoding="utf-8")
    settings = AutoResearchSettings(
        project_dir=tmp_path,
        project_id="evo",
        max_rounds=3,
        max_experiments=1,
        max_pareto_items=4,
        max_active_context_chars=1200,
        use_git_versioning=False,
    )
    actions = [
        AutoResearchAction(
            type="run",
            rationale=f"experiment_result_trial_{idx}",
            command="printf 'primary_metric_name: accuracy\nprimary_metric: 0.80\n' && printf '{\"latency\": 10}' > metrics.json",
        )
        for idx in range(3)
    ]

    def planner(parent_context, round_index):
        return actions[round_index]

    result = AutoResearchLoop(settings, planner=planner).run()

    assert result["rounds_completed"] == 3
    assert [obs["kind"] for obs in result["observations"]].count("experiment_budget") == 2
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert len(state["experiments"]) == 1
    experiment = state["experiments"][0]
    assert experiment["metrics"]["accuracy"] == 0.8
    assert experiment["metrics"]["latency"] == 10.0
    assert experiment["git_available"] is False
    assert experiment["base_commit"] == ""
    assert experiment["changed_files"] == []
    assert experiment["diff_path"].endswith("manifest.json")
    assert Path(experiment["diff_path"]).exists()

    best_path = tmp_path / ".autoresearch" / "best.json"
    pareto_path = tmp_path / ".autoresearch" / "pareto_front.json"
    active_context_path = tmp_path / ".autoresearch" / "active_context.md"
    assert result["best_path"] == str(best_path)
    assert result["pareto_front_path"] == str(pareto_path)
    assert result["active_context_path"] == str(active_context_path)
    assert best_path.exists()
    assert pareto_path.exists()
    assert active_context_path.exists()
    best = json.loads(best_path.read_text(encoding="utf-8"))
    front = json.loads(pareto_path.read_text(encoding="utf-8"))
    active = active_context_path.read_text(encoding="utf-8")
    assert best["experiment_id"] == experiment["experiment_id"]
    assert [item["experiment_id"] for item in front] == [experiment["experiment_id"]]
    assert "# Active autoresearch context" in active
    assert "## Best experiment" in active
    assert "## Pareto front" in active
    assert len(active) <= 1200


def test_git_snapshot_disabled_and_non_git_safe_degrade(tmp_path):
    from core.autoresearch_loop import git_snapshot, save_project_diff

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    disabled = git_snapshot(tmp_path, enabled=False)
    non_git = git_snapshot(tmp_path, enabled=True)
    assert disabled == {"git_available": False, "reason": "disabled"}
    assert non_git["git_available"] is False
    assert non_git["reason"] == "not_git_repo"

    loop = AutoResearchLoop(AutoResearchSettings(project_dir=tmp_path, max_rounds=0))
    manifest_path = save_project_diff(tmp_path, loop.artifacts, "nongit", git_available=False)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert any(row["path"] == "program.md" for row in manifest["files"])
    assert not (tmp_path / ".git").exists()


def test_multi_objective_pareto_front_keeps_only_non_dominated_candidates():
    from core.autoresearch_loop import choose_best_experiment, pareto_front

    experiments = [
        {"experiment_id": "accurate-but-slow", "created_at": 1, "status": "ok", "metrics": {"accuracy": 0.90, "latency": 100}, "primary_metric_name": "accuracy"},
        {"experiment_id": "faster-tradeoff", "created_at": 2, "status": "ok", "metrics": {"accuracy": 0.88, "latency": 90}, "primary_metric_name": "accuracy"},
        {"experiment_id": "dominated", "created_at": 3, "status": "ok", "metrics": {"accuracy": 0.80, "latency": 110}, "primary_metric_name": "accuracy"},
        {"experiment_id": "failed", "created_at": 4, "status": "failed", "metrics": {"accuracy": 0.99, "latency": 1}, "primary_metric_name": "accuracy"},
    ]
    directions = {"accuracy": True, "latency": False}

    front = pareto_front(experiments, directions, max_items=8)
    front_ids = {item["experiment_id"] for item in front}
    assert front_ids == {"accurate-but-slow", "faster-tradeoff"}
    assert "dominated" not in front_ids
    assert "failed" not in front_ids
    assert [item["experiment_id"] for item in pareto_front(experiments, directions, max_items=1)] == ["faster-tradeoff"]

    best = choose_best_experiment(experiments, directions, primary_name="accuracy")
    assert best["experiment_id"] == "accurate-but-slow"


def _git(repo: Path, *args: str) -> str:
    import subprocess

    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _init_tiny_git_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tester@example.invalid")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "program.md").write_text("# Program\nmaximize accuracy\n", encoding="utf-8")
    (tmp_path / "model.txt").write_text("base\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".autoresearch/\nresults.tsv\nmetrics.json\n", encoding="utf-8")
    _git(tmp_path, "add", "program.md", "model.txt", ".gitignore")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def _run_policy_trials(repo: Path, policy: str, actions: list[AutoResearchAction]):
    settings = AutoResearchSettings(
        project_dir=repo,
        project_id=f"policy-{policy.replace('_', '-')}",
        max_rounds=len(actions),
        max_experiments=max(1, len(actions)),
        versioning_policy=policy,
        use_git_versioning=True,
        max_useful_failures=5,
    )

    def planner(parent_context, round_index):
        return actions[round_index]

    result = AutoResearchLoop(settings, planner=planner).run()
    state = json.loads((repo / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    return result, state


def test_versioning_policy_artifact_only_saves_patch_without_commit(tmp_path):
    repo = _init_tiny_git_repo(tmp_path)
    initial_head = _git(repo, "rev-parse", "HEAD")

    _, state = _run_policy_trials(repo, "artifact_only", [
        AutoResearchAction(
            type="run",
            rationale="trial_artifact_only",
            command="printf changed > model.txt && printf 'primary_metric_name: accuracy\nprimary_metric: 0.80\nhigher_is_better: true\nlatency: 10\n'",
        )
    ])

    record = state["experiments"][0]
    assert _git(repo, "rev-parse", "HEAD") == initial_head
    assert record["version_policy"] == "artifact_only"
    assert record["version_action"] == "artifact_only"
    assert record["commit_sha"] == ""
    assert record["rollback_status"] == "skipped_artifact_only"
    assert Path(record["diff_path"]).exists()
    assert "model.txt" in record["changed_files"]
    assert "M model.txt" in _git(repo, "status", "--porcelain=v1")


def test_versioning_policy_commit_all_trials_commits_valid_trials(tmp_path):
    repo = _init_tiny_git_repo(tmp_path)
    initial_head = _git(repo, "rev-parse", "HEAD")

    _, state = _run_policy_trials(repo, "commit_all_trials", [
        AutoResearchAction(
            type="run",
            rationale="trial_commit_all",
            command="printf committed > model.txt && printf 'primary_metric_name: accuracy\nprimary_metric: 0.81\nhigher_is_better: true\n'",
        )
    ])

    record = state["experiments"][0]
    assert record["version_action"] == "committed"
    assert record["commit_sha"]
    assert record["git_commit"] == record["commit_sha"]
    assert _git(repo, "rev-parse", "HEAD") != initial_head
    assert _git(repo, "status", "--porcelain=v1") == ""
    assert _git(repo, "log", "-1", "--pretty=%s").startswith("auto_research exp-0001-")


def test_versioning_policy_commit_pareto_commits_only_best_or_pareto_and_rolls_back_dominated(tmp_path):
    repo = _init_tiny_git_repo(tmp_path)
    initial_head = _git(repo, "rev-parse", "HEAD")

    _, state = _run_policy_trials(repo, "commit_pareto", [
        AutoResearchAction(
            type="run",
            rationale="trial_pareto_best",
            command="printf best > model.txt && sleep 0.05 && printf 'primary_metric_name: accuracy\nprimary_metric: 0.90\nhigher_is_better: true\nlatency: 100\n'",
        ),
        AutoResearchAction(
            type="run",
            rationale="trial_dominated",
            command="printf dominated > model.txt && printf 'primary_metric_name: accuracy\nprimary_metric: 0.80\nhigher_is_better: true\nlatency: 110\n'",
        ),
    ])

    first, second = state["experiments"]
    assert first["version_action"] == "committed"
    assert first["commit_sha"]
    assert second["version_action"] == "artifact_only_not_selected"
    assert second["commit_sha"] == ""
    assert second["rollback_status"] == "rolled_back"
    assert Path(second["diff_path"]).exists()
    assert (repo / "model.txt").read_text(encoding="utf-8") == "best"
    assert _git(repo, "rev-list", "--count", f"{initial_head}..HEAD") == "1"
    assert [item["experiment_id"] for item in state["pareto_front"]] == [first["experiment_id"]]
    assert state["best_experiment"]["experiment_id"] == first["experiment_id"]


def test_versioning_policy_branch_per_trial_creates_branch_record_and_returns_to_base(tmp_path):
    repo = _init_tiny_git_repo(tmp_path)
    initial_head = _git(repo, "rev-parse", "HEAD")
    initial_branch = _git(repo, "branch", "--show-current")

    _, state = _run_policy_trials(repo, "branch_per_trial", [
        AutoResearchAction(
            type="run",
            rationale="trial_branch",
            command="printf branch-change > model.txt && printf 'primary_metric_name: accuracy\nprimary_metric: 0.82\nhigher_is_better: true\n'",
        )
    ])

    record = state["experiments"][0]
    assert record["version_action"] == "branched"
    assert record["branch"].startswith("autoresearch/exp-0001-")
    assert record["commit_sha"]
    assert record["rollback_status"] == "returned_to_base"
    assert _git(repo, "branch", "--show-current") == initial_branch
    assert _git(repo, "rev-parse", "HEAD") == initial_head
    assert _git(repo, "rev-parse", record["branch"]) == record["commit_sha"]
    assert (repo / "model.txt").read_text(encoding="utf-8") == "base\n"
    assert _git(repo, "status", "--porcelain=v1") == ""


def test_failed_trial_saves_patch_and_rolls_back_tracked_changes(tmp_path):
    repo = _init_tiny_git_repo(tmp_path)
    initial_head = _git(repo, "rev-parse", "HEAD")

    _, state = _run_policy_trials(repo, "commit_all_trials", [
        AutoResearchAction(
            type="run",
            rationale="trial_failed_rollback",
            command="printf failed-change > model.txt; printf 'primary_metric_name: accuracy\nprimary_metric: 0.70\nhigher_is_better: true\n'; exit 2",
        )
    ])

    record = state["experiments"][0]
    assert record["status"] == "failed"
    assert record["version_action"] == "artifact_only_not_selected"
    assert record["commit_sha"] == ""
    assert record["rollback_status"] == "rolled_back"
    assert Path(record["diff_path"]).exists()
    assert state["useful_failures"][0]["diff_path"] == record["diff_path"]
    assert (repo / "model.txt").read_text(encoding="utf-8") == "base\n"
    assert _git(repo, "rev-parse", "HEAD") == initial_head
    assert _git(repo, "status", "--porcelain=v1") == ""


def test_versioning_policy_non_git_safely_degrades_to_manifest_without_git_init(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    (tmp_path / "model.txt").write_text("base\n", encoding="utf-8")

    _, state = _run_policy_trials(tmp_path, "commit_all_trials", [
        AutoResearchAction(
            type="run",
            rationale="trial_non_git_degrade",
            command="printf changed > model.txt && printf 'primary_metric_name: accuracy\nprimary_metric: 0.80\nhigher_is_better: true\n'",
        )
    ])

    record = state["experiments"][0]
    assert record["git_available"] is False
    assert record["version_action"] == "artifact_only_no_git"
    assert record["commit_sha"] == ""
    assert record["rollback_status"] == "skipped_no_git"
    assert record["diff_path"].endswith("manifest.json")
    assert Path(record["diff_path"]).exists()
    assert not (tmp_path / ".git").exists()
