import json
from pathlib import Path

from autoresearch.anomalies import detect_run_anomalies, normalize_run_spec, snapshot_files
from autoresearch.reproducibility import compare_metric, build_reproducibility_report
from autoresearch.state.passport import build_passport, render_passport_markdown
from autoresearch.state.schema import stamp_state_revision, validate_autoresearch_state


def test_passport_renders_structured_header():
    passport = build_passport(
        origin_mode="run",
        project_id="p",
        artifact_type="experiment_record",
        verification_status="verified",
        record_id="exp-1",
    )
    assert passport["verification_status"] == "VERIFIED"
    text = render_passport_markdown(passport)
    assert "## Material Passport" in text
    assert "Origin Skill: R-Agent AutoResearch" in text
    assert "Verification Status: VERIFIED" in text
    assert "Record ID: exp-1" in text


def test_state_revision_stamping_and_validation(tmp_path):
    state = stamp_state_revision({"summary": "x", "observations": [], "experiments": [], "pareto_front": [], "useful_failures": []})
    assert state["schema_version"] == 1
    assert state["revision"] == 1
    (tmp_path / ".autoresearch").mkdir()
    (tmp_path / ".autoresearch" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "project.md").write_text("# Project\n<!-- PHASE: plan -->\n", encoding="utf-8")

    check = validate_autoresearch_state(tmp_path)
    assert check["ok"] is True
    assert any(item["name"] == "state_json" and item["ok"] for item in check["checks"])


def test_state_validation_flags_corrupt_json(tmp_path):
    (tmp_path / ".autoresearch").mkdir()
    (tmp_path / ".autoresearch" / "state.json").write_text("{bad", encoding="utf-8")
    check = validate_autoresearch_state(tmp_path)
    assert check["ok"] is False
    assert any("state" in item.get("name", "") and not item.get("ok") for item in check["checks"])


def test_run_spec_monitoring_detects_missing_and_stalled_outputs(tmp_path):
    (tmp_path / "out.txt").write_text("same", encoding="utf-8")
    spec = normalize_run_spec({"expected_outputs": ["missing.json"], "monitor_files": ["out.txt"]})
    before = snapshot_files(tmp_path, ["missing.json", "out.txt"])
    after = snapshot_files(tmp_path, ["missing.json", "out.txt"])
    anomalies = detect_run_anomalies(
        root=tmp_path,
        run_spec=spec,
        result={"status": "ok", "returncode": 0},
        before_files=before,
        after_files=after,
        elapsed_seconds=0.1,
    )
    types = {item["type"] for item in anomalies}
    assert "MISSING_OUTPUT" in types
    assert "OUTPUT_STALL" in types


def test_anomaly_classifier_distinguishes_schema_error():
    anomalies = detect_run_anomalies(
        root=Path("."),
        run_spec={},
        result={"status": "failed", "returncode": 1, "stderr": "SUBMISSION SCHEMA ERROR: bad"},
    )
    assert anomalies[0]["type"] == "SCHEMA_ERROR"


def test_reproducibility_comparison_modes():
    exact = compare_metric(1.0, 1.0, determinism="deterministic")
    assert exact["match"] is True
    mismatch = compare_metric(1.0, 1.01, determinism="deterministic")
    assert mismatch["match"] is False
    stochastic = compare_metric(1.0, 1.03, determinism="stochastic", threshold=0.05)
    assert stochastic["match"] is True


def test_reproducibility_report_sets_passport_status():
    report = build_reproducibility_report(
        best={"primary_metric_name": "score", "metrics": {"score": 1.0}},
        reruns=[{"metrics": {"score": 1.0}, "status": "ok"}],
        determinism="deterministic",
        threshold=0.0,
    )
    assert report["verdict"] == "REPRODUCIBLE"
    assert report["passport_status"] == "VERIFIED"
