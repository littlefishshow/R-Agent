import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _is_caption_text(text: str, include_tables: bool = True) -> bool:
    pattern = r"^\s*(Figure|Fig\.?|Table)\s*\d+\s*(?:[:.|]|—|-)"
    if not include_tables:
        pattern = r"^\s*(Figure|Fig\.?)\s*\d+\s*(?:[:.|]|—|-)"
    return bool(re.search(pattern, text or "", re.IGNORECASE))


def _looks_like_body_paragraph(text: str, bbox) -> bool:
    """Heuristic separator: prose/section blocks should not be merged into table crops.

    This is deliberately conservative for table rows: numeric / math-heavy rows
    are allowed through, while prose paragraphs and section headings stop table
    expansion.  Appendix headings such as "C Evaluation Metric" are also
    treated as separators; otherwise a below-caption table may accidentally crop
    the next appendix section instead of the table above the caption.
    """
    txt = (text or "").strip()
    if not txt:
        return False
    x0, y0, x1, y1 = map(float, bbox)
    h = y1 - y0
    spaces = txt.count(" ")
    alpha = sum(ch.isalpha() for ch in txt)
    digits = sum(ch.isdigit() for ch in txt)
    # Section headings such as "4.1. Experimental Testbed" are compact but
    # should stop expansion before numeric/table-row exemptions are applied.
    if (re.match(r"^\d+(?:\.\d+)+\.?\s*[A-Z][A-Za-z]", txt)
            or re.match(r"^\d+\.?\s+[A-Z][A-Za-z]", txt)):
        return True
    # Table rows are often indented relative to prose and contain formulas,
    # brackets, percentages, or compact metric tokens.  Do not stop on such
    # blocks even if PyMuPDF extracts them as long strings with spaces.
    mathish = bool(re.search(r"[𝑃𝐺Í∑∈𝜎𝜖𝛼𝜌]|[=+−–×/%\[\]{}]|arg max|top-", txt))
    # Wide compact rows with several answer/metric columns are table content
    # even if they contain many alphabetic words.
    if (x1 - x0) > 300 and digits > 0 and not re.search(r"[.!?;:]\s+[A-Z]", txt):
        return False
    if x0 > 85 and (mathish or digits > 0):
        return False
    # Appendix/letter headings such as "C Evaluation Metric".  Require a
    # short title-like block and no digits/math to avoid rejecting normal table
    # headers containing metrics or values.
    if re.match(r"^[A-Z]\s+[A-Z][A-Za-z]\w*(?:\s+[A-Z][A-Za-z]\w*){0,5}$", txt) and digits == 0 and not mathish:
        return True
    # Subfigure labels like "(a) Cognitive islands.(b) Shared ..." are figure
    # content rather than prose separators.
    if re.search(r"\([a-z]\)", txt):
        return False
    if len(txt) <= 80 and spaces >= 1 and alpha > digits and re.search(r"[A-Z][a-z]{3,}", txt) and not re.search(r"[%𝜌=×]", txt):
        # Table headers often contain several words plus metric names; do not
        # classify them as prose unless there is sentence-like punctuation.
        if re.search(r"[?.:]", txt):
            return True
    # PyMuPDF often extracts table cells as compact strings with few spaces;
    # body prose has many word spaces and long sentence-like lines.
    if h >= 18 and len(txt) >= 70 and spaces >= 6 and alpha > digits * 2:
        return True
    if h >= 45 and alpha > digits:
        return True
    return False


def _candidate_table_rect_by_text_blocks(fitz, page, cap_bbox, wx0, wx1, direction: str, margin: float = 8.0):
    """Tight table crop from caption plus adjacent non-prose text blocks.

    Row projection alone can bridge a table to following paragraphs when the
    paragraph starts close to the last row.  For tables, text-block semantics are
    usually more reliable: include compact header/row blocks, stop at prose or
    the next caption.
    """
    cx0, cy0, cx1, cy1 = map(float, cap_bbox)
    cap_rect = fitz.Rect(cx0, cy0, cx1, cy1)
    blocks = []
    for b in _text_blocks(page):
        bx0, by0, bx1, by1 = b["bbox"]
        if _horizontal_overlap_ratio((bx0, by0, bx1, by1), (wx0, 0, wx1, page.rect.height)) < 0.25:
            continue
        blocks.append(b)
    blocks.sort(key=lambda b: (float(b["bbox"][1]), float(b["bbox"][0])))
    selected = []
    if direction == "below":
        last_y = cy1
        for b in blocks:
            bx0, by0, bx1, by1 = map(float, b["bbox"])
            if by1 <= cy1 + 1:
                continue
            gap = by0 - last_y
            if gap > 36:
                break
            txt = b["text"]
            if _is_caption_text(txt, include_tables=True) or _looks_like_body_paragraph(txt, b["bbox"]):
                break
            selected.append(b)
            last_y = max(last_y, by1)
    else:
        last_y = cy0
        for b in reversed(blocks):
            bx0, by0, bx1, by1 = map(float, b["bbox"])
            if by0 >= cy0 - 1:
                continue
            gap = last_y - by1
            if gap > 36:
                break
            txt = b["text"]
            if _is_caption_text(txt, include_tables=True) or _looks_like_body_paragraph(txt, b["bbox"]):
                break
            selected.append(b)
            last_y = min(last_y, by0)
    if not selected:
        return None
    rect = cap_rect
    for b in selected:
        rect = rect | fitz.Rect(*map(float, b["bbox"]))
    return (rect + (-margin, -margin, margin, margin)) & page.rect



def _candidate_figure_rect_by_text_blocks(fitz, page, cap_bbox, wx0, wx1, direction: str, margin: float = 8.0):
    """Tight crop for vector/text figures adjacent to a caption.

    Some PDF figures are not image blocks; PyMuPDF exposes axis labels, legends
    and diagram labels as text.  Row projection alone may bridge through nearby
    body paragraphs or previous captions.  This routine walks compact,
    non-prose text blocks in the target direction and stops at prose/section text
    or another caption.
    """
    cx0, cy0, cx1, cy1 = map(float, cap_bbox)
    cap_rect = fitz.Rect(cx0, cy0, cx1, cy1)
    win_w = max(1.0, wx1 - wx0)
    blocks = []
    for b in _text_blocks(page):
        bx0, by0, bx1, by1 = map(float, b["bbox"])
        bw = bx1 - bx0
        center = (bx0 + bx1) / 2.0
        if center < wx0 - margin or center > wx1 + margin:
            continue
        if _horizontal_overlap_ratio((bx0, by0, bx1, by1), (wx0, 0, wx1, page.rect.height)) < 0.18:
            continue
        # Full-width title/author/prose blocks that merely cross the column are
        # not figure content.  The current caption itself is handled separately.
        if bw > win_w * 1.35 and not _segment_intersects((by0, by1), cy0, cy1):
            continue
        blocks.append(b)
    blocks.sort(key=lambda b: (float(b["bbox"][1]), float(b["bbox"][0])))

    selected = []
    if direction == "above":
        last_y = cy0
        for b in reversed(blocks):
            bx0, by0, bx1, by1 = map(float, b["bbox"])
            if by0 >= cy0 - 1:
                continue
            gap = last_y - by1
            if gap > 52:
                break
            txt = b["text"]
            if _is_caption_text(txt, include_tables=True) or _looks_like_body_paragraph(txt, b["bbox"]):
                break
            selected.append(b)
            last_y = min(last_y, by0)
    else:
        last_y = cy1
        for b in blocks:
            bx0, by0, bx1, by1 = map(float, b["bbox"])
            if by1 <= cy1 + 1:
                continue
            gap = by0 - last_y
            if gap > 52:
                break
            txt = b["text"]
            if _is_caption_text(txt, include_tables=True) or _looks_like_body_paragraph(txt, b["bbox"]):
                break
            selected.append(b)
            last_y = max(last_y, by1)
    if not selected:
        return None
    rect = cap_rect
    for b in selected:
        rect = rect | fitz.Rect(*map(float, b["bbox"]))
    return (rect + (-margin, -margin, margin, margin)) & page.rect

def _caption_blocks(page, include_tables: bool = True):
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
        # Require a caption-like delimiter after the number.  This avoids false
        # positives such as body paragraphs starting with "Table 4 summarizes...".
        if _is_caption_text(text, include_tables=include_tables):
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


def _candidate_rect_by_direction(fitz, page, cap_bbox, wx0, wx1, direction: str, margin: float = 8.0, kind: str = "figure"):
    """Build a local crop around a caption.

    direction='above': target visual object is above a below-caption (typical Figure).
    direction='below': target visual object is below an above-caption (typical Table).
    The selection walks from the caption segment toward the target and stops at a
    large whitespace gap, preventing abstract/body paragraphs from being pulled in.
    """
    cx0, cy0, cx1, cy1 = map(float, cap_bbox)
    page_h = page.rect.height

    # First try explicit raster image blocks for figures.  This handles cases
    # where the gap between image and caption is larger than the row-projection
    # whitespace threshold; otherwise the crop may return only caption + below
    # paragraph and miss the actual picture (e.g. RAGEN2 Figure 8).
    if kind == "figure":
        imgs = _image_blocks_between(page, cap_bbox, wx0, wx1, direction, max_gap=80.0)
        if imgs:
            rect = fitz.Rect(cx0, cy0, cx1, cy1)
            for ib in imgs:
                rect = rect | fitz.Rect(*ib)
            return (rect + (-margin, -margin, margin, margin)) & page.rect
        text_rect = _candidate_figure_rect_by_text_blocks(fitz, page, cap_bbox, wx0, wx1, direction, margin=margin)
        if text_rect is not None:
            return text_rect
    if kind == "table":
        text_rect = _candidate_table_rect_by_text_blocks(fitz, page, cap_bbox, wx0, wx1, direction, margin=margin)
        if text_rect is not None:
            return text_rect
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
    # Tables should stop at the first normal paragraph/section after their last
    # row, while figures often have a larger visual gap between image and caption.
    max_gap = 18.0 if kind == "table" else 45.0
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



def _page_text_window_x(page, margin: float = 8.0):
    """Estimate the main content x-window, excluding page numbers and tiny marginal marks."""
    xs0, xs1 = [], []
    for b in _text_blocks(page):
        x0, y0, x1, y1 = b["bbox"]
        txt = b["text"].strip()
        # Ignore tiny page numbers / line fragments.
        if (x1 - x0) < 28 or re.fullmatch(r"\d+", txt):
            continue
        xs0.append(x0)
        xs1.append(x1)
    if not xs0:
        return margin, page.rect.width - margin
    return max(0.0, min(xs0) - margin), min(page.rect.width, max(xs1) + margin)


def _horizontal_overlap_ratio(a, b) -> float:
    ax0, _, ax1, _ = map(float, a)
    bx0, _, bx1, _ = map(float, b)
    inter = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    denom = max(1.0, min(ax1 - ax0, bx1 - bx0))
    return inter / denom


def _image_blocks_between(page, cap_bbox, wx0, wx1, direction: str, max_gap: float = 70.0):
    """Find raster image blocks adjacent to a caption in the target direction.

    Require overlap with the caption as well as the crop window.  On pages with
    several nearby subfigures this avoids a lower caption swallowing the previous
    figure in the same column.
    """
    cx0, cy0, cx1, cy1 = map(float, cap_bbox)
    cap_w = max(1.0, cx1 - cx0)
    out = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") != 1 or not b.get("bbox"):
            continue
        x0, y0, x1, y1 = map(float, b["bbox"])
        ib = (x0, y0, x1, y1)
        if _horizontal_overlap_ratio(ib, (wx0, 0, wx1, page.rect.height)) < 0.35:
            continue
        # If the caption is not full-column/full-width, the image should overlap
        # the caption x-range; this filters adjacent panels from other captions.
        if cap_w < (wx1 - wx0) * 0.88 and _horizontal_overlap_ratio(ib, (cx0, 0, cx1, page.rect.height)) < 0.25:
            continue
        if direction == "above" and y1 <= cy0 and cy0 - y1 <= max_gap:
            out.append((x0, y0, x1, y1))
        elif direction == "below" and y0 >= cy1 and y0 - cy1 <= max_gap:
            out.append((x0, y0, x1, y1))
    return out

def _same_row(a, b, tol: float = 12.0) -> bool:
    ay0, ay1 = float(a["bbox"][1]), float(a["bbox"][3])
    by0, by1 = float(b["bbox"][1]), float(b["bbox"][3])
    return max(ay0, by0) <= min(ay1, by1) + tol



def _estimate_column_windows(page, margin: float = 8.0) -> List[Tuple[float, float]]:
    """Estimate one/two main text-column x windows from text blocks.

    Many ACL-style PDFs are two-column.  A single full text window makes a
    right-column caption crop union the unrelated left-column paragraphs/figures.
    We infer columns from non-caption, non-tiny text block centers; if no clear
    split exists we keep the legacy full content window.
    """
    page_w = float(page.rect.width)
    candidates = []
    for b in _text_blocks(page):
        x0, y0, x1, y1 = map(float, b["bbox"])
        txt = b["text"].strip()
        width = x1 - x0
        if width < 30 or re.fullmatch(r"\d+", txt):
            continue
        if x0 < page_w * 0.08 or x1 > page_w * 0.96:
            # page numbers, arXiv side marks, marginal artifacts
            continue
        if _is_caption_text(txt, include_tables=True):
            continue
        if width > page_w * 0.62:
            # title/author/full-width display blocks are not column evidence
            continue
        candidates.append((x0, x1, (x0 + x1) / 2.0, width))
    if len(candidates) < 6:
        return [_page_text_window_x(page, margin=margin)]

    centers = sorted(c[2] for c in candidates)
    # Find a center gap close to the page middle.  In two-column papers this gap
    # is much larger than within-column jitter.
    best = None
    for a, b in zip(centers, centers[1:]):
        gap = b - a
        mid = (a + b) / 2.0
        if page_w * 0.38 <= mid <= page_w * 0.62 and gap > page_w * 0.12:
            if best is None or gap > best[0]:
                best = (gap, mid)
    if best is None:
        return [_page_text_window_x(page, margin=margin)]

    split = best[1]
    left = [(x0, x1) for x0, x1, c, _ in candidates if c < split]
    right = [(x0, x1) for x0, x1, c, _ in candidates if c >= split]
    if len(left) < 3 or len(right) < 3:
        return [_page_text_window_x(page, margin=margin)]

    def win(items, hard0, hard1):
        return (max(0.0, min(x0 for x0, _ in items) - margin, hard0),
                min(page_w, max(x1 for _, x1 in items) + margin, hard1))

    return [win(left, 0.0, split + margin), win(right, split - margin, page_w)]


def _caption_column_window_x(page, cap, margin: float = 8.0) -> Tuple[float, float]:
    """Return the inferred column window containing the caption center."""
    x0, _, x1, _ = map(float, cap["bbox"])
    center = (x0 + x1) / 2.0
    windows = _estimate_column_windows(page, margin=margin)
    for wx0, wx1 in windows:
        if wx0 - margin <= center <= wx1 + margin:
            return wx0, wx1
    return min(windows, key=lambda w: min(abs(center - w[0]), abs(center - w[1])))


def _clip_rect_to_caption_column(fitz, page, rect, cap, margin: float = 8.0, kind: str = "figure"):
    """Clip suspicious crops to the caption column, but preserve full-width tables.

    Two-column papers often need column clipping for figures and column-local
    tables.  However, centered table captions can belong to a full-width table
    spanning both columns; if text-block detection already found such a wide
    table, clipping it back to the caption's inferred column cuts the table in
    half.
    """
    if kind == "table" and float(rect.width) > float(page.rect.width) * 0.60:
        return rect
    wx0, wx1 = _caption_column_window_x(page, cap, margin=margin)
    if (wx1 - wx0) < page.rect.width * 0.72:
        return rect & fitz.Rect(wx0, 0, wx1, page.rect.height)
    return rect


def _vertical_gap_to_caption(rect, cap_bbox, direction: str) -> float:
    cx0, cy0, cx1, cy1 = map(float, cap_bbox)
    if direction == "above":
        return max(0.0, cy0 - float(rect.y1))
    return max(0.0, float(rect.y0) - cy1)


def _extra_height_on_side(rect, cap_bbox, direction: str) -> float:
    cx0, cy0, cx1, cy1 = map(float, cap_bbox)
    if direction == "above":
        return max(0.0, cy0 - float(rect.y0))
    return max(0.0, float(rect.y1) - cy1)




def _caption_window_x(page, cap, all_caps, margin: float = 8.0):
    """Estimate x-window for a caption crop.

    Backward-compatible behavior is preserved for full-width captions/tables, but
    isolated captions in two-column papers now use the caption's own column.
    This prevents row-projection/pixel refinement from unioning unrelated content
    in the opposite column (common for ACL two-column layouts).
    """
    x0, y0, x1, y1 = map(float, cap["bbox"])
    w = page.rect.width
    same = sorted([c for c in all_caps if _same_row(c, cap)], key=lambda c: float(c["bbox"][0]))
    if len(same) <= 1 or (x1 - x0) > w * 0.45:
        col_wx0, col_wx1 = _caption_column_window_x(page, cap, margin=margin)
        # A wide caption likely belongs to a full-width object; otherwise prefer
        # the inferred column window.  If no clear two-column split is found,
        # _caption_column_window_x returns the legacy main content window.
        if (col_wx1 - col_wx0) < w * 0.72 and (x1 - x0) <= w * 0.55:
            return col_wx0, col_wx1
        return _page_text_window_x(page, margin=margin)
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


def _crop_quality(rect, page_rect, max_area_ratio: float = 0.45, *, full_page_snapshot: bool = False) -> Dict[str, Any]:
    """Return note-facing quality metadata for a crop/page snapshot.

    A suspicious crop is one that covers too much of the page or is effectively a
    full-page rectangle.  The field names are intentionally explicit so callers
    can decide whether to insert the image into final notes or rerun with
    --mode crops / --crops-json.
    """
    page_area = max(1.0, _rect_area(page_rect))
    crop_area_ratio = _rect_area(rect) / page_area
    width_ratio = max(0.0, float(rect.width)) / max(1.0, float(page_rect.width))
    height_ratio = max(0.0, float(rect.height)) / max(1.0, float(page_rect.height))
    reasons = []
    if crop_area_ratio > float(max_area_ratio):
        reasons.append(f"crop_area_ratio>{float(max_area_ratio):.2f}")
    if width_ratio >= 0.92 and height_ratio >= 0.85:
        reasons.append("crop_width_height_close_to_full_page")
    if full_page_snapshot:
        reasons.append("full_page_snapshot")
    suspicious = bool(reasons)
    quality: Dict[str, Any] = {
        "crop_area_ratio": round(crop_area_ratio, 4),
        "crop_width_ratio": round(width_ratio, 4),
        "crop_height_ratio": round(height_ratio, 4),
        "max_area_ratio": float(max_area_ratio),
        "is_suspicious_large_crop": suspicious,
        "recommended_for_notes": not suspicious,
        "warning": "; ".join(reasons) if suspicious else "",
        "recommended_manual_crop": (
            "Rerun pdf_snapshot.py with --mode crops --crops-json using a tighter bbox_points crop."
            if suspicious else ""
        ),
    }
    if full_page_snapshot:
        quality["full_page_snapshot"] = True
        quality["recommended_for_notes"] = False
        if "full_page_snapshot" not in quality["warning"]:
            quality["warning"] = (quality["warning"] + "; full_page_snapshot").strip("; ")
    return quality


def _relative_markdown_path(out: Path) -> str:
    """Best-effort Markdown image path relative to the note's output directory.

    read_paper stores images under outputs/papers_output/<category>/assets/<stem>/
    and notes in outputs/papers_output/<category>/.  In that common layout this
    returns assets/<stem>/<file>.  For custom output directories, fall back to the
    workspace-relative path to preserve compatibility.
    """
    try:
        rel = out.resolve().relative_to(WORKSPACE)
    except ValueError:
        return out.as_posix()
    parts = rel.parts
    if "assets" in parts:
        idx = parts.index("assets")
        return Path(*parts[idx:]).as_posix()
    return rel.as_posix()


def _result_paths(label_text: str, out: Path) -> Dict[str, str]:
    workspace_path = out.relative_to(WORKSPACE).as_posix()
    rel_md_path = _relative_markdown_path(out)
    return {
        "path": workspace_path,
        "markdown": f"![{label_text}]({workspace_path})",
        "markdown_workspace": f"![{label_text}]({workspace_path})",
        "relative_markdown": f"![{label_text}]({rel_md_path})",
        "relative_path": rel_md_path,
    }


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
    cap_bbox = (x0, y0, x1, y1)
    pref_rect = _candidate_rect_by_direction(fitz, page, cap_bbox, wx0, wx1, preferred, margin=margin, kind=kind)
    pref_rect = _clip_rect_to_caption_column(fitz, page, pref_rect, cap, margin=margin, kind=kind)

    # Direction sanity check: if preferred side contains almost no object besides caption,
    # try the other side and choose it when it has much more plausible adjacent content.
    alt_rect = _candidate_rect_by_direction(fitz, page, cap_bbox, wx0, wx1, alternate, margin=margin, kind=kind)
    alt_rect = _clip_rect_to_caption_column(fitz, page, alt_rect, cap, margin=margin, kind=kind)
    cap_h = max(1.0, y1 - y0)
    pref_extra_h = _extra_height_on_side(pref_rect, cap_bbox, preferred)
    alt_extra_h = _extra_height_on_side(alt_rect, cap_bbox, alternate)
    pref_gap = _vertical_gap_to_caption(pref_rect, cap_bbox, preferred)
    alt_gap = _vertical_gap_to_caption(alt_rect, cap_bbox, alternate)

    # For tables, captions are often below the actual table in appendix/main text
    # despite the common "caption above" convention.  If the preferred below
    # side is just prose/section text, prefer a compact table block above.
    if kind == "table":
        # A table may appear above a below-caption, especially in appendices or
        # when a long caption is laid out under a table.  Prefer the alternate
        # side when it is a substantial nearby block and the preferred side is
        # either tiny or suspiciously continues into prose below the caption.
        if pref_extra_h < cap_h * 1.2 and alt_extra_h > pref_extra_h * 1.5:
            return alt_rect
        if alt_extra_h >= 18 and alt_gap <= 90 and (pref_extra_h < 55 or _rect_area(pref_rect) > _rect_area(alt_rect) * 1.8):
            return alt_rect
        if preferred == "below" and alt_extra_h >= cap_h * 2.0 and alt_extra_h >= pref_extra_h * 0.55:
            return alt_rect

    if pref_extra_h < cap_h * 1.2 and alt_extra_h > pref_extra_h * 2.0:
        return alt_rect
    # For figures, strongly avoid returning caption + below paragraph when above has content.
    if kind == "figure" and pref_rect.y0 <= y0 and pref_extra_h >= cap_h * 1.2:
        return pref_rect
    # If the default figure-above crop is essentially only caption / partial
    # labels, do not switch to a huge below paragraph just because it has more
    # area.  Returning the preferred side is a safer failure mode.
    if kind != "figure" and _rect_area(alt_rect) > _rect_area(pref_rect) * 2.8 and pref_extra_h < 40:
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
    max_area_ratio: float = 0.45,
    reject_large_crops: bool = False,
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
        if max_area_ratio <= 0 or max_area_ratio > 1:
            raise ValueError("max_area_ratio must be in (0, 1]")
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
                quality = _crop_quality(page.rect, page.rect, max_area_ratio, full_page_snapshot=True)
                results.append({
                    "type": "page",
                    "page": pi + 1,
                    "label": label,
                    "warning": "full_page_snapshot",
                    "full_page_snapshot": True,
                    "crop_area_ratio": 1,
                    "recommended_for_notes": False,
                    "quality": quality,
                    **_result_paths(label, out),
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
                quality = _crop_quality(rect, page.rect, max_area_ratio)
                size = _render_rect(page, rect, out, dpi)
                results.append({
                    "type": "crop",
                    "page": pi + 1,
                    "label": label,
                    "bbox_points": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                    "quality": quality,
                    **_result_paths(label, out),
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
                    quality = _crop_quality(rect, page.rect, max_area_ratio)
                    item = {
                        "type": "smart_caption_crop" if (mode == "smart" or smart_crop) else "auto_caption_crop",
                        "page": pi + 1,
                        "label": label,
                        "caption": cap["text"],
                        "caption_bbox_points": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                        "crop_bbox_points": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                        "quality": quality,
                    }
                    if reject_large_crops and quality["is_suspicious_large_crop"]:
                        item.update({
                            "status": "skipped",
                            "skipped": True,
                            "skip_reason": quality["warning"] or "suspicious_large_crop",
                        })
                    else:
                        size = _render_rect(page, rect, out, dpi)
                        item.update({
                            "status": "rendered",
                            **_result_paths(label_text, out),
                            **size,
                        })
                    results.append(item)

        return json.dumps({
            "success": True,
            "pdf_path": str(pdf.relative_to(WORKSPACE)),
            "output_dir": str(out_dir.relative_to(WORKSPACE)),
            "page_count": doc.page_count,
            "mode": mode,
            "dpi": dpi,
            "max_area_ratio": max_area_ratio,
            "reject_large_crops": reject_large_crops,
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
    parser.add_argument("--max-area-ratio", type=float, default=0.45, help="Mark smart/auto/crops suspicious when crop/page area exceeds this ratio.")
    parser.add_argument("--reject-large-crops", action="store_true", default=False, help="In smart/auto mode, skip rendering suspiciously large crops.")
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
        max_area_ratio=args.max_area_ratio,
        reject_large_crops=args.reject_large_crops,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
