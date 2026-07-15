import json
from pathlib import Path

from autoresearch.diagnostics import validate_submission_artifacts
from autoresearch.state.regression_check import (
    build_regression_check,
    run_regression_check,
)


# --------------------------------------------------------------------------- #
# Submission schema pre-validation
# --------------------------------------------------------------------------- #

def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_schema_validation_noop_without_reference(tmp_path):
    _write(tmp_path / "submission" / "predictions.json", {"row_0": 1})
    result = validate_submission_artifacts(tmp_path)
    assert result["checked"] is False
    assert result["ok"] is True


def test_schema_validation_flags_list_instead_of_dict(tmp_path):
    _write(tmp_path / "data" / "truth.json", {"row_0": {}, "row_1": {}})
    _write(tmp_path / "submission" / "predictions.json", [1, 2])
    result = validate_submission_artifacts(tmp_path)
    assert result["checked"] is True
    assert result["ok"] is False
    assert "must be a JSON object keyed by case id" in result["diagnostic"]


def test_schema_validation_flags_missing_ids(tmp_path):
    _write(tmp_path / "data" / "truth.json", {"row_0": {}, "row_1": {}, "row_2": {}})
    _write(tmp_path / "submission" / "predictions.json", {"row_0": {}})
    result = validate_submission_artifacts(tmp_path)
    assert result["ok"] is False
    assert "missing" in result["diagnostic"]
    assert "row_1" in result["diagnostic"] or "row_2" in result["diagnostic"]


def test_schema_validation_accepts_complete_dict(tmp_path):
    _write(tmp_path / "data" / "truth.json", {"row_0": {}, "row_1": {}})
    _write(tmp_path / "submission" / "predictions.json", {"row_0": {}, "row_1": {}})
    result = validate_submission_artifacts(tmp_path)
    assert result["checked"] is True
    assert result["ok"] is True
    assert result["problems"] == []


def test_schema_validation_supports_test_cases_list_reference(tmp_path):
    _write(tmp_path / "data" / "test_cases.json", [{"id": "cc_0"}, {"id": "cc_1"}])
    _write(tmp_path / "submission" / "predictions.json", {"cc_0": 1})
    result = validate_submission_artifacts(tmp_path)
    assert result["ok"] is False
    assert "cc_1" in result["diagnostic"]


# --------------------------------------------------------------------------- #
# Generated submission validator helper (run pre-eval by the run handler)
# --------------------------------------------------------------------------- #

def test_generated_submission_validator_exit_codes(tmp_path):
    import subprocess
    import sys
    from autoresearch.run_handler import _ensure_submission_validator

    _write(tmp_path / "data" / "truth.json", {"row_0": {}, "row_1": {}})
    helper = _ensure_submission_validator(tmp_path)
    assert helper is not None

    _write(tmp_path / "submission" / "predictions.json", [1, 2])
    bad = subprocess.run([sys.executable, str(helper)], cwd=tmp_path, capture_output=True, text=True)
    assert bad.returncode == 3
    assert "SUBMISSION SCHEMA ERROR" in bad.stderr

    _write(tmp_path / "submission" / "predictions.json", {"row_0": {}, "row_1": {}})
    good = subprocess.run([sys.executable, str(helper)], cwd=tmp_path, capture_output=True, text=True)
    assert good.returncode == 0


def test_submission_validator_noop_without_reference(tmp_path):
    from autoresearch.run_handler import _ensure_submission_validator
    assert _ensure_submission_validator(tmp_path) is None


# --------------------------------------------------------------------------- #
# Executable per-case regression check
# --------------------------------------------------------------------------- #

def _function_task(tmp_path, solution_body):
    (tmp_path / "eval.py").write_text(
        "import importlib.util\n"
        "from pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('solution', Path('solution.py'))\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "sol = mod\n"
        "sol.decode_text('x')\n",
        encoding="utf-8",
    )
    (tmp_path / "solution.py").write_text(solution_body, encoding="utf-8")
    _write(tmp_path / "metrics.json", {
        "metric_name": "acc",
        "primary_metric": 0.5,
        "failures": [
            {"input": "a", "expected": "A-OK", "pred": "A"},
            {"input": "b", "expected": "B-OK", "pred": "B"},
        ],
    })


def test_regression_check_generates_and_reports_per_case(tmp_path):
    _function_task(tmp_path, "def decode_text(t):\n    return t.upper()\n")
    path = build_regression_check(tmp_path)
    assert path is not None and path.exists()
    out = run_regression_check(tmp_path)
    assert '"case": 0' in out
    assert "A-OK" in out  # expected shown
    assert "still_failing=2/2" in out


def test_regression_check_detects_fixed_cases(tmp_path):
    # A solution that now returns the expected value should report 0 still failing.
    _function_task(tmp_path, "def decode_text(t):\n    return t.upper() + '-OK'\n")
    out = run_regression_check(tmp_path)
    assert "still_failing=0/2" in out


def test_regression_check_noop_without_failures(tmp_path):
    (tmp_path / "solution.py").write_text("def decode_text(t):\n    return t\n", encoding="utf-8")
    _write(tmp_path / "metrics.json", {"metric_name": "acc", "primary_metric": 1.0, "failures": []})
    assert build_regression_check(tmp_path) is None


def test_regression_check_noop_without_entry_function(tmp_path):
    # No eval.py -> cannot detect entry function -> no check generated.
    (tmp_path / "solution.py").write_text("def decode_text(t):\n    return t\n", encoding="utf-8")
    _write(tmp_path / "metrics.json", {"failures": [{"input": "a", "expected": "b"}]})
    assert build_regression_check(tmp_path) is None
