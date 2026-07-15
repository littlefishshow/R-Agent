# unknown_tools

This package is the quarantine area for AutoResearch helpers whose purpose is
not clear enough to keep on the main reading path.

Current status:

- No runtime helper has been moved here yet because the current tests still use
  the legacy service module for safety checks, patch application, metrics,
  versioning, and compatibility imports.
- `CATALOG.md` lists the legacy behaviors that should be reviewed next.
- `legacy_candidates.py` re-exports a small set of review candidates from
  `autoresearch.legacy.loop` so they have an explicit import surface without
  duplicating live code.
- Future cleanup can move unused or experimental helpers here once tests prove
  they are not part of `auto_research_run_v2`.

