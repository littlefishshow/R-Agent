#!/usr/bin/env python3
"""回放会话的虚拟上下文时间流（Context Log）。

虚拟上下文（``core/context_log.py``）与物理 ``messages`` 隔离，是会话级、append-only、
无损的真相源：即使上下文被压缩，被折叠掉的原始消息仍完整留在时间流里。本脚本只读，
用于排查 bug、溯源早期任务、核对压缩前后的差异。

用法::

    python3 scripts/replay_context.py --list                 # 列出所有会话时间流
    python3 scripts/replay_context.py <session>              # 按 seq 回放时间线
    python3 scripts/replay_context.py <session> --expand     # 展开 fold 段原文
    python3 scripts/replay_context.py <session> --json       # 原样打印每条 entry
    python3 scripts/replay_context.py /abs/path/to/<s>.context.jsonl

文件默认位于 ``sandbox/context_log/<session>.context.jsonl``（可用环境变量
``CONTEXT_LOG_DIR`` 覆盖），启用 per-session 沙箱时位于 ``<session-root>/context_log/``。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.context_log import read_context_log  # noqa: E402


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    primary = os.environ.get("CONTEXT_LOG_DIR", "").strip() or os.path.join("sandbox", "context_log")
    dirs.append(Path(primary))
    # per-session 沙箱：sandbox/sessions/*/context_log 与 SESSION_SANDBOX_ROOT/*/context_log
    session_roots = [Path("sandbox") / "sessions"]
    env_root = os.environ.get("SESSION_SANDBOX_ROOT", "").strip()
    if env_root:
        session_roots.append(Path(env_root))
    for root in session_roots:
        if root.exists():
            for child in sorted(root.glob("*/context_log")):
                dirs.append(child)
    seen: set = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _list() -> int:
    found = False
    for d in _candidate_dirs():
        if not d.exists():
            continue
        files = sorted(d.glob("*.context.jsonl"))
        if not files:
            continue
        found = True
        print(f"[{d}]")
        for f in files:
            rows = read_context_log(f)
            session = f.name[: -len(".context.jsonl")]
            folds = sum(1 for r in rows if r.get("type") == "fold")
            msgs = sum(1 for r in rows if r.get("type") in {"user", "assistant", "tool_result", "system"})
            print(f"  {session}  entries={len(rows)}  messages={msgs}  folds={folds}")
    if not found:
        print("未找到任何虚拟上下文时间流。")
    return 0


def _resolve(target: str) -> Path | None:
    p = Path(target)
    if p.exists():
        return p
    name = target if target.endswith(".context.jsonl") else f"{target}.context.jsonl"
    for d in _candidate_dirs():
        candidate = d / name
        if candidate.exists():
            return candidate
    return None


def _preview(text: str, limit: int = 140) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _replay(path: Path, *, expand: bool, as_json: bool) -> int:
    rows = read_context_log(path)
    if as_json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False, default=str))
        return 0

    by_seq = {int(r.get("seq") or 0): r for r in rows}
    print(f"回放：{path}   共 {len(rows)} 条 entry\n")
    for r in rows:
        seq = r.get("seq")
        etype = str(r.get("type") or "")
        role = r.get("role") or ""
        meta = r.get("meta") or {}
        if etype == "fold":
            start, end = meta.get("start_seq"), meta.get("end_seq")
            reason = meta.get("reason", "")
            print(f"#{seq:<4} [fold      ] {reason}  折叠 seq {start}..{end}  摘要={_preview(r.get('content'))}")
            if expand and start and end:
                for s in range(int(start), int(end) + 1):
                    inner = by_seq.get(s)
                    if inner and inner.get("type") in {"user", "assistant", "tool_result", "system"}:
                        print(f"        └ #{s:<4} [{inner.get('role',''):<9}] {_preview(inner.get('content'))}")
            continue
        if etype in {"run_start", "run_end", "durable_promote"}:
            print(f"#{seq:<4} [{etype:<10}] {_preview(r.get('content'))}  {json.dumps({k: v for k, v in meta.items() if k != 'stats'}, ensure_ascii=False)}")
            continue
        label = role or etype
        extra = ""
        if meta.get("thinking"):
            extra = f"  (thinking: {_preview(meta['thinking'], 60)})"
        if meta.get("name"):
            extra += f"  tool={meta['name']}"
        print(f"#{seq:<4} [{label:<10}] {_preview(r.get('content'))}{extra}")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv if a not in ("--expand", "--json")]
    expand = "--expand" in argv
    as_json = "--json" in argv
    if not args or args[0] == "--list":
        return _list()
    path = _resolve(args[0])
    if path is None:
        print(f"未找到会话时间流：{args[0]}")
        return 1
    return _replay(path, expand=expand, as_json=as_json)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
