"""Budget configuration for large tool-result persistence.

R-Agent borrows Hermes-agent's three-layer idea:
1. individual tools may limit/paginate their own output;
2. oversized single tool results are persisted as artifacts;
3. future aggregate turn budgets can spill multiple medium results.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict


# Avoid persist -> read_file -> persist loops. read_file already has pagination.
PINNED_THRESHOLDS: Dict[str, float] = {
    "read_file": float("inf"),
    "artifact_slice": float("inf"),
    "artifact_search": float("inf"),
    "artifact_inspect": float("inf"),
}

DEFAULT_RESULT_SIZE_CHARS = 80_000
DEFAULT_TURN_BUDGET_CHARS = 160_000
DEFAULT_PREVIEW_SIZE_CHARS = 2_000


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class BudgetConfig:
    """Immutable budget constants for tool result persistence."""

    default_result_size: int = field(default_factory=lambda: _env_int("R_AGENT_TOOL_RESULT_MAX_CHARS", DEFAULT_RESULT_SIZE_CHARS))
    turn_budget: int = field(default_factory=lambda: _env_int("R_AGENT_TOOL_TURN_BUDGET_CHARS", DEFAULT_TURN_BUDGET_CHARS))
    preview_size: int = field(default_factory=lambda: _env_int("R_AGENT_TOOL_PREVIEW_CHARS", DEFAULT_PREVIEW_SIZE_CHARS))
    tool_overrides: Dict[str, int] = field(default_factory=dict)

    def resolve_threshold(self, tool_name: str) -> int | float:
        if tool_name in PINNED_THRESHOLDS:
            return PINNED_THRESHOLDS[tool_name]
        if tool_name in self.tool_overrides:
            return self.tool_overrides[tool_name]
        env_key = "R_AGENT_TOOL_MAX_CHARS_" + "".join(
            ch if ch.isalnum() else "_" for ch in str(tool_name).upper()
        )
        if os.getenv(env_key):
            return _env_int(env_key, self.default_result_size)
        return self.default_result_size


DEFAULT_BUDGET = BudgetConfig()
