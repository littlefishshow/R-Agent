#!/usr/bin/env python3
"""Structured paper metrics lookup for paper_research_scout.

This script is intentionally skill-local. It is not registered as a global
R-Agent tool; call it with run_command from the paper_research_scout skill.

It prefers official/structured APIs over webpage extraction:
- OpenAlex for cited_by_count, with title validation
- Semantic Scholar Academic Graph for citationCount, with rate-limit hints
- DataCite for arXiv/DataCite DOI metadata and conservative citationCount
- Crossref for DOI metadata and is-referenced-by-count
- Hugging Face Papers API for upvotes/comments/linked artifacts
- GitHub REST API for repository activity/health basics
- OpenReview API2/API1 lightweight forum lookup
- Optional SerpApi Google Scholar when SERPAPI_API_KEY is set

All network calls are best-effort and return structured errors instead of
raising, so scouting reports can mark N/A with reasons.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
from difflib import SequenceMatcher
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

USER_AGENT = "R-Agent paper_research_scout/1.0 (+https://github.com/)"
DEFAULT_TIMEOUT = 15


def utc_date() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def compact_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.strip() or None


def normalize_arxiv(arxiv_id: Optional[str]) -> Optional[str]:
    if not arxiv_id:
        return None
    s = arxiv_id.strip()
    s = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", s, flags=re.I)
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.I)
    return s.strip() or None


def datacite_doi_for_arxiv(arxiv_id: Optional[str]) -> Optional[str]:
    aid = normalize_arxiv(arxiv_id)
    return f"10.48550/arXiv.{aid}" if aid else None


def normalize_title_for_match(title: Optional[str]) -> str:
    if not title:
        return ""
    text = title.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_similarity(expected: Optional[str], observed: Optional[str]) -> float:
    exp = normalize_title_for_match(expected)
    obs = normalize_title_for_match(observed)
    if not exp or not obs:
        return 0.0
    if exp == obs:
        return 1.0
    # Combine order-sensitive SequenceMatcher with token overlap to be robust
    # against subtitles/punctuation while still rejecting unrelated records.
    seq = SequenceMatcher(None, exp, obs).ratio()
    exp_tokens = set(exp.split())
    obs_tokens = set(obs.split())
    overlap = len(exp_tokens & obs_tokens) / max(1, len(exp_tokens | obs_tokens))
    return round(max(seq, overlap), 4)


def _extract_title(data: Dict[str, Any]) -> Optional[str]:
    return data.get("title") or data.get("display_name") or data.get("name")


def annotate_title_validation(call_result: Dict[str, Any], expected_title: Optional[str], *, min_confidence: float = 0.72) -> Dict[str, Any]:
    """Annotate a source result with title match confidence.

    For singleton records, low confidence changes ok=false because citation
    counts from mismatched records are unsafe. For search result lists, each
    candidate is annotated and the call remains successful: the caller can
    inspect best matches.
    """
    if not expected_title or not isinstance(call_result, dict) or not call_result.get("data"):
        return call_result
    data = call_result.get("data")
    if not isinstance(data, dict):
        return call_result

    results = data.get("results")
    if isinstance(results, list):
        best = 0.0
        for item in results:
            if isinstance(item, dict):
                score = title_similarity(expected_title, _extract_title(item))
                item["title_match_confidence"] = score
                item["expected_title"] = expected_title
                best = max(best, score)
        data["best_title_match_confidence"] = best
        return call_result

    observed_title = _extract_title(data)
    if observed_title:
        score = title_similarity(expected_title, observed_title)
        data["title_match_confidence"] = score
        data["expected_title"] = expected_title
        if score < min_confidence:
            call_result["ok"] = False
            call_result["error"] = (
                f"Title mismatch: expected {expected_title!r}, got {observed_title!r}; "
                f"confidence={score:.3f} < {min_confidence:.2f}. Citation metrics discarded."
            )
    return call_result


def request_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        return json.loads(text)


def safe_call(name: str, fn, *args, **kwargs) -> Dict[str, Any]:
    started = time.time()
    try:
        data = fn(*args, **kwargs)
        return {"ok": True, "source": name, "retrieved_at": utc_date(), "elapsed_sec": round(time.time() - started, 3), "data": data}
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        payload = {"ok": False, "source": name, "retrieved_at": utc_date(), "elapsed_sec": round(time.time() - started, 3), "error": f"HTTP {exc.code}: {exc.reason}", "body_excerpt": body}
        if exc.code == 429 and "semantic" in name.lower():
            payload["hint"] = "Semantic Scholar anonymous/shared rate limit hit; set SEMANTIC_SCHOLAR_API_KEY and retry with backoff instead of repeated anonymous calls."
        return payload
    except Exception as exc:
        return {"ok": False, "source": name, "retrieved_at": utc_date(), "elapsed_sec": round(time.time() - started, 3), "error": compact_error(exc)}


def openalex_by_doi(doi: str, mailto: Optional[str] = None) -> Dict[str, Any]:
    doi_url = "https://doi.org/" + normalize_doi(doi)
    params = {"select": "id,doi,title,display_name,cited_by_count,publication_year,updated_date,authorships,primary_location"}
    if mailto:
        params["mailto"] = mailto
    url = "https://api.openalex.org/works/" + urllib.parse.quote(doi_url, safe="") + "?" + urllib.parse.urlencode(params)
    data = request_json(url)
    return {
        "id": data.get("id"),
        "doi": data.get("doi"),
        "title": data.get("display_name") or data.get("title"),
        "year": data.get("publication_year"),
        "cited_by_count": data.get("cited_by_count"),
        "updated_date": data.get("updated_date"),
        "primary_location": data.get("primary_location"),
    }


def openalex_search_title(title: str, mailto: Optional[str] = None) -> Dict[str, Any]:
    params = {
        "search": title,
        "per-page": "3",
        "select": "id,doi,title,display_name,cited_by_count,publication_year,updated_date",
    }
    if mailto:
        params["mailto"] = mailto
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = request_json(url)
    return {"query": title, "results": data.get("results", [])[:3]}


def semantic_scholar_paper(paper_id: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    fields = "title,year,venue,citationCount,influentialCitationCount,externalIds,authors,url,openAccessPdf"
    url = "https://api.semanticscholar.org/graph/v1/paper/" + urllib.parse.quote(paper_id, safe=":/") + "?" + urllib.parse.urlencode({"fields": fields})
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    data = request_json(url, headers=headers)
    return {
        "paperId": data.get("paperId"),
        "title": data.get("title"),
        "year": data.get("year"),
        "venue": data.get("venue"),
        "citationCount": data.get("citationCount"),
        "influentialCitationCount": data.get("influentialCitationCount"),
        "externalIds": data.get("externalIds"),
        "url": data.get("url"),
        "openAccessPdf": data.get("openAccessPdf"),
    }


def semantic_scholar_search(title: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    fields = "title,year,venue,citationCount,influentialCitationCount,externalIds,authors,url"
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode({"query": title, "limit": 3, "fields": fields})
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    data = request_json(url, headers=headers)
    return {"query": title, "results": data.get("data", [])[:3]}


def crossref_by_doi(doi: str, mailto: Optional[str] = None) -> Dict[str, Any]:
    params = {}
    if mailto:
        params["mailto"] = mailto
    url = "https://api.crossref.org/works/" + urllib.parse.quote(normalize_doi(doi), safe="")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = request_json(url)
    msg = data.get("message", {})
    return {
        "DOI": msg.get("DOI"),
        "title": (msg.get("title") or [None])[0],
        "container_title": (msg.get("container-title") or [None])[0],
        "published": msg.get("published-print") or msg.get("published-online") or msg.get("created"),
        "is_referenced_by_count": msg.get("is-referenced-by-count"),
        "type": msg.get("type"),
        "URL": msg.get("URL"),
    }


def datacite_by_doi(doi: str) -> Dict[str, Any]:
    url = "https://api.datacite.org/dois/" + urllib.parse.quote(normalize_doi(doi), safe="/")
    data = request_json(url)
    attrs = data.get("data", {}).get("attributes", {})
    titles = attrs.get("titles") or []
    return {
        "DOI": attrs.get("doi"),
        "title": (titles[0] or {}).get("title") if titles else None,
        "publisher": attrs.get("publisher"),
        "publicationYear": attrs.get("publicationYear"),
        "published": attrs.get("published"),
        "url": attrs.get("url"),
        "citationCount": attrs.get("citationCount"),
        "referenceCount": attrs.get("referenceCount"),
        "viewCount": attrs.get("viewCount"),
        "downloadCount": attrs.get("downloadCount"),
        "identifiers": attrs.get("identifiers"),
        "alternateIdentifiers": attrs.get("alternateIdentifiers"),
    }


def hf_paper(paper_id: str) -> Dict[str, Any]:
    url = "https://huggingface.co/api/papers/" + urllib.parse.quote(paper_id, safe="")
    data = request_json(url)
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "upvotes": data.get("upvotes"),
        "githubStars": data.get("githubStars"),
        "numComments": data.get("numComments"),
        "isAuthorParticipating": data.get("isAuthorParticipating"),
        "numTotalModels": data.get("numTotalModels"),
        "numTotalDatasets": data.get("numTotalDatasets"),
        "numTotalSpaces": data.get("numTotalSpaces"),
        "url": f"https://huggingface.co/papers/{paper_id}",
    }


def hf_search(query: str, limit: int = 5) -> Dict[str, Any]:
    url = "https://huggingface.co/api/papers/search?" + urllib.parse.urlencode({"q": query, "limit": limit})
    return request_json(url)


def hf_daily(date: Optional[str], limit: int = 10, sort: Optional[str] = None, week: Optional[str] = None, month: Optional[str] = None) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": limit}
    if date:
        params["date"] = date
    if sort:
        params["sort"] = sort
    if week:
        params["week"] = week
    if month:
        params["month"] = month
    url = "https://huggingface.co/api/daily_papers?" + urllib.parse.urlencode(params)
    data = request_json(url)
    # Keep response compact.
    rows = []
    for item in data if isinstance(data, list) else data.get("dailyPapers", []):
        paper = item.get("paper", item) if isinstance(item, dict) else {}
        rows.append({
            "id": paper.get("id"),
            "title": paper.get("title"),
            "upvotes": paper.get("upvotes"),
            "githubStars": paper.get("githubStars"),
            "numComments": item.get("numComments") if isinstance(item, dict) else paper.get("numComments"),
            "publishedAt": paper.get("publishedAt") or item.get("publishedAt") if isinstance(item, dict) else None,
        })
    return {"params": params, "papers": rows[:limit]}


def github_repo(owner_repo: str, token: Optional[str] = None) -> Dict[str, Any]:
    owner_repo = owner_repo.strip().removeprefix("https://github.com/").strip("/")
    url = "https://api.github.com/repos/" + owner_repo
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = request_json(url, headers=headers)
    return {
        "full_name": data.get("full_name"),
        "html_url": data.get("html_url"),
        "description": data.get("description"),
        "stargazers_count": data.get("stargazers_count"),
        "forks_count": data.get("forks_count"),
        "subscribers_count": data.get("subscribers_count"),
        "open_issues_count": data.get("open_issues_count"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "pushed_at": data.get("pushed_at"),
        "archived": data.get("archived"),
        "language": data.get("language"),
        "license": data.get("license"),
        "topics": data.get("topics"),
    }


def openreview_forum(forum_id: str) -> Dict[str, Any]:
    # Lightweight no-auth lookup. For venue-wide or private data, use openreview-py.
    outputs = {}
    for base in ("https://api2.openreview.net", "https://api.openreview.net"):
        url = base + "/notes?" + urllib.parse.urlencode({"forum": forum_id})
        try:
            data = request_json(url)
            notes = data.get("notes", [])
            outputs[base] = {"count": len(notes), "notes": notes[:5]}
        except Exception as exc:
            outputs[base] = {"error": compact_error(exc)}
    return outputs


def serpapi_google_scholar(title: str, api_key: str) -> Dict[str, Any]:
    params = {"engine": "google_scholar", "q": title, "api_key": api_key, "num": 3}
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    data = request_json(url)
    rows = []
    for item in data.get("organic_results", [])[:3]:
        rows.append({
            "title": item.get("title"),
            "link": item.get("link"),
            "result_id": item.get("result_id"),
            "cited_by": item.get("inline_links", {}).get("cited_by"),
        })
    return {"query": title, "results": rows}


def build_lookup(args: argparse.Namespace) -> Dict[str, Any]:
    doi = normalize_doi(args.doi)
    arxiv_id = normalize_arxiv(args.arxiv_id)
    title = args.title.strip() if args.title else None
    retrieved_at = utc_date()
    out: Dict[str, Any] = {
        "query": {"title": title, "doi": doi, "arxiv_id": arxiv_id, "github_repo": args.github_repo, "openreview_forum": args.openreview_forum},
        "retrieved_at": retrieved_at,
        "citations": {},
        "hf": {},
        "github": {},
        "openreview": {},
        "notes": [],
    }
    mailto = args.mailto or os.environ.get("OPENALEX_MAILTO") or os.environ.get("CROSSREF_MAILTO")
    s2_key = args.semantic_scholar_api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    gh_token = args.github_token or os.environ.get("GITHUB_TOKEN")
    serp_key = args.serpapi_api_key or os.environ.get("SERPAPI_API_KEY")

    if doi:
        out["citations"]["semantic_scholar"] = annotate_title_validation(
            safe_call("semantic_scholar", semantic_scholar_paper, "DOI:" + doi, s2_key), title
        )
        out["citations"]["datacite"] = annotate_title_validation(
            safe_call("datacite", datacite_by_doi, doi), title
        )
        out["citations"]["openalex"] = annotate_title_validation(
            safe_call("openalex", openalex_by_doi, doi, mailto), title
        )
        out["citations"]["crossref"] = annotate_title_validation(
            safe_call("crossref", crossref_by_doi, doi, mailto), title
        )
    elif arxiv_id:
        out["citations"]["semantic_scholar"] = annotate_title_validation(
            safe_call("semantic_scholar", semantic_scholar_paper, "ARXIV:" + arxiv_id, s2_key), title
        )
        arxiv_doi = datacite_doi_for_arxiv(arxiv_id)
        if arxiv_doi:
            out["citations"]["datacite"] = annotate_title_validation(
                safe_call("datacite", datacite_by_doi, arxiv_doi), title
            )
        if title:
            out["citations"]["openalex_search"] = annotate_title_validation(
                safe_call("openalex_search", openalex_search_title, title, mailto), title
            )
    elif title:
        out["citations"]["openalex_search"] = annotate_title_validation(
            safe_call("openalex_search", openalex_search_title, title, mailto), title
        )
        out["citations"]["semantic_scholar_search"] = annotate_title_validation(
            safe_call("semantic_scholar_search", semantic_scholar_search, title, s2_key), title
        )
    else:
        out["notes"].append("No DOI/arXiv/title provided; citation lookup skipped.")

    if title and serp_key:
        out["citations"]["google_scholar_serpapi"] = safe_call("google_scholar_serpapi", serpapi_google_scholar, title, serp_key)
    elif title:
        out["citations"]["google_scholar"] = {"ok": False, "source": "google_scholar", "retrieved_at": retrieved_at, "error": "No official Google Scholar API; set SERPAPI_API_KEY for optional SerpApi lookup."}

    if arxiv_id:
        out["hf"]["paper"] = safe_call("huggingface_paper", hf_paper, arxiv_id)
    if args.hf_query or title:
        out["hf"]["search"] = safe_call("huggingface_papers_search", hf_search, args.hf_query or title, args.hf_limit)
    if args.hf_date or args.hf_week or args.hf_month:
        out["hf"]["daily"] = safe_call("huggingface_daily_papers", hf_daily, args.hf_date, args.hf_limit, args.hf_sort, args.hf_week, args.hf_month)

    if args.github_repo:
        out["github"][args.github_repo] = safe_call("github", github_repo, args.github_repo, gh_token)

    if args.openreview_forum:
        out["openreview"][args.openreview_forum] = safe_call("openreview", openreview_forum, args.openreview_forum)

    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Skill-local structured paper metrics lookup for paper_research_scout.")
    parser.add_argument("--title", help="Paper title for fuzzy lookup/search.")
    parser.add_argument("--doi", help="DOI or DOI URL.")
    parser.add_argument("--arxiv-id", help="arXiv id or arXiv URL, e.g. 2504.20073.")
    parser.add_argument("--github-repo", help="GitHub repo owner/name or https://github.com/owner/name.")
    parser.add_argument("--openreview-forum", help="OpenReview forum id. For venue-wide/private data use openreview-py manually.")
    parser.add_argument("--hf-query", help="Hugging Face Papers search query. Defaults to title when title is provided.")
    parser.add_argument("--hf-date", help="Daily Papers date YYYY-MM-DD.")
    parser.add_argument("--hf-week", help="Daily Papers week parameter when supported by HF API.")
    parser.add_argument("--hf-month", help="Daily Papers month parameter when supported by HF API.")
    parser.add_argument("--hf-sort", choices=["publishedAt", "trending"], help="HF Daily Papers sort.")
    parser.add_argument("--hf-limit", type=int, default=5, help="HF search/daily limit.")
    parser.add_argument("--mailto", help="Contact email for polite OpenAlex/Crossref requests; env OPENALEX_MAILTO/CROSSREF_MAILTO also supported.")
    parser.add_argument("--semantic-scholar-api-key", help="Semantic Scholar API key; env SEMANTIC_SCHOLAR_API_KEY also supported.")
    parser.add_argument("--github-token", help="GitHub token; env GITHUB_TOKEN also supported.")
    parser.add_argument("--serpapi-api-key", help="SerpApi key for optional Google Scholar lookup; env SERPAPI_API_KEY also supported.")
    parser.add_argument("--output", help="Write JSON output to path instead of stdout only.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    args = parser.parse_args(argv)

    result = build_lookup(args)
    text = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=False)
    if args.output:
        path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
