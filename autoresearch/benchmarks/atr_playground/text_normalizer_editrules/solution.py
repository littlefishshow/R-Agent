import re

def normalize(text: str) -> str:
    """Baseline: intentionally weak normalizer."""
    return re.sub(r"\s+", " ", text.strip().lower())
