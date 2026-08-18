from __future__ import annotations

import base64
import hashlib
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _load_pymupdf():
    """Import PyMuPDF through its canonical name, with the legacy alias fallback."""
    try:
        import pymupdf

        return pymupdf
    except Exception as canonical_exc:
        try:
            import fitz

            return fitz
        except Exception as legacy_exc:
            raise RuntimeError(
                "PyMuPDF is not available. Install the Cockpit backend dependencies "
                "with `python3 -m pip install -r requirements.txt`. "
                f"pymupdf import failed: {canonical_exc}; fitz import failed: {legacy_exc}"
            ) from legacy_exc


class FileWorkspace:
    """A small, path-confined file workspace for the learning GUI."""

    def __init__(self, root: str | Path = "outputs"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "papers").mkdir(parents=True, exist_ok=True)
        (self.root / ".gui_cache" / "pdf_pages").mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str = "") -> Path:
        raw = str(relative_path or "").strip().replace("\\", "/")
        if raw.startswith("/"):
            raise ValueError("absolute paths are not allowed")
        candidate = (self.root / raw).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("path escapes workspace")
        return candidate

    def _rel(self, path: Path) -> str:
        if path == self.root:
            return ""
        return path.relative_to(self.root).as_posix()

    def _stat_item(self, path: Path) -> Dict[str, Any]:
        stat = path.stat()
        content_type, _ = mimetypes.guess_type(path.name)
        item_type = "directory" if path.is_dir() else "file"
        return {
            "name": path.name,
            "path": self._rel(path),
            "type": item_type,
            "size": 0 if path.is_dir() else stat.st_size,
            "mtime": stat.st_mtime,
            "content_type": content_type or ("inode/directory" if path.is_dir() else "application/octet-stream"),
            "is_pdf": path.is_file() and path.suffix.lower() == ".pdf",
            "is_markdown": path.is_file() and path.suffix.lower() in {".md", ".markdown"},
        }

    def list_dir(self, relative_path: str = "") -> Dict[str, Any]:
        path = self._resolve(relative_path)
        if not path.exists():
            raise FileNotFoundError(self._rel(path))
        if not path.is_dir():
            raise NotADirectoryError(self._rel(path))
        items: List[Dict[str, Any]] = []
        for child in sorted((p for p in path.iterdir() if not p.name.startswith(".")), key=lambda p: (not p.is_dir(), p.name.lower())):
            items.append(self._stat_item(child))
        parent = "" if path == self.root else self._rel(path.parent)
        return {
            "cwd": self._rel(path),
            "parent": parent,
            "items": items,
            "root_name": "outputs",
        }

    def tree(self, expanded_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        expanded: Set[str] = set(str(path or "").strip().replace("\\", "/") for path in (expanded_paths or []))
        expanded.add("")

        def build(path: Path) -> Dict[str, Any]:
            item = self._stat_item(path)
            rel = self._rel(path)
            if path.is_dir() and rel in expanded:
                item["children"] = [
                    build(child)
                    for child in sorted((p for p in path.iterdir() if not p.name.startswith(".")), key=lambda p: (not p.is_dir(), p.name.lower()))
                ]
            elif path.is_dir():
                item["children"] = []
                try:
                    item["has_children"] = any(path.iterdir())
                except OSError:
                    item["has_children"] = False
            return item

        return {
            "root": {
                "name": "outputs",
                "path": "",
                "type": "directory",
                "children": [
                    build(child)
                    for child in sorted((p for p in self.root.iterdir() if not p.name.startswith(".")), key=lambda p: (not p.is_dir(), p.name.lower()))
                ],
            }
        }

    def create_folder(self, directory: str, name: str) -> Dict[str, Any]:
        safe_name = self._validate_name(name)
        parent = self._resolve(directory)
        if not parent.exists():
            raise FileNotFoundError(self._rel(parent))
        if not parent.is_dir():
            raise NotADirectoryError(self._rel(parent))
        target = parent / safe_name
        target.mkdir()
        return self._stat_item(target)

    def write_base64_file(self, directory: str, name: str, content_base64: str) -> Dict[str, Any]:
        safe_name = self._validate_name(name)
        parent = self._resolve(directory)
        if not parent.exists():
            raise FileNotFoundError(self._rel(parent))
        if not parent.is_dir():
            raise NotADirectoryError(self._rel(parent))
        target = self._dedupe_path(parent / safe_name)
        data = base64.b64decode(str(content_base64 or ""), validate=True)
        target.write_bytes(data)
        return self._stat_item(target)

    def delete(self, relative_path: str) -> Dict[str, Any]:
        path = self._resolve(relative_path)
        if path == self.root:
            raise ValueError("cannot delete workspace root")
        if not path.exists():
            raise FileNotFoundError(self._rel(path))
        deleted = self._rel(path)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return {"deleted": deleted}

    def copy(self, source: str, target_dir: str, name: Optional[str] = None) -> Dict[str, Any]:
        src = self._resolve(source)
        dst_dir = self._resolve(target_dir)
        if src == self.root:
            raise ValueError("cannot copy workspace root")
        if not src.exists():
            raise FileNotFoundError(self._rel(src))
        if not dst_dir.exists():
            raise FileNotFoundError(self._rel(dst_dir))
        if not dst_dir.is_dir():
            raise NotADirectoryError(self._rel(dst_dir))
        if src.is_dir() and (dst_dir == src or src in dst_dir.parents):
            raise ValueError("cannot copy a folder into itself")
        safe_name = self._validate_name(name) if name else src.name
        dst = self._dedupe_path(dst_dir / safe_name)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return self._stat_item(dst)

    def get_file(self, relative_path: str) -> Path:
        path = self._resolve(relative_path)
        if not path.exists():
            raise FileNotFoundError(self._rel(path))
        if not path.is_file():
            raise IsADirectoryError(self._rel(path))
        return path

    def read_text_file(self, relative_path: str) -> Dict[str, Any]:
        path = self.get_file(relative_path)
        if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            raise ValueError("file is not editable text")
        return {
            "path": self._rel(path),
            "name": path.name,
            "content": path.read_text(encoding="utf-8"),
            "item": self._stat_item(path),
        }

    def write_text_file(self, relative_path: str, content: str) -> Dict[str, Any]:
        path = self.get_file(relative_path)
        if path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            raise ValueError("file is not editable text")
        path.write_text(str(content or ""), encoding="utf-8")
        return self._stat_item(path)

    def extract_pdf_text(self, relative_path: str) -> Dict[str, Any]:
        path = self.get_file(relative_path)
        if path.suffix.lower() != ".pdf":
            raise ValueError("file is not a PDF")
        fitz = _load_pymupdf()
        pages = []
        with fitz.open(str(path)) as doc:
            for index, page in enumerate(doc):
                text = page.get_text("text") or ""
                words = []
                for word in page.get_text("words") or []:
                    if len(word) < 5:
                        continue
                    x0, y0, x1, y1, value = word[:5]
                    if not str(value).strip():
                        continue
                    words.append({
                        "x0": float(x0),
                        "y0": float(y0),
                        "x1": float(x1),
                        "y1": float(y1),
                        "text": str(value),
                    })
                lines = []
                raw = page.get_text("dict") or {}
                for block in raw.get("blocks", []):
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        line_text = "".join(str(span.get("text") or "") for span in spans).strip()
                        if not line_text:
                            continue
                        bbox = line.get("bbox")
                        if not bbox and spans:
                            boxes = [span.get("bbox") for span in spans if span.get("bbox")]
                            if boxes:
                                bbox = [
                                    min(box[0] for box in boxes),
                                    min(box[1] for box in boxes),
                                    max(box[2] for box in boxes),
                                    max(box[3] for box in boxes),
                                ]
                        if not bbox:
                            continue
                        font_size = max([float(span.get("size") or 0) for span in spans] or [0])
                        lines.append({
                            "x0": float(bbox[0]),
                            "y0": float(bbox[1]),
                            "x1": float(bbox[2]),
                            "y1": float(bbox[3]),
                            "text": line_text,
                            "font_size": font_size,
                        })
                pages.append({
                    "page": index + 1,
                    "text": text.strip(),
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                    "words": words,
                    "lines": lines,
                })
        return {
            "path": self._rel(path),
            "name": path.name,
            "page_count": len(pages),
            "pages": pages,
        }

    def render_pdf_page_png(self, relative_path: str, page_number: int, *, zoom: float = 1.6) -> bytes:
        path = self.get_file(relative_path)
        if path.suffix.lower() != ".pdf":
            raise ValueError("file is not a PDF")
        fitz = _load_pymupdf()
        stat = path.stat()
        cache_key = hashlib.sha256(
            f"{self._rel(path)}:{stat.st_mtime_ns}:{stat.st_size}:{page_number}:{float(zoom):.2f}".encode("utf-8")
        ).hexdigest()
        cache_path = self.root / ".gui_cache" / "pdf_pages" / f"{cache_key}.png"
        if cache_path.exists():
            return cache_path.read_bytes()
        with fitz.open(str(path)) as doc:
            if page_number < 1 or page_number > len(doc):
                raise ValueError("page is out of range")
            page = doc[page_number - 1]
            matrix = fitz.Matrix(float(zoom), float(zoom))
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            png = pix.tobytes("png")
            cache_path.write_bytes(png)
            return png

    @staticmethod
    def _validate_name(name: str) -> str:
        value = str(name or "").strip()
        if not value:
            raise ValueError("name is required")
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("invalid name")
        return value

    @staticmethod
    def _dedupe_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        candidate = parent / f"{stem} copy {timestamp}{suffix}"
        counter = 2
        while candidate.exists():
            candidate = parent / f"{stem} copy {timestamp}-{counter}{suffix}"
            counter += 1
        return candidate
