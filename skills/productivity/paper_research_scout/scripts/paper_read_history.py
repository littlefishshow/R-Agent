#!/usr/bin/env python3
"""Skill-local read-paper history registry for paper_research_scout.

This script is intentionally local to paper_research_scout. It records papers
that have already been read (usually by the read_paper skill) and lets paper
scouting filter them out before presenting recommendations.

Storage default:
  skills/productivity/paper_research_scout/references/read_papers.json

Commands:
  add             Add/update one read paper record.
  check           Check whether a candidate appears already read.
  import-outputs  Import existing PDFs from outputs/papers/.
  list            List records.
  filter          Filter a JSON candidate list, removing read papers.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_HISTORY = Path(__file__).resolve().parents[1] / "references" / "read_papers.json"
ARXIV_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", re.I)
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def norm_arxiv(value: Optional[str]) -> str:
    if not value:
        return ""
    s = str(value).strip()
    s = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", s, flags=re.I)
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.I)
    m = ARXIV_RE.search(s)
    return m.group(1) if m else s.strip()


def norm_doi(value: Optional[str]) -> str:
    if not value:
        return ""
    s = str(value).strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = DOI_RE.search(s)
    return (m.group(0) if m else s).lower().strip()


def title_from_stem(stem: str) -> str:
    s = stem
    # Drop leading date prefixes such as 2026-07-09_ or 20260706_
    s = re.sub(r"^\d{4}-\d{2}-\d{2}[_ -]+", "", s)
    s = re.sub(r"^\d{8}[_ -]+", "", s)
    arxiv_only = ARXIV_RE.fullmatch(s)
    if arxiv_only:
        return arxiv_only.group(1)
    s = re.sub(r"^\d{4}\.\d{4,5}[_ -]*", "", s)
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or extract_arxiv(stem) or stem


def norm_title(title: Optional[str]) -> str:
    if not title:
        return ""
    s = str(title).lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_arxiv(*values: Optional[str]) -> str:
    for value in values:
        if not value:
            continue
        m = ARXIV_RE.search(str(value))
        if m:
            return m.group(1)
    return ""


def canonical_url(url: Optional[str], arxiv_id: Optional[str] = None, doi: Optional[str] = None) -> str:
    if url:
        return str(url).strip()
    aid = norm_arxiv(arxiv_id)
    if aid:
        return f"https://arxiv.org/abs/{aid}"
    d = norm_doi(doi)
    if d:
        return f"https://doi.org/{d}"
    return ""


def load_history(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": now_iso(), "records": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "records" not in data or not isinstance(data["records"], list):
        raise ValueError(f"Invalid history file: {path}")
    return data


def save_history(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def make_record(*, title: Optional[str], path: Optional[str], url: Optional[str], doi: Optional[str], arxiv_id: Optional[str], category: Optional[str], source: str, notes: Optional[str]) -> Dict[str, Any]:
    inferred_arxiv = norm_arxiv(arxiv_id) or extract_arxiv(url, doi, path, title)
    inferred_doi = norm_doi(doi)
    if not inferred_doi and inferred_arxiv:
        inferred_doi = f"10.48550/arxiv.{inferred_arxiv}".lower()
    final_title = title.strip() if title else ""
    if not final_title and path:
        final_title = title_from_stem(Path(path).stem)
    final_url = canonical_url(url, inferred_arxiv, inferred_doi)
    return {
        "title": final_title,
        "normalized_title": norm_title(final_title),
        "arxiv_id": inferred_arxiv,
        "doi": inferred_doi,
        "url": final_url,
        "local_path": path or "",
        "category": category or "",
        "source": source,
        "notes": notes or "",
        "read_at": now_iso(),
        "updated_at": now_iso(),
    }


def record_keys(record: Dict[str, Any]) -> List[Tuple[str, str]]:
    keys: List[Tuple[str, str]] = []
    if record.get("arxiv_id"):
        keys.append(("arxiv_id", norm_arxiv(record.get("arxiv_id"))))
    if record.get("doi"):
        keys.append(("doi", norm_doi(record.get("doi"))))
    if record.get("url"):
        keys.append(("url", str(record.get("url")).strip().lower()))
    if record.get("normalized_title"):
        keys.append(("normalized_title", record.get("normalized_title")))
    return keys


def candidate_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return make_record(
        title=getattr(args, "title", None),
        path=getattr(args, "path", None),
        url=getattr(args, "url", None),
        doi=getattr(args, "doi", None),
        arxiv_id=getattr(args, "arxiv_id", None),
        category=getattr(args, "category", None),
        source=getattr(args, "source", "manual") or "manual",
        notes=getattr(args, "notes", None),
    )


def match_record(candidate: Dict[str, Any], record: Dict[str, Any]) -> Tuple[bool, str]:
    c = dict(candidate)
    c["normalized_title"] = c.get("normalized_title") or norm_title(c.get("title"))
    for key, value in record_keys(c):
        if not value:
            continue
        if key == "arxiv_id" and value and value == norm_arxiv(record.get("arxiv_id")):
            return True, f"arxiv_id:{value}"
        if key == "doi" and value and value == norm_doi(record.get("doi")):
            return True, f"doi:{value}"
        if key == "url" and value and value == str(record.get("url", "")).strip().lower():
            return True, f"url:{value}"
        if key == "normalized_title" and value and value == record.get("normalized_title"):
            return True, f"title:{value}"
    return False, ""


def upsert_record(data: Dict[str, Any], new_record: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    records = data.setdefault("records", [])
    for rec in records:
        ok, reason = match_record(new_record, rec)
        if ok:
            # Preserve original read_at but fill missing fields and update paths/notes.
            original_read_at = rec.get("read_at")
            for k, v in new_record.items():
                should_update_title = (
                    k == "title"
                    and v
                    and rec.get("title")
                    and re.match(r"^\d{4}-\d{2}-\d{2}[_ -]+\d{4}\.\d{4,5}$", str(rec.get("title")))
                )
                if v and (not rec.get(k) or k in {"local_path", "url", "category", "notes", "source"} or should_update_title):
                    rec[k] = v
                    if k == "title":
                        rec["normalized_title"] = norm_title(v)
            rec["read_at"] = original_read_at or new_record.get("read_at")
            rec["updated_at"] = now_iso()
            rec.setdefault("aliases", [])
            if reason not in rec["aliases"]:
                rec["aliases"].append(reason)
            return "updated", rec
    records.append(new_record)
    return "added", new_record


def import_outputs(args: argparse.Namespace) -> Dict[str, Any]:
    history_path = Path(args.history)
    data = load_history(history_path)
    papers_dir = Path(args.papers_dir)
    pdfs = sorted(p for p in papers_dir.rglob("*.pdf") if p.is_file())
    added = updated = 0
    records = []
    for pdf in pdfs:
        try:
            rel = pdf.relative_to(papers_dir)
            category = rel.parts[0] if len(rel.parts) > 1 else ""
        except Exception:
            category = ""
        rec = make_record(
            title=title_from_stem(pdf.stem),
            path=str(pdf),
            url=None,
            doi=None,
            arxiv_id=extract_arxiv(pdf.name),
            category=category,
            source="import_outputs",
            notes="Imported from existing outputs/papers PDF inventory.",
        )
        status, stored = upsert_record(data, rec)
        if status == "added":
            added += 1
        else:
            updated += 1
        records.append({"status": status, "title": stored.get("title"), "path": stored.get("local_path"), "arxiv_id": stored.get("arxiv_id"), "doi": stored.get("doi")})
    save_history(history_path, data)
    return {"history": str(history_path), "papers_dir": str(papers_dir), "pdf_count": len(pdfs), "added": added, "updated": updated, "records": records}


def cmd_add(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.history)
    data = load_history(path)
    rec = candidate_from_args(args)
    status, stored = upsert_record(data, rec)
    save_history(path, data)
    return {"status": status, "history": str(path), "record": stored}


def cmd_check(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.history)
    data = load_history(path)
    cand = candidate_from_args(args)
    matches = []
    for rec in data.get("records", []):
        ok, reason = match_record(cand, rec)
        if ok:
            matches.append({"reason": reason, "record": rec})
    return {"already_read": bool(matches), "match_count": len(matches), "matches": matches, "candidate": cand, "history": str(path)}


def cmd_list(args: argparse.Namespace) -> Dict[str, Any]:
    path = Path(args.history)
    data = load_history(path)
    records = data.get("records", [])
    if args.category:
        records = [r for r in records if r.get("category") == args.category]
    return {"history": str(path), "count": len(records), "records": records[: args.limit if args.limit else None]}


def candidate_from_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    return make_record(
        title=item.get("title") or item.get("paper") or item.get("name"),
        path=item.get("path") or item.get("local_path") or item.get("pdf_path"),
        url=item.get("url") or item.get("link") or item.get("paper_url"),
        doi=item.get("doi") or item.get("DOI"),
        arxiv_id=item.get("arxiv_id") or item.get("arxiv") or extract_arxiv(item.get("url"), item.get("title")),
        category=item.get("category"),
        source="candidate",
        notes=None,
    )


def cmd_filter(args: argparse.Namespace) -> Dict[str, Any]:
    history = load_history(Path(args.history))
    with open(args.input, "r", encoding="utf-8") as f:
        payload = json.load(f)
    candidates = payload.get("candidates", payload if isinstance(payload, list) else [])
    kept = []
    removed = []
    for item in candidates:
        cand = candidate_from_dict(item)
        match = None
        for rec in history.get("records", []):
            ok, reason = match_record(cand, rec)
            if ok:
                match = {"reason": reason, "record": rec}
                break
        if match:
            removed.append({"candidate": item, "match": match})
        else:
            kept.append(item)
    result = {"input": args.input, "history": args.history, "kept_count": len(kept), "removed_count": len(removed), "kept": kept, "removed_already_read": removed}
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return result


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-paper history registry for paper_research_scout.")
    parser.add_argument("--history", default=str(DEFAULT_HISTORY), help="History JSON path.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--title")
        sp.add_argument("--url")
        sp.add_argument("--doi")
        sp.add_argument("--arxiv-id")
        sp.add_argument("--path")
        sp.add_argument("--category")
        sp.add_argument("--source", default="manual")
        sp.add_argument("--notes")

    sp = sub.add_parser("add", help="Add/update one read paper record.")
    add_common(sp)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("check", help="Check whether a candidate has already been read.")
    add_common(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("import-outputs", help="Import existing PDF inventory from outputs/papers.")
    sp.add_argument("--papers-dir", default="outputs/papers")
    sp.set_defaults(func=import_outputs)

    sp = sub.add_parser("list", help="List read paper records.")
    sp.add_argument("--category")
    sp.add_argument("--limit", type=int, default=0)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("filter", help="Filter a JSON candidate list, removing already-read papers.")
    sp.add_argument("--input", required=True, help="JSON list or {'candidates': [...]} file.")
    sp.add_argument("--output", help="Optional output JSON path.")
    sp.set_defaults(func=cmd_filter)

    args = parser.parse_args(argv)
    result = args.func(args)
    print_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
