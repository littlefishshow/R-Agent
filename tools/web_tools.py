import html as html_lib
import json
import re
import shutil
import subprocess
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


def web_search_tool(query: str, limit: int = 5) -> str:
    """Search the web using DuckDuckGo's lightweight HTML endpoint."""
    try:
        limit = max(1, min(int(limit), 10))
    except Exception:
        limit = 5

    provider_errors = []
    for provider in ("duckduckgo", "bing", "yahoo"):
        try:
            results = _search_with_provider(provider, query, limit)
            if results:
                return _json_success({"results": results, "provider": provider})
        except Exception as e:
            provider_errors.append(f"{provider}: {e}")

    payload: Dict[str, Any] = {"results": [], "provider": None}
    if provider_errors:
        payload["warnings"] = provider_errors
    return _json_success(payload)


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
    description="在互联网上搜索信息。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询词"},
            "limit": {"type": "integer", "description": "最大返回结果数 (默认 5)", "default": 5},
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
