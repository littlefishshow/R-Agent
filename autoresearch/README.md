# AutoResearch Runtime Package

This directory contains the AutoResearch subsystem inside the larger R-Agent
repository.  The repository root is R-Agent; this package is only the
AutoResearch runtime and tool implementation.

## Why this package exists

AutoResearch used to be spread across `core/autoresearch_*.py` plus
`tools/autoresearch_tool.py`.  That made it hard to tell which files belonged
to the experiment loop and which files belonged to the general R-Agent core.

The runtime now lives here so the subsystem can be read as one unit.  The main
reading path is intentionally short:

```text
autoresearch/
  tool.py                 # real tool implementation and registry handlers
  phases.py               # public V3 loop entrypoint and phase dataclasses
  controller.py           # current plan -> attempt -> conclude controller
  planner.py              # plan persona debate and task DAG creation
  execution.py            # attempt/run handlers
  runtime_policy.py       # step policies, tool/skill whitelists
  phase_handlers.py       # deterministic init/evaluate/compress helpers
  preflight.py            # git preflight checks

  state/
    memory.py             # program.md/project.md/.auto/lessons helpers
    todo.py               # persisted task state
    gates.py              # persisted gate signals
    completion.py         # Completion Criteria parsing

  observability/
    monitor.py            # monitor.json status rendering
    debug.py              # debug events and inflight state
    budget.py             # token/cost ledger
    timeout.py            # framework-side deadlines

  legacy/
    loop.py               # old loop plus shared services kept for compatibility

  unknown_tools/          # quarantine for unclear helpers after future pruning
```

The old `autoresearch_*.py` compatibility files have been removed from this
package. Repository code and tests should import the modules shown above
directly.

## Tool registry shim

`tools/autoresearch_tool.py` intentionally remains in `tools/` as a thin
compatibility shim.  R-Agent's `ToolRegistry.reload_all()` discovers tools by
importing modules from `tools/`, so the shim imports and reloads
`autoresearch.tool` to re-run tool registration after the registry is cleared.

New AutoResearch runtime code should be added under this package unless it is
shared general R-Agent infrastructure.

## Built-in benchmark examples

This package also contains small deterministic benchmark projects under:

```text
autoresearch/benchmarks/atr_playground/
```

These projects are used as local smoke tests and examples for the AutoResearch
loop. They are intentionally CPU-only, network-free, and metric-driven. A typical
project contains `program.md`, `prepare.py`, `train/train.sh`, `eval.sh`, and
`metrics.json`.

Example:

```bash
python main.py
# then in the R-Agent CLI:
/autoresearch run autoresearch/benchmarks/atr_playground/json_repair_micro
```

The benchmark directory is part of the R-Agent repository, not a nested git
repository. Runtime artifacts such as `.auto/`, `.autoresearch/`, `__pycache__/`,
and logs should remain untracked.

