# AutoResearch Runtime Package

This directory contains the AutoResearch subsystem inside the larger R-Agent
repository.  The repository root is R-Agent; this package is only the
AutoResearch runtime and tool implementation.

## Why this package exists

AutoResearch used to be spread across `core/autoresearch_*.py` plus
`tools/autoresearch_tool.py`.  That made it hard to tell which files belonged
to the experiment loop and which files belonged to the general R-Agent core.

The runtime now lives here so the subsystem can be read as one unit:

```text
autoresearch/
  autoresearch_tool.py            # real tool implementation
  autoresearch_loop.py            # legacy workflow loop and shared services
  autoresearch_three_step.py      # current three-step controller
  autoresearch_step_runtime.py    # step policies, tool/skill whitelists
  autoresearch_execution.py       # attempt/run handlers
  autoresearch_personas.py        # plan persona debate and task creation
  autoresearch_phases.py          # public loop entrypoint and phase types
  autoresearch_phase_handlers.py  # init/evaluate/compress handlers
  autoresearch_memory.py          # program/project/.auto/lessons helpers
  autoresearch_todo_state.py      # persisted task state
  autoresearch_monitor.py         # monitor.json status rendering
  autoresearch_debug.py           # debug events and inflight state
  autoresearch_budget.py          # token/cost ledger
  autoresearch_gate_state.py      # persisted gate signals
  autoresearch_preflight.py       # git preflight checks
  autoresearch_timeout.py         # framework-side deadlines
```

## Tool registry shim

`tools/autoresearch_tool.py` intentionally remains in `tools/` as a thin
compatibility shim.  R-Agent's `ToolRegistry.reload_all()` discovers tools by
importing modules from `tools/`, so the shim imports and reloads
`autoresearch.autoresearch_tool` to re-run tool registration after the registry
is cleared.

New AutoResearch runtime code should be added under this package unless it is
shared general R-Agent infrastructure.
