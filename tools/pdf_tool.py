"""pdf_read: download a PDF once, cache its extracted text, and page through it.

The first call for a URL downloads the PDF and extracts its full text into
``sandbox/pdf_cache/``. Later calls read straight from that cache, so paging or
jumping around never re-downloads or re-parses. A small per-PDF cursor lets the
model keep calling ``pdf_read(url)`` to read the next chunk without computing
offsets itself; passing an explicit ``offset`` jumps anywhere and moves the
cursor there.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from tools.registry import registry
from tools.web_tools import _extract_pdf, _fetch_bytes, _looks_like_pdf, _normalize_pdf_url

_DEFAULT_READ_CHARS = 12000
_CACHE_DIR = Path("sandbox") / "pdf_cache"
_CACHE_DIR_ENV = "R_AGENT_PDF_CACHE_DIR"


def _cache_dir() -> Path:
    override = os.environ.get(_CACHE_DIR_ENV, "").strip()
    directory = Path(override) if override else _CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cache_key(url_or_path: str) -> str:
    return hashlib.sha256(url_or_path.strip().encode("utf-8")).hexdigest()[:16]


def _json_failure(message: str, **extra: Any) -> str:
    return json.dumps({"success": False, "error": message, **extra}, ensure_ascii=False)


def _load_full_text(url_or_path: str) -> Dict[str, Any]:
    """Return cached PDF text, downloading and extracting it on first use.

    Result dict: {text, page_count, char_count, cached}. Raises on failure.
    """
    directory = _cache_dir()
    key = _cache_key(url_or_path)
    text_path = directory / f"{key}.txt"

    if text_path.exists():
        text = text_path.read_text(encoding="utf-8")
        meta = json.loads((directory / f"{key}.meta.json").read_text(encoding="utf-8"))
        return {"text": text, "page_count": meta.get("page_count", 0), "char_count": len(text), "cached": True}

    # Not cached yet: fetch bytes (from a local file or the network) and parse.
    local = Path(url_or_path)
    if local.is_file():
        data = local.read_bytes()
    else:
        data, content_type = _fetch_bytes(_normalize_pdf_url(url_or_path), timeout=60)
        if not _looks_like_pdf(data, content_type, url_or_path):
            raise ValueError("URL did not return a PDF (magic bytes / content-type check failed)")

    parsed = _extract_pdf(data, max_chars=10**9)  # no preview limit: cache the whole text
    if "error" in parsed:
        raise ValueError(parsed["error"])

    text = parsed.get("content", "")
    text_path.write_text(text, encoding="utf-8")
    (directory / f"{key}.meta.json").write_text(
        json.dumps({"page_count": parsed.get("page_count", 0), "cursor": 0}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"text": text, "page_count": parsed.get("page_count", 0), "char_count": len(text), "cached": False}


def _read_cursor(url_or_path: str) -> int:
    path = _cache_dir() / f"{_cache_key(url_or_path)}.meta.json"
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("cursor", 0))
    except Exception:
        return 0


def _write_cursor(url_or_path: str, cursor: int) -> None:
    path = _cache_dir() / f"{_cache_key(url_or_path)}.meta.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    meta["cursor"] = cursor
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def pdf_read_tool(url_or_path: str, offset: int = -1, max_chars: int = _DEFAULT_READ_CHARS) -> str:
    """Read a chunk of a PDF's text, downloading and caching it on first use."""
    if not isinstance(url_or_path, str) or not url_or_path.strip():
        return _json_failure("url_or_path must be a non-empty string")

    try:
        max_chars = max(1000, min(int(max_chars), 100000))
    except Exception:
        max_chars = _DEFAULT_READ_CHARS

    try:
        doc = _load_full_text(url_or_path)
    except Exception as e:
        return _json_failure(str(e))

    text = doc["text"]
    char_count = doc["char_count"]

    # offset < 0 means "continue from where we left off"; otherwise jump there.
    try:
        offset = int(offset)
    except Exception:
        offset = -1
    start = _read_cursor(url_or_path) if offset < 0 else offset
    start = max(0, min(start, char_count))

    chunk = text[start:start + max_chars]
    end = start + len(chunk)
    _write_cursor(url_or_path, end)

    has_more = end < char_count
    result: Dict[str, Any] = {
        "success": True,
        "content_type": "pdf",
        "page_count": doc["page_count"],
        "char_count": char_count,
        "offset": start,
        "returned_chars": len(chunk),
        "has_more": has_more,
        "cached": doc["cached"],
        "content": chunk,
    }
    if has_more:
        result["next_offset"] = end
    return json.dumps(result, ensure_ascii=False)


registry.register(
    name="pdf_read",
    description=(
        "分段读取 PDF 的文字内容。首次调用会下载并用 pymupdf 解析全文、缓存到本地；"
        "之后的调用直接读缓存，不再重复下载或解析。\n"
        "\n"
        "参数：\n"
        "- url_or_path：PDF 的 URL 或本地文件路径（arxiv.org/abs 链接会自动转成 PDF）。\n"
        "- offset：起始字符位置，默认 -1。\n"
        "- max_chars：本次最多返回多少字符，默认 12000。\n"
        "\n"
        "返回：offset（本次起点）、returned_chars（本次返回字符数）、next_offset（下一段起点，"
        "读到结尾时不返回该字段）、has_more（是否还有后文）、char_count（全文总字符数）、"
        "page_count（总页数）、cached（本次是否命中缓存）、content（本次文字）。\n"
        "\n"
        "翻页方式：\n"
        "1. 不传 offset（或 offset=-1）：从上次读完的位置自动继续；第一次读则从头开始。\n"
        "2. 传具体 offset：跳到该字符位置读取，并把内部游标移到这里，后续自动继续会从此处往后。\n"
        "\n"
        "推荐用法：\n"
        "a. 想顺序往下读：反复调用本工具且不传 offset 即可，会自动接着上次继续，无需自己算位置。\n"
        "b. 想跳到某处读：把 offset 设为目标字符位置（可参考 char_count 判断大致进度）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url_or_path": {
                "type": "string",
                "description": "PDF 的 URL，或已下载的本地文件路径（arxiv.org/abs 链接会自动转 PDF）",
            },
            "offset": {
                "type": "integer",
                "description": "起始字符位置。省略或 -1 表示从上次读完处自动继续；给具体值表示跳读到该位置",
                "default": -1,
            },
            "max_chars": {
                "type": "integer",
                "description": "本次返回的最大字符数 (默认 12000)",
                "default": _DEFAULT_READ_CHARS,
            },
        },
        "required": ["url_or_path"],
    },
    handler=pdf_read_tool,
)
