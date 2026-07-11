import json
import time
from pathlib import Path

from autoresearch.autoresearch_loop import AutoResearchSettings, AutoResearchLoop
from autoresearch.autoresearch_monitor import RunMonitor, read_monitor, render_monitor_text
from autoresearch.autoresearch_phases import run_phase_loop
from autoresearch.autoresearch_three_step import ThreeStepController
from autoresearch.autoresearch_tool import auto_research_run_v2_tool, auto_research_v2_status_tool


def test_run_monitor_writes_and_reads_heartbeat(tmp_path):
    mon = RunMonitor(tmp_path / "monitor.json", run_id="r1", project_id="p1")
    mon.set_max_steps(6)
    mon.start()
    mon.update_step(step_index=1, current_phase="init", next_phase="plan",
                    summary="did init", budget_snapshot={"total_tokens": 10, "estimated_usd": 0.01, "calls": 1, "status": "ok"})
    data = read_monitor(tmp_path / "monitor.json")
    assert data["status"] == "running"
    assert data["step_index"] == 1
    assert data["current_phase"] == "init"
    assert data["budget"]["total_tokens"] == 10
    mon.finish(status="completed")
    assert read_monitor(tmp_path / "monitor.json")["status"] == "completed"


def test_read_monitor_missing_file():
    data = read_monitor("/no/such/monitor.json")
    assert data["status"] == "unknown"


def test_render_monitor_text_has_rounds_and_tokens(tmp_path):
    mon = RunMonitor(tmp_path / "m.json", run_id="rX")
    mon.set_max_steps(10)
    mon.start()
    mon.update_step(step_index=3, current_phase="run", next_phase="evaluate", summary="ran",
                    budget_snapshot={"total_tokens": 500, "estimated_usd": 0.5, "calls": 4, "status": "ok",
                                     "duration_seconds_total": 12.0, "duration_seconds_last": 3.0,
                                     "duration_seconds_max": 5.0,
                                     "limits": {"max_tokens": 1000, "max_usd": 5.0}})
    data = read_monitor(tmp_path / "m.json")
    text = render_monitor_text(data)
    assert "rounds: 3/10" in text
    assert "completed tokens: 500/1000" in text
    assert "usd: 0.5/5.0" in text
    # thinking-time line surfaces total/avg/last/max
    assert "think time: total=12.0s" in text
    assert "avg=3.0s" in text  # 12.0 / 4 calls
    assert "last=3.0s" in text
    assert data["budget"]["duration_seconds_avg"] == 3.0


def test_monitor_renders_inflight(tmp_path):
    from autoresearch.autoresearch_debug import inflight_start, set_debug

    set_debug(tmp_path, True)
    inflight_start(tmp_path, "llm", run_id="rI", phase="execute", detail="apply_change attempt 1/1")
    mon = RunMonitor(tmp_path / ".autoresearch" / "monitor.json", run_id="rI")
    mon.set_max_steps(10)
    mon.start()
    data = read_monitor(mon.path)
    text = render_monitor_text(data)
    assert data["inflight"]["kind"] == "llm"
    assert data["inflight"]["run_id"] == "rI"
    assert "inflight: llm" in text
    assert "phase=execute" in text


def test_debug_summary_includes_monitor_budget_events_and_tasks(tmp_path):
    from autoresearch.autoresearch_debug import build_debug_summary, debug_event, inflight_start, set_debug
    from autoresearch.autoresearch_gate_state import save_gate_state
    from autoresearch.autoresearch_todo_state import save_todo_state

    set_debug(tmp_path, True)
    mon = RunMonitor(tmp_path / ".autoresearch" / "monitor.json", run_id="rD", project_id="p")
    mon.set_max_steps(5)
    mon.start()
    mon.update_step(step_index=2, current_phase="run", next_phase="evaluate", summary="ran",
                    budget_snapshot={"total_tokens": 12, "estimated_usd": 0.1, "calls": 2,
                                     "duration_seconds_total": 3.0, "duration_seconds_max": 2.0,
                                     "status": "ok"})
    (tmp_path / ".autoresearch" / "state.json").write_text(json.dumps({
        "best_experiment": {"experiment_id": "e1", "decision": "improved", "metrics": {"z": 1}},
    }), encoding="utf-8")
    save_gate_state(tmp_path, {"pareto_changed": True, "plateau_counter": 0, "plan_still_valid": True})
    save_todo_state(tmp_path, {"tasks": [{"task_id": "t1", "goal": "do it", "status": "pending"}]})
    inflight_start(tmp_path, "shell", detail="bash eval.sh")
    debug_event(tmp_path, "phase_finish", time="now", phase="run", next_phase="evaluate", summary="done")
    debug_event(tmp_path, "shell_finish", returncode=0, elapsed_seconds=0.2, detail="bash eval.sh")

    text = build_debug_summary(tmp_path)
    assert "AutoResearch Debug Summary" in text
    assert "status=running" in text
    assert "inflight: kind=shell" in text
    assert "tokens=12" in text
    assert "best: id=e1" in text
    assert "todo: total=1" in text
    assert "recent shell" in text


def test_monitor_preserves_step_index_on_resume(tmp_path):
    p = tmp_path / "monitor.json"
    p.write_text(json.dumps({
        "run_id": "old", "project_id": "p", "pid": 1, "status": "failed",
        "current_phase": "compress", "next_phase": "execute",
        "step_index": 7, "max_steps": 100,
    }), encoding="utf-8")
    mon = RunMonitor(p, run_id="new", project_id="p")
    data = read_monitor(p)
    assert data["step_index"] == 7
    assert data["current_phase"] == "execute"


def test_read_monitor_marks_missing_running_pid_stale(tmp_path):
    p = tmp_path / "monitor.json"
    p.write_text(json.dumps({
        "run_id": "r", "project_id": "p", "pid": 99999999, "status": "running",
        "current_phase": "execute", "next_phase": "(running)",
        "step_index": 3, "max_steps": 10, "updated_at": time.time(),
    }), encoding="utf-8")
    data = read_monitor(p)
    assert data["status"] == "running"
    assert data["stale"] is True
    assert data["stale_reason"] == "pid_not_found"
    assert "pid_not_found" in render_monitor_text(data)


def test_controller_updates_monitor_each_step(tmp_path):
    (tmp_path / "program.md").write_text("Goal: x\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    mon = RunMonitor(settings.monitor_file(), run_id="rc", project_id="p")
    ctrl = ThreeStepController(settings, loop=loop, monitor=mon)
    ctrl.run(max_steps=4)
    data = read_monitor(settings.monitor_file())
    assert data["step_index"] >= 1
    assert data["status"] in {"completed", "paused"}
    assert data["max_steps"] == 4


def test_run_phase_loop_creates_monitor(tmp_path):
    (tmp_path / "program.md").write_text("Goal: x\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    result = run_phase_loop(settings, max_steps=3, run_id="rl1")
    assert Path(result["monitor_path"]).exists()
    data = read_monitor(result["monitor_path"])
    assert data["run_id"] == "rl1"
    assert data["step_index"] >= 1


def test_v2_background_run_is_nonblocking_and_status_pure_read(tmp_path):
    (tmp_path / "program.md").write_text("Goal: minimize\n", encoding="utf-8")
    (tmp_path / "train.py").write_text("print('t')\n", encoding="utf-8")

    payload = json.loads(auto_research_run_v2_tool(
        str(tmp_path),
        project_id="bgv2",
        max_steps=6,
        use_llm_step_agents=False,
        use_git_versioning=False,
        background=True,
    ))
    assert payload["success"] is True
    assert payload["background"] is True
    assert payload["run_id"].startswith("arv2-")
    assert Path(payload["monitor_path"]).exists()  # queued heartbeat seeded synchronously

    # poll the monitor via the pure-file-read status tool (no LLM)
    final = {}
    for _ in range(100):
        status = json.loads(auto_research_v2_status_tool(project_dir=str(tmp_path)))
        assert status["success"] is True
        final = status
        if status.get("status") in {"completed", "paused", "failed"}:
            break
        time.sleep(0.1)

    assert final.get("status") in {"running", "completed", "paused", "queued", "failed"}
    assert "monitor_text" in final
    # background run eventually advanced at least one round and finished
    assert final.get("step_index", 0) >= 1
    assert final.get("run_id") == payload["run_id"]
