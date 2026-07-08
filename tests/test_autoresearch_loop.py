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


def test_default_rounds_reaches_record_decision(tmp_path):
    """The default rounds must be large enough to reach the record_decision step."""
    from core.autoresearch_loop import FixedAutoResearchPlanner
    from tools.autoresearch_tool import auto_research_run_tool

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    payload = json.loads(auto_research_run_tool(str(tmp_path), project_id="rounds-default"))

    assert payload["success"] is True
    assert payload["rounds_completed"] == len(FixedAutoResearchPlanner.DEFAULT_STEPS)
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    rationales = [obs.get("summary", "") for obs in state["observations"]]
    assert any("conclusion_record_decision" in r for r in rationales)


def test_action_role_drives_experiment_recording(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="role", max_rounds=1, max_experiments=1)
    action = AutoResearchAction(
        type="run",
        rationale="just_running_something",
        command="printf 'primary_metric: 0.5\nprimary_metric_name: accuracy\n'",
        role="trial",
    )

    def planner(parent_context, round_index):
        return action

    AutoResearchLoop(settings, planner=planner).run()

    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert len(state["experiments"]) == 1
    assert state["experiments"][0]["hypothesis"] == "just_running_something"


def test_action_role_baseline_does_not_consume_trial_budget(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="role-baseline", max_rounds=1, max_experiments=1)
    action = AutoResearchAction(
        type="run",
        rationale="an_evaluation",
        command="printf 'primary_metric: 0.5\nprimary_metric_name: accuracy\n'",
        role="baseline",
    )

    def planner(parent_context, round_index):
        return action

    AutoResearchLoop(settings, planner=planner).run()
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert state.get("experiments", []) == []
    assert state.get("baseline_metric") == 0.5


def test_rationale_fallback_still_recognizes_trial(tmp_path):
    """Legacy planners without explicit role should still be counted as trials."""
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="legacy", max_rounds=1, max_experiments=1)
    action = AutoResearchAction(
        type="run",
        rationale="experiment_result_trial_legacy",
        command="printf 'primary_metric: 0.7\nprimary_metric_name: accuracy\n'",
    )

    def planner(parent_context, round_index):
        return action

    AutoResearchLoop(settings, planner=planner).run()
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert len(state["experiments"]) == 1


def test_apply_change_from_write_spec_creates_new_file(tmp_path):
    """plan_change note carrying a JSON write spec should upgrade apply_change to apply_patch."""
    from core.autoresearch_loop import AutoResearchWorkflowStep, FixedAutoResearchPlanner

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    spec = {"kind": "write", "path": "greeting.txt", "content": "hello autoresearch\n"}
    plan_step = AutoResearchWorkflowStep(
        name="plan_change",
        action_type="note",
        rationale="proposed_write",
        content=json.dumps(spec),
        allowed_tools=("note", "read"),
    )
    apply_step = AutoResearchWorkflowStep(
        name="apply_change",
        action_type="note",
        rationale="apply_change_placeholder",
        content="fallback note",
        allowed_tools=("apply_patch", "note", "read"),
    )
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="spec-write", max_rounds=2)
    result = AutoResearchLoop(settings, planner=FixedAutoResearchPlanner([plan_step, apply_step])).run()

    assert result["rounds_completed"] == 2
    assert (tmp_path / "greeting.txt").read_text(encoding="utf-8") == "hello autoresearch\n"
    apply_obs = result["observations"][1]
    assert apply_obs["kind"] == "apply_patch"
    assert apply_obs["status"] == "ok"


def test_apply_change_from_search_replace_spec(tmp_path):
    from core.autoresearch_loop import AutoResearchWorkflowStep, FixedAutoResearchPlanner

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    (tmp_path / "model.py").write_text("dropout = 0.3\n", encoding="utf-8")
    spec = {"kind": "search_replace", "path": "model.py", "old": "dropout = 0.3", "new": "dropout = 0.1"}
    plan_step = AutoResearchWorkflowStep(
        name="propose_experiment",
        action_type="note",
        rationale="proposed_edit",
        content=json.dumps(spec),
        allowed_tools=("note", "read"),
    )
    apply_step = AutoResearchWorkflowStep(
        name="apply_change",
        action_type="note",
        rationale="apply_change_placeholder",
        content="fallback note",
        allowed_tools=("apply_patch", "note", "read"),
    )
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="spec-sr", max_rounds=2)
    AutoResearchLoop(settings, planner=FixedAutoResearchPlanner([plan_step, apply_step])).run()

    assert (tmp_path / "model.py").read_text(encoding="utf-8") == "dropout = 0.1\n"
    assert (tmp_path / ".autoresearch" / "proposed_change.json").exists()


def test_apply_change_without_spec_keeps_note_fallback(tmp_path):
    from core.autoresearch_loop import AutoResearchWorkflowStep, FixedAutoResearchPlanner

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    apply_step = AutoResearchWorkflowStep(
        name="apply_change",
        action_type="note",
        rationale="apply_change_placeholder",
        content="no spec available",
        allowed_tools=("apply_patch", "note", "read"),
    )
    settings = AutoResearchSettings(project_dir=tmp_path, project_id="no-spec", max_rounds=1)
    result = AutoResearchLoop(settings, planner=FixedAutoResearchPlanner([apply_step])).run()

    assert result["observations"][0]["kind"] == "note"
    assert result["observations"][0]["status"] == "ok"


def test_evolutionary_planner_uses_full_experiment_budget(tmp_path):
    """With max_experiments=3 and enough rounds, evolutionary planner should record 3 trials."""
    from core.autoresearch_loop import EvolutionaryAutoResearchPlanner

    (tmp_path / "program.md").write_text("# Program\nmax accuracy\n", encoding="utf-8")
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'primary_metric_name: accuracy\\nprimary_metric: 0.5\\nhigher_is_better: true\\n'\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").chmod(0o755)
    settings = AutoResearchSettings(
        project_dir=tmp_path,
        project_id="evo",
        max_rounds=22,
        max_experiments=3,
        planner_kind="evolutionary",
        use_git_versioning=False,
    )
    AutoResearchLoop(settings).run()
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert len(state["experiments"]) == 3


def test_evolutionary_planner_respects_max_experiments_of_one(tmp_path):
    (tmp_path / "program.md").write_text("# Program\nmax accuracy\n", encoding="utf-8")
    (tmp_path / "eval.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'primary_metric_name: accuracy\\nprimary_metric: 0.5\\n'\n",
        encoding="utf-8",
    )
    (tmp_path / "eval.sh").chmod(0o755)
    settings = AutoResearchSettings(
        project_dir=tmp_path,
        project_id="evo-1",
        max_rounds=22,
        max_experiments=1,
        planner_kind="evolutionary",
        use_git_versioning=False,
    )
    AutoResearchLoop(settings).run()
    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert len(state["experiments"]) == 1


def test_collect_metric_files_ignores_root_state_json(tmp_path):
    """A user-owned state.json in project root must not be misread as metrics."""
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    (tmp_path / "state.json").write_text(json.dumps({"accuracy": 0.999, "confidence": 0.9}), encoding="utf-8")

    settings = AutoResearchSettings(project_dir=tmp_path, project_id="state-json", max_rounds=1, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    metrics, _ = loop._collect_metric_files()
    assert "accuracy" not in metrics
    assert "confidence" not in metrics


def test_normalize_planner_kind_and_settings_defaults():
    from core.autoresearch_loop import normalize_planner_kind

    assert normalize_planner_kind(None) == "fixed"
    assert normalize_planner_kind("evolutionary") == "evolutionary"
    assert normalize_planner_kind("Weird") == "fixed"


def test_step_agent_retries_before_falling_back(tmp_path):
    from core.autoresearch_loop import AutoResearchStepAgent, AutoResearchWorkflowStep

    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(
        project_dir=tmp_path,
        project_id="retry",
        max_rounds=1,
        use_llm_step_agents=True,
        llm_request_timeout=1.0,
        llm_retry_attempts=1,
    )

    class _FakeChat:
        def __init__(self):
            self.calls = 0

        class _Completions:
            def __init__(self, outer):
                self.outer = outer

            def create(self, **kwargs):
                self.outer.calls += 1
                if self.outer.calls == 1:
                    raise RuntimeError("boom")

                class _Msg:
                    content = '{"action": {"type": "note", "rationale": "ok", "content": "recovered"}, "bucket_updates": {}}'

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        def __init__wrap(self):
            self.completions = _FakeChat._Completions(self)

        # attach in constructor
        @property
        def completions(self):
            if not hasattr(self, "_completions"):
                self._completions = _FakeChat._Completions(self)
            return self._completions

    class _FakeClient:
        def __init__(self):
            self.chat = _FakeChat()

    client = _FakeClient()
    agent = AutoResearchStepAgent(settings, client=client, model="test-model")
    step = AutoResearchWorkflowStep(name="apply_change", action_type="note", rationale="fb", content="fb", allowed_tools=("note",))
    result = agent.plan_step(step=step, fallback_action=step.to_action(), parent_context="{}", round_index=0)

    assert client.chat.calls == 2
    assert result.action.type == "note"
    assert "recovered" in result.action.content


def test_eval_readonly_guard_blocks_apply_patch_to_prepare_py(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    (tmp_path / "prepare.py").write_text("print('eval harness')\n", encoding="utf-8")
    patch = """--- a/prepare.py
+++ b/prepare.py
@@ -1 +1 @@
-print('eval harness')
+print('tampered')
"""
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0)
    loop = AutoResearchLoop(settings)
    obs = loop.execute_action(AutoResearchAction(type="apply_patch", rationale="tamper_eval", patch=patch))
    assert obs.status == "failed"
    assert (tmp_path / "prepare.py").read_text(encoding="utf-8") == "print('eval harness')\n"


def test_eval_readonly_guard_blocks_write_action_to_eval_dir(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0)
    loop = AutoResearchLoop(settings)
    obs = loop.execute_action(AutoResearchAction(type="write", rationale="tamper", path="eval/metric.py", content="x"))
    assert obs.status == "failed"
    assert not (tmp_path / "eval" / "metric.py").exists()


def test_eval_readonly_guard_allows_non_eval_write(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0)
    loop = AutoResearchLoop(settings)
    obs = loop.execute_action(AutoResearchAction(type="write", rationale="ok", path="train/model.py", content="ok"))
    assert obs.status == "ok"
    assert (tmp_path / "train" / "model.py").read_text(encoding="utf-8") == "ok"


def test_loop_builds_budget_ledger_and_tiers(tmp_path):
    (tmp_path / "program.md").write_text("# Program\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, max_tokens=5000, model_tier_util="cheap-model")
    loop = AutoResearchLoop(settings)
    assert loop.budget is not None
    assert loop.budget.limits.max_tokens == 5000
    assert loop.model_tiers.resolve("util") == "cheap-model"
    assert str(loop.settings.budget_file()).endswith(".autoresearch/budget.json")

