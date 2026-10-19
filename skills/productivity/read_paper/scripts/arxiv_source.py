"""Download and prepare arXiv LaTeX sources for the read_paper skill.

The module intentionally uses only the Python standard library.  PyMuPDF is
used opportunistically to convert figure PDFs to PNG, but the source pipeline
still succeeds when it is unavailable.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_SANDBOX_ROOT = Path("sandbox/read_paper")
ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
MODERN_ID_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5}(?:v\d+)?)(?!\d)", re.IGNORECASE)
LEGACY_ID_RE = re.compile(
    r"(?<![A-Za-z0-9.-])([A-Za-z][A-Za-z0-9.-]*/\d{7}(?:v\d+)?)(?!\d)",
    re.IGNORECASE,
)
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".eps", ".ps"}


class SourcePipelineError(RuntimeError):
    def __init__(self, code: str, message: str, *, fallback_required: bool = False):
        super().__init__(message)
        self.code = code
        self.fallback_required = fallback_required


@dataclass
class Limits:
    max_download_bytes: int = 200 * 1024 * 1024
    max_extracted_bytes: int = 1024 * 1024 * 1024
    max_files: int = 20_000


def _workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "skills").is_dir() and (parent / "tools").is_dir():
            return parent
    return here.parents[3]


def _workspace_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _workspace_root() / path
    path = path.resolve()
    try:
        path.relative_to(_workspace_root().resolve())
    except ValueError as exc:
        raise SourcePipelineError("unsafe_path", f"Path must stay inside the workspace: {value}") from exc
    return path


def normalize_arxiv_id(value: str) -> Optional[str]:
    """Return a modern or legacy arXiv identifier from an ID, URL, or text."""
    candidate = urllib.parse.unquote(value.strip())
    for prefix in ("arXiv:", "arxiv:"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
    modern = MODERN_ID_RE.search(candidate)
    if modern:
        return modern.group(1)
    legacy = LEGACY_ID_RE.search(candidate)
    if legacy:
        return legacy.group(1)
    return None


def _safe_slug(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._-")
    return (value or "paper")[:120]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_source(arxiv_id: str, destination: Path, timeout: float, limits: Limits) -> Dict[str, Any]:
    source_url = "https://arxiv.org/src/" + urllib.parse.quote(arxiv_id, safe="/.")
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "R-Agent-read-paper/2.0 (source-first research reader)"},
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as out:
            final_host = (urllib.parse.urlparse(response.geturl()).hostname or "").lower()
            if final_host not in ARXIV_HOSTS:
                raise SourcePipelineError("unsafe_redirect", f"arXiv redirected to an unexpected host: {final_host}")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > limits.max_download_bytes:
                raise SourcePipelineError("download_too_large", "The source archive exceeds the configured limit.")
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_download_bytes:
                    raise SourcePipelineError("download_too_large", "The source archive exceeds the configured limit.")
                out.write(chunk)
            return {
                "source_url": source_url,
                "final_url": response.geturl(),
                "content_type": response.headers.get("Content-Type", ""),
                "content_disposition": response.headers.get("Content-Disposition", ""),
                "size_bytes": total,
            }
    except urllib.error.HTTPError as exc:
        fallback = exc.code in {400, 403, 404, 410, 415}
        raise SourcePipelineError(
            "source_http_error",
            f"arXiv source download failed with HTTP {exc.code}: {source_url}",
            fallback_required=fallback,
        ) from exc
    except urllib.error.URLError as exc:
        raise SourcePipelineError("source_network_error", f"Could not download arXiv source: {exc}") from exc


def _safe_member_path(root: Path, name: str) -> Path:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        raise SourcePipelineError("unsafe_archive", f"Archive contains an absolute path: {name}")
    destination = (root / normalized).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise SourcePipelineError("unsafe_archive", f"Archive path escapes the extraction root: {name}") from exc
    return destination


def _extract_tar(archive: Path, source_dir: Path, limits: Limits) -> Tuple[List[str], List[str]]:
    extracted: List[str] = []
    warnings: List[str] = []
    total = 0
    with tarfile.open(str(archive), mode="r:*") as tar:
        members = tar.getmembers()
        if len(members) > limits.max_files:
            raise SourcePipelineError("archive_too_many_files", "The source archive contains too many entries.")
        for member in members:
            target = _safe_member_path(source_dir, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym() or member.islnk():
                warnings.append(f"Skipped archive link: {member.name}")
                continue
            if not member.isfile():
                warnings.append(f"Skipped non-regular archive entry: {member.name}")
                continue
            total += max(0, int(member.size))
            if total > limits.max_extracted_bytes:
                raise SourcePipelineError("archive_too_large", "Extracted source exceeds the configured limit.")
            fileobj = tar.extractfile(member)
            if fileobj is None:
                warnings.append(f"Could not read archive entry: {member.name}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                shutil.copyfileobj(fileobj, out, length=1024 * 1024)
            extracted.append(target.relative_to(source_dir).as_posix())
    return extracted, warnings


def _looks_like_pdf(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


def _looks_like_tex(data: bytes) -> bool:
    sample = data[:256 * 1024].decode("utf-8", errors="ignore")
    return bool(re.search(r"\\(?:documentclass|begin\s*\{document\}|section|title)\b", sample))


def _extract_payload(payload: Path, source_dir: Path, limits: Limits) -> Tuple[str, List[str], List[str]]:
    """Extract tar, gzip-single-TeX, or plain TeX source payloads."""
    source_dir.mkdir(parents=True, exist_ok=True)
    if _looks_like_pdf(payload):
        raise SourcePipelineError(
            "pdf_only_submission",
            "The arXiv source endpoint returned PDF content; use the PDF fallback.",
            fallback_required=True,
        )
    if tarfile.is_tarfile(str(payload)):
        extracted, warnings = _extract_tar(payload, source_dir, limits)
        return "tar", extracted, warnings

    with payload.open("rb") as handle:
        magic = handle.read(2)
    if magic == b"\x1f\x8b":
        target = source_dir / "main.tex"
        total = 0
        try:
            with gzip.open(str(payload), "rb") as compressed, target.open("wb") as out:
                while True:
                    chunk = compressed.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limits.max_extracted_bytes:
                        raise SourcePipelineError("archive_too_large", "Decompressed source exceeds the configured limit.")
                    out.write(chunk)
        except SourcePipelineError:
            target.unlink(missing_ok=True)
            raise
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise SourcePipelineError("invalid_gzip", "The source payload is not valid gzip data.") from exc
        if _looks_like_pdf(target):
            target.unlink(missing_ok=True)
            raise SourcePipelineError(
                "pdf_only_submission",
                "The gzip payload contains a PDF rather than TeX source.",
                fallback_required=True,
            )
        with target.open("rb") as handle:
            tex_sample = handle.read(256 * 1024)
        if not _looks_like_tex(tex_sample):
            target.unlink(missing_ok=True)
            raise SourcePipelineError("unknown_source_format", "The gzip payload is neither a tar archive nor recognizable TeX.")
        return "gzip_tex", [target.name], []

    raw = payload.read_bytes()
    if _looks_like_tex(raw):
        target = source_dir / "main.tex"
        target.write_bytes(raw)
        return "plain_tex", [target.name], []
    raise SourcePipelineError("unknown_source_format", "The downloaded payload is not tar, gzip TeX, plain TeX, or PDF.")


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def strip_tex_comments(text: str) -> str:
    lines: List[str] = []
    for line in text.splitlines():
        cut = len(line)
        for idx, char in enumerate(line):
            if char != "%":
                continue
            backslashes = 0
            cursor = idx - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                cut = idx
                break
        lines.append(line[:cut].rstrip())
    return "\n".join(lines) + "\n"


def _tex_score(path: Path, text: str, referenced: bool) -> int:
    score = 0
    if re.search(r"\\documentclass(?:\[[^]]*\])?\s*\{", text):
        score += 1000
    if re.search(r"\\begin\s*\{document\}", text):
        score += 500
    if re.search(r"\\(?:title|maketitle|begin\s*\{abstract\})", text):
        score += 100
    if path.stem.lower() in {"main", "paper", "article", "manuscript", "ms"}:
        score += 100
    if referenced:
        score -= 250
    score -= len(path.parts) * 5
    score -= min(len(text) // 100_000, 20)
    return score


INCLUDE_RE = re.compile(r"\\(?:input|include|subfile)\s*(?:\[[^]]*\])?\s*\{([^{}]+)\}")


def _resolve_include(current: Path, source_dir: Path, raw_name: str) -> Optional[Path]:
    name = raw_name.strip()
    if not name or "\\" in name:
        return None
    candidate = current.parent / name
    candidates = [candidate]
    if not candidate.suffix:
        candidates.append(candidate.with_suffix(".tex"))
    for path in candidates:
        try:
            resolved = path.resolve()
            resolved.relative_to(source_dir.resolve())
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _find_main_tex(source_dir: Path) -> Tuple[Path, List[Dict[str, Any]]]:
    tex_files = sorted(p for p in source_dir.rglob("*.tex") if p.is_file())
    if not tex_files:
        raise SourcePipelineError("no_tex_source", "The source package contains no .tex files.", fallback_required=True)
    texts = {path: strip_tex_comments(_read_text(path)) for path in tex_files}
    referenced_paths = set()
    for path, text in texts.items():
        for match in INCLUDE_RE.finditer(text):
            include = _resolve_include(path, source_dir, match.group(1))
            if include:
                referenced_paths.add(include)
    candidates = []
    for path in tex_files:
        score = _tex_score(path.relative_to(source_dir), texts[path], path in referenced_paths)
        candidates.append({"path": path.relative_to(source_dir).as_posix(), "score": score})
    candidates.sort(key=lambda item: (-int(item["score"]), len(str(item["path"])), str(item["path"])))
    return source_dir / candidates[0]["path"], candidates


def _expand_tex(path: Path, source_dir: Path, stack: Sequence[Path], warnings: List[str]) -> str:
    if path in stack:
        warnings.append("Include cycle skipped: " + " -> ".join(p.name for p in list(stack) + [path]))
        return f"\n% [read_paper: include cycle skipped: {path.name}]\n"
    text = strip_tex_comments(_read_text(path))
    chunks: List[str] = []
    cursor = 0
    for match in INCLUDE_RE.finditer(text):
        chunks.append(text[cursor:match.start()])
        include = _resolve_include(path, source_dir, match.group(1))
        if include is None:
            warnings.append(f"Unresolved include in {path.relative_to(source_dir)}: {match.group(1)}")
            chunks.append(match.group(0))
        else:
            rel = include.relative_to(source_dir).as_posix()
            chunks.append(f"\n% [read_paper: begin include {rel}]\n")
            chunks.append(_expand_tex(include, source_dir, list(stack) + [path], warnings))
            chunks.append(f"\n% [read_paper: end include {rel}]\n")
        cursor = match.end()
    chunks.append(text[cursor:])
    return "".join(chunks)


def _balanced_argument(text: str, open_brace: int) -> Optional[Tuple[str, int]]:
    if open_brace >= len(text) or text[open_brace] != "{":
        return None
    depth = 0
    for idx in range(open_brace, len(text)):
        char = text[idx]
        escaped = idx > 0 and text[idx - 1] == "\\"
        if escaped:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1:idx], idx + 1
    return None


def _command_values(text: str, command: str) -> List[str]:
    values: List[str] = []
    pattern = re.compile(r"\\" + re.escape(command) + r"\*?\s*(?:\[[^]]*\]\s*)?\{")
    for match in pattern.finditer(text):
        parsed = _balanced_argument(text, match.end() - 1)
        if parsed:
            values.append(parsed[0].strip())
    return values


def _clean_inline_tex(value: str) -> str:
    value = re.sub(r"\\(?:label|cite\w*|ref|eqref)\s*\{([^{}]*)\}", r"[\1]", value)
    for _ in range(4):
        value = re.sub(
            r"\\(?:textbf|textit|emph|textrm|texttt|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}",
            r"\1",
            value,
        )
    value = re.sub(r"\\(?:url|href)\s*\{([^{}]*)\}(?:\s*\{([^{}]*)\})?", lambda m: m.group(2) or m.group(1), value)
    value = value.replace("~", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


SECTION_RE = re.compile(r"\\(section|subsection|subsubsection|paragraph)\*?\s*\{")


def _section_records(text: str) -> List[Dict[str, Any]]:
    headings: List[Dict[str, Any]] = []
    for match in SECTION_RE.finditer(text):
        parsed = _balanced_argument(text, match.end() - 1)
        if not parsed:
            continue
        title, title_end = parsed
        headings.append({
            "level": {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}[match.group(1)],
            "title": _clean_inline_tex(title),
            "start": match.start(),
            "content_start": title_end,
        })
    records: List[Dict[str, Any]] = []
    for idx, heading in enumerate(headings):
        end = headings[idx + 1]["start"] if idx + 1 < len(headings) else len(text)
        records.append(dict(heading, end=end))
    return records


def _document_body(text: str) -> str:
    begin = re.search(r"\\begin\s*\{document\}", text)
    end_matches = list(re.finditer(r"\\end\s*\{document\}", text))
    start = begin.end() if begin else 0
    end = end_matches[-1].start() if end_matches else len(text)
    return text[start:end]


def _readable_markdown(tex: str) -> str:
    text = tex
    replacements = {
        "itemize": "",
        "enumerate": "",
        "description": "",
        "document": "",
    }
    for env, replacement in replacements.items():
        text = re.sub(r"\\begin\s*\{" + env + r"\}", replacement, text)
        text = re.sub(r"\\end\s*\{" + env + r"\}", replacement, text)
    text = re.sub(r"\\item(?:\[[^]]*\])?\s*", "\n- ", text)
    for command, hashes in (("section", "##"), ("subsection", "###"), ("subsubsection", "####"), ("paragraph", "#####")):
        pattern = re.compile(r"\\" + command + r"\*?\s*\{([^{}]*)\}")
        text = pattern.sub(lambda m: f"\n{hashes} {_clean_inline_tex(m.group(1))}\n", text)
    text = re.sub(r"\\begin\s*\{abstract\}", "\n## Abstract\n", text)
    text = re.sub(r"\\end\s*\{abstract\}", "\n", text)
    text = re.sub(r"\\(?:label)\s*\{[^{}]*\}", "", text)
    text = re.sub(r"\\(?:cite\w*|ref|eqref)\s*\{([^{}]*)\}", r"[\1]", text)
    for _ in range(4):
        text = re.sub(r"\\(?:textbf|textit|emph|textrm|texttt)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


ENV_RE = re.compile(r"\\begin\s*\{(figure\*?|table\*?)\}(.*?)\\end\s*\{\1\}", re.DOTALL | re.IGNORECASE)
GRAPHICS_RE = re.compile(r"\\includegraphics\s*(?:\[[^]]*\]\s*)?\{([^{}]+)\}", re.IGNORECASE)


def _asset_catalog(source_dir: Path) -> List[Path]:
    return sorted(p for p in source_dir.rglob("*") if p.is_file() and p.suffix.lower() in ASSET_EXTENSIONS)


def _resolve_graphic(raw_name: str, assets: Sequence[Path], source_dir: Path) -> Tuple[Optional[Path], bool]:
    normalized = raw_name.strip().replace("\\", "/")
    raw_path = Path(normalized)
    exact = []
    for asset in assets:
        relative = asset.relative_to(source_dir)
        without_suffix = relative.with_suffix("").as_posix()
        if relative.as_posix() == normalized or without_suffix == normalized:
            exact.append(asset)
    candidates = exact or [asset for asset in assets if asset.stem == raw_path.stem]
    candidates = sorted(set(candidates), key=lambda p: (len(p.parts), p.as_posix()))
    return (candidates[0] if candidates else None), len(candidates) > 1


def _copy_or_convert_asset(source: Path, asset_dir: Path, index: int) -> Tuple[str, str, Optional[str]]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_slug(f"figure_{index:03d}_{source.stem}")
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        target = asset_dir / (stem + ".png")
        try:
            import fitz  # type: ignore

            document = fitz.open(str(source))
            if document.page_count < 1:
                raise ValueError("empty PDF")
            page = document.load_page(0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            pixmap.save(str(target))
            document.close()
            return target.name, "pdf_to_png", None
        except Exception as exc:  # optional best-effort conversion
            target = asset_dir / (stem + suffix)
            shutil.copy2(source, target)
            return target.name, "copied_pdf", f"PDF-to-PNG conversion unavailable: {exc}"
    target = asset_dir / (stem + suffix)
    shutil.copy2(source, target)
    mode = "copied_renderable" if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg"} else "copied_non_renderable"
    warning = None if mode == "copied_renderable" else f"{suffix} may require conversion before Markdown embedding"
    return target.name, mode, warning


def _table_preview(block: str) -> str:
    tabular = re.search(r"\\begin\s*\{tabular\*?\}(?:\{[^{}]*\}){1,2}(.*?)\\end\s*\{tabular\*?\}", block, re.DOTALL)
    if not tabular:
        return "No simple tabular environment could be converted; read the raw .tex artifact.\n"
    content = re.sub(r"\\(?:toprule|midrule|bottomrule|hline|cline\{[^{}]*\})", "", tabular.group(1))
    rows = re.split(r"(?<!\\)\\\\(?:\s*\[[^]]*\])?", content)
    parsed_rows: List[List[str]] = []
    for row in rows:
        cells = [_clean_inline_tex(cell) for cell in re.split(r"(?<!\\)&", row)]
        cells = [re.sub(r"\\(?:multicolumn|multirow)\s*\{[^{}]*\}\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", cell) for cell in cells]
        if any(cells):
            parsed_rows.append(cells)
    if not parsed_rows:
        return "The tabular environment was found but no rows were converted; read the raw .tex artifact.\n"
    width = max(len(row) for row in parsed_rows)
    normalized_rows = [row + [""] * (width - len(row)) for row in parsed_rows]
    output = ["| " + " | ".join(normalized_rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    output.extend("| " + " | ".join(row) + " |" for row in normalized_rows[1:])
    return "\n".join(output) + "\n"


def _write_indexes(expanded: str, source_dir: Path, output_dir: Path, main_tex: Path) -> Dict[str, Any]:
    sections_dir = output_dir / "sections"
    tables_dir = output_dir / "tables"
    assets_dir = output_dir / "assets"
    sections_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    body = _document_body(expanded)
    body_offset = expanded.find(body)
    body_line_offset = expanded[:max(0, body_offset)].count("\n")
    records = _section_records(body)
    section_items: List[Dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        slug = _safe_slug(record["title"]) or f"section_{index}"
        tex_name = f"{index:03d}_{slug}.tex"
        md_name = f"{index:03d}_{slug}.md"
        chunk = body[record["start"]:record["end"]].strip() + "\n"
        (sections_dir / tex_name).write_text(chunk, encoding="utf-8")
        (sections_dir / md_name).write_text(_readable_markdown(chunk), encoding="utf-8")
        section_items.append({
            "index": index,
            "level": record["level"],
            "title": record["title"],
            "tex_path": f"sections/{tex_name}",
            "markdown_path": f"sections/{md_name}",
            "expanded_line_start": body_line_offset + body[:record["start"]].count("\n") + 1,
            "expanded_line_end": body_line_offset + body[:record["end"]].count("\n") + 1,
        })

    section_index = {
        "main_tex": main_tex.relative_to(source_dir).as_posix(),
        "count": len(section_items),
        "items": section_items,
    }
    (output_dir / "section_index.json").write_text(json.dumps(section_index, ensure_ascii=False, indent=2), encoding="utf-8")
    section_md = ["# Section index", ""]
    section_md.extend(
        f"{'  ' * max(0, item['level'] - 1)}- [{item['title']}]({item['markdown_path']}) (`{item['tex_path']}`)"
        for item in section_items
    )
    (output_dir / "section_index.md").write_text("\n".join(section_md) + "\n", encoding="utf-8")

    asset_files = _asset_catalog(source_dir)
    evidence_items: List[Dict[str, Any]] = []
    figure_count = 0
    table_count = 0
    asset_count = 0
    warnings: List[str] = []
    for match in ENV_RE.finditer(body):
        env = match.group(1).lower()
        block = match.group(0)
        caption = (_command_values(block, "caption") or [""])[0]
        label = (_command_values(block, "label") or [""])[0]
        line_start = body_line_offset + body[:match.start()].count("\n") + 1
        if env.startswith("figure"):
            figure_count += 1
            graphics = []
            for raw_graphic in GRAPHICS_RE.findall(block):
                resolved, ambiguous = _resolve_graphic(raw_graphic, asset_files, source_dir)
                graphic: Dict[str, Any] = {"declared_path": raw_graphic, "resolved_source_path": None, "asset_path": None}
                if resolved:
                    asset_count += 1
                    copied, mode, warning = _copy_or_convert_asset(resolved, assets_dir, asset_count)
                    graphic.update({
                        "resolved_source_path": resolved.relative_to(source_dir).as_posix(),
                        "asset_path": f"assets/{copied}",
                        "export_mode": mode,
                        "ambiguous_match": ambiguous,
                    })
                    if warning:
                        warnings.append(f"{raw_graphic}: {warning}")
                else:
                    warnings.append(f"Unresolved figure asset: {raw_graphic}")
                graphics.append(graphic)
            evidence_items.append({
                "kind": "figure",
                "index": figure_count,
                "caption": _clean_inline_tex(caption),
                "label": label,
                "expanded_line_start": line_start,
                "graphics": graphics,
            })
        else:
            table_count += 1
            raw_name = f"table_{table_count:03d}.tex"
            preview_name = f"table_{table_count:03d}.md"
            (tables_dir / raw_name).write_text(block.strip() + "\n", encoding="utf-8")
            preview = (
                f"# Table {table_count}\n\n"
                f"- Caption: {_clean_inline_tex(caption) or 'not found'}\n"
                f"- Label: {label or 'not found'}\n"
                f"- Expanded source line: {line_start}\n"
                f"- Conversion: best effort; raw LaTeX remains authoritative.\n\n"
                + _table_preview(block)
            )
            (tables_dir / preview_name).write_text(preview, encoding="utf-8")
            evidence_items.append({
                "kind": "table",
                "index": table_count,
                "caption": _clean_inline_tex(caption),
                "label": label,
                "expanded_line_start": line_start,
                "raw_tex_path": f"tables/{raw_name}",
                "preview_path": f"tables/{preview_name}",
            })

    evidence_index = {
        "figure_count": figure_count,
        "table_count": table_count,
        "items": evidence_items,
        "warnings": warnings,
    }
    (output_dir / "figure_table_index.json").write_text(json.dumps(evidence_index, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_md = ["# Figure and table index", "", "> LaTeX source is authoritative; converted tables are best effort.", ""]
    for item in evidence_items:
        title = item["caption"] or item["label"] or f"{item['kind']} {item['index']}"
        if item["kind"] == "table":
            evidence_md.append(f"- Table {item['index']}: {title} — [{item['preview_path']}]({item['preview_path']})")
        else:
            assets = [g["asset_path"] for g in item["graphics"] if g.get("asset_path")]
            evidence_md.append(f"- Figure {item['index']}: {title} — assets: {', '.join(assets) or 'unresolved'}")
    (output_dir / "figure_table_index.md").write_text("\n".join(evidence_md) + "\n", encoding="utf-8")
    return {"sections": section_index, "evidence": evidence_index}


def _infer_arxiv_id_from_pdf(path: Path) -> Optional[str]:
    arxiv_id = normalize_arxiv_id(path.name)
    if arxiv_id:
        return arxiv_id
    try:
        import fitz  # type: ignore

        document = fitz.open(str(path))
        text = "\n".join(document.load_page(i).get_text("text") for i in range(min(2, document.page_count)))
        document.close()
        return normalize_arxiv_id(text)
    except Exception:
        return None


def prepare_source(
    reference: str,
    output_dir: Path,
    *,
    force: bool = False,
    timeout: float = 60.0,
    limits: Optional[Limits] = None,
) -> Dict[str, Any]:
    limits = limits or Limits()
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "source_manifest.json"
    if manifest_path.exists() and not force:
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        cached["cache_hit"] = True
        return cached
    if output_dir.exists() and force:
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    local = Path(reference).expanduser()
    if not local.is_absolute():
        candidate = (_workspace_root() / local).resolve()
        if candidate.exists():
            local = candidate
    arxiv_id = normalize_arxiv_id(reference)
    local_source_archive = local.exists() and local.is_file() and local.suffix.lower() != ".pdf"
    if local.exists() and local.is_file() and local.suffix.lower() == ".pdf":
        arxiv_id = arxiv_id or _infer_arxiv_id_from_pdf(local)
        if not arxiv_id:
            raise SourcePipelineError(
                "arxiv_id_not_found",
                "Could not infer an arXiv ID from the local PDF; use the PDF fallback or provide --arxiv-id.",
                fallback_required=True,
            )

    downloads = output_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    payload = downloads / ((_safe_slug(arxiv_id) if arxiv_id else _safe_slug(local.name)) + ".source")
    download_info: Dict[str, Any] = {}
    if arxiv_id and not local_source_archive:
        download_info = _download_source(arxiv_id, payload, timeout, limits)
    elif local.exists() and local.is_file():
        try:
            local.resolve().relative_to(_workspace_root().resolve())
        except ValueError as exc:
            raise SourcePipelineError("unsafe_path", "Local source archives must stay inside the workspace.") from exc
        if local.stat().st_size > limits.max_download_bytes:
            raise SourcePipelineError("source_too_large", "The local source archive exceeds the configured limit.")
        shutil.copy2(local, payload)
        download_info = {"local_source": str(local), "size_bytes": payload.stat().st_size}
    else:
        raise SourcePipelineError("invalid_reference", "Provide an arXiv ID/URL or a workspace-local source archive/PDF.")

    source_dir = output_dir / "source"
    source_mode, extracted, warnings = _extract_payload(payload, source_dir, limits)
    main_tex, main_candidates = _find_main_tex(source_dir)
    expanded = _expand_tex(main_tex, source_dir, [], warnings)
    (output_dir / "expanded.tex").write_text(expanded, encoding="utf-8")

    title = (_command_values(expanded, "title") or [""])[0]
    author = (_command_values(expanded, "author") or [""])[0]
    body = _document_body(expanded)
    paper_md = (
        "# Source-first paper text\n\n"
        f"- arXiv ID: {arxiv_id or 'local source'}\n"
        f"- Main TeX: `{main_tex.relative_to(source_dir).as_posix()}`\n"
        f"- Title: {_clean_inline_tex(title) or 'not parsed'}\n"
        f"- Authors: {_clean_inline_tex(author) or 'not parsed'}\n"
        "- Note: `expanded.tex` is authoritative; this Markdown is a conservative reading view.\n\n"
        + _readable_markdown(body)
    )
    (output_dir / "paper.md").write_text(paper_md, encoding="utf-8")
    indexes = _write_indexes(expanded, source_dir, output_dir, main_tex)

    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "status": "source_ready",
        "cache_hit": False,
        "requested_reference": reference,
        "arxiv_id": arxiv_id,
        "source_mode": source_mode,
        "download": dict(download_info, sha256=_sha256(payload)),
        "main_tex": main_tex.relative_to(source_dir).as_posix(),
        "main_tex_candidates": main_candidates[:20],
        "extracted_file_count": len(extracted),
        "tex_file_count": len(list(source_dir.rglob("*.tex"))),
        "section_count": indexes["sections"]["count"],
        "figure_count": indexes["evidence"]["figure_count"],
        "table_count": indexes["evidence"]["table_count"],
        "warnings": warnings + indexes["evidence"]["warnings"],
        "artifacts": {
            "source_dir": "source",
            "expanded_tex": "expanded.tex",
            "paper_markdown": "paper.md",
            "section_index_json": "section_index.json",
            "section_index_markdown": "section_index.md",
            "figure_table_index_json": "figure_table_index.json",
            "figure_table_index_markdown": "figure_table_index.md",
            "sections_dir": "sections",
            "tables_dir": "tables",
            "assets_dir": "assets",
        },
        "pdf_fallback_required": False,
        "pdf_role": "page-number and final-layout verification only",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_failure_manifest(output_dir: Path, reference: str, error: SourcePipelineError) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "status": "pdf_fallback_required" if error.fallback_required else "source_error",
        "requested_reference": reference,
        "error": {"code": error.code, "message": str(error)},
        "pdf_fallback_required": error.fallback_required,
    }
    (output_dir / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description="Prepare arXiv LaTeX source for read_paper.")
    parser.add_argument("reference", help="arXiv ID/URL, workspace-local source archive, or local PDF with an arXiv ID.")
    parser.add_argument("--arxiv-id", default="", help="Explicit arXiv ID when it cannot be inferred from a local PDF.")
    parser.add_argument("--output-dir", default="", help="Workspace-relative artifact directory.")
    parser.add_argument("--force", action="store_true", help="Replace an existing source preparation directory.")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-download-mb", type=int, default=200)
    parser.add_argument("--max-extracted-mb", type=int, default=1024)
    parser.add_argument("--max-files", type=int, default=20_000)
    args = parser.parse_args()

    reference = args.arxiv_id.strip() or args.reference
    arxiv_id = normalize_arxiv_id(reference) or normalize_arxiv_id(args.reference)
    slug = _safe_slug(arxiv_id or Path(args.reference).stem)
    output_dir = _workspace_path(args.output_dir) if args.output_dir else (_workspace_root() / DEFAULT_SANDBOX_ROOT / slug).resolve()
    limits = Limits(
        max_download_bytes=max(1, args.max_download_mb) * 1024 * 1024,
        max_extracted_bytes=max(1, args.max_extracted_mb) * 1024 * 1024,
        max_files=max(1, args.max_files),
    )
    try:
        manifest = prepare_source(reference, output_dir, force=args.force, timeout=args.timeout, limits=limits)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except SourcePipelineError as error:
        manifest = _write_failure_manifest(output_dir, reference, error)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
