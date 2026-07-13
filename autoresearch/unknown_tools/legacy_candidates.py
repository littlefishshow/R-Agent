"""Review candidates kept off the main AutoResearch reading path.

The objects re-exported here are still implemented in ``autoresearch.legacy``
because current tests and compatibility paths depend on that module. Importing
them through this package makes the "unknown / review later" surface explicit
without duplicating live runtime code.
"""

from autoresearch.legacy.loop import (
    AutoResearchProgressView,
    EvolutionaryAutoResearchPlanner,
    FixedAutoResearchPlanner,
    apply_unified_patch_limited,
)

__all__ = [
    "AutoResearchProgressView",
    "EvolutionaryAutoResearchPlanner",
    "FixedAutoResearchPlanner",
    "apply_unified_patch_limited",
]
