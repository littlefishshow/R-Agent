import re

def repair_json(text: str) -> str:
    """Baseline: a few naive JSON repairs."""
    s = text.strip()
    s = s.replace("'", '"')
    s = re.sub(r",\s*([}\]])", r"", s)
    return s
