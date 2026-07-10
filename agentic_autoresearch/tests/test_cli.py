from __future__ import annotations

import json

from agentic_autoresearch.cli import main


def test_cli_status_reads_monitor(tmp_path, capsys):
    mon = tmp_path / ".autoresearch" / "monitor.json"
    mon.parent.mkdir()
    mon.write_text(json.dumps({
        "run_id": "r1",
        "status": "completed",
        "current_step": "conclude",
        "next_step": "plan",
        "cycle": 1,
        "max_cycles": 1,
        "last_summary": "done",
        "usage": {"llm_calls": 2, "tool_calls": 3},
    }), encoding="utf-8")

    assert main(["status", str(tmp_path), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"run_id": "r1"' in out


def test_cli_stop_and_resume(tmp_path, capsys):
    assert main(["stop", str(tmp_path)]) == 0
    stop_path = tmp_path / ".autoresearch" / "STOP"
    assert stop_path.exists()
    assert '"action": "stop"' in capsys.readouterr().out

    assert main(["stop", str(tmp_path), "--resume"]) == 0
    assert not stop_path.exists()
    assert '"action": "resume"' in capsys.readouterr().out
