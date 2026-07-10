import json
import threading
from pathlib import Path

import pytest

from core.autoresearch import (
    AutoresearchInterrupted,
    build_paths,
    init_state,
    run_autoresearch_cycle,
)
from main import _format_autoresearch_result


def test_autoresearch_cycle_creates_minimal_state_files(tmp_path):
    result = run_autoresearch_cycle(tmp_path, objective="测试最小闭环")

    state_dir = tmp_path / ".autoresearch"
    assert result["success"] is True
    assert state_dir.exists()
    assert (state_dir / "state.json").exists()
    assert (state_dir / "plan.json").exists()
    assert (state_dir / "execute_result.json").exists()
    assert (state_dir / "conclude_result.json").exists()
    assert (state_dir / "memory.md").exists()
    assert (state_dir / "lessons.md").exists()
    assert (state_dir / "results.tsv").exists()

    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "completed"
    assert state["decision"] == "keep"

    plan = json.loads((state_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["role"] == "Plan"
    assert plan["experiments"][0]["commands"]

    execute_result = json.loads((state_dir / "execute_result.json").read_text(encoding="utf-8"))
    assert execute_result["role"] == "Execute"
    assert len(execute_result["command_results"]) >= 1

    conclude_result = json.loads((state_dir / "conclude_result.json").read_text(encoding="utf-8"))
    assert conclude_result["role"] == "Conclude"
    assert "本轮没有自动修改项目代码。" in conclude_result["notes"]


def test_autoresearch_cancel_marks_state_interrupted(tmp_path):
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(AutoresearchInterrupted):
        run_autoresearch_cycle(tmp_path, cancel_event=cancel_event)

    state = json.loads((tmp_path / ".autoresearch" / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "interrupted"
    assert state["interrupted"] is True


def test_autoresearch_format_result_is_user_facing(tmp_path):
    result = {
        "project_root": str(tmp_path),
        "state_dir": str(tmp_path / ".autoresearch"),
        "run_dir": str(tmp_path / ".autoresearch" / "runs" / "exp_test"),
        "execute_result": {"command_results": [{"returncode": 0}]},
        "conclude_result": {"decision": "keep", "status": "completed", "summary": "ok"},
    }

    text = _format_autoresearch_result(result)

    assert "Autoresearch 本轮完成" in text
    assert "执行命令数：1" in text
    assert "决策：`keep`" in text


def test_init_state_preserves_expected_paths(tmp_path):
    paths = build_paths(tmp_path, run_id="exp_fixed")
    init_state(paths, objective="固定 run")

    assert paths.run_dir.name == "exp_fixed"
    assert paths.state_json.exists()
    assert paths.results_tsv.read_text(encoding="utf-8").startswith("run_id\tstarted_at")


def test_autoresearch_debug_trace_archives_workers_contexts_and_flow(tmp_path):
    result = run_autoresearch_cycle(tmp_path, objective="测试 debug trace")

    trace = result["trace"]
    trace_jsonl = Path(trace["trace_jsonl"])
    flow_md = Path(trace["flow_md"])
    contexts_dir = Path(trace["contexts_dir"])
    run_dir = Path(result["run_dir"])

    assert trace_jsonl.exists()
    assert flow_md.exists()
    assert contexts_dir.exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "flow.md").exists()

    records = [json.loads(line) for line in trace_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    workers = {record["worker"] for record in records}
    assert {"Main", "Plan", "Execute", "Conclude"}.issubset(workers)
    assert any(record["worker"] == "Execute" and record["event"] == "command_finish" for record in records)

    assert (tmp_path / ".autoresearch" / "traces" / "plan.jsonl").exists()
    assert (tmp_path / ".autoresearch" / "traces" / "execute.jsonl").exists()
    assert (tmp_path / ".autoresearch" / "traces" / "conclude.jsonl").exists()
    assert (contexts_dir / "plan_latest.json").exists()
    assert (contexts_dir / "execute_latest.json").exists()
    assert (contexts_dir / "conclude_latest.json").exists()

    flow_text = flow_md.read_text(encoding="utf-8")
    assert "Plan.start" in flow_text
    assert "Execute.command_finish" in flow_text
    assert "Conclude.finish" in flow_text

    conclude_result = json.loads((tmp_path / ".autoresearch" / "conclude_result.json").read_text(encoding="utf-8"))
    assert trace["trace_jsonl"] in conclude_result["kept_files"]
    assert trace["flow_md"] in conclude_result["kept_files"]
