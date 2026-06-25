#!/usr/bin/env python3
"""Project progress context helper for R-Agent skills.

This script stores and reads project handoff/context notes under a skill-local
Project_progress/ directory. It is intentionally skill-local instead of a global
LLM tool: call it via run_command when the project_progress_context skill is in
use.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
from pathlib import Path
from typing import Iterable

SCRIPT_PATH = Path(__file__).resolve()
SKILL_DIR = SCRIPT_PATH.parent.parent
PROGRESS_DIR = SKILL_DIR / "Project_progress"


def _slug(text: str) -> str:
    text = (text or "general").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff._-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-._")
    return text or "general"


def _today() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d")


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _project_files(project: str | None = None) -> list[Path]:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [p for p in PROGRESS_DIR.glob("*") if p.is_file() and p.name != "README.md"],
        key=lambda p: p.stat().st_mtime,
    )
    if project:
        s = _slug(project)
        files = [p for p in files if s in p.stem.lower()]
    return files


def _format_list(files: Iterable[Path]) -> str:
    rows = []
    for p in files:
        mtime = _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        rows.append(f"{mtime}\t{p}")
    return "\n".join(rows) if rows else "(no project progress files found)"


def save(args: argparse.Namespace) -> None:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    project = _slug(args.project)
    path = Path(args.output) if args.output else PROGRESS_DIR / f"{_today()}_{project}_context.md"
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    # Keep writes scoped to the skill-local Project_progress dir unless explicitly absolute inside it.
    try:
        path.relative_to(PROGRESS_DIR.resolve())
    except ValueError:
        if args.output:
            raise SystemExit(f"Refusing to write outside {PROGRESS_DIR}: {path}")

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    files = "\n".join(f"- `{f}`" for f in (args.file or [])) or "- (not specified)"
    body = f"""

---

## Progress Entry — {_timestamp()}

### Project

{args.project}

### Summary

{args.summary.strip() if args.summary else "(not specified)"}

### Current Status

{args.status.strip() if args.status else "(not specified)"}

### Key Files / Code Locations

{files}

### Decisions / Context

{args.context.strip() if args.context else "(not specified)"}

### Verification

{args.verification.strip() if args.verification else "(not specified)"}

### Unfinished / Next Steps

{args.next_steps.strip() if args.next_steps else "(not specified)"}
""".lstrip()

    if not existing:
        header = f"# Project Progress Context — {args.project}\n\nCreated: {_timestamp()}\n"
        path.write_text(header + body, encoding="utf-8")
    else:
        path.write_text(existing.rstrip() + "\n\n" + body, encoding="utf-8")
    print(path)


def list_files(args: argparse.Namespace) -> None:
    print(_format_list(_project_files(args.project)))


def latest(args: argparse.Namespace) -> None:
    files = _project_files(args.project)
    if not files:
        raise SystemExit("No project progress files found.")
    print(files[-1])


def read(args: argparse.Namespace) -> None:
    if args.latest:
        files = _project_files(args.project)
        if not files:
            raise SystemExit("No project progress files found.")
        path = files[-1]
    elif args.path:
        path = Path(args.path)
    else:
        raise SystemExit("Provide --latest or --path.")
    print(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Skill-local project progress context helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save", help="Append a progress entry")
    p_save.add_argument("--project", required=True, help="Project/function name")
    p_save.add_argument("--summary", default="", help="Short summary of what happened")
    p_save.add_argument("--status", default="", help="Current status")
    p_save.add_argument("--context", default="", help="Design/context notes")
    p_save.add_argument("--verification", default="", help="Verification performed or pending")
    p_save.add_argument("--next-steps", default="", help="Unfinished work and next steps")
    p_save.add_argument("--file", action="append", help="Relevant file/code path; may repeat")
    p_save.add_argument("--output", default="", help="Optional output path under Project_progress")
    p_save.set_defaults(func=save)

    p_list = sub.add_parser("list", help="List progress files")
    p_list.add_argument("--project", default="", help="Optional project filter")
    p_list.set_defaults(func=list_files)

    p_latest = sub.add_parser("latest", help="Print latest progress file path")
    p_latest.add_argument("--project", default="", help="Optional project filter")
    p_latest.set_defaults(func=latest)

    p_read = sub.add_parser("read", help="Read a progress file")
    p_read.add_argument("--latest", action="store_true", help="Read latest file")
    p_read.add_argument("--project", default="", help="Optional project filter for --latest")
    p_read.add_argument("--path", default="", help="Specific file path")
    p_read.set_defaults(func=read)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
