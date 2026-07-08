"""AutoResearch v2 — budget ledger, metered LLM client, and model tiers.

This is the cost-control spine described in AUTORESEARCH_DESIGN_v2.md §3.  It is
intentionally dependency-light: a JSON ledger on disk plus a thin proxy around
any OpenAI-style client so every completion call is metered and checked against
hard limits before the loop is allowed to keep running "forever".
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Model tiers
# ---------------------------------------------------------------------------

# Coarse USD-per-1K-token estimates.  These are best-effort defaults used only
# for budget accounting/estimation; they never need to be exact to be useful as
# a stop signal.  Override via AUTORESEARCH_PRICE_<MODEL> env if needed.
_DEFAULT_PRICE_PER_1K = {
    # model substring -> (prompt_usd_per_1k, completion_usd_per_1k)
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
    "o1-mini": (0.003, 0.012),
    "o1": (0.015, 0.06),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5": (0.0005, 0.0015),
}
_FALLBACK_PRICE_PER_1K = (0.001, 0.003)

TIER_NAMES = ("plan", "exec", "util")


def price_per_1k(model: str) -> tuple[float, float]:
    """Best-effort (prompt, completion) USD per 1K tokens for a model name."""
    name = (model or "").lower()
    for key, price in _DEFAULT_PRICE_PER_1K.items():
        if key in name:
            return price
    return _FALLBACK_PRICE_PER_1K


def estimate_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p, c = price_per_1k(model)
    return (max(0, prompt_tokens) / 1000.0) * p + (max(0, completion_tokens) / 1000.0) * c


@dataclass
class ModelTiers:
    """Model selection by cost tier.

    plan = expensive reasoning/debate; exec = execution; util = cheap utility
    (monitoring, compression, surveys).  Falls back to the base model when a
    tier is unset.
    """

    plan: str = ""
    exec: str = ""
    util: str = ""
    base: str = ""

    def resolve(self, tier: str) -> str:
        tier = (tier or "").strip().lower()
        chosen = getattr(self, tier, "") if tier in TIER_NAMES else ""
        return chosen or self.base or _default_base_model()

    @classmethod
    def from_env(cls, base: str = "") -> "ModelTiers":
        base = base or _default_base_model()
        return cls(
            plan=os.environ.get("AUTORESEARCH_MODEL_PLAN", "") or base,
            exec=os.environ.get("AUTORESEARCH_MODEL_EXEC", "") or base,
            util=os.environ.get("AUTORESEARCH_MODEL_UTIL", "") or base,
            base=base,
        )


def _default_base_model() -> str:
    try:
        from core import config

        return config.get_model()
    except Exception:
        return os.environ.get("LLM_MODEL") or "gpt-4o"


# ---------------------------------------------------------------------------
# Budget ledger
# ---------------------------------------------------------------------------


@dataclass
class BudgetLimits:
    max_usd: float = 0.0        # 0 => unlimited
    max_tokens: int = 0         # 0 => unlimited
    # Fraction of the limit at which we start degrading (cheaper models / fewer
    # personas) instead of hard-stopping.
    degrade_ratio: float = 0.8

    def is_unlimited(self) -> bool:
        return self.max_usd <= 0 and self.max_tokens <= 0

    @classmethod
    def from_env(cls) -> "BudgetLimits":
        def _f(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, str(default)))
            except ValueError:
                return default

        return cls(
            max_usd=_f("AUTORESEARCH_MAX_USD", 0.0),
            max_tokens=_f("AUTORESEARCH_MAX_TOKENS", 0.0),
            degrade_ratio=_f("AUTORESEARCH_DEGRADE_RATIO", 0.8),
        )


class BudgetLedger:
    """Append-only-ish JSON ledger of LLM spend, with hard limits.

    Thread-safe within a process; atomic on-disk writes so a crash cannot leave
    a truncated ledger.  Not designed for cross-process contention beyond the
    single autoresearch run that owns it.
    """

    def __init__(self, path: str | Path, limits: Optional[BudgetLimits] = None):
        self.path = Path(path)
        self.limits = limits or BudgetLimits.from_env()
        self._lock = threading.Lock()
        self._data = self._load()
        # Persist immediately so a run always has an on-disk ledger, even before
        # the first metered LLM call (deterministic phases make no calls).
        self._save()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("prompt_tokens", 0)
                    data.setdefault("completion_tokens", 0)
                    data.setdefault("total_tokens", 0)
                    data.setdefault("estimated_usd", 0.0)
                    data.setdefault("calls", 0)
                    data.setdefault("by_phase", {})
                    data.setdefault("by_model", {})
                    return data
            except Exception:
                pass
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_usd": 0.0,
            "calls": 0,
            "by_phase": {},
            "by_model": {},
            "created_at": time.time(),
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data["updated_at"] = time.time()
        self._data["limits"] = {
            "max_usd": self.limits.max_usd,
            "max_tokens": self.limits.max_tokens,
            "degrade_ratio": self.limits.degrade_ratio,
        }
        self._data["status"] = self.status()
        payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    def record(self, *, prompt_tokens: int, completion_tokens: int, model: str, phase: str = "") -> dict:
        with self._lock:
            usd = estimate_usd(model, prompt_tokens, completion_tokens)
            total = max(0, int(prompt_tokens)) + max(0, int(completion_tokens))
            self._data["prompt_tokens"] += max(0, int(prompt_tokens))
            self._data["completion_tokens"] += max(0, int(completion_tokens))
            self._data["total_tokens"] += total
            self._data["estimated_usd"] = round(self._data["estimated_usd"] + usd, 6)
            self._data["calls"] += 1
            phase_key = phase or "unknown"
            phase_row = self._data["by_phase"].setdefault(phase_key, {"tokens": 0, "usd": 0.0, "calls": 0})
            phase_row["tokens"] += total
            phase_row["usd"] = round(phase_row["usd"] + usd, 6)
            phase_row["calls"] += 1
            model_key = model or "unknown"
            model_row = self._data["by_model"].setdefault(model_key, {"tokens": 0, "usd": 0.0, "calls": 0})
            model_row["tokens"] += total
            model_row["usd"] = round(model_row["usd"] + usd, 6)
            model_row["calls"] += 1
            self._save()
            return {"usd": usd, "tokens": total, "estimated_usd_total": self._data["estimated_usd"]}

    # ---- read-side ----

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def _usd_ratio(self) -> float:
        if self.limits.max_usd <= 0:
            return 0.0
        return self._data["estimated_usd"] / self.limits.max_usd

    def _token_ratio(self) -> float:
        if self.limits.max_tokens <= 0:
            return 0.0
        return self._data["total_tokens"] / self.limits.max_tokens

    def is_exhausted(self) -> bool:
        if self.limits.is_unlimited():
            return False
        if self.limits.max_usd > 0 and self._data["estimated_usd"] >= self.limits.max_usd:
            return True
        if self.limits.max_tokens > 0 and self._data["total_tokens"] >= self.limits.max_tokens:
            return True
        return False

    def should_degrade(self) -> bool:
        if self.limits.is_unlimited() or self.is_exhausted():
            return False
        ratio = max(self._usd_ratio(), self._token_ratio())
        return ratio >= max(0.0, min(1.0, self.limits.degrade_ratio))

    def status(self) -> str:
        if self.is_exhausted():
            return "exhausted"
        if self.should_degrade():
            return "degrade"
        return "ok"


# ---------------------------------------------------------------------------
# Metered client proxy
# ---------------------------------------------------------------------------


class _MeteredCompletions:
    def __init__(self, inner, ledger: BudgetLedger, get_phase, get_model):
        self._inner = inner
        self._ledger = ledger
        self._get_phase = get_phase
        self._get_model = get_model

    def create(self, *args, **kwargs):
        response = self._inner.create(*args, **kwargs)
        try:
            usage = getattr(response, "usage", None)
            model = kwargs.get("model") or (self._get_model() if callable(self._get_model) else "") or ""
            prompt = _usage_field(usage, "prompt_tokens")
            completion = _usage_field(usage, "completion_tokens")
            phase = self._get_phase() if callable(self._get_phase) else (self._get_phase or "")
            self._ledger.record(prompt_tokens=prompt, completion_tokens=completion, model=model, phase=phase)
        except Exception:
            # Metering must never break the actual completion path.
            pass
        return response


class _MeteredChat:
    def __init__(self, inner, ledger, get_phase, get_model):
        self._inner = inner
        self.completions = _MeteredCompletions(inner.completions, ledger, get_phase, get_model)


class MeteredLLMClient:
    """Transparent proxy that meters ``chat.completions.create`` into a ledger.

    Any attribute other than ``chat`` is delegated to the wrapped client, so it
    is a drop-in replacement wherever an OpenAI-style client is expected.
    """

    def __init__(self, inner, ledger: BudgetLedger, *, get_phase=None, get_model=None):
        self._inner = inner
        self._ledger = ledger
        self.chat = _MeteredChat(inner.chat, ledger, get_phase, get_model)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _usage_field(usage, key: str) -> int:
    if usage is None:
        return 0
    try:
        if isinstance(usage, dict):
            return int(usage.get(key) or 0)
        return int(getattr(usage, key, 0) or 0)
    except Exception:
        return 0


def make_metered_client(ledger: BudgetLedger, *, inner=None, get_phase=None, get_model=None):
    """Create a metered client wrapping ``inner`` (or a fresh config client)."""
    if inner is None:
        from core import config

        inner = config.create_llm_client()
    return MeteredLLMClient(inner, ledger, get_phase=get_phase, get_model=get_model)


__all__ = [
    "BudgetLedger",
    "BudgetLimits",
    "ModelTiers",
    "MeteredLLMClient",
    "make_metered_client",
    "price_per_1k",
    "estimate_usd",
    "TIER_NAMES",
]
