# AutoResearch v2 Roadmap

## Goal

Build an AutoResearch framework that can run long-lived research loops on large projects without losing context, overfitting to one benchmark shape, or hiding failure modes.

The target architecture is:

```text
Outer lifecycle phases:
  Plan -> Execute -> Run -> Evaluate -> Compress -> Gate

Inner bounded loops:
  Each phase may run a small task-specific agent/tool loop, but must persist state before releasing context.

Durable state:
  program.md        L0/L1 constitution + evolving belief
  project.md        L2 project overview and coarse state
  .autoresearch/    machine state, monitor, debug, artifacts, todo_state
  .auto/            compact implementation notes
  git               code version boundary and rollback/commit substrate
```

## Current Assessment

The current v2 state machine is useful as a lifecycle skeleton, but the implementation is still too rigid for large projects:

- Plan writes prose into `.auto/plan.md`; Execute reparses that prose and loses intent.
- Execute is still too close to "turn todo text into a file edit" rather than an inner read/edit/verify loop.
- Run currently uses generic train/eval execution and cheap-loop heuristics; it should be driven by per-task run specs.
- Evaluate records outcomes, but does not yet produce strong gate signals such as `plan_still_valid`, `plateau_counter`, task completion state, or recommended next action.
- Debug visibility exists (`monitor.json`, `debug.jsonl`, `inflight.json`) but needs a higher-level diagnostic summary.

The desired direction is not to remove phase boundaries. Phase boundaries are valuable for context control, persistence, and recovery. The needed change is to make each phase internally flexible while keeping phase outputs structured.

## Design Principles

1. Outer phases are lifecycle boundaries, not rigid action scripts.
2. Inner phase work should be bounded, inspectable, and resumable.
3. Plan/Execute/Run/Evaluate communicate through structured state, not Markdown parsing.
4. Run behavior belongs to task-level run specs, not global framework heuristics.
5. Evaluation must produce control signals, not just notes.
6. Debug output should answer "what is it doing now and why is it stuck?" without LLM calls.
7. The framework should default to general project behavior; benchmark-specific search helpers must be opt-in or project-provided.

## Implementation Plan

### Phase 1: Structured Todo State

Create `.autoresearch/todo_state.json` as the primary contract between Plan, Execute, Run, and Evaluate.

Planned schema:

```json
{
  "version": 1,
  "updated_at": 0,
  "tasks": [
    {
      "task_id": "t1",
      "goal": "short objective",
      "type": "implementation|experiment|validation|analysis|maintenance",
      "status": "pending|in_progress|verified|failed|blocked|skipped",
      "priority": 0,
      "allowed_paths": ["train/**"],
      "context_paths": ["train/train.py", "program.md"],
      "plan_summary": "",
      "run_spec": {},
      "verification": {},
      "artifacts": [],
      "last_result": {},
      "lessons": []
    }
  ]
}
```

Deliverables:

- Parser/writer helpers in `core/autoresearch_memory.py` or a new `core/autoresearch_todo_state.py`.
- Plan handler writes structured tasks.
- Execute handler reads ready tasks from `todo_state.json`.
- Existing `.auto/plan.md` becomes human-readable mirror only.

### Phase 2: Bounded Execute Inner Loop

Replace "one todo -> one LLM action" with a bounded task loop:

```text
load task -> read relevant files -> edit -> local verification -> update task status
```

Deliverables:

- Execute loop budget: max actions, max wall time, max changed files.
- Each task records verification commands/results.
- Execute updates task status atomically.
- Non-implementation tasks are not sent to edit LLM.

### Phase 3: Per-Task Run Spec

Replace global `run_strategy` thinking with a per-task `run_spec`.

Example:

```json
{
  "mode": "single|loop|long_job|custom_sequence",
  "commands": ["bash train/train.sh", "bash eval.sh"],
  "max_iters": 1,
  "max_seconds": 600,
  "cheap_threshold_seconds": 2.0,
  "stop_condition": {
    "type": "plateau|metric_threshold|command_success|manual",
    "patience": 3,
    "metric": "primary_metric",
    "threshold": null
  }
}
```

Deliverables:

- Generic run-spec interpreter.
- `single`, `loop`, and `long_job` built-in modes.
- No benchmark-specific numeric probing in the default path.
- Task run results stored back into `todo_state.json`.

### Phase 4: Strong Evaluate Signals

Evaluate should update control signals used by Gate.

Deliverables:

- Compare current experiment with previous best/Pareto state.
- Update per-task `last_result`, `status`, and `lessons`.
- Compute:
  - `pareto_changed`
  - `plateau_counter`
  - `plan_still_valid`
  - `needs_replan`
  - `blocked_reason`
- Persist signals in machine-readable state.

### Phase 5: Repository and Safety Preconditions

Large-project AutoResearch should not silently operate in the wrong git repository.

Deliverables:

- Startup validation for target project:
  - is git repo?
  - has baseline commit?
  - worktree dirty?
  - `.autoresearch` ignored or safely handled?
- Clear warnings or refusal when versioning is requested but target is not a standalone repo.
- Safe cleanup of stale STOP/inflight files.

### Phase 6: Debug Summary

Upgrade `/autoresearch debug show` from tail output to a diagnostic report.

Deliverables:

- Current in-flight operation and age.
- Recent phase durations.
- Recent LLM call durations and prompt sizes.
- Recent shell commands and return codes.
- Current best metric and last metric.
- Staleness diagnosis.
- Likely stuck reason.

### Phase 7: Documentation and Migration

Document the architecture and migration from current v2 behavior.

Deliverables:

- Update `AUTORESEARCH_DESIGN_v2.md`.
- Update `AUTORESEARCH_小学生版.md` after behavior stabilizes.
- Add "large project checklist".
- Add examples for:
  - cheap unit-test loop
  - long training job
  - black-box toy task
  - analysis-only task

## Progress

- [x] v2 phase state machine exists.
- [x] Non-blocking `/autoresearch run`, `/autoresearch show`, `/autoresearch kill`.
- [x] Debug files: `.autoresearch/debug/debug.jsonl`, `.autoresearch/debug/inflight.json`.
- [x] Monitor shows completed token count and in-flight state.
- [x] Hard-coded solved threshold removed; solved now requires explicit `solved_metric_threshold`.
- [x] Hard-coded numeric black-box probe removed from the generic run loop.
- [x] Phase 1a: pure `todo_state.json` helpers and tests.
- [x] Phase 1b: Plan writes structured tasks into `todo_state.json` and mirrors them into `.auto/plan.md`.
- [x] Phase 2a: Execute reads ready tasks from `todo_state.json` and updates task status.
- [ ] Phase 2b: Bounded Execute inner loop with per-task read/edit/verify cycles.
- [x] Phase 3a: Run reads per-task `run_spec.commands` and updates task result.
- [ ] Phase 3b: richer run spec modes (`single`, `loop`, `long_job`) with monitoring semantics.
- [x] Phase 4a: Evaluate writes `gate_signals.json`; PhaseController reads it into `PhaseSignals`.
- [ ] Phase 4b: Evaluate updates per-task status and richer replan reasons.
- [ ] Git preflight validation.
- [ ] Debug diagnostic summary.
- [ ] Large-project docs.

## Open Questions

1. Should Plan always create `todo_state.json`, or should it only do so after enough project survey evidence exists?
2. Should dynamic project-local run specs allow arbitrary shell commands, or only a small safe command DSL?
3. How strict should git preflight be: warning-only, or refuse when versioning is enabled and repo is missing?
4. How should long-job monitoring integrate with existing task logs and external training platforms?

## Next Step

Implement Phase 1: `todo_state.json` helpers and make Plan produce structured tasks while keeping `.auto/plan.md` as a readable mirror.
