import json

from rich.console import Console

import autoresearch.tool as autoresearch_tool
from main import _handle_autoresearch_command, get_completions


def _recording_console() -> Console:
    return Console(record=True, width=120)


def test_autoresearch_run_command_launches_v2_background(tmp_path, monkeypatch):
    calls = []

    def fake_run(project_dir, **kwargs):
        calls.append((project_dir, kwargs))
        return json.dumps({
            "success": True,
            "project_dir": project_dir,
            "run_id": "arv2-test",
            "monitor_path": str(tmp_path / ".autoresearch" / "monitor.json"),
        })

    monkeypatch.setattr(autoresearch_tool, "auto_research_run_v2_tool", fake_run)
    console = _recording_console()

    _handle_autoresearch_command(["run", str(tmp_path)], console)

    assert calls == [(str(tmp_path), {"background": True, "detach": True, "debug_mode": True})]
    output = console.export_text()
    assert "已在后台启动 autoresearch" in output
    assert "arv2-test" in output


def test_autoresearch_show_reads_monitor_without_llm(tmp_path, monkeypatch):
    (tmp_path / "project.md").write_text(
        "# Project State\n\n"
        "## 当前计划\nImprove the optimizer and validate it.\n\n"
        "## 短期结论\n- best improved and completion is not met yet.\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".autoresearch"
    state_dir.mkdir()
    (state_dir / "todo_state.json").write_text(json.dumps({
        "tasks": [
            {"task_id": "baseline", "status": "verified"},
            {"task_id": "impl", "status": "pending"},
        ]
    }), encoding="utf-8")
    (state_dir / "state.json").write_text(json.dumps({
        "experiments": [
            {
                "experiment_id": "exp-baseline",
                "status": "ok",
                "primary_metric_name": "score",
                "metrics": {"score": 10.0},
                "metric_directions": {"score": False},
            },
            {
                "experiment_id": "exp-best",
                "status": "ok",
                "primary_metric_name": "score",
                "metrics": {"score": 7.0},
                "metric_directions": {"score": False},
            },
        ],
        "best_experiment": {
            "experiment_id": "exp-best",
            "primary_metric_name": "score",
            "metrics": {"score": 7.0},
            "metric_directions": {"score": False},
        },
    }), encoding="utf-8")

    def fake_status(project_dir=".", monitor_path=""):
        return json.dumps({
            "success": True,
            "monitor_text": f"status for {project_dir}",
        })

    monkeypatch.setattr(autoresearch_tool, "auto_research_v2_status_tool", fake_status)
    console = _recording_console()

    _handle_autoresearch_command(["show", str(tmp_path)], console)

    output = console.export_text()
    assert f"status for {tmp_path}" in output
    assert "Project progress" in output
    assert "tasks: 1/2 done" in output
    assert "Current plan" in output
    assert "Improve the optimizer" in output
    assert "Latest conclusion" in output
    assert "Metric progress" in output
    assert "baseline=10" in output
    assert "improvement=+3" in output


def test_autoresearch_debug_routes_to_debug_helpers(tmp_path, monkeypatch):
    import autoresearch.observability.debug as debug_module

    states = []
    monkeypatch.setattr(debug_module, "set_debug", lambda root, enabled: states.append((root, enabled)) or root / ".autoresearch" / "DEBUG")
    monkeypatch.setattr(debug_module, "build_debug_summary", lambda root: f"debug summary: {root}")
    console = _recording_console()

    _handle_autoresearch_command(["debug", "on", str(tmp_path)], console)
    _handle_autoresearch_command(["debug", "show", str(tmp_path)], console)

    assert states == [(tmp_path, True)]
    assert "debug summary" in console.export_text()


def test_autoresearch_kill_reports_no_processes(monkeypatch):
    import main

    monkeypatch.setattr(main, "_find_autoresearch_processes", lambda: [])
    console = _recording_console()

    _handle_autoresearch_command(["kill"], console)

    assert "没有发现正在运行的 autoresearch 进程" in console.export_text()


def test_autoresearch_slash_completion_exposes_subcommands():
    completer = get_completions()

    autoresearch = completer.options["/autoresearch"]
    assert {"run", "show", "debug", "kill"}.issubset(autoresearch.options)
    assert {"on", "off", "show"}.issubset(autoresearch.options["debug"].options)

