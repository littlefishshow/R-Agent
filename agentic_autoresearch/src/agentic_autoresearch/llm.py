from __future__ import annotations


def create_default_client():
    """Create the default OpenAI-compatible client.

    When this package is used inside the R-Agent checkout, reuse its existing
    ``core.config`` client factory so users do not need a second set of env vars.
    Outside that checkout, fall back to the standard OpenAI client.
    """
    try:
        from core import config

        return config.create_llm_client()
    except Exception:
        from openai import OpenAI

        return OpenAI()


def default_model() -> str:
    try:
        from core import config

        return config.get_model()
    except Exception:
        import os

        return os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL") or "gpt-4o"

