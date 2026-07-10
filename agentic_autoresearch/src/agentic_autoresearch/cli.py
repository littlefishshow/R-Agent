from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .llm import create_default_client, default_model
from .monitor import read_monitor, render_monitor_text
from .runner import ThreeStepAutoResearch
from .types import AutoResearchConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-autoresearch",
        description="Run the standalone agentic_autoresearch three-step loop.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run plan -> attempt -> conclude cycles for a project.")
    run.add_argument("project_dir", help="Project directory containing program.md/train/eval files.")
    run.add_argument("--run-id", default="", help="Run id written into monitor/state files.")
    run.add_argument("--model", default="", help="Model name. Defaults to core.config.get_model() when available.")
    run.add_argument("--max-cycles", type=int, default=3, help="Number of full plan/attempt/conclude cycles.")
    run.add_argument("--max-iterations-per-step", type=int, default=12, help="LLM tool-call iterations per step.")
    run.add_argument("--command-timeout-seconds", type=int, default=300, help="Timeout for run_command tool.")
    run.add_argument("--context-char-budget", type=int, default=24000, help="Approximate step context budget.")
    run.add_argument("--no-trace", action="store_true", help="Disable .autoresearch/traces JSON dumps.")
    run.add_argument("--debug", action="store_true", help="Enable .autoresearch/debug/debug.jsonl and inflight.json.")
    run.add_argument("--continue-on-step-failure", action="store_true", help="Pause instead of failing when a step misses its done tag.")

    status = sub.add_parser("status", help="Read a project's .autoresearch/monitor.json.")
    status.add_argument("project_dir")
    status.add_argument("--json", action="store_true", help="Print raw JSON instead of text summary.")

    stop = sub.add_parser("stop", help="Request graceful stop by writing .autoresearch/STOP.")
    stop.add_argument("project_dir")
    stop.add_argument("--resume", action="store_true", help="Remove STOP sentinel instead of creating it.")

    debug = sub.add_parser("debug", help="Print recent debug events.")
    debug.add_argument("project_dir")
    debug.add_argument("--tail", type=int, default=40)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "status":
        return _status(args)
    if args.command == "stop":
        return _stop(args)
    if args.command == "debug":
        return _debug(args)
    raise AssertionError(args.command)


def _config_from_args(args) -> AutoResearchConfig:
    run_id = args.run_id or f"agentic-{int(time.time())}"
    return AutoResearchConfig(
        project_dir=args.project_dir,
        run_id=run_id,
        model=args.model or default_model(),
        max_cycles=args.max_cycles,
        max_iterations_per_step=args.max_iterations_per_step,
        command_timeout_seconds=args.command_timeout_seconds,
        context_char_budget=args.context_char_budget,
        trace=not args.no_trace,
        debug=bool(args.debug),
        stop_on_step_failure=not args.continue_on_step_failure,
    )


def _run(args) -> int:
    config = _config_from_args(args)
    runner = ThreeStepAutoResearch(config, client=create_default_client())
    result = runner.run()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"completed", "stopped"} else 1


def _status(args) -> int:
    root = Path(args.project_dir).expanduser().resolve()
    data = read_monitor(root / ".autoresearch" / "monitor.json")
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_monitor_text(data))
    return 0


def _stop(args) -> int:
    root = Path(args.project_dir).expanduser().resolve()
    stop_path = root / ".autoresearch" / "STOP"
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    if args.resume:
        existed = stop_path.exists()
        stop_path.unlink(missing_ok=True)
        print(json.dumps({"action": "resume", "removed": existed, "stop_path": str(stop_path)}, ensure_ascii=False))
    else:
        stop_path.write_text(f"stop requested at {time.time()}\n", encoding="utf-8")
        print(json.dumps({"action": "stop", "stop_path": str(stop_path)}, ensure_ascii=False))
    return 0


def _debug(args) -> int:
    root = Path(args.project_dir).expanduser().resolve()
    path = root / ".autoresearch" / "debug" / "debug.jsonl"
    if not path.exists():
        print(f"no debug log: {path}")
        return 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-max(1, int(args.tail or 40)):]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
