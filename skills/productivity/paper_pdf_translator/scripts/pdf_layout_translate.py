#!/usr/bin/env python3
"""Local layout-preserving PDF translation helper.

This script deliberately does not call translation APIs. It extracts PDF text
regions into JSONL for the agent/user to translate, then writes translated text
back into the same page boxes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


FitRect = Tuple[float, float, float, float]


def require_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except Exception as exc:  # pragma: no cover - depends on local env
        raise SystemExit(
            "Missing dependency: PyMuPDF. Install it with:\n"
            "  python3 -m pip install --user pymupdf\n"
            "This helper uses PyMuPDF for coordinate-aware PDF editing."
        ) from exc


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_text(value: str) -> str:
    value = value.replace("\u00ad", "")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def mostly_symbolic(text: str) -> bool:
    chars = [ch for ch in text if not ch.isspace()]
    if len(chars) < 4:
        return True
    letters = sum(ch.isalpha() for ch in chars)
    digits = sum(ch.isdigit() for ch in chars)
    symbols = len(chars) - letters - digits
    return letters == 0 or symbols / max(len(chars), 1) > 0.55


def merge_bbox(boxes: Sequence[Sequence[float]]) -> FitRect:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def median_font_size(lines: Sequence[Dict[str, Any]]) -> float:
    sizes: List[float] = []
    for line in lines:
        for span in line.get("spans", []):
            text = span.get("text", "")
            if text.strip():
                sizes.append(float(span.get("size", 9.0)))
    if not sizes:
        return 9.0
    return float(statistics.median(sizes))


def line_record(line: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
    if not spans:
        return None
    text = clean_text("".join(span.get("text", "") for span in spans))
    if not text:
        return None
    bbox = tuple(float(v) for v in line.get("bbox", merge_bbox([span["bbox"] for span in spans])))
    sizes = [float(span.get("size", 9.0)) for span in spans]
    return {
        "text": text,
        "bbox": bbox,
        "font_size": float(statistics.median(sizes)) if sizes else 9.0,
    }


def collect_lines(blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            record = line_record(line)
            if record:
                lines.append(record)
    return sorted(lines, key=lambda r: (round(r["bbox"][1], 1), r["bbox"][0]))


def horizontal_overlap_ratio(a: FitRect, b: FitRect) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    return overlap / max(1.0, min(a[2] - a[0], b[2] - b[0]))


def should_merge_line(group: List[Dict[str, Any]], line: Dict[str, Any]) -> bool:
    prev = group[-1]
    prev_box = prev["bbox"]
    line_box = line["bbox"]
    prev_height = max(1.0, prev_box[3] - prev_box[1])
    vertical_gap = line_box[1] - prev_box[3]
    same_column = horizontal_overlap_ratio(prev_box, line_box) >= 0.45
    left_aligned = abs(line_box[0] - prev_box[0]) <= max(10.0, prev_height * 1.5)
    close_vertical = -prev_height * 0.35 <= vertical_gap <= prev_height * 1.55
    return close_vertical and (same_column or left_aligned)


def group_lines(lines: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    groups: List[List[Dict[str, Any]]] = []
    for line in lines:
        if groups and should_merge_line(groups[-1], line):
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def group_text(group: Sequence[Dict[str, Any]]) -> str:
    return clean_text("\n".join(line["text"] for line in group))


def group_font_size(group: Sequence[Dict[str, Any]]) -> float:
    return float(statistics.median(float(line["font_size"]) for line in group))


def iter_text_records(
    pdf_path: Path,
    target: str,
    min_chars: int,
    skip_math_like: bool,
) -> Iterable[Dict[str, Any]]:
    fitz = require_fitz()
    doc = fitz.open(str(pdf_path))
    try:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            data = page.get_text("dict", sort=True)
            lines = collect_lines(data.get("blocks", []))
            for group_index, group in enumerate(group_lines(lines)):
                text = group_text(group)
                if len(text.replace("\n", "").strip()) < min_chars:
                    continue
                if skip_math_like and mostly_symbolic(text):
                    continue
                bbox = merge_bbox([line["bbox"] for line in group])
                yield {
                    "id": f"p{page_index + 1:04d}_g{group_index:04d}",
                    "page": page_index + 1,
                    "bbox": [round(v, 3) for v in bbox],
                    "font_size": round(group_font_size(group), 2),
                    "target": target,
                    "text": text,
                    "translation": "",
                }
    finally:
        doc.close()


def write_jsonl_chunks(records: List[Dict[str, Any]], out_dir: Path, max_records: int) -> List[Path]:
    chunk_dir = out_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for start in range(0, len(records), max_records):
        path = chunk_dir / f"chunk_{len(paths) + 1:04d}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for record in records[start : start + max_records]:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        paths.append(path)
    return paths


def write_translation_prompt(out_dir: Path, target: str) -> None:
    prompt = f"""Translate each JSONL record into {target}.

Rules:
- Preserve every field and every record order.
- Do not change id, page, bbox, font_size, target, or text.
- Fill only the translation field.
- Keep formulas, citation markers, variable names, and units intact.
- Make translated text concise enough to fit the original PDF box.
- Return valid JSONL, one object per line, with no Markdown fences.
"""
    (out_dir / "translation_prompt.md").write_text(prompt, encoding="utf-8")


def cmd_extract(args: argparse.Namespace) -> None:
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"Input PDF not found: {pdf_path}")
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    records = list(
        iter_text_records(
            pdf_path=pdf_path,
            target=args.target,
            min_chars=args.min_chars,
            skip_math_like=not args.keep_math_like,
        )
    )
    chunk_paths = write_jsonl_chunks(records, out_dir, args.max_records_per_file)
    write_translation_prompt(out_dir, args.target)

    fitz = require_fitz()
    doc = fitz.open(str(pdf_path))
    try:
        manifest = {
            "source_pdf": str(pdf_path),
            "source_sha256": sha256_file(pdf_path),
            "target": args.target,
            "page_count": doc.page_count,
            "record_count": len(records),
            "chunk_files": [str(p.relative_to(out_dir)) for p in chunk_paths],
            "notes": [
                "Translate chunk files and keep JSONL structure unchanged.",
                "Use the apply command after filling translation fields.",
            ],
        }
    finally:
        doc.close()
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def iter_jsonl_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for item in sorted(path.glob("*.jsonl")):
        if item.is_file():
            yield item


def load_translations(path: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for jsonl in iter_jsonl_files(path):
        with jsonl.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"Invalid JSON in {jsonl}:{line_no}: {exc}") from exc
                rid = record.get("id")
                if not rid:
                    raise SystemExit(f"Missing id in {jsonl}:{line_no}")
                records[str(rid)] = record
    return records


def builtin_font_for_target(target: str) -> str:
    lower = target.lower()
    if lower.startswith("zh") or "chinese" in lower:
        return "china-s"
    if lower.startswith("ja") or "japanese" in lower:
        return "japan"
    if lower.startswith("ko") or "korean" in lower:
        return "korea"
    return "helv"


def padded_rect(fitz: Any, bbox: Sequence[float], pad: float, page_rect: Any) -> Any:
    rect = fitz.Rect(*bbox)
    rect.x0 = max(page_rect.x0, rect.x0 - pad)
    rect.y0 = max(page_rect.y0, rect.y0 - pad)
    rect.x1 = min(page_rect.x1, rect.x1 + pad)
    rect.y1 = min(page_rect.y1, rect.y1 + pad)
    return rect


def redact_regions(fitz: Any, page: Any, rects: Sequence[Any]) -> None:
    for rect in rects:
        page.add_redact_annot(rect, fill=(1, 1, 1))
    kwargs = {}
    for attr, value_name in [
        ("images", "PDF_REDACT_IMAGE_NONE"),
        ("graphics", "PDF_REDACT_LINE_ART_NONE"),
        ("text", "PDF_REDACT_TEXT_REMOVE"),
    ]:
        if hasattr(fitz, value_name):
            kwargs[attr] = getattr(fitz, value_name)
    try:
        page.apply_redactions(**kwargs)
    except TypeError:
        page.apply_redactions()


def draw_cover(page: Any, rects: Sequence[Any]) -> None:
    for rect in rects:
        page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)


def insert_textbox_fit(
    page: Any,
    rect: Any,
    text: str,
    fontname: str,
    fontfile: Optional[str],
    base_size: float,
    min_size: float,
    font_scale: float,
    lineheight: float,
) -> Tuple[bool, float, float]:
    size = max(min_size, base_size * font_scale)
    last_rc = -math.inf
    while size >= min_size - 0.01:
        shape = page.new_shape()
        kwargs = {
            "fontname": fontname,
            "fontsize": size,
            "color": (0, 0, 0),
            "align": 0,
        }
        if fontfile:
            kwargs["fontfile"] = fontfile
        try:
            rc = shape.insert_textbox(rect, text, lineheight=lineheight, **kwargs)
        except TypeError:
            rc = shape.insert_textbox(rect, text, **kwargs)
        last_rc = float(rc)
        if rc >= 0:
            shape.commit(overlay=True)
            return True, size, last_rc
        size -= 0.5
    return False, max(min_size, size), last_rc


def cmd_apply(args: argparse.Namespace) -> None:
    fitz = require_fitz()
    pdf_path = Path(args.pdf).expanduser().resolve()
    translations_path = Path(args.translations).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not pdf_path.is_file():
        raise SystemExit(f"Input PDF not found: {pdf_path}")
    if not translations_path.exists():
        raise SystemExit(f"Translations path not found: {translations_path}")

    records = load_translations(translations_path)
    if not records:
        raise SystemExit(f"No JSONL translation records found in: {translations_path}")

    fontname = args.fontname or builtin_font_for_target(args.target)
    fontfile = str(Path(args.fontfile).expanduser().resolve()) if args.fontfile else None
    if fontfile and not Path(fontfile).is_file():
        raise SystemExit(f"Font file not found: {fontfile}")

    doc = fitz.open(str(pdf_path))
    failures: List[Dict[str, Any]] = []
    applied = 0
    try:
        by_page: Dict[int, List[Dict[str, Any]]] = {}
        for record in records.values():
            text = str(record.get("translation") or "").strip()
            if not text:
                if args.require_all:
                    raise SystemExit(f"Missing translation for record {record.get('id')}")
                continue
            page_num = int(record["page"])
            if page_num < 1 or page_num > doc.page_count:
                raise SystemExit(f"Record {record.get('id')} has invalid page {page_num}")
            by_page.setdefault(page_num - 1, []).append(record)

        for page_index, page_records in sorted(by_page.items()):
            page = doc[page_index]
            rects = [padded_rect(fitz, r["bbox"], args.padding, page.rect) for r in page_records]
            if args.erase_mode == "redact":
                redact_regions(fitz, page, rects)
            elif args.erase_mode == "cover":
                draw_cover(page, rects)

            for record, rect in zip(page_records, rects):
                ok, used_size, rc = insert_textbox_fit(
                    page=page,
                    rect=rect,
                    text=str(record.get("translation") or "").strip(),
                    fontname=fontname,
                    fontfile=fontfile,
                    base_size=float(record.get("font_size") or args.default_font_size),
                    min_size=args.min_font_size,
                    font_scale=args.font_scale,
                    lineheight=args.lineheight,
                )
                if ok:
                    applied += 1
                else:
                    failures.append(
                        {
                            "id": record.get("id"),
                            "page": record.get("page"),
                            "bbox": record.get("bbox"),
                            "font_size_tried": used_size,
                            "fit_result": rc,
                            "translation": record.get("translation"),
                        }
                    )

        output.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    summary = {
        "output": str(output),
        "applied_records": applied,
        "failed_records": len(failures),
        "fontname": fontname,
        "fontfile": fontfile,
        "erase_mode": args.erase_mode,
    }
    if failures:
        fail_path = output.with_suffix(output.suffix + ".fit_failures.json")
        fail_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["failure_report"] = str(fail_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures and args.fail_on_overflow:
        raise SystemExit("Some translated text did not fit. See failure_report.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract and reapply translated PDF text without external translation APIs.")
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="extract positioned text records into JSONL chunks")
    extract.add_argument("pdf", help="source PDF")
    extract.add_argument("--out-dir", required=True, help="work directory for manifest and chunks")
    extract.add_argument("--target", default="zh-CN", help="target language tag")
    extract.add_argument("--min-chars", type=int, default=4, help="skip extracted blocks shorter than this")
    extract.add_argument("--max-records-per-file", type=int, default=80, help="records per JSONL chunk")
    extract.add_argument("--keep-math-like", action="store_true", help="do not skip mostly symbolic/math-like blocks")
    extract.set_defaults(func=cmd_extract)

    apply = sub.add_parser("apply", help="write translated JSONL records back into a PDF")
    apply.add_argument("pdf", help="source PDF")
    apply.add_argument("translations", help="JSONL file or directory with translated records")
    apply.add_argument("--output", required=True, help="output translated PDF")
    apply.add_argument("--target", default="zh-CN", help="target language tag, used for default font selection")
    apply.add_argument("--fontname", default=None, help="PyMuPDF font name; default picks china-s/japan/korea/helv by target")
    apply.add_argument("--fontfile", default=None, help="optional font file to embed")
    apply.add_argument("--erase-mode", choices=["redact", "cover", "none"], default="redact", help="how to hide/remove source text")
    apply.add_argument("--padding", type=float, default=0.7, help="points added around each text box")
    apply.add_argument("--font-scale", type=float, default=0.92, help="scale original font size before fitting")
    apply.add_argument("--default-font-size", type=float, default=9.0, help="fallback font size")
    apply.add_argument("--min-font-size", type=float, default=5.5, help="minimum font size when fitting")
    apply.add_argument("--lineheight", type=float, default=1.05, help="line-height multiplier where supported")
    apply.add_argument("--require-all", action="store_true", help="fail if any record has empty translation")
    apply.add_argument("--fail-on-overflow", action="store_true", help="fail when translated text cannot fit a box")
    apply.set_defaults(func=cmd_apply)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
