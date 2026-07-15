import html

def decode_text(text: str) -> str:
    """Baseline: only handles HTML entities and simple python unicode escapes."""
    s = html.unescape(text)
    try:
        s = s.encode('utf-8').decode('unicode_escape')
    except Exception:
        pass
    return s
