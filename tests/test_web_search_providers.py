import json

from tools import web_tools
from tools.registry import registry


def test_local_html_provider_returns_unified_shape(monkeypatch):
    monkeypatch.setattr(
        web_tools,
        "_search_with_provider",
        lambda provider, query, limit: [{"title": "Example", "href": "https://example.com", "body": "Snippet"}] if provider == "bing" else [],
    )

    payload = json.loads(
        web_tools.web_search_tool("example", limit=3, provider="local_html")
    )

    assert payload["success"] is True
    assert payload["status"] == "ok"
    assert payload["provider"] == "local_html"
    assert payload["source_provider"] == "bing"
    assert payload["total_results"] == 1
    assert payload["results"][0]["url"] == "https://example.com"
    assert payload["results"][0]["snippet"] == "Snippet"
    # Backward-compatible aliases from the previous R-Agent shape.
    assert payload["results"][0]["href"] == "https://example.com"
    assert payload["results"][0]["body"] == "Snippet"


def test_auto_provider_falls_back_to_local_html_without_keys(monkeypatch):
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_SEARCH_ENGINE_ID", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GROUNDROUTE_API_KEY", raising=False)
    monkeypatch.setattr(
        web_tools,
        "_search_with_provider",
        lambda provider, query, limit: [{"title": "Fallback", "href": "https://fallback.test", "body": ""}] if provider == "bing" else [],
    )

    payload = json.loads(web_tools.web_search_tool("fallback", provider="auto"))

    assert payload["success"] is True
    assert payload["provider"] == "bing"
    assert payload["results"][0]["source"] == "bing"


def test_serper_provider_reports_missing_key(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    payload = json.loads(web_tools.web_search_tool("query", provider="serper"))

    assert payload["success"] is False
    assert payload["status"] == "missing_api_key"
    assert payload["provider"] == "serper"


def test_google_cse_provider_reports_missing_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_SEARCH_ENGINE_ID", raising=False)

    payload = json.loads(web_tools.web_search_tool("query", provider="google_cse"))

    assert payload["success"] is False
    assert payload["status"] == "missing_api_key"
    assert payload["provider"] == "google_cse"
    assert "GOOGLE_SEARCH_API_KEY" in payload["error"]
    assert "GOOGLE_SEARCH_ENGINE_ID" in payload["error"]


def test_google_cse_provider_returns_unified_shape(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "google-key")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "engine-id")
    captured = {}

    def fake_get_json(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return {
            "items": [{
                "title": "Official result",
                "link": "https://example.com/google",
                "snippet": "Google snippet",
            }]
        }

    monkeypatch.setattr(web_tools, "_get_json", fake_get_json)

    payload = json.loads(
        web_tools.web_search_tool("agent runtime", limit=20, provider="google_cse")
    )

    assert payload["success"] is True
    assert payload["provider"] == "google_cse"
    assert payload["source_provider"] == "google"
    assert payload["results"][0]["url"] == "https://example.com/google"
    assert payload["results"][0]["snippet"] == "Google snippet"
    parsed = web_tools.urllib.parse.urlparse(captured["url"])
    params = web_tools.urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "www.googleapis.com"
    assert params["key"] == ["google-key"]
    assert params["cx"] == ["engine-id"]
    assert params["q"] == ["agent runtime"]
    assert params["num"] == ["10"]


def test_groundroute_provider_reports_missing_key(monkeypatch):
    monkeypatch.delenv("GROUNDROUTE_API_KEY", raising=False)

    payload = json.loads(web_tools.web_search_tool("query", provider="groundroute"))

    assert payload["success"] is False
    assert payload["status"] == "missing_api_key"
    assert payload["provider"] == "groundroute"


def test_web_search_schema_exposes_provider_parameter():
    schemas = registry.get_all_schemas()
    web_search_schema = next(schema for schema in schemas if schema["function"]["name"] == "web_search")

    properties = web_search_schema["function"]["parameters"]["properties"]
    assert "provider" in properties
    assert properties["provider"]["default"] == "auto"


def test_local_html_order_can_be_configured(monkeypatch):
    seen = []

    def fake_search(provider, query, limit):
        seen.append(provider)
        return [{"title": "Y", "href": "https://y.test", "body": ""}] if provider == "yahoo" else []

    monkeypatch.setenv("WEB_SEARCH_LOCAL_HTML_ORDER", "yahoo,bing,duckduckgo")
    monkeypatch.setattr(web_tools, "_search_with_provider", fake_search)

    payload = json.loads(web_tools.web_search_tool("custom", provider="local_html"))

    assert payload["source_provider"] == "yahoo"
    assert seen == ["yahoo"]


def test_auto_provider_order_can_be_configured(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "dummy")
    monkeypatch.setenv("GROUNDROUTE_API_KEY", "dummy")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER_ORDER", "serper,groundroute,local_html")

    assert web_tools._provider_order("auto") == ["serper", "groundroute", "local_html"]


def test_auto_provider_places_google_cse_after_bing_when_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "google-key")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "engine-id")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GROUNDROUTE_API_KEY", raising=False)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER_ORDER", raising=False)

    assert web_tools._provider_order("auto") == [
        "bing",
        "google_cse",
        "yahoo",
        "duckduckgo",
    ]


def test_auto_uses_google_after_bing_returns_no_results(monkeypatch):
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "google-key")
    monkeypatch.setenv("GOOGLE_SEARCH_ENGINE_ID", "engine-id")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GROUNDROUTE_API_KEY", raising=False)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER_ORDER", raising=False)
    calls = []

    def fake_search_provider(provider, query, limit):
        calls.append(provider)
        if provider == "google_cse":
            return {
                "success": True,
                "status": "ok",
                "provider": "google_cse",
                "source_provider": "google",
                "query": query,
                "total_results": 1,
                "results": [{
                    "title": "Google",
                    "url": "https://google.test/result",
                    "snippet": "",
                }],
                "warnings": [],
            }
        return {
            "success": True,
            "status": "no_results",
            "provider": provider,
            "source_provider": provider,
            "query": query,
            "total_results": 0,
            "results": [],
            "warnings": [],
        }

    monkeypatch.setattr(web_tools, "_search_provider", fake_search_provider)

    payload = json.loads(web_tools.web_search_tool("query", provider="auto"))

    assert calls == ["bing", "google_cse"]
    assert payload["provider"] == "google_cse"
    assert payload["results"][0]["url"] == "https://google.test/result"


def test_auto_provider_without_google_keeps_local_fallback_order(monkeypatch):
    monkeypatch.delenv("GOOGLE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_SEARCH_ENGINE_ID", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GROUNDROUTE_API_KEY", raising=False)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER_ORDER", raising=False)

    assert web_tools._provider_order("auto") == [
        "bing",
        "yahoo",
        "duckduckgo",
    ]


# --- web_extract PDF support ---------------------------------------------
# The real NeurIPS 2023 "AUF" paper. It previously came back as raw "%PDF"
# garbage; these tests pin that it now returns parsed text.
AUF_PDF_URL = "https://proceedings.neurips.cc/paper_files/paper/2023/file/fed1ea8dcc2a13f3835cc854e8c8294c-Paper-Conference.pdf"


def test_web_extract_reads_pdf_as_text_not_garbage():
    result = json.loads(web_tools.web_extract_tool(urls=[AUF_PDF_URL]))["results"][0]

    assert result["content_type"] == "pdf"
    assert result["page_count"] > 0
    assert not result["content"].startswith("%PDF")
    assert "Rehearsal Learning for Avoiding Undesired Future" in result["content"]


def test_web_extract_pdf_preview_is_bounded_and_flags_truncation():
    result = json.loads(web_tools.web_extract_tool(urls=[AUF_PDF_URL], max_chars=2000))["results"][0]

    assert len(result["content"]) <= 2000
    assert result["char_count"] > 2000  # full paper is longer than the preview
    assert result["truncated"] is True


def test_pdf_detected_by_content_not_by_url_suffix():
    # Recognised by magic bytes or content-type, regardless of the URL.
    assert web_tools._looks_like_pdf(b"%PDF-1.5 ...", "", "https://x/no-extension")
    assert web_tools._looks_like_pdf(b"anything", "application/pdf", "https://x")
    # A ".pdf" suffix alone is not enough; real HTML stays HTML.
    assert not web_tools._looks_like_pdf(b"<html>", "text/html", "https://x/page.pdf")


def test_arxiv_abstract_url_rewritten_to_pdf():
    assert web_tools._normalize_pdf_url("https://arxiv.org/abs/2402.03300") == "https://arxiv.org/pdf/2402.03300"
    assert web_tools._normalize_pdf_url("https://example.com/foo") == "https://example.com/foo"


def test_web_extract_html_still_returns_text(monkeypatch):
    monkeypatch.setattr(
        web_tools,
        "_fetch_bytes",
        lambda url, timeout=10: (b"<html><body>Hello <b>world</b></body></html>", "text/html"),
    )

    result = json.loads(web_tools.web_extract_tool(urls=["https://example.com"]))["results"][0]

    assert result["content_type"] == "html"
    assert "Hello world" in result["content"]
