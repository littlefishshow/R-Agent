import json

from tools import web_tools
from tools.registry import registry


def test_local_html_provider_returns_unified_shape(monkeypatch):
    monkeypatch.setattr(
        web_tools,
        "_search_with_provider",
        lambda provider, query, limit: [{"title": "Example", "href": "https://example.com", "body": "Snippet"}],
    )

    payload = json.loads(web_tools.web_search_tool("example", limit=3))

    assert payload["success"] is True
    assert payload["status"] == "ok"
    assert payload["provider"] == "local_html"
    assert payload["source_provider"] == "duckduckgo"
    assert payload["total_results"] == 1
    assert payload["results"][0]["url"] == "https://example.com"
    assert payload["results"][0]["snippet"] == "Snippet"
    # Backward-compatible aliases from the previous R-Agent shape.
    assert payload["results"][0]["href"] == "https://example.com"
    assert payload["results"][0]["body"] == "Snippet"


def test_auto_provider_falls_back_to_local_html_without_keys(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("GROUNDROUTE_API_KEY", raising=False)
    monkeypatch.setattr(
        web_tools,
        "_search_with_provider",
        lambda provider, query, limit: [{"title": "Fallback", "href": "https://fallback.test", "body": ""}],
    )

    payload = json.loads(web_tools.web_search_tool("fallback", provider="auto"))

    assert payload["success"] is True
    assert payload["provider"] == "local_html"
    assert payload["results"][0]["source"] == "duckduckgo"


def test_serper_provider_reports_missing_key(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    payload = json.loads(web_tools.web_search_tool("query", provider="serper"))

    assert payload["success"] is False
    assert payload["status"] == "missing_api_key"
    assert payload["provider"] == "serper"


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
    assert properties["provider"]["default"] == "local_html"
