import re

def _tokens(s: str):
    return set(re.findall(r"[a-z0-9]+", s.lower()))

def rank(query: str, documents: list[str]) -> list[int]:
    """Return document indices from best to worst. Baseline: raw token overlap."""
    q = _tokens(query)
    scored = []
    for i, doc in enumerate(documents):
        d = _tokens(doc)
        scored.append((len(q & d), -i, i))
    scored.sort(reverse=True)
    return [i for _, __, i in scored]
