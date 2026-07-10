# agentic-autoresearch

A small, R-Agent-style autoresearch runner.

This is a fresh implementation. It does not reuse the old fixed planner, phase
handlers, persona planner, or CLI layer. The design keeps the part that matters:
each outer step is a complete tool-calling agent loop.

## Control Model

The runner cycles through three steps:

```text
plan -> attempt -> conclude -> plan -> ...
```

Each step has its own full agent loop:

```text
step context -> LLM -> tool calls -> tool results -> LLM ... -> final answer
```

The outer runner advances only when the final assistant message contains the
step tag as JSON:

```json
{"PLAN_DONE": true}
{"ATTEMPT_DONE": true}
{"CONCLUDE_DONE": true}
```

If the tag is missing, the step is not complete and the runner does not advance.

## What Is Included

- OpenAI-compatible tool-calling loop
- Project-confined tools:
  - `read_file`
  - `write_file`
  - `search_files`
  - `run_command`
  - `skill_search`
  - `skill_view`
  - `artifact_write`
- Automatic three-step runner
- File-backed state in `.autoresearch/runner_state.json`
- Monitor heartbeat in `.autoresearch/monitor.json`
- Optional debug events and `inflight.json`
- Optional full per-step traces in `.autoresearch/traces/`

## What Is Not Included

- No CLI
- No background process launcher
- No old autoresearch phase handlers
- No multi-persona planner
- No separate todo state machine

Those can be added later, but the base loop should stay small.

## Usage

```python
from agentic_autoresearch import AutoResearchConfig, ThreeStepAutoResearch

config = AutoResearchConfig(
    project_dir="/path/to/project",
    run_id="exp-001",
    model="your-model",
    max_cycles=5,
    max_iterations_per_step=12,
    trace=True,
    debug=True,
)

runner = ThreeStepAutoResearch(config, client=openai_compatible_client)
result = runner.run()
print(result)
```

The client must support:

```python
client.chat.completions.create(model=..., messages=..., tools=...)
```

Tests use a fake client, so the core behavior is verified without network calls.

## Manual Test CLI

This project has a standalone CLI for manual testing. It is not wired into the
main R-Agent loop.

From the parent repo:

```bash
PYTHONPATH=agentic_autoresearch/src python3 -m agentic_autoresearch.cli run /path/to/project \
  --max-cycles 3 \
  --max-iterations-per-step 12 \
  --debug
```

Read status:

```bash
PYTHONPATH=agentic_autoresearch/src python3 -m agentic_autoresearch.cli status /path/to/project
PYTHONPATH=agentic_autoresearch/src python3 -m agentic_autoresearch.cli status /path/to/project --json
```

Request graceful stop or resume:

```bash
PYTHONPATH=agentic_autoresearch/src python3 -m agentic_autoresearch.cli stop /path/to/project
PYTHONPATH=agentic_autoresearch/src python3 -m agentic_autoresearch.cli stop /path/to/project --resume
```

Show debug tail:

```bash
PYTHONPATH=agentic_autoresearch/src python3 -m agentic_autoresearch.cli debug /path/to/project --tail 80
```

If installed as a package, replace
`PYTHONPATH=agentic_autoresearch/src python3 -m agentic_autoresearch.cli` with:

```bash
agentic-autoresearch
```

## State Files

```text
<project>/
  program.md
  project.md
  .autoresearch/
    runner_state.json
    monitor.json
    plan.md
    attempt.md
    notes.md
    artifacts/
    traces/
    debug/
      debug.jsonl
      inflight.json
```

Durable knowledge should go into project files or `.autoresearch` files. The
agent loop message history is step-local and discarded after each step trace.
