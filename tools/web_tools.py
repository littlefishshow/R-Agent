import html as html_lib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from tools.registry import registry


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


# Important: use an opener with an explicit empty ProxyHandler.
# On macOS, urllib's default opener may call the system proxy resolver
# (_scproxy / CoreFoundation).  When tools run in a forked child process from
# the Agent runtime, that resolver can terminate the child before Python can
# return an exception, which surfaces as: "Tool process ended without returning
# a result."  Disabling implicit OS proxy discovery keeps web tools fork-safe.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_DEFAULT_LOCAL_HTML_ORDER = "bing,yahoo,duckduckgo"
_DEFAULT_LOCAL_HTML_TIMEOUT_SECONDS = 5
_DEFAULT_API_TIMEOUT_SECONDS = 20


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 120) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, min(int(raw), maximum))
    except ValueError:
        return default


def _split_provider_list(raw: str) -> List[str]:
    return [
        item.strip().lower().replace("-", "_")
        for item in re.split(r"[,\s]+", raw or "")
        if item.strip()
    ]


def _json_success(payload: Dict[str, Any]) -> str:
    return json.dumps({"success": True, **payload}, ensure_ascii=False)


def _json_failure(message: str, **extra: Any) -> str:
    return json.dumps({"success": False, "error": message, **extra}, ensure_ascii=False)


def _fetch_text(url: str, timeout: int = 10) -> str:
    # Prefer curl when available. The chat/runtime tool wrapper already runs
    # handlers in a daemonized forked process; using curl moves DNS/TLS/proxy
    # resolution out of that Python child and avoids macOS framework crashes.
    curl = shutil.which("curl")
    if curl:
        completed = subprocess.run(
            [
                curl,
                "-L",
                "--silent",
                "--show-error",
                "--max-time",
                str(timeout),
                "-A",
                _USER_AGENT,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.decode("utf-8", errors="replace")
        # Fall through to urllib once; this preserves functionality in unusual
        # curl failures while still returning a normal JSON error if both fail.
        curl_error = completed.stderr.decode("utf-8", errors="replace").strip()
    else:
        curl_error = "curl not found"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with _OPENER.open(req, timeout=timeout) as response:
            raw = response.read()
            charset = None
            headers = getattr(response, "headers", None)
            if headers is not None:
                get_charset = getattr(headers, "get_content_charset", None)
                if callable(get_charset):
                    charset = get_charset()
            return raw.decode(charset or "utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(f"curl failed: {curl_error}; urllib failed: {exc}") from exc


def _fetch_bytes(url: str, timeout: int = 10) -> tuple[bytes, str]:
    """Fetch raw bytes and content-type without decoding.

    Needed for binary payloads such as PDFs, where ``_fetch_text``'s
    ``decode(errors="replace")`` would corrupt the content. Keeps the same
    curl-first / urllib-fallback strategy as ``_fetch_text`` to stay fork-safe
    on macOS.
    """
    curl = shutil.which("curl")
    if curl:
        completed = subprocess.run(
            [
                curl,
                "-L",
                "--silent",
                "--show-error",
                "--max-time",
                str(timeout),
                "-A",
                _USER_AGENT,
                "-w",
                "\n%{content_type}",
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode == 0:
            out = completed.stdout
            # The -w content_type is appended after a trailing newline.
            newline = out.rfind(b"\n")
            if newline >= 0:
                content_type = out[newline + 1:].decode("utf-8", errors="replace").strip()
                body = out[:newline]
            else:
                content_type = ""
                body = out
            return body, content_type
        curl_error = completed.stderr.decode("utf-8", errors="replace").strip()
    else:
        curl_error = "curl not found"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with _OPENER.open(req, timeout=timeout) as response:
            raw = response.read()
            content_type = ""
            headers = getattr(response, "headers", None)
            if headers is not None:
                get_content_type = getattr(headers, "get_content_type", None)
                if callable(get_content_type):
                    content_type = get_content_type() or ""
            return raw, content_type
    except Exception as exc:
        raise RuntimeError(f"curl failed: {curl_error}; urllib failed: {exc}") from exc


def _looks_like_pdf(data: bytes, content_type: str, url: str) -> bool:
    if data[:5] == b"%PDF-":
        return True
    if "application/pdf" in (content_type or "").lower():
        return True
    return False


def _normalize_pdf_url(url: str) -> str:
    """Rewrite arxiv abstract links to their PDF endpoint.

    ``arxiv.org/abs/<id>`` serves HTML; ``arxiv.org/pdf/<id>`` serves the PDF.
    Other URLs are returned unchanged.
    """
    match = re.match(r"(?i)^(https?://(?:www\.)?arxiv\.org)/abs/(.+?)/?$", url.strip())
    if match:
        return f"{match.group(1)}/pdf/{match.group(2)}"
    return url


def _extract_pdf(data: bytes, max_chars: int) -> Dict[str, Any]:
    """Extract text + light metadata from PDF bytes using pymupdf.

    Returns a dict with content_type=pdf, a bounded ``content`` preview, and
    ``char_count`` / ``page_count`` / ``truncated`` so the model can decide
    whether the preview is enough or a deeper read is needed.
    """
    try:
        import pymupdf  # fitz; provided by pymupdf>=1.26 in requirements
    except Exception as exc:  # pragma: no cover - depends on runtime env
        return {"content_type": "pdf", "error": f"pymupdf unavailable: {exc}"}

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        return {"content_type": "pdf", "error": f"failed to open PDF: {exc}"}

    try:
        pages = []
        for page in doc:
            pages.append(page.get_text())
        full = "\n".join(pages)
        page_count = doc.page_count
        meta = doc.metadata or {}
    finally:
        doc.close()

    char_count = len(full)
    content = full[:max_chars]
    result = {
        "content_type": "pdf",
        "page_count": page_count,
        "char_count": char_count,
        "content": content,
        "truncated": char_count > max_chars,
    }
    title = (meta.get("title") or "").strip()
    author = (meta.get("author") or "").strip()
    if title:
        result["title"] = title
    if author:
        result["authors"] = author
    return result


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read()
            text = raw.decode("utf-8", errors="replace")
            data = json.loads(text)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected JSON response type: {type(data).__name__}")
    return data


def _get_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        method="GET",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read()
            text = raw.decode("utf-8", errors="replace")
            data = json.loads(text)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected JSON response type: {type(data).__name__}")
    return data


def _strip_html(source: str) -> str:
    source = re.sub(r"(?is)<(script|style|noscript|svg|iframe)\b.*?</\1>", " ", source)
    source = re.sub(r"(?is)<br\s*/?>", "\n", source)
    source = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article|header|footer)>", "\n", source)
    text = re.sub(r"(?s)<[^>]+>", " ", source)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _duckduckgo_href(href: str) -> str:
    href = html_lib.unescape(href)
    parsed = urllib.parse.urlparse(href)
    qs = urllib.parse.parse_qs(parsed.query)
    if "uddg" in qs and qs["uddg"]:
        return qs["uddg"][0]
    return href


def _parse_duckduckgo_results(page_html: str, limit: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    blocks = re.findall(r'(?is)<div[^>]+class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*</div>', page_html)
    if not blocks:
        blocks = re.findall(r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]*>.*?(?=<a[^>]+class="[^"]*result__a|</body>)', page_html)

    for block in blocks:
        if len(results) >= limit:
            break
        link_match = re.search(r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not link_match:
            continue
        snippet_match = re.search(r'(?is)<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', block)
        if not snippet_match:
            snippet_match = re.search(r'(?is)<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>', block)

        title = _strip_html(link_match.group(2))
        href = _duckduckgo_href(link_match.group(1))
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        if title or href:
            results.append({"title": title, "href": href, "body": snippet})

    # Fallback for DuckDuckGo markup variants: pair result links and snippets.
    if not results:
        link_matches = list(re.finditer(r'(?is)<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page_html))
        snippet_matches = list(re.finditer(r'(?is)<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', page_html))
        for i, link_match in enumerate(link_matches[:limit]):
            snippet = _strip_html(snippet_matches[i].group(1)) if i < len(snippet_matches) else ""
            results.append({
                "title": _strip_html(link_match.group(2)),
                "href": _duckduckgo_href(link_match.group(1)),
                "body": snippet,
            })

    return results[:limit]


def _parse_bing_results(page_html: str, limit: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    blocks = re.findall(
        r'(?is)<li[^>]+class="[^"]*\bb_algo\b[^"]*"[^>]*>(.*?)(?=<li[^>]+class="[^"]*\bb_algo\b|</ol>)',
        page_html,
    )
    for block in blocks:
        if len(results) >= limit:
            break
        link_match = re.search(r'(?is)<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>', block)
        if not link_match:
            continue
        snippet_match = re.search(r'(?is)<p[^>]*>(.*?)</p>', block)
        title = _strip_html(link_match.group(2))
        href = html_lib.unescape(link_match.group(1))
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        if title and href and not href.startswith("javascript:"):
            results.append({"title": title, "href": href, "body": snippet})
    return results[:limit]


def _yahoo_href(href: str) -> str:
    href = html_lib.unescape(href)
    # Yahoo redirect URLs contain /RU=<encoded-url>/RK=...
    match = re.search(r'/RU=([^/]+)', href)
    if match:
        return urllib.parse.unquote(match.group(1))
    return href


def _parse_yahoo_results(page_html: str, limit: int) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    blocks = re.findall(
        r'(?is)<div[^>]+class="[^"]*\balgo-sr\b[^"]*"[^>]*>(.*?)(?=<div[^>]+class="[^"]*\balgo-sr\b|</ol>)',
        page_html,
    )
    for block in blocks:
        if len(results) >= limit:
            break
        link_match = re.search(r'(?is)<div[^>]+class="[^"]*compTitle[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not link_match:
            continue
        snippet_match = re.search(r'(?is)<div[^>]+class="[^"]*compText[^"]*"[^>]*>(.*?)</div>', block)
        title = _strip_html(link_match.group(2))
        href = _yahoo_href(link_match.group(1))
        snippet = _strip_html(snippet_match.group(1)) if snippet_match else ""
        if title and href:
            results.append({"title": title, "href": href, "body": snippet})
    return results[:limit]


def _normalize_result(raw: Dict[str, Any], source: str) -> Dict[str, str]:
    title = str(raw.get("title") or "")
    url = str(raw.get("url") or raw.get("href") or raw.get("link") or "")
    snippet = str(raw.get("snippet") or raw.get("content") or raw.get("body") or "")
    # Keep href/body aliases for older R-Agent prompts or callers that already
    # learned the previous shape, while making url/snippet the canonical names.
    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "source": source,
        "href": url,
        "body": snippet,
    }


def _local_html_provider_order() -> List[str]:
    raw_order = os.getenv("WEB_SEARCH_LOCAL_HTML_ORDER", _DEFAULT_LOCAL_HTML_ORDER)
    providers = []
    for provider in _split_provider_list(raw_order):
        if provider in {"duckduckgo", "bing", "yahoo"} and provider not in providers:
            providers.append(provider)
    return providers or _split_provider_list(_DEFAULT_LOCAL_HTML_ORDER)


def _search_local_html(query: str, limit: int) -> Dict[str, Any]:
    provider_errors = []
    for provider in _local_html_provider_order():
        try:
            results = _search_with_provider(provider, query, limit)
            if results:
                normalized = [_normalize_result(result, provider) for result in results]
                return {
                    "success": True,
                    "status": "ok",
                    "query": query,
                    "provider": "local_html",
                    "source_provider": provider,
                    "total_results": len(normalized),
                    "results": normalized,
                    "warnings": provider_errors,
                }
        except Exception as e:
            provider_errors.append(f"{provider}: {e}")

    return {
        "success": True,
        "status": "no_results",
        "query": query,
        "provider": "local_html",
        "source_provider": None,
        "total_results": 0,
        "results": [],
        "warnings": provider_errors,
    }


def _search_serper(query: str, limit: int) -> Dict[str, Any]:
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        return {
            "success": False,
            "status": "missing_api_key",
            "provider": "serper",
            "query": query,
            "error": "SERPER_API_KEY is not configured",
        }

    cleaned_query = query.strip()[:500]
    count = max(1, min(limit, 10))
    data = _post_json(
        "https://google.serper.dev/search",
        {"q": cleaned_query, "num": count},
        {"X-API-KEY": api_key},
        timeout=_env_int("WEB_SEARCH_API_TIMEOUT", _DEFAULT_API_TIMEOUT_SECONDS),
    )
    items = data.get("organic") or []
    if not isinstance(items, list):
        return {
            "success": False,
            "status": "bad_response",
            "provider": "serper",
            "query": cleaned_query,
            "error": "Serper returned an unexpected response format",
        }

    normalized = [
        _normalize_result(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            },
            "serper",
        )
        for item in items[:count]
        if isinstance(item, dict)
    ]
    return {
        "success": True,
        "status": "ok" if normalized else "no_results",
        "query": cleaned_query,
        "provider": "serper",
        "source_provider": "google",
        "total_results": len(normalized),
        "results": normalized,
        "warnings": [],
    }


def _search_google_cse(query: str, limit: int) -> Dict[str, Any]:
    api_key = os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
    engine_id = os.getenv("GOOGLE_SEARCH_ENGINE_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("GOOGLE_SEARCH_API_KEY", api_key),
            ("GOOGLE_SEARCH_ENGINE_ID", engine_id),
        )
        if not value
    ]
    if missing:
        return {
            "success": False,
            "status": "missing_api_key",
            "provider": "google_cse",
            "query": query,
            "error": f"{', '.join(missing)} is not configured",
        }

    cleaned_query = query.strip()[:2048]
    count = max(1, min(limit, 10))
    params = urllib.parse.urlencode({
        "key": api_key,
        "cx": engine_id,
        "q": cleaned_query,
        "num": count,
    })
    data = _get_json(
        f"https://www.googleapis.com/customsearch/v1?{params}",
        timeout=_env_int("WEB_SEARCH_API_TIMEOUT", _DEFAULT_API_TIMEOUT_SECONDS),
    )
    items = data.get("items") or []
    if not isinstance(items, list):
        return {
            "success": False,
            "status": "bad_response",
            "provider": "google_cse",
            "query": cleaned_query,
            "error": "Google Programmable Search returned an unexpected response format",
        }

    normalized = [
        _normalize_result(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            },
            "google_cse",
        )
        for item in items[:count]
        if isinstance(item, dict)
    ]
    return {
        "success": True,
        "status": "ok" if normalized else "no_results",
        "query": cleaned_query,
        "provider": "google_cse",
        "source_provider": "google",
        "total_results": len(normalized),
        "results": normalized,
        "warnings": [],
    }


def _search_groundroute(query: str, limit: int) -> Dict[str, Any]:
    api_key = os.getenv("GROUNDROUTE_API_KEY", "").strip()
    if not api_key:
        return {
            "success": False,
            "status": "missing_api_key",
            "provider": "groundroute",
            "query": query,
            "error": "GROUNDROUTE_API_KEY is not configured",
        }

    count = max(1, min(limit, 50))
    data = _post_json(
        "https://api.groundroute.ai/v1/search",
        {"query": query, "max_results": count},
        {"Authorization": f"Bearer {api_key}"},
        timeout=_env_int("WEB_SEARCH_API_TIMEOUT", _DEFAULT_API_TIMEOUT_SECONDS),
    )
    items = data.get("results") or []
    if not isinstance(items, list):
        return {
            "success": False,
            "status": "bad_response",
            "provider": "groundroute",
            "query": query,
            "error": "GroundRoute returned an unexpected response format",
        }

    normalized = [
        _normalize_result(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet") or item.get("content", ""),
            },
            str(item.get("source_engine") or "groundroute"),
        )
        for item in items[:count]
        if isinstance(item, dict)
    ]
    return {
        "success": True,
        "status": "ok" if normalized else "no_results",
        "query": query,
        "provider": "groundroute",
        "source_provider": "groundroute",
        "total_results": len(normalized),
        "results": normalized,
        "warnings": [],
    }


def _provider_order(provider: str) -> List[str]:
    normalized = (provider or "auto").strip().lower().replace("-", "_")
    if normalized in {"auto", "best"}:
        configured_order = _split_provider_list(os.getenv("WEB_SEARCH_PROVIDER_ORDER", ""))
        base_order = configured_order or [
            "bing",
            "google_cse",
            "groundroute",
            "serper",
            "yahoo",
            "duckduckgo",
        ]
        order = []
        for candidate in base_order:
            if candidate in {"ground_route", "groundroute"}:
                candidate = "groundroute"
            elif candidate in {"google_cse", "google_custom_search", "google_official"}:
                candidate = "google_cse"
            elif candidate in {"google", "serper"}:
                candidate = "serper"
            elif candidate in {"local", "html", "local_html"}:
                candidate = "local_html"
            if candidate == "google_cse" and not (
                os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
                and os.getenv("GOOGLE_SEARCH_ENGINE_ID", "").strip()
            ):
                continue
            if candidate == "groundroute" and not os.getenv("GROUNDROUTE_API_KEY", "").strip():
                continue
            if candidate == "serper" and not os.getenv("SERPER_API_KEY", "").strip():
                continue
            if candidate in {
                "bing",
                "google_cse",
                "groundroute",
                "serper",
                "yahoo",
                "duckduckgo",
                "local_html",
            } and candidate not in order:
                order.append(candidate)
        return order
    if normalized in {"local", "html", "local_html"}:
        return ["local_html"]
    if normalized in {"duckduckgo", "bing", "yahoo"}:
        return [normalized]
    if normalized in {"serper", "google"}:
        return ["serper"]
    if normalized in {"google_cse", "google_custom_search", "google_official"}:
        return ["google_cse"]
    if normalized in {"groundroute", "ground_route"}:
        return ["groundroute"]
    return [normalized]


def _search_provider(provider: str, query: str, limit: int) -> Dict[str, Any]:
    if provider == "local_html":
        return _search_local_html(query, limit)
    if provider in {"bing", "yahoo", "duckduckgo"}:
        results = _search_with_provider(provider, query, limit)
        normalized = [_normalize_result(result, provider) for result in results]
        return {
            "success": True,
            "status": "ok" if normalized else "no_results",
            "query": query,
            "provider": provider,
            "source_provider": provider,
            "total_results": len(normalized),
            "results": normalized,
            "warnings": [],
        }
    if provider == "serper":
        return _search_serper(query, limit)
    if provider == "google_cse":
        return _search_google_cse(query, limit)
    if provider == "groundroute":
        return _search_groundroute(query, limit)
    return {
        "success": False,
        "status": "unknown_provider",
        "provider": provider,
        "query": query,
        "error": f"Unknown web_search provider: {provider}",
        "available_providers": [
            "auto",
            "bing",
            "google_cse",
            "groundroute",
            "serper",
            "yahoo",
            "duckduckgo",
            "local_html",
        ],
    }


def _search_with_provider(provider: str, query: str, limit: int) -> List[Dict[str, str]]:
    encoded = urllib.parse.quote(query)
    if provider == "duckduckgo":
        page_html = _fetch_text("https://html.duckduckgo.com/html/?q=" + encoded, timeout=_env_int("WEB_SEARCH_LOCAL_HTML_TIMEOUT", _DEFAULT_LOCAL_HTML_TIMEOUT_SECONDS))
        if "anomaly" in page_html[:20000].lower() and "result__" not in page_html:
            return []
        return _parse_duckduckgo_results(page_html, limit)
    if provider == "bing":
        page_html = _fetch_text("https://www.bing.com/search?q=" + encoded, timeout=_env_int("WEB_SEARCH_LOCAL_HTML_TIMEOUT", _DEFAULT_LOCAL_HTML_TIMEOUT_SECONDS))
        return _parse_bing_results(page_html, limit)
    if provider == "yahoo":
        page_html = _fetch_text("https://search.yahoo.com/search?p=" + encoded, timeout=_env_int("WEB_SEARCH_LOCAL_HTML_TIMEOUT", _DEFAULT_LOCAL_HTML_TIMEOUT_SECONDS))
        return _parse_yahoo_results(page_html, limit)
    return []


def web_search_tool(query: str, limit: int = 5, provider: str = "auto") -> str:
    """Search the web using a selectable provider.

    Providers:
    - auto: Bing -> configured Google CSE -> configured API providers -> Yahoo -> DuckDuckGo.
    - local_html: zero-key Bing -> Yahoo -> DuckDuckGo fallback by default.
    - google_cse: Google Programmable Search via GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID.
    - serper: Google Search via SERPER_API_KEY.
    - groundroute: meta search via GROUNDROUTE_API_KEY.

    Environment knobs:
    - WEB_SEARCH_PROVIDER_ORDER=bing,google_cse,groundroute,serper,yahoo,duckduckgo
    - WEB_SEARCH_LOCAL_HTML_ORDER=bing,yahoo,duckduckgo
    - WEB_SEARCH_LOCAL_HTML_TIMEOUT=5
    - WEB_SEARCH_API_TIMEOUT=20
    """
    try:
        limit = max(1, min(int(limit), 50))
    except Exception:
        limit = 5

    warnings = []
    normalized_provider = (provider or "auto").strip().lower().replace("-", "_")
    allow_fallback = normalized_provider in {"auto", "best"}
    for selected_provider in _provider_order(provider):
        try:
            payload = _search_provider(selected_provider, query, limit)
        except Exception as e:
            warnings.append(f"{selected_provider}: {e}")
            continue

        if payload.get("success") and payload.get("results"):
            payload["warnings"] = [*warnings, *(payload.get("warnings") or [])]
            return json.dumps(payload, ensure_ascii=False)

        if not allow_fallback:
            payload["warnings"] = [*warnings, *(payload.get("warnings") or [])]
            return json.dumps(payload, ensure_ascii=False)

        if not payload.get("success"):
            warnings.append(f"{selected_provider}: {payload.get('error') or payload.get('status')}")

    return _json_success({
        "status": "no_results",
        "query": query,
        "provider": provider,
        "total_results": 0,
        "results": [],
        "warnings": warnings,
    })


_DEFAULT_PDF_PREVIEW_CHARS = 12000


def web_extract_tool(urls: list, max_chars: int = _DEFAULT_PDF_PREVIEW_CHARS) -> str:
    """Extract readable text from up to five web page URLs.

    HTML pages return a text preview. PDFs (detected by magic bytes or
    content-type, regardless of site) are parsed with pymupdf and return
    structured metadata plus a bounded ``content`` preview so the model can
    tell an abstract-level preview from the full paper.
    """
    if not isinstance(urls, list):
        return _json_failure("urls must be a list of URL strings")

    try:
        max_chars = max(1000, min(int(max_chars), 200000))
    except Exception:
        max_chars = _DEFAULT_PDF_PREVIEW_CHARS

    results = []
    for url in urls[:5]:
        if not isinstance(url, str) or not url.strip():
            results.append({"url": url, "error": "invalid URL"})
            continue
        target = _normalize_pdf_url(url.strip())
        try:
            data, content_type = _fetch_bytes(target, timeout=30)
            if _looks_like_pdf(data, content_type, target):
                pdf_result = _extract_pdf(data, max_chars)
                results.append({"url": url, **pdf_result})
            else:
                text = _strip_html(data.decode("utf-8", errors="replace"))
                results.append({"url": url, "content_type": "html", "content": text[:5000]})
        except Exception as e:
            results.append({"url": url, "error": str(e)})

    return _json_success({"results": results})


registry.register(
    name="web_search",
    description="在互联网上搜索信息。默认 auto：先 Bing，再按配置尝试 Google CSE/其它 API provider，最后回退 Yahoo/DuckDuckGo。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询词"},
            "limit": {"type": "integer", "description": "最大返回结果数 (默认 5)", "default": 5},
            "provider": {
                "type": "string",
                "description": "搜索提供方：auto（默认顺序 Bing→Google CSE→其它 API→Yahoo→DuckDuckGo）、google_cse、bing、yahoo、duckduckgo、local_html、serper、groundroute",
                "default": "auto",
            },
        },
        "required": ["query"],
    },
    handler=web_search_tool,
)

registry.register(
    name="web_extract",
    description=(
        "从指定的 URL 提取内容。HTML 返回正文文本预览；PDF（按 magic bytes/content-type "
        "自动识别，含 arxiv.org/abs 链接）用 pymupdf 解析，返回 title/authors/page_count/"
        "char_count 元数据 + 前 max_chars 字符的正文预览。若 truncated=true 表示只是预览、"
        "全文更长，可调大 max_chars 深读。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要提取内容的 URL 列表 (最多 5 个)",
            },
            "max_chars": {
                "type": "integer",
                "description": "PDF 正文预览的最大字符数 (默认 12000，足够覆盖标题/作者/摘要/引言)",
                "default": _DEFAULT_PDF_PREVIEW_CHARS,
            },
        },
        "required": ["urls"],
    },
    handler=web_extract_tool,
)
