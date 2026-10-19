import gzip
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "productivity" / "read_paper" / "scripts" / "arxiv_source.py"
SPEC = importlib.util.spec_from_file_location("read_paper_arxiv_source", SCRIPT)
assert SPEC and SPEC.loader
arxiv_source = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = arxiv_source
SPEC.loader.exec_module(arxiv_source)


def _write_tar(path: Path, files):
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            payload = content if isinstance(content, bytes) else content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_normalize_arxiv_id_accepts_current_legacy_and_urls():
    assert arxiv_source.normalize_arxiv_id("arXiv:1706.03762v7") == "1706.03762v7"
    assert arxiv_source.normalize_arxiv_id("https://arxiv.org/pdf/2501.01234.pdf") == "2501.01234"
    assert arxiv_source.normalize_arxiv_id("https://arxiv.org/abs/hep-th/9901001v2") == "hep-th/9901001v2"
    assert arxiv_source.normalize_arxiv_id("not an arxiv paper") is None


def test_source_pipeline_extracts_sections_tables_and_original_figures(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # A local archive whose filename contains an arXiv ID must remain local;
    # the pipeline must not silently redownload it.
    archive = workspace / "1706.03762.tar.gz"
    # Minimal valid PNG. The parser copies it without image decoding.
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    main = r"""
\documentclass{article}
\title{A Source First Paper}
\author{Ada Researcher}
\begin{document}
\maketitle
\begin{abstract}A structured abstract.\end{abstract}
\section{Introduction}
We test source extraction. See Figure~\ref{fig:plot}.
\input{sections/method}
\begin{figure}
  \includegraphics[width=0.5\linewidth]{figs/plot}
  \caption{An original source figure.}
  \label{fig:plot}
\end{figure}
\begin{table}
  \caption{Main result.}
  \label{tab:main}
  \begin{tabular}{lc}
  Method & Score \\
  \midrule
  Ours & 42.0 \\
  \end{tabular}
\end{table}
\end{document}
"""
    method = r"""
\section{Method}
The objective is
\begin{equation}
L(\theta)=\sum_i x_i.
\end{equation}
\subsection{Training}
Training details remain structured.
"""
    _write_tar(archive, {"main.tex": main, "sections/method.tex": method, "figs/plot.png": png})
    monkeypatch.setattr(arxiv_source, "_workspace_root", lambda: workspace)

    output = workspace / "sandbox" / "read_paper" / "fixture"
    manifest = arxiv_source.prepare_source(str(archive), output, force=True)

    assert manifest["status"] == "source_ready"
    assert manifest["download"]["local_source"] == str(archive)
    assert manifest["main_tex"] == "main.tex"
    assert manifest["section_count"] == 3
    assert manifest["figure_count"] == 1
    assert manifest["table_count"] == 1
    assert "Training details remain structured" in (output / "expanded.tex").read_text()
    assert (output / "tables" / "table_001.tex").exists()
    assert "42.0" in (output / "tables" / "table_001.md").read_text()

    evidence = json.loads((output / "figure_table_index.json").read_text())
    figure = next(item for item in evidence["items"] if item["kind"] == "figure")
    assert figure["graphics"][0]["resolved_source_path"] == "figs/plot.png"
    assert (output / figure["graphics"][0]["asset_path"]).exists()

    cached = arxiv_source.prepare_source(str(archive), output)
    assert cached["cache_hit"] is True


def test_extracts_single_gzipped_tex(tmp_path):
    payload = tmp_path / "single.source"
    payload.write_bytes(gzip.compress(b"\\documentclass{article}\n\\begin{document}Hi\\end{document}\n"))
    source_dir = tmp_path / "source"

    mode, extracted, warnings = arxiv_source._extract_payload(payload, source_dir, arxiv_source.Limits())

    assert mode == "gzip_tex"
    assert extracted == ["main.tex"]
    assert warnings == []
    assert "begin{document}" in (source_dir / "main.tex").read_text()


def test_safe_tar_extraction_rejects_path_traversal(tmp_path):
    payload = tmp_path / "unsafe.tar.gz"
    _write_tar(payload, {"../escape.tex": "\\documentclass{article}"})

    with pytest.raises(arxiv_source.SourcePipelineError) as exc:
        arxiv_source._extract_payload(payload, tmp_path / "source", arxiv_source.Limits())

    assert exc.value.code == "unsafe_archive"
    assert not (tmp_path / "escape.tex").exists()


def test_archive_links_are_skipped(tmp_path):
    payload = tmp_path / "links.tar.gz"
    with tarfile.open(payload, "w:gz") as archive:
        tex = b"\\documentclass{article}\n\\begin{document}Hi\\end{document}\n"
        main = tarfile.TarInfo("main.tex")
        main.size = len(tex)
        archive.addfile(main, io.BytesIO(tex))
        link = tarfile.TarInfo("outside")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        archive.addfile(link)

    mode, extracted, warnings = arxiv_source._extract_payload(
        payload, tmp_path / "source", arxiv_source.Limits()
    )

    assert mode == "tar"
    assert extracted == ["main.tex"]
    assert any("Skipped archive link" in warning for warning in warnings)
    assert not (tmp_path / "source" / "outside").exists()


def test_pdf_payload_requests_explicit_fallback(tmp_path):
    payload = tmp_path / "paper.source"
    payload.write_bytes(b"%PDF-1.7\nnot-a-real-pdf")

    with pytest.raises(arxiv_source.SourcePipelineError) as exc:
        arxiv_source._extract_payload(payload, tmp_path / "source", arxiv_source.Limits())

    assert exc.value.code == "pdf_only_submission"
    assert exc.value.fallback_required is True


def test_strip_tex_comments_preserves_escaped_percent():
    source = "value is 90\\% complete % remove this\nnext line\n"
    assert arxiv_source.strip_tex_comments(source) == "value is 90\\% complete\nnext line\n"

