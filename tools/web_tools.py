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


def _search_local_html(query: str, limit: int) -> Dict[str, Any]:
    provider_errors = []
    for provider in ("duckduckgo", "bing", "yahoo"):
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
        timeout=30,
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
        timeout=30,
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
    normalized = (provider or "local_html").strip().lower().replace("-", "_")
    if normalized in {"auto", "best"}:
        order = []
        if os.getenv("GROUNDROUTE_API_KEY", "").strip():
            order.append("groundroute")
        if os.getenv("SERPER_API_KEY", "").strip():
            order.append("serper")
        order.append("local_html")
        return order
    if normalized in {"local", "html", "local_html", "duckduckgo"}:
        return ["local_html"]
    if normalized in {"serper", "google"}:
        return ["serper"]
    if normalized in {"groundroute", "ground_route"}:
        return ["groundroute"]
    return [normalized]


def _search_provider(provider: str, query: str, limit: int) -> Dict[str, Any]:
    if provider == "local_html":
        return _search_local_html(query, limit)
    if provider == "serper":
        return _search_serper(query, limit)
    if provider == "groundroute":
        return _search_groundroute(query, limit)
    return {
        "success": False,
        "status": "unknown_provider",
        "provider": provider,
        "query": query,
        "error": f"Unknown web_search provider: {provider}",
        "available_providers": ["local_html", "serper", "groundroute", "auto"],
    }


def _search_with_provider(provider: str, query: str, limit: int) -> List[Dict[str, str]]:
    encoded = urllib.parse.quote(query)
    if provider == "duckduckgo":
        page_html = _fetch_text("https://html.duckduckgo.com/html/?q=" + encoded, timeout=10)
        if "anomaly" in page_html[:20000].lower() and "result__" not in page_html:
            return []
        return _parse_duckduckgo_results(page_html, limit)
    if provider == "bing":
        page_html = _fetch_text("https://www.bing.com/search?q=" + encoded, timeout=10)
        return _parse_bing_results(page_html, limit)
    if provider == "yahoo":
        page_html = _fetch_text("https://search.yahoo.com/search?p=" + encoded, timeout=10)
        return _parse_yahoo_results(page_html, limit)
    return []


def web_search_tool(query: str, limit: int = 5, provider: str = "local_html") -> str:
    """Search the web using a selectable provider.

    Providers:
    - local_html: zero-key DuckDuckGo -> Bing -> Yahoo fallback.
    - serper: Google Search via SERPER_API_KEY.
    - groundroute: meta search via GROUNDROUTE_API_KEY.
    - auto: use configured API providers first, then local_html.
    """
    try:
        limit = max(1, min(int(limit), 50))
    except Exception:
        limit = 5

    warnings = []
    normalized_provider = (provider or "local_html").strip().lower().replace("-", "_")
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


def web_extract_tool(urls: list) -> str:
    """Extract readable text from up to five web page URLs."""
    if not isinstance(urls, list):
        return _json_failure("urls must be a list of URL strings")

    results = []
    for url in urls[:5]:
        if not isinstance(url, str) or not url.strip():
            results.append({"url": url, "error": "invalid URL"})
            continue
        try:
            page_html = _fetch_text(url.strip(), timeout=10)
            text = _strip_html(page_html)
            results.append({"url": url, "content": text[:5000]})
        except Exception as e:
            results.append({"url": url, "error": str(e)})

    return _json_success({"results": results})


registry.register(
    name="web_search",
    description="在互联网上搜索信息。默认使用零配置本地 HTML 搜索，也可通过 provider 选择 serper、groundroute 或 auto。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询词"},
            "limit": {"type": "integer", "description": "最大返回结果数 (默认 5)", "default": 5},
            "provider": {
                "type": "string",
                "description": "搜索提供方：local_html（默认，DuckDuckGo/Bing/Yahoo fallback）、serper、groundroute、auto",
                "default": "local_html",
            },
        },
        "required": ["query"],
    },
    handler=web_search_tool,
)

registry.register(
    name="web_extract",
    description="从指定的 URL 提取网页文本内容。",
    parameters={
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要提取内容的 URL 列表 (最多 5 个)",
            }
        },
        "required": ["urls"],
    },
    handler=web_extract_tool,
)
