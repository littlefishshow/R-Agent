import json
import urllib.request
import urllib.parse
from tools.registry import registry

def web_search_tool(query: str, limit: int = 5) -> str:
    """Simple web search using DuckDuckGo HTML search."""
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8')
            
            # Simple regex-based extraction to avoid BeautifulSoup dependency
            import re
            results = []
            
            # Find result snippets in duckduckgo html
            matches = re.finditer(r'<a class="result__url" href="([^"]+)".*?>(.*?)</a>.*?<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
            
            for i, match in enumerate(matches):
                if i >= limit:
                    break
                    
                url = match.group(1)
                title = re.sub('<[^<]+>', '', match.group(2)).strip()
                snippet = re.sub('<[^<]+>', '', match.group(3)).strip()
                
                results.append({
                    "title": title,
                    "href": url,
                    "body": snippet
                })
                
            return json.dumps({"success": True, "results": results}, ensure_ascii=False)
            
    except Exception as e:
        return json.dumps({
            "success": False, 
            "error": f"Search failed: {str(e)}"
        }, ensure_ascii=False)

def web_extract_tool(urls: list) -> str:
    """Extract content from web page URLs."""
    results = []
    for url in urls[:5]:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8')
                # Very naive HTML stripping
                import re
                text = re.sub('<[^<]+>', '', html)
                text = re.sub('\s+', ' ', text).strip()
                results.append({"url": url, "content": text[:5000]})
        except Exception as e:
            results.append({"url": url, "error": str(e)})
            
    return json.dumps({"results": results}, ensure_ascii=False)

registry.register(
    name="web_search",
    description="在互联网上搜索信息。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询词"},
            "limit": {"type": "integer", "description": "最大返回结果数 (默认 5)", "default": 5}
        },
        "required": ["query"]
    },
    handler=web_search_tool
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
                "description": "要提取内容的 URL 列表 (最多 5 个)"
            }
        },
        "required": ["urls"]
    },
    handler=web_extract_tool
)
