"""Persist oversized tool results instead of injecting them into model context.

Full outputs are stored under ``sandbox/tool_outputs``. The model receives a
compact ``<persisted-output>`` block containing size metadata, a short preview,
and instructions to use artifact_* tools for targeted follow-up.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Iterable

from core.context.budget_config import BudgetConfig, DEFAULT_BUDGET

PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"
ARTIFACT_DIR = Path("sandbox") / "tool_outputs"
# 迁移到 per-session 沙箱时，Agent 会设置该环境变量指向 <session-root>/tool_outputs。
# 未设置时回退到全局 sandbox/tool_outputs（默认行为不变）。env 变量便于跨隔离子进程继承。
ARTIFACT_DIR_ENV = "R_AGENT_TOOL_OUTPUTS_DIR"


def _artifact_dir() -> Path:
    override = os.environ.get(ARTIFACT_DIR_ENV, "").strip()
    return Path(override) if override else ARTIFACT_DIR


_ERROR_PATTERNS = (
    "traceback",
    "exception",
    "error",
    "failed",
    "fatal",
    "warning",
)


def _safe_name(value: str, default: str = "tool") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or default)).strip("._")
    return value[:80] or default


def _artifact_path(tool_name: str, tool_use_id: str, content: str) -> Path:
    artifact_dir = _artifact_dir()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:10]
    name = f"{stamp}_{_safe_name(tool_name)}_{_safe_name(tool_use_id, 'call')}_{digest}.txt"
    return artifact_dir / name


def generate_preview(content: str, max_chars: int) -> tuple[str, bool]:
    """Return a newline-aware preview and whether content was truncated."""
    if len(content) <= max_chars:
        return content, False
    truncated = content[:max_chars]
    last_nl = truncated.rfind("\n")
    if last_nl > max_chars // 2:
        truncated = truncated[: last_nl + 1]
    return truncated, True


def _count_lines(content: str) -> int:
    if not content:
        return 0
    return content.count("\n") + (0 if content.endswith("\n") else 1)


def _detect_format(content: str) -> str:
    stripped = content.lstrip()
    if not stripped:
        return "empty"
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(content)
            return "json"
        except Exception:
            pass
    first_lines = [line for line in content.splitlines()[:5] if line.strip()]
    if first_lines and any("," in line for line in first_lines):
        return "text_or_csv"
    lowered = content[:50_000].lower()
    if any(pattern in lowered for pattern in _ERROR_PATTERNS):
        return "log_like_text"
    return "text"


def _keyword_counts(content: str) -> dict:
    lowered = content.lower()
    return {pattern: lowered.count(pattern) for pattern in _ERROR_PATTERNS if lowered.count(pattern)}


def _json_overview(content: str) -> dict:
    try:
        data = json.loads(content)
    except Exception:
        return {}
    if isinstance(data, dict):
        return {
            "json_type": "object",
            "top_level_keys": list(data.keys())[:50],
            "top_level_key_count": len(data),
        }
    if isinstance(data, list):
        sample = data[:3]
        keys = []
        for item in sample:
            if isinstance(item, dict):
                for key in item.keys():
                    if key not in keys:
                        keys.append(key)
        return {
            "json_type": "array",
            "array_length": len(data),
            "sample_keys": keys[:50],
        }
    return {"json_type": type(data).__name__}


def summarize_content(content: str) -> dict:
    fmt = _detect_format(content)
    summary = {
        "detected_format": fmt,
        "chars": len(content),
        "lines": _count_lines(content),
    }
    counts = _keyword_counts(content[:300_000])
    if counts:
        summary["keyword_counts_first_300k"] = counts
    if fmt == "json":
        summary.update(_json_overview(content))
    return summary


def _build_persisted_message(preview: str, has_more: bool, original_size: int, file_path: Path, summary: dict) -> str:
    size_kb = original_size / 1024
    size_str = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb:.1f} KB"
    summary_text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    message = [
        PERSISTED_OUTPUT_TAG,
        f"This tool result was too large ({original_size:,} characters, {size_str}).",
        f"Full output saved to: {file_path}",
        "The full output was NOT injected into context.",
        "Use artifact_inspect for overview, artifact_search for targeted matching, or artifact_slice for a bounded line range. Avoid reading the whole artifact back into context.",
        "",
        "Summary:",
        summary_text,
        "",
        f"Preview (first {len(preview)} chars):",
        preview,
    ]
    if has_more:
        message.append("...")
    message.append(PERSISTED_OUTPUT_CLOSING_TAG)
    return "\n".join(message)


def maybe_persist_tool_result(
    content: str,
    tool_name: str,
    tool_use_id: str | None = None,
    config: BudgetConfig = DEFAULT_BUDGET,
    threshold: int | float | None = None,
) -> str:
    """Persist an oversized tool result and return a compact replacement."""
    if content is None:
        return content
    if not isinstance(content, str):
        content = str(content)
    effective_threshold = threshold if threshold is not None else config.resolve_threshold(tool_name)
    if effective_threshold == float("inf") or len(content) <= effective_threshold:
        return content

    tool_use_id = tool_use_id or f"{int(time.time() * 1000)}"
    preview, has_more = generate_preview(content, max_chars=config.preview_size)
    summary = summarize_content(content)
    path = _artifact_path(tool_name, tool_use_id, content)
    try:
        path.write_text(content, encoding="utf-8")
        return _build_persisted_message(preview, has_more, len(content), path, summary)
    except Exception as exc:
        return (
            f"{preview}\n\n"
            f"[Truncated: tool response was {len(content):,} chars, but saving full output failed: {exc}]"
        )


def enforce_turn_budget(tool_messages: list[dict], config: BudgetConfig = DEFAULT_BUDGET) -> list[dict]:
    """Persist largest non-persisted tool messages until aggregate budget fits."""
    total = 0
    candidates: list[tuple[int, int]] = []
    for idx, msg in enumerate(tool_messages):
        content = msg.get("content", "")
        size = len(str(content))
        total += size
        if PERSISTED_OUTPUT_TAG not in str(content):
            candidates.append((idx, size))
    if total <= config.turn_budget:
        return tool_messages
    for idx, size in sorted(candidates, key=lambda item: item[1], reverse=True):
        if total <= config.turn_budget:
            break
        content = str(tool_messages[idx].get("content", ""))
        replacement = maybe_persist_tool_result(
            content,
            tool_messages[idx].get("name", "__budget_enforcement__"),
            tool_messages[idx].get("tool_call_id") or f"budget_{idx}",
            config=config,
            threshold=0,
        )
        # Very small outputs can produce a persisted-output notice that is
        # larger than the original content (mostly in tests or tiny budgets).
        # Do not replace those: aggregate enforcement should reduce context,
        # not make the next LLM request larger.
        if replacement != content and len(replacement) < size:
            total = total - size + len(replacement)
            tool_messages[idx]["content"] = replacement
    return tool_messages
