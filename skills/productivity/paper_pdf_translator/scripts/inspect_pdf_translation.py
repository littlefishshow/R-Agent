#!/usr/bin/env python3
"""Inspect translated PDF output for basic layout-preservation signals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def require_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except Exception as exc:  # pragma: no cover - depends on local env
        raise SystemExit(
            "Missing dependency: PyMuPDF. Install it with:\n"
            "  python3 -m pip install --user pymupdf"
        ) from exc


def summarize_pdf(path: Path, max_pages: int) -> Dict[str, Any]:
    fitz = require_fitz()
    doc = fitz.open(str(path))
    try:
        pages: List[Dict[str, Any]] = []
        total_chars = 0
        total_images = 0
        inspected = min(doc.page_count, max_pages)
        for index in range(inspected):
            page = doc[index]
            text = page.get_text("text") or ""
            image_count = len(page.get_images(full=True))
            total_chars += len(text.strip())
            total_images += image_count
            pages.append(
                {
                    "page": index + 1,
                    "width": round(float(page.rect.width), 2),
                    "height": round(float(page.rect.height), 2),
                    "text_chars": len(text.strip()),
                    "image_count": image_count,
                }
            )
        return {
            "path": str(path),
            "page_count": doc.page_count,
            "inspected_pages": inspected,
            "text_chars_in_inspected_pages": total_chars,
            "images_in_inspected_pages": total_images,
            "pages": pages,
        }
    finally:
        doc.close()


def render_samples(path: Path, out_dir: Path, pages: Sequence[int], zoom: float) -> List[str]:
    fitz = require_fitz()
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(path))
    written: List[str] = []
    try:
        matrix = fitz.Matrix(zoom, zoom)
        for page_num in pages:
            if page_num < 1 or page_num > doc.page_count:
                continue
            pix = doc[page_num - 1].get_pixmap(matrix=matrix, alpha=False)
            out = out_dir / f"{path.stem}.p{page_num:04d}.png"
            pix.save(str(out))
            written.append(str(out))
    finally:
        doc.close()
    return written


def cmd_inspect(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve() if args.output else None
    if not source.is_file():
        raise SystemExit(f"Source PDF not found: {source}")
    if output and not output.is_file():
        raise SystemExit(f"Output PDF not found: {output}")

    result: Dict[str, Any] = {"source": summarize_pdf(source, args.max_pages)}
    warnings: List[str] = []
    if output:
        result["output"] = summarize_pdf(output, args.max_pages)
        if result["source"]["page_count"] != result["output"]["page_count"]:
            warnings.append("Page count differs between source and output.")
        if result["output"]["text_chars_in_inspected_pages"] < args.min_output_chars:
            warnings.append("Output has little extractable text; it may not be editable/selectable.")
        src_images = result["source"]["images_in_inspected_pages"]
        out_images = result["output"]["images_in_inspected_pages"]
        if src_images and out_images < src_images:
            warnings.append("Output reports fewer images on sampled pages; check figure preservation.")

    if args.render_dir:
        pages = [int(p) for p in args.render_pages.split(",") if p.strip()]
        render_dir = Path(args.render_dir).expanduser().resolve()
        result["rendered_source_pages"] = render_samples(source, render_dir, pages, args.zoom)
        if output:
            result["rendered_output_pages"] = render_samples(output, render_dir, pages, args.zoom)

    result["warnings"] = warnings
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if warnings and args.fail_on_warning else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect PDF translation output for basic editability/layout signals.")
    parser.add_argument("source", help="source PDF")
    parser.add_argument("output", nargs="?", help="translated output PDF")
    parser.add_argument("--max-pages", type=int, default=5, help="sample this many leading pages")
    parser.add_argument("--min-output-chars", type=int, default=10, help="minimum extracted chars expected in output sample")
    parser.add_argument("--render-dir", default=None, help="optional directory for PNG page samples")
    parser.add_argument("--render-pages", default="1", help="comma-separated pages to render when --render-dir is set")
    parser.add_argument("--zoom", type=float, default=1.5, help="render zoom")
    parser.add_argument("--fail-on-warning", action="store_true", help="exit non-zero when warnings are produced")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return cmd_inspect(args)


if __name__ == "__main__":
    raise SystemExit(main())
