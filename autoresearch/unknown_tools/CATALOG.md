# Unknown Tools Catalog

These behaviors are intentionally off the main reading path after the cleanup.
They still live in `autoresearch/legacy/loop.py` or adjacent modules because
tests or the V3 runner still depend on shared pieces from that module.

Review candidates:

- Legacy fixed workflow: `FixedAutoResearchPlanner` and
  `EvolutionaryAutoResearchPlanner`.
- Legacy standalone entrypoint: `auto_research_run` and
  `auto_research_status`.
- Web action surface: `web_search` and `web_extract` actions inside the legacy
  loop.
- Git versioning policies beyond artifact storage: `commit_pareto`,
  `commit_all_trials`, and `branch_per_trial`.
- Old round progress renderer for the legacy loop.

Keep for now:

- `AutoResearchSettings`, `AutoResearchAction`, `AutoResearchStepAgent`, and
  the action/result dataclasses used by V3 execution.
- `ProjectBoundary`, `ProjectConfinedCommandRunner`, artifact storage, patch
  application, metric parsing, source snapshots, and experiment finalization.
- Historical `autoresearch_*.py` alias modules have already been removed from
  the package; keep new code on the direct module paths.

Decision rule:

Move a candidate here only after a focused test shows `auto_research_run_v2`
still works without importing it. If it is still required by the V3 path, either
leave it in `legacy/loop.py` or extract it into a named service module instead
of hiding it here.

