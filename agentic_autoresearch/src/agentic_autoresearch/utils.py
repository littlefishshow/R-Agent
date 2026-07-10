from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: str | Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def safe_relative_path(root: str | Path, path: str | Path) -> Path:
    root = Path(root).expanduser().resolve()
    raw = Path(path)
    target = raw.expanduser().resolve() if raw.is_absolute() else (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {path}") from exc
    return target


def truncate_middle(text: str, max_chars: int) -> str:
    text = str(text or "")
    max_chars = max(0, int(max_chars or 0))
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return text[:max_chars]
    keep = max_chars - 15
    head = keep // 2
    tail = keep - head
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from an LLM response."""
    raw = str(text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}

