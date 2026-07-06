import json
import os
import re
from pathlib import Path
from tools.registry import registry

DEFAULT_MAX_SLICE_LINES = 500


def _workspace_dir() -> Path:
    return Path(os.getcwd()).resolve()


def _resolve_artifact_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = _workspace_dir() / candidate
    candidate = candidate.resolve()
    try:
        if os.path.commonpath([str(candidate), str(_workspace_dir())]) != str(_workspace_dir()):
            raise ValueError(f"artifact_path must be inside workspace: {path}")
    except ValueError as exc:
        raise ValueError(f"artifact_path must be inside workspace: {path}") from exc
    if not candidate.exists():
        raise FileNotFoundError(f"Artifact not found: {path}")
    if not candidate.is_file():
        raise ValueError(f"Artifact is not a file: {path}")
    return candidate


def _read_lines(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def _line_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for count, _ in enumerate(f, start=1):
            pass
    return count


def _detect_format(sample: str) -> str:
    stripped = sample.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(sample)
            return "json"
        except Exception:
            return "json_like_or_text"
    if any(marker in sample.lower() for marker in ["traceback", "error", "exception", "warning", "failed"]):
        return "log_like_text"
    first = [line for line in sample.splitlines()[:5] if line.strip()]
    if first and any("," in line for line in first):
        return "text_or_csv"
    return "text"


def artifact_inspect_tool(artifact_path: str, sample_lines: int = 20) -> str:
    try:
        path = _resolve_artifact_path(artifact_path)
        sample_lines = max(1, min(int(sample_lines or 20), 100))
        lines = _read_lines(path)
        stat = path.stat()
        head = "".join(lines[:sample_lines])
        tail = "".join(lines[-sample_lines:]) if len(lines) > sample_lines else ""
        return json.dumps({
            "artifact_path": str(path.relative_to(_workspace_dir())),
            "bytes": stat.st_size,
            "lines": len(lines),
            "detected_format": _detect_format(head),
            "head_sample": head,
            "tail_sample": tail,
            "next_actions": [
                "artifact_search for keywords such as ERROR/Traceback/failed",
                "artifact_slice for a specific bounded line range",
                "avoid reading the whole artifact into context",
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def artifact_search_tool(artifact_path: str, query: str, context_lines: int = 3, limit: int = 20, ignore_case: bool = True) -> str:
    try:
        path = _resolve_artifact_path(artifact_path)
        context_lines = max(0, min(int(context_lines or 0), 20))
        limit = max(1, min(int(limit or 20), 100))
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(query, flags)
        lines = _read_lines(path)
        matches = []
        total = 0
        for idx, line in enumerate(lines):
            if regex.search(line):
                total += 1
                if len(matches) >= limit:
                    continue
                start = max(0, idx - context_lines)
                end = min(len(lines), idx + context_lines + 1)
                matches.append({
                    "line": idx + 1,
                    "text": line.rstrip("\n"),
                    "context_start": start + 1,
                    "context_end": end,
                    "context": "".join(f"{i + 1}|{lines[i]}" for i in range(start, end)),
                })
        return json.dumps({
            "artifact_path": str(path.relative_to(_workspace_dir())),
            "query": query,
            "total_matches": total,
            "returned_matches": len(matches),
            "truncated": total > len(matches),
            "matches": matches,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def artifact_slice_tool(artifact_path: str, offset: int = 1, limit: int = 100) -> str:
    try:
        path = _resolve_artifact_path(artifact_path)
        offset = max(1, int(offset or 1))
        requested_limit = int(limit or 100)
        limit = max(1, min(requested_limit, DEFAULT_MAX_SLICE_LINES))
        lines = _read_lines(path)
        start = min(len(lines), offset - 1)
        end = min(len(lines), start + limit)
        content = "".join(f"{i + 1}|{lines[i]}" for i in range(start, end))
        return json.dumps({
            "artifact_path": str(path.relative_to(_workspace_dir())),
            "content": content,
            "total_lines": len(lines),
            "offset": offset,
            "limit": limit,
            "requested_limit": requested_limit,
            "limit_capped": requested_limit != limit,
            "has_more_after": end < len(lines),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


registry.register(
    name="artifact_inspect",
    description="查看大工具输出 artifact 的规模、类型和少量 head/tail 样本；避免整份读回上下文。",
    parameters={
        "type": "object",
        "properties": {
            "artifact_path": {"type": "string", "description": "artifact 文件路径，通常来自 <persisted-output>"},
            "sample_lines": {"type": "integer", "description": "head/tail 样本行数，默认 20，最大 100", "default": 20},
        },
        "required": ["artifact_path"],
    },
    handler=artifact_inspect_tool,
)

registry.register(
    name="artifact_search",
    description="在大工具输出 artifact 中按正则检索，只返回有限命中及上下文片段。",
    parameters={
        "type": "object",
        "properties": {
            "artifact_path": {"type": "string", "description": "artifact 文件路径"},
            "query": {"type": "string", "description": "正则表达式关键词"},
            "context_lines": {"type": "integer", "description": "每个命中前后文行数，默认 3，最大 20", "default": 3},
            "limit": {"type": "integer", "description": "最多返回命中数，默认 20，最大 100", "default": 20},
            "ignore_case": {"type": "boolean", "description": "是否忽略大小写，默认 true", "default": True},
        },
        "required": ["artifact_path", "query"],
    },
    handler=artifact_search_tool,
)

registry.register(
    name="artifact_slice",
    description="安全读取大工具输出 artifact 的局部行范围；limit 有硬上限，避免整份回灌上下文。",
    parameters={
        "type": "object",
        "properties": {
            "artifact_path": {"type": "string", "description": "artifact 文件路径"},
            "offset": {"type": "integer", "description": "起始行号，默认 1", "default": 1},
            "limit": {"type": "integer", "description": "读取行数，默认 100，最大 500", "default": 100},
        },
        "required": ["artifact_path"],
    },
    handler=artifact_slice_tool,
)
