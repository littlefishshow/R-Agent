"""Tests for the pdf_read tool: caching, paging, auto-cursor, and jumping.

Cache-behavior tests build a tiny PDF and point the cache at a tmp dir, so they
are fast and offline. One end-to-end test reads the real NeurIPS AUF paper.
"""

import json

import pytest

from tools import pdf_tool


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(pdf_tool._CACHE_DIR_ENV, str(tmp_path))
    return tmp_path


def _make_pdf(text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    # insert_textbox wraps a long paragraph within the page, so the extracted
    # text is comfortably longer than one read chunk.
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(36, 36, 560, 780), text, fontsize=8)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def local_pdf(tmp_path):
    # A local .pdf file avoids network; pdf_read reads local paths directly.
    # Body is long enough (>2500 chars) to exercise paging at the 1000-char floor.
    body = " ".join(f"word{i:04d}" for i in range(400))
    path = tmp_path / "sample.pdf"
    path.write_bytes(_make_pdf(body))
    return str(path)


def _full_text(cache_dir) -> str:
    # Read the cached extracted text directly, so we don't move the read cursor.
    return next(cache_dir.glob("*.txt")).read_text(encoding="utf-8")


def test_first_read_parses_and_caches(cache_dir, local_pdf):
    result = json.loads(pdf_tool.pdf_read_tool(local_pdf, offset=0, max_chars=1000))

    assert result["success"] is True
    assert result["cached"] is False          # first read had to parse
    assert result["offset"] == 0
    assert result["returned_chars"] == 1000    # bounded to max_chars
    assert result["has_more"] is True
    assert result["next_offset"] == 1000

    again = json.loads(pdf_tool.pdf_read_tool(local_pdf, offset=0, max_chars=1000))
    assert again["cached"] is True            # second read hit the cache


def test_omitting_offset_continues_from_last_position(cache_dir, local_pdf):
    first = json.loads(pdf_tool.pdf_read_tool(local_pdf, max_chars=1000))
    second = json.loads(pdf_tool.pdf_read_tool(local_pdf, max_chars=1000))

    full = _full_text(cache_dir)
    assert first["offset"] == 0
    assert second["offset"] == 1000           # auto-continued, no offset computed
    assert first["content"] + second["content"] == full[:2000]


def test_explicit_offset_jumps_and_moves_cursor(cache_dir, local_pdf):
    jumped = json.loads(pdf_tool.pdf_read_tool(local_pdf, offset=100, max_chars=1000))
    full = _full_text(cache_dir)
    assert jumped["offset"] == 100
    assert jumped["content"] == full[100:1100]

    # Cursor now sits after the jump, so the next auto-read continues from there.
    following = json.loads(pdf_tool.pdf_read_tool(local_pdf, max_chars=1000))
    assert following["offset"] == 1100


def test_reading_to_end_sets_has_more_false(cache_dir, local_pdf):
    result = json.loads(pdf_tool.pdf_read_tool(local_pdf, offset=0, max_chars=100000))
    assert result["has_more"] is False
    assert "next_offset" not in result


def test_invalid_input_returns_error(cache_dir):
    result = json.loads(pdf_tool.pdf_read_tool(""))
    assert result["success"] is False


def test_reads_real_auf_paper(cache_dir):
    url = "https://proceedings.neurips.cc/paper_files/paper/2023/file/fed1ea8dcc2a13f3835cc854e8c8294c-Paper-Conference.pdf"

    first = json.loads(pdf_tool.pdf_read_tool(url, max_chars=2000))
    assert first["content_type"] == "pdf"
    assert "Rehearsal Learning for Avoiding Undesired Future" in first["content"]
    assert first["char_count"] > 2000 and first["has_more"] is True

    # Next call auto-continues from the cache without re-downloading.
    second = json.loads(pdf_tool.pdf_read_tool(url, max_chars=2000))
    assert second["cached"] is True
    assert second["offset"] == 2000
