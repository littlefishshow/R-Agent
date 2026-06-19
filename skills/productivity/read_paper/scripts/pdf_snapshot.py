import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE = Path.cwd().resolve()
DEFAULT_PAPERS_ROOT = Path("outputs/papers")
DEFAULT_OUTPUT_ROOT = Path("outputs/papers_output")


def _resolve_workspace_path(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = WORKSPACE / p
    p = p.resolve()
    try:
        p.relative_to(WORKSPACE)
    except ValueError:
        raise ValueError(f"Path must be inside workspace: {path}")
    return p


def _safe_slug(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", s).strip("._-")
    return (s or "snapshot")[:max_len]


def _page_list(doc, pages: Optional[List[int]]) -> List[int]:
    if not pages:
        return list(range(doc.page_count))
    out = []
    for p in pages:
        if p < 1 or p > doc.page_count:
            raise ValueError(f"Page out of range: {p}; PDF has {doc.page_count} pages")
        out.append(p - 1)
    return out


def _rect_from_crop(fitz, page, crop: Dict[str, Any]):
    units = crop.get("units", "points")
    x0 = float(crop["x0"])
    y0 = float(crop["y0"])
    x1 = float(crop["x1"])
    y1 = float(crop["y1"])
    if units == "normalized":
        w, h = page.rect.width, page.rect.height
        x0, x1 = x0 * w, x1 * w
        y0, y1 = y0 * h, y1 * h
    elif units != "points":
        raise ValueError("crop.units must be 'points' or 'normalized'")
    rect = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return rect & page.rect


def _render_rect(page, rect, output_path: Path, dpi: int):
    zoom = dpi / 72.0
    mat = page.parent.__class__.__module__  # dummy to keep linters quiet
    import fitz
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(output_path))
    return {"width": pix.width, "height": pix.height}


def _caption_blocks(page, include_tables: bool = True):
    pattern = r"^\s*(Figure|Fig\.?|Table)\s*\d+[:.\s]"
    if not include_tables:
        pattern = r"^\s*(Figure|Fig\.?)\s*\d+[:.\s]"
    rx = re.compile(pattern, re.IGNORECASE)
    d = page.get_text("dict")
    blocks = []
    for b in d.get("blocks", []):
        if "lines" not in b:
            continue
        parts = []
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
        text = " ".join("".join(parts).split())
        if rx.search(text):
            blocks.append({"bbox": b.get("bbox"), "text": text})
    return blocks


def _caption_kind(text: str) -> str:
    return "table" if re.search(r"^\s*Table\b", text, re.I) else "figure"



def _pixmap_content_bbox(page, rect, dpi: int = 96, white_threshold: int = 248):
    """Detect non-white pixel bounding box inside rect and return it in PDF points."""
    try:
        import fitz
        import numpy as np
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), clip=rect, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n >= 3:
            rgb = arr[:, :, :3]
            mask = (rgb < white_threshold).any(axis=2)
        else:
            mask = arr[:, :, 0] < white_threshold
        row_counts = mask.sum(axis=1)
        col_counts = mask.sum(axis=0)
        rows = np.where(row_counts > max(3, pix.width * 0.002))[0]
        cols = np.where(col_counts > max(3, pix.height * 0.002))[0]
        if len(rows) == 0 or len(cols) == 0:
            return None
        x0, x1 = cols[0], cols[-1] + 1
        y0, y1 = rows[0], rows[-1] + 1
        sx = rect.width / pix.width
        sy = rect.height / pix.height
        return page.rect.__class__(rect.x0 + x0 * sx, rect.y0 + y0 * sy, rect.x0 + x1 * sx, rect.y0 + y1 * sy) & page.rect
    except Exception:
        return None

def _text_blocks(page):
    d = page.get_text("dict")
    blocks = []
    for b in d.get("blocks", []):
        if "lines" not in b or not b.get("bbox"):
            continue
        parts = []
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
        txt = " ".join("".join(parts).split())
        if txt:
            blocks.append({"bbox": tuple(map(float, b["bbox"])), "text": txt})
    return blocks



def _content_y_segments(page, rect, dpi: int = 96, white_threshold: int = 248, min_dark_ratio: float = 0.003):
    """Return merged vertical non-white content segments inside rect in PDF points.

    The smart cropper uses row projection instead of a single full-window bbox so
    that text blocks far above/below a caption (e.g. abstract paragraphs) are not
    accidentally unioned with the target figure/table.
    """
    try:
        import fitz
        import numpy as np
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), clip=rect, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        rgb = arr[:, :, :3] if pix.n >= 3 else arr[:, :, :1]
        mask = (rgb < white_threshold).any(axis=2)
        row_counts = mask.sum(axis=1)
        threshold = max(4, int(pix.width * min_dark_ratio))
        rows = row_counts > threshold
        segments = []
        i = 0
        while i < len(rows):
            if not rows[i]:
                i += 1
                continue
            j = i + 1
            while j < len(rows) and rows[j]:
                j += 1
            y0 = rect.y0 + i * rect.height / pix.height
            y1 = rect.y0 + j * rect.height / pix.height
            if y1 - y0 >= 1.0:
                segments.append([y0, y1])
            i = j
        # Merge tiny gaps inside diagrams/tables; keep larger whitespace as separators.
        merged = []
        for seg in segments:
            if not merged or seg[0] - merged[-1][1] > 10.0:
                merged.append(seg)
            else:
                merged[-1][1] = seg[1]
        return merged
    except Exception:
        return []


def _segment_intersects(seg, y0, y1, tol: float = 2.0) -> bool:
    return max(seg[0], y0 - tol) <= min(seg[1], y1 + tol)


def _pick_caption_segment(segments, cy0, cy1):
    for idx, seg in enumerate(segments):
        if _segment_intersects(seg, cy0, cy1):
            return idx
    if not segments:
        return None
    mid = (cy0 + cy1) / 2.0
    return min(range(len(segments)), key=lambda i: min(abs(segments[i][0] - mid), abs(segments[i][1] - mid)))


def _candidate_rect_by_direction(fitz, page, cap_bbox, wx0, wx1, direction: str, margin: float = 8.0):
    """Build a local crop around a caption.

    direction='above': target visual object is above a below-caption (typical Figure).
    direction='below': target visual object is below an above-caption (typical Table).
    The selection walks from the caption segment toward the target and stops at a
    large whitespace gap, preventing abstract/body paragraphs from being pulled in.
    """
    cx0, cy0, cx1, cy1 = map(float, cap_bbox)
    page_h = page.rect.height
    max_window = min(page_h * 0.62, 460.0)
    if direction == "above":
        probe = fitz.Rect(wx0, max(0.0, cy0 - max_window), wx1, min(page_h, cy1 + margin)) & page.rect
    else:
        probe = fitz.Rect(wx0, max(0.0, cy0 - margin), wx1, min(page_h, cy1 + max_window)) & page.rect
    segments = _content_y_segments(page, probe)
    cap_idx = _pick_caption_segment(segments, cy0, cy1)
    if cap_idx is None:
        return probe

    selected = [cap_idx]
    max_gap = 26.0
    if direction == "above":
        i = cap_idx - 1
        last_top = segments[cap_idx][0]
        while i >= 0:
            gap = last_top - segments[i][1]
            if gap > max_gap:
                break
            selected.append(i)
            last_top = segments[i][0]
            i -= 1
    else:
        i = cap_idx + 1
        last_bottom = segments[cap_idx][1]
        while i < len(segments):
            gap = segments[i][0] - last_bottom
            if gap > max_gap:
                break
            selected.append(i)
            last_bottom = segments[i][1]
            i += 1

    y0 = max(0.0, min(segments[i][0] for i in selected) - margin)
    y1 = min(page_h, max(segments[i][1] for i in selected) + margin)
    rect = fitz.Rect(wx0, y0, wx1, y1) & page.rect
    # Refine x/y to actual non-white content within the selected local band, then re-add caption.
    bbox = _pixmap_content_bbox(page, rect, dpi=120)
    cap_rect = fitz.Rect(cx0, cy0, cx1, cy1)
    if bbox:
        rect = (bbox | cap_rect) + (-margin, -margin, margin, margin)
        rect = rect & page.rect
    return rect


def _same_row(a, b, tol: float = 12.0) -> bool:
    ay0, ay1 = float(a["bbox"][1]), float(a["bbox"][3])
    by0, by1 = float(b["bbox"][1]), float(b["bbox"][3])
    return max(ay0, by0) <= min(ay1, by1) + tol


def _caption_window_x(page, cap, all_caps, margin: float = 8.0):
    """Split side-by-side captions into columns; otherwise use full page width."""
    x0, y0, x1, y1 = map(float, cap["bbox"])
    w = page.rect.width
    same = sorted([c for c in all_caps if _same_row(c, cap)], key=lambda c: float(c["bbox"][0]))
    if len(same) <= 1 or (x1 - x0) > w * 0.45:
        # Use the caption text column instead of the full physical page.  This
        # avoids marginal artifacts such as arXiv side stamps from bridging
        # abstract/body text with the target figure in row-projection cropping.
        return max(0.0, x0 - margin), min(w, x1 + margin)
    idx = same.index(cap)
    left = 0.0
    right = w
    if idx > 0:
        left = (float(same[idx - 1]["bbox"][2]) + x0) / 2.0
    if idx < len(same) - 1:
        right = (x1 + float(same[idx + 1]["bbox"][0])) / 2.0
    return max(0.0, left - margin), min(w, right + margin)


def _rect_area(rect) -> float:
    return max(0.0, float(rect.width)) * max(0.0, float(rect.height))


def _smart_caption_rect(fitz, page, cap, all_caps=None, margin: float = 8.0, content_refine: bool = True):
    """Estimate tight Figure/Table crop from caption anchor + row-projection content bands.

    Fixes two common caption-crop failures:
    1. A full-window pixel bbox unions the target figure with abstract/body text above it.
    2. Caption direction is guessed backwards, producing caption + following paragraph
       while missing the visual object above the caption.
    """
    all_caps = all_caps or [cap]
    x0, y0, x1, y1 = map(float, cap["bbox"])
    wx0, wx1 = _caption_window_x(page, cap, all_caps, margin=margin)
    kind = _caption_kind(cap.get("text", ""))

    # Default conventions: figures usually have captions below; tables often captions above.
    preferred = "below" if kind == "table" else "above"
    alternate = "above" if preferred == "below" else "below"
    pref_rect = _candidate_rect_by_direction(fitz, page, (x0, y0, x1, y1), wx0, wx1, preferred, margin=margin)

    # Direction sanity check: if preferred side contains almost no object besides caption,
    # try the other side and choose it when it has much more content area.
    alt_rect = _candidate_rect_by_direction(fitz, page, (x0, y0, x1, y1), wx0, wx1, alternate, margin=margin)
    cap_h = max(1.0, y1 - y0)
    pref_extra_h = max(0.0, pref_rect.height - cap_h - 2 * margin)
    alt_extra_h = max(0.0, alt_rect.height - cap_h - 2 * margin)
    if pref_extra_h < cap_h * 1.2 and alt_extra_h > pref_extra_h * 2.0:
        return alt_rect
    # For figures, strongly avoid returning caption + below paragraph when above has content.
    if kind == "figure" and pref_rect.y0 <= y0 and pref_extra_h >= cap_h * 1.2:
        return pref_rect
    if _rect_area(alt_rect) > _rect_area(pref_rect) * 2.8 and pref_extra_h < 40:
        return alt_rect
    return pref_rect


def pdf_snapshot_tool(
    pdf_path: str,
    output_dir: str = "",
    pages: list = None,
    crops: list = None,
    mode: str = "auto",
    dpi: int = 200,
    include_tables: bool = True,
    caption_above_ratio: float = 0.48,
    caption_below_ratio: float = 0.16,
    max_auto_per_page: int = 8,
    smart_crop: bool = True,
    content_refine: bool = True,
    crop_margin: float = 8.0,
) -> str:
    """Render PDF pages/regions to PNG screenshots for paper notes."""
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        return json.dumps({"success": False, "error": f"PyMuPDF/fitz is not available: {e}"}, ensure_ascii=False)

    try:
        pdf = _resolve_workspace_path(pdf_path)
        if not pdf.exists():
            raise FileNotFoundError(str(pdf))
        if output_dir:
            out_dir = _resolve_workspace_path(output_dir)
        else:
            # Mirror outputs/papers/<category>/paper.pdf to
            # outputs/papers_output/<category>/assets/<paper_stem>/.
            papers_root = (WORKSPACE / DEFAULT_PAPERS_ROOT).resolve()
            output_root = (WORKSPACE / DEFAULT_OUTPUT_ROOT).resolve()
            try:
                rel_pdf = pdf.relative_to(papers_root)
                rel_dir = rel_pdf.parent if str(rel_pdf.parent) != "." else Path("")
            except ValueError:
                rel_dir = Path("")
            out_dir = (output_root / rel_dir / "assets" / pdf.stem).resolve()
        try:
            out_dir.relative_to(WORKSPACE)
        except ValueError:
            raise ValueError("output_dir must be inside workspace")

        if dpi < 72 or dpi > 600:
            raise ValueError("dpi must be between 72 and 600")
        if mode not in {"auto", "smart", "pages", "crops"}:
            raise ValueError("mode must be one of: auto, smart, pages, crops")

        doc = fitz.open(str(pdf))
        results: List[Dict[str, Any]] = []
        page_indexes = _page_list(doc, pages)

        if mode == "pages":
            for pi in page_indexes:
                page = doc[pi]
                label = f"page_{pi+1:03d}"
                fn = f"{pdf.stem}_{label}.png"
                out = out_dir / fn
                size = _render_rect(page, page.rect, out, dpi)
                results.append({
                    "type": "page",
                    "page": pi + 1,
                    "label": label,
                    "path": str(out.relative_to(WORKSPACE)),
                    "markdown": f"![{label}]({out.relative_to(WORKSPACE).as_posix()})",
                    **size,
                })

        elif mode == "crops":
            if not crops:
                raise ValueError("mode='crops' requires crops=[{page,x0,y0,x1,y1,units?,label?}, ...]")
            for idx, crop in enumerate(crops, 1):
                pi = int(crop["page"]) - 1
                if pi < 0 or pi >= doc.page_count:
                    raise ValueError(f"Crop page out of range: {pi+1}")
                page = doc[pi]
                rect = _rect_from_crop(fitz, page, crop)
                label = _safe_slug(crop.get("label") or f"p{pi+1:03d}_crop_{idx:02d}")
                fn = f"{pdf.stem}_{label}.png"
                out = out_dir / fn
                size = _render_rect(page, rect, out, dpi)
                results.append({
                    "type": "crop",
                    "page": pi + 1,
                    "label": label,
                    "bbox_points": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                    "path": str(out.relative_to(WORKSPACE)),
                    "markdown": f"![{label}]({out.relative_to(WORKSPACE).as_posix()})",
                    **size,
                })

        else:  # auto by caption blocks
            for pi in page_indexes:
                page = doc[pi]
                captions = _caption_blocks(page, include_tables=include_tables)[:max_auto_per_page]
                for ci, cap in enumerate(captions, 1):
                    bbox = cap["bbox"]
                    if not bbox:
                        continue
                    x0, y0, x1, y1 = map(float, bbox)
                    h = page.rect.height
                    if mode == "smart" or smart_crop:
                        rect = _smart_caption_rect(fitz, page, cap, all_caps=captions, margin=float(crop_margin), content_refine=bool(content_refine))
                    else:
                        # Legacy fallback: generous vertical band around the caption.
                        rect = fitz.Rect(
                            0,
                            max(0, y0 - h * float(caption_above_ratio)),
                            page.rect.width,
                            min(h, y1 + h * float(caption_below_ratio)),
                        ) & page.rect
                    label_text = cap["text"][:60]
                    m = re.search(r"(Figure|Fig\.?|Table)\s*(\d+)", cap["text"], re.IGNORECASE)
                    base = f"p{pi+1:03d}_{m.group(1).lower().replace('.', '')}_{m.group(2)}" if m else f"p{pi+1:03d}_visual_{ci:02d}"
                    label = _safe_slug(f"{base}_{ci:02d}")
                    fn = f"{pdf.stem}_{label}.png"
                    out = out_dir / fn
                    size = _render_rect(page, rect, out, dpi)
                    results.append({
                        "type": "smart_caption_crop" if (mode == "smart" or smart_crop) else "auto_caption_crop",
                        "page": pi + 1,
                        "label": label,
                        "caption": cap["text"],
                        "caption_bbox_points": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                        "crop_bbox_points": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                        "path": str(out.relative_to(WORKSPACE)),
                        "markdown": f"![{label_text}]({out.relative_to(WORKSPACE).as_posix()})",
                        **size,
                    })

        return json.dumps({
            "success": True,
            "pdf_path": str(pdf.relative_to(WORKSPACE)),
            "output_dir": str(out_dir.relative_to(WORKSPACE)),
            "page_count": doc.page_count,
            "mode": mode,
            "dpi": dpi,
            "count": len(results),
            "results": results,
            "note": "Use the returned markdown links in paper reading notes. Smart/auto mode estimates tighter regions from Figure/Table captions plus neighboring text and pixel content; for imperfect crops, rerun mode='crops' with bbox_points adjusted.",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)




def _parse_pages_arg(value: str):
    if not value:
        return None
    pages = []
    for part in value.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start, end = part.split('-', 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    return pages or None


def _load_json_arg(value: str):
    if not value:
        return None
    path = Path(value)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return json.loads(value)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Render PDF pages/figures/tables to PNG screenshots for read_paper notes.")
    parser.add_argument("pdf_path", help="Workspace-local PDF path.")
    parser.add_argument("--output-dir", default="", help="Workspace-relative output directory.")
    parser.add_argument("--pages", default="", help="1-based pages, e.g. '1,3,5-7'. Empty means all pages.")
    parser.add_argument("--crops-json", default="", help="JSON string or JSON file for crops; required for mode=crops.")
    parser.add_argument("--mode", default="auto", choices=["auto", "smart", "pages", "crops"])
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--include-tables", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--caption-above-ratio", type=float, default=0.48)
    parser.add_argument("--caption-below-ratio", type=float, default=0.16)
    parser.add_argument("--max-auto-per-page", type=int, default=8)
    parser.add_argument("--smart-crop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--content-refine", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--crop-margin", type=float, default=8.0)
    args = parser.parse_args()
    print(pdf_snapshot_tool(
        pdf_path=args.pdf_path,
        output_dir=args.output_dir,
        pages=_parse_pages_arg(args.pages),
        crops=_load_json_arg(args.crops_json),
        mode=args.mode,
        dpi=args.dpi,
        include_tables=args.include_tables,
        caption_above_ratio=args.caption_above_ratio,
        caption_below_ratio=args.caption_below_ratio,
        max_auto_per_page=args.max_auto_per_page,
        smart_crop=args.smart_crop,
        content_refine=args.content_refine,
        crop_margin=args.crop_margin,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
