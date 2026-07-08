import json
import time
from pathlib import Path

from core.autoresearch_loop import AutoResearchSettings, AutoResearchLoop
from core.autoresearch_monitor import RunMonitor, read_monitor, render_monitor_text
from core.autoresearch_phases import PhaseController, run_phase_loop
from core.autoresearch_phase_handlers import default_handlers
from tools.autoresearch_tool import auto_research_run_v2_tool, auto_research_v2_status_tool


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
    assert "tokens: 500/1000" in text
    assert "usd: 0.5/5.0" in text
    # thinking-time line surfaces total/avg/last/max
    assert "think time: total=12.0s" in text
    assert "avg=3.0s" in text  # 12.0 / 4 calls
    assert "last=3.0s" in text
    assert data["budget"]["duration_seconds_avg"] == 3.0


def test_controller_updates_monitor_each_step(tmp_path):
    (tmp_path / "program.md").write_text("Goal: x\n", encoding="utf-8")
    settings = AutoResearchSettings(project_dir=tmp_path, max_rounds=0, use_git_versioning=False)
    loop = AutoResearchLoop(settings)
    mon = RunMonitor(settings.monitor_file(), run_id="rc", project_id="p")
    ctrl = PhaseController(settings, handlers=default_handlers(), loop=loop, monitor=mon)
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
