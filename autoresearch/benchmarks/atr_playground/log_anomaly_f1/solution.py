def is_anomaly(line: str) -> bool:
    """Baseline: keyword-only anomaly detector."""
    s = line.lower()
    return any(k in s for k in ["error", "fatal", "exception", "panic", "failed"])
