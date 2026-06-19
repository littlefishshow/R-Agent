"""Paper locating helper functions for the read_paper skill scripts.

The tool intentionally keeps matching simple and deterministic:
- default input root: outputs/papers
- default output root: outputs/papers_output
- recursively search PDFs and common text/markdown files
- mirror the paper's relative subdirectory under the output root
"""

from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PAPERS_DIR = "outputs/papers"
DEFAULT_OUTPUT_DIR = "outputs/papers_output"
SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".markdown"}


def _workspace_root() -> Path:
    """Return repository/workspace root from this skill script location."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "tools").is_dir() and (parent / "skills").is_dir():
            return parent
    # Fallback for the canonical path: skills/productivity/read_paper/scripts/*.py
    return here.parents[3]


def _is_inside_workspace(path: Path) -> bool:
    root = _workspace_root().resolve()
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _date_variants(query: str) -> List[str]:
    variants: List[str] = []
    for m in re.finditer(r"(20\d{2})[-_.年/]?(\d{1,2})[-_.月/]?(\d{1,2})", query):
        y, mo, d = m.groups()
        mo = mo.zfill(2)
        d = d.zfill(2)
        variants.extend([f"{y}-{mo}-{d}", f"{y}_{mo}_{d}", f"{y}.{mo}.{d}", f"{y}{mo}{d}"])
    return list(dict.fromkeys(variants))


def _safe_stem(stem: str, max_len: int = 150) -> str:
    stem = re.sub(r"[\\/:*?\"<>|]+", "_", stem).strip(" ._-")
    stem = re.sub(r"\s+", "_", stem)
    if not stem:
        stem = "paper"
    return stem[:max_len].rstrip(" ._-")


def _iter_papers(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted(
        [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS],
        key=lambda p: str(p).lower(),
    )


def _score_path(path: Path, query: str, category: str = "") -> int:
    if not query and not category:
        return 1
    path_str = str(path).lower()
    name = path.name.lower()
    norm_path = _norm(str(path))
    norm_name = _norm(path.stem)
    score = 0

    q = query.strip()
    q_lower = q.lower()
    q_norm = _norm(q)

    if q:
        if q_lower in path_str:
            score += 100
        if q_lower in name:
            score += 120
        if q_norm and q_norm in norm_name:
            score += 90
        if q_norm and q_norm in norm_path:
            score += 70

        for variant in _date_variants(q):
            if variant.lower() in path_str or _norm(variant) in norm_path:
                score += 150

        # Simple token scoring; no fuzzy/semantic matching on purpose.
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", q_lower) if len(t) >= 2]
        for t in tokens:
            if t in path_str:
                score += 15
            if t in name:
                score += 25

    if category.strip():
        c_norm = _norm(category)
        parts_norm = [_norm(part) for part in path.parts]
        if c_norm in parts_norm:
            score += 120
        elif c_norm and c_norm in norm_path:
            score += 70

    return score


def locate_paper_tool(
    query: str = "",
    category: str = "",
    papers_dir: str = DEFAULT_PAPERS_DIR,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    limit: int = 10,
) -> str:
    """Locate a paper under the default papers directory and compute output paths."""
    root = (Path(papers_dir) if papers_dir else Path(DEFAULT_PAPERS_DIR)).expanduser()
    out_root = (Path(output_dir) if output_dir else Path(DEFAULT_OUTPUT_DIR)).expanduser()

    if root.is_absolute() or out_root.is_absolute():
        # Keep this tool workspace-local and side-effect-light.
        return json.dumps({
            "success": False,
            "error": "Only workspace-relative papers_dir and output_dir are supported.",
        }, ensure_ascii=False)

    root_abs = (_workspace_root() / root).resolve()
    out_abs = (_workspace_root() / out_root).resolve()
    if not _is_inside_workspace(root_abs) or not _is_inside_workspace(out_abs):
        return json.dumps({
            "success": False,
            "error": "Resolved paths must stay inside the workspace.",
        }, ensure_ascii=False)

    files = _iter_papers(root_abs)
    if not files:
        return json.dumps({
            "success": True,
            "status": "no_files",
            "message": f"No supported paper files found under {root}.",
            "papers_dir": str(root),
            "output_dir": str(out_root),
            "candidates": [],
        }, ensure_ascii=False)

    scored: List[Dict[str, Any]] = []
    for p in files:
        score = _score_path(p, query, category)
        if query or category:
            if score <= 0:
                continue
        rel = p.relative_to(root_abs)
        out_rel = rel.with_name(_safe_stem(rel.stem) + "_阅读笔记.md")
        scored.append({
            "paper_path": str(root / rel),
            "relative_path": str(rel),
            "category_dir": str(rel.parent) if str(rel.parent) != "." else "",
            "output_path": str(out_root / out_rel),
            "score": score,
            "modified_time": p.stat().st_mtime,
            "size_bytes": p.stat().st_size,
        })

    scored.sort(key=lambda x: (-int(x["score"]), str(x["paper_path"]).lower()))
    limit = max(1, min(int(limit or 10), 50))
    candidates = scored[:limit]

    if not candidates:
        return json.dumps({
            "success": True,
            "status": "no_match",
            "message": "No paper matched the query/category under the papers directory.",
            "query": query,
            "category": category,
            "papers_dir": str(root),
            "output_dir": str(out_root),
            "total_files": len(files),
            "candidates": [],
        }, ensure_ascii=False)

    top_score = candidates[0]["score"]
    tied = [c for c in candidates if c["score"] == top_score]
    status = "unique" if len(tied) == 1 else "ambiguous"

    return json.dumps({
        "success": True,
        "status": status,
        "query": query,
        "category": category,
        "papers_dir": str(root),
        "output_dir": str(out_root),
        "total_files": len(files),
        "selected": candidates[0] if status == "unique" else None,
        "candidates": candidates,
        "note": "If status is ambiguous, ask the user to choose one candidate unless context makes the choice obvious.",
    }, ensure_ascii=False)




def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Locate a paper and compute read_paper Markdown output path.")
    parser.add_argument("query", nargs="?", default="", help="Paper clue: date, title keyword, filename fragment, or arXiv ID.")
    parser.add_argument("--category", default="", help="Optional category directory such as agentic_rl.")
    parser.add_argument("--papers-dir", default=DEFAULT_PAPERS_DIR, help="Workspace-relative paper root.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Workspace-relative output root.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum candidates to return.")
    args = parser.parse_args()
    print(locate_paper_tool(
        query=args.query,
        category=args.category,
        papers_dir=args.papers_dir,
        output_dir=args.output_dir,
        limit=args.limit,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
