from __future__ import annotations

import json
import sys

from agentic_autoresearch.command_monitor import read_command_status, run_monitored_command
from agentic_autoresearch.monitor import render_monitor_text
from agentic_autoresearch.steps import ATTEMPT_TOOLS, CONCLUDE_TOOLS
from agentic_autoresearch.tools import build_default_tools


def test_run_monitored_command_success(tmp_path):
    result = run_monitored_command(tmp_path, f"{sys.executable} -c \"print('ok')\"", kind="eval", timeout_seconds=5)

    assert result["status"] == "ok"
    assert result["returncode"] == 0
    assert "ok" in result["stdout_tail"]
    status = read_command_status(tmp_path, latest=True)
    assert status["command_id"] == result["command_id"]
    assert status["status"] == "ok"


def test_run_monitored_command_timeout(tmp_path):
    result = run_monitored_command(tmp_path, f"{sys.executable} -c \"import time; time.sleep(5)\"", kind="train", timeout_seconds=1)

    assert result["status"] == "timeout"
    assert result["timed_out"] is True
    status = read_command_status(tmp_path, latest=True)
    assert status["status"] == "timeout"


def test_run_monitored_command_python_shim_uses_python3(tmp_path):
    result = run_monitored_command(
        tmp_path,
        "python -c \"print(f'{1+1}')\"",
        kind="command",
        timeout_seconds=5,
    )

    assert result["status"] == "ok"
    assert result["stdout_tail"].strip() == "2"


def test_monitored_train_eval_tools_registered(tmp_path):
    assert "run_train" in ATTEMPT_TOOLS
    assert "run_eval" in ATTEMPT_TOOLS
    assert "run_pipeline" in ATTEMPT_TOOLS
    assert "command_status" in ATTEMPT_TOOLS
    assert "command_status" in CONCLUDE_TOOLS
    tools = build_default_tools(tmp_path)
    names = {schema["function"]["name"] for schema in tools.schemas()}
    assert {"run_train", "run_eval", "run_pipeline", "command_status"}.issubset(names)


def test_run_pipeline_tool_runs_train_eval_and_read_eval(tmp_path):
    (tmp_path / "program.md").write_text("metric_name: score\nhigher_is_better: true\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "train.sh").write_text(
        "#!/usr/bin/env bash\nset -e\ncat > metrics.json <<'JSON'\n"
        '{"primary_metric": 1.0, "primary_metric_name": "score", "higher_is_better": true, "score": 1.0}\n'
        "JSON\n",
        encoding="utf-8",
    )
    (tmp_path / "train" / "train.sh").chmod(0o755)
    (tmp_path / "eval.sh").write_text("#!/usr/bin/env bash\nset -e\ncat metrics.json\n", encoding="utf-8")
    (tmp_path / "eval.sh").chmod(0o755)
    tools = build_default_tools(tmp_path)

    payload = json.loads(tools.execute("run_pipeline", {}))

    assert payload["success"] is True
    result = payload["result"]
    assert result["status"] == "ok"
    assert result["solved"] is True
    assert result["metric_value"] == 1.0


def test_monitor_text_includes_latest_command():
    text = render_monitor_text({
        "run_id": "r",
        "status": "running",
        "current_step": "attempt",
        "next_step": "(running)",
        "cycle": 0,
        "max_cycles": 1,
        "usage": {},
        "latest_command": {
            "kind": "eval",
            "status": "running",
            "duration_seconds": 12.3,
            "heartbeat_age_seconds": 0.4,
            "command": "bash eval.sh",
        },
    })
    assert "latest_command: eval status=running" in text
