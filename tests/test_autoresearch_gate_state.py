from core.autoresearch_gate_state import (
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
