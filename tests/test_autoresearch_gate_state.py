from autoresearch.state.gates import (
    default_gate_state,
    load_gate_state,
    save_gate_state,
    update_gate_state_from_experiment_state,
)


def test_default_gate_state_shape():
    state = default_gate_state()
    assert state["version"] == 1
    assert state["plan_still_valid"] is True
    assert state["plateau_counter"] == 0


def test_update_gate_state_detects_new_best_and_plateau(tmp_path):
    exp_state = {
        "experiments": [{"experiment_id": "e1", "metrics": {"z": 1}}],
        "pareto_front": [{"experiment_id": "e1"}],
        "best_experiment": {"experiment_id": "e1"},
    }
    first = update_gate_state_from_experiment_state(tmp_path, exp_state)
    assert first["pareto_changed"] is True
    assert first["plateau_counter"] == 0

    second = update_gate_state_from_experiment_state(tmp_path, exp_state)
    assert second["pareto_changed"] is False
    assert second["plateau_counter"] == 1
    assert second["needs_replan"] is True


def test_experiment_count_growth_alone_is_not_pareto_changed(tmp_path):
    first_state = {
        "experiments": [{"experiment_id": "e1", "metrics": {"z": 1}}],
        "pareto_front": [{"experiment_id": "e1"}],
        "best_experiment": {"experiment_id": "e1"},
    }
    second_state = {
        "experiments": [
            {"experiment_id": "e1", "metrics": {"z": 1}},
            {"experiment_id": "e2", "metrics": {"z": 2}},
        ],
        "pareto_front": [{"experiment_id": "e1"}],
        "best_experiment": {"experiment_id": "e1"},
    }
    update_gate_state_from_experiment_state(tmp_path, first_state)
    second = update_gate_state_from_experiment_state(tmp_path, second_state)
    assert second["experiment_count"] == 2
    assert second["pareto_changed"] is False
    assert second["plateau_counter"] == 1


def test_update_gate_state_major_error_invalidates_plan(tmp_path):
    state = update_gate_state_from_experiment_state(tmp_path, {}, major_error=True)
    assert state["plan_still_valid"] is False
    assert state["needs_replan"] is True
    assert state["blocked_reason"] == "major_error"
    assert load_gate_state(tmp_path)["plan_still_valid"] is False


def test_save_gate_state_normalizes(tmp_path):
    save_gate_state(tmp_path, {"plateau_counter": "2", "plan_still_valid": 0})
    state = load_gate_state(tmp_path)
    assert state["plateau_counter"] == 2
    assert state["plan_still_valid"] is False


def _frozen_experiment(idx: int, runtime: float) -> dict:
    """An experiment whose objective is frozen but whose telemetry jitters."""
    return {
        "experiment_id": f"exp-{idx:04d}",
        "status": "ok",
        "created_at": 1000.0 + idx,
        "metrics": {
            "positive_f1": 0.9473684210526316,
            "primary_metric": 0.9473684210526316,
            "precision": 1.0,
            "recall": 0.9,
            "runtime_seconds": runtime,
            "duration_seconds": runtime * 10,
            "returncode": 0.0,
            "num_cases": 18.0,
        },
    }


def test_frozen_objective_with_runtime_jitter_accrues_plateau(tmp_path):
    """Regression: runtime/duration jitter must not reset the plateau brake.

    Previously the Pareto id list rotated whenever an equal-quality result was
    recorded (equal points are mutually non-dominated once telemetry is out of
    dominance), so plateau_counter kept resetting and a converged task span to
    max_steps. The objective-value signature keeps the counter accruing.
    """
    counters = []
    experiments = []
    for i, rt in enumerate([0.0029, 0.0044, 0.0031, 0.0038, 0.0052], start=1):
        experiments.append(_frozen_experiment(i, rt))
        gate = update_gate_state_from_experiment_state(
            tmp_path,
            {
                # All equal-objective points pile onto the front.
                "experiments": experiments,
                "pareto_front": list(experiments),
                "best_experiment": experiments[0],
            },
        )
        counters.append(gate["plateau_counter"])
    # First scored experiment is progress (0); every frozen one after accrues.
    assert counters[0] == 0
    assert counters[-1] >= 3
    assert gate["pareto_changed"] is False
    assert gate["needs_replan"] is True


def test_real_objective_gain_resets_plateau(tmp_path):
    experiments = [_frozen_experiment(1, 0.003), _frozen_experiment(2, 0.004)]
    update_gate_state_from_experiment_state(
        tmp_path,
        {"experiments": experiments, "pareto_front": list(experiments), "best_experiment": experiments[0]},
    )
    improved = {
        "experiment_id": "exp-0003",
        "status": "ok",
        "created_at": 1100.0,
        "metrics": {"positive_f1": 1.0, "primary_metric": 1.0, "precision": 1.0, "recall": 1.0,
                    "runtime_seconds": 0.003, "duration_seconds": 0.03, "returncode": 0.0},
    }
    experiments.append(improved)
    gate = update_gate_state_from_experiment_state(
        tmp_path,
        {"experiments": experiments, "pareto_front": [improved], "best_experiment": improved},
    )
    assert gate["pareto_changed"] is True
    assert gate["plateau_counter"] == 0

