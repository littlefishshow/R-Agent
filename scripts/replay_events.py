#!/usr/bin/env python3
"""回放一次 run 的运行事件流（Run Event Stream）。

用法：
    python3 scripts/replay_events.py <run_id>
    python3 scripts/replay_events.py <run_id>.jsonl
    python3 scripts/replay_events.py /abs/path/to/<run_id>.jsonl
    python3 scripts/replay_events.py --list          # 列出所有 run 文件
    python3 scripts/replay_events.py <run_id> --json  # 原样打印每条事件 JSON

事件文件默认位于 ``sandbox/run_events/<run_id>.jsonl``（可用环境变量
``RUN_EVENTS_DIR`` 覆盖），由 core/events.py 的 RunEventStore 追加写入。
本脚本只读，用于事后调试、回放和验证升级效果。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 允许从仓库根目录直接运行。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import events as run_events  # noqa: E402


def _events_dir() -> Path:
    return Path(os.environ.get("RUN_EVENTS_DIR", "").strip() or os.path.join("sandbox", "run_events"))


def _candidate_dirs() -> list[Path]:
    """Return all directories that may hold run-event files.

    Covers the legacy global ``sandbox/run_events`` plus per-session sandbox
    locations (``sandbox/sessions/*/run_events`` and ``SESSION_SANDBOX_ROOT``)
    so replay keeps working after the P2-3 sandbox migration.
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path)
        if key not in seen:
            seen.add(key)
            dirs.append(path)

    _add(_events_dir())
    session_root = Path(os.environ.get("SESSION_SANDBOX_ROOT", "").strip() or os.path.join("sandbox", "sessions"))
    if session_root.exists():
        for child in session_root.glob("*/run_events"):
            _add(child)
    return dirs


def _resolve_path(arg: str) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    if not arg.endswith(".jsonl"):
        arg = arg + ".jsonl"
    for directory in _candidate_dirs():
        candidate = directory / arg
        if candidate.exists():
            return candidate
    return _events_dir() / arg


def _list_runs() -> int:
    candidates = [d for d in _candidate_dirs() if d.exists()]
    if not candidates:
        print(f"(没有事件目录：{_events_dir()})")
        return 1
    files = []
    for directory in candidates:
        files.extend(directory.glob("*.jsonl"))
    if not files:
        print(f"(事件目录为空：{', '.join(str(d) for d in candidates)})")
        return 1
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    print(f"事件目录：{', '.join(str(d) for d in candidates)}\n")
    for f in files:
        rows = run_events.read_events(f)
        print(f"  {f.stem:<48} {len(rows):>4} 事件")
    return 0


def _fmt_content(content: dict) -> str:
    if not content:
        return ""
    parts = []
    for k, v in content.items():
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return "  ".join(parts)


def replay(path: Path, as_json: bool = False) -> int:
    rows = run_events.read_events(path)
    if not rows:
        print(f"(无事件或文件不存在：{path})")
        return 1
    print(f"回放：{path}   共 {len(rows)} 条事件\n")
    for r in rows:
        if as_json:
            print(json.dumps(r, ensure_ascii=False))
            continue
        seq = r.get("seq")
        etype = r.get("event_type", "?")
        cat = r.get("category", "?")
        content = _fmt_content(r.get("content") or {})
        print(f"  #{seq:<4} [{cat:<9}] {etype:<16} {content}")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv if a not in ("--json", "--list")]
    as_json = "--json" in argv
    if "--list" in argv or not args:
        return _list_runs() if ("--list" in argv or not args) else 0
    path = _resolve_path(args[0])
    return replay(path, as_json=as_json)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
