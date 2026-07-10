# autoresearch program

This file is the execution protocol for the autonomous research loop in this project.
It must be customized from the user's prompt, paper, code repository, dataset, and resource constraints.

## Research objective

- Goal: <state the research goal>
- Primary metric: <metric name>
- Direction: <higher_is_better | lower_is_better>
- Baseline: <unknown until first run>
- Resource budget: <max rounds/time/GPU/CPU limits>

## In-scope files

Read before starting:

- `README.md` or project docs
- `prepare.py` if present
- `eval.py` and `eval.sh`
- `train/train.sh`
- training source files under `train/`
- relevant paper notes or repository files supplied by the user

## Fixed files / do not modify without user approval

- `eval.py`
- `eval.sh`
- fixed test/validation data
- metric definitions
- external benchmark protocol

## Setup

1. Confirm the run tag, e.g. `autoresearch/<date-or-topic>`.
2. Check git state: `git status --short`.
3. Create branch: `git checkout -b autoresearch/<tag>`.
4. Install dependencies: `uv sync`.
5. Prepare data if needed: `uv run python prepare.py`.
6. Initialize results:

```bash
printf 'timestamp\tcommit\tprimary_metric\tmetric_name\thigher_is_better\tmemory_gb\tstatus\thypothesis\tchange_summary\tnotes\n' > results.tsv
```

## Baseline

Before any experimental modification:

```bash
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json
```

Record the baseline in `results.tsv` with status `keep`.

## Experiment loop

Repeat only within the user-approved budget.

1. Inspect current best result and git state.
2. Propose exactly one hypothesis:
   - hypothesis:
   - target files:
   - expected metric effect:
   - expected runtime/memory effect:
   - risk:
3. Make the minimal code change required for that hypothesis.
4. Commit before/after as appropriate so rollback is easy.
5. Run:

```bash
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
```

6. Parse:
   - `metrics.json`
   - summary block in `eval.log`
   - errors from `tail -n 80 run.log` or `tail -n 80 eval.log` if needed
7. Decide:
   - keep: primary metric improved, or equal with meaningful simplification;
   - discard: primary metric worsened;
   - crash: no valid metric due to failure/OOM/timeout;
   - repeat: metric noise is too high.
8. Record the result in `results.tsv`.
9. Roll back discarded/crashed changes to the previous best commit.

## Report

Write `reports/autoresearch_report.md` with:

- objective;
- evaluation protocol;
- environment;
- baseline;
- experiment table;
- best result;
- failed ideas;
- conclusion;
- next recommended experiments.

## Stop conditions

Stop when any is reached:

- user-approved round limit;
- user-approved wall-clock limit;
- resource budget exhausted;
- repeated infrastructure failure;
- no valid next hypothesis after documented attempts.

Do not run indefinitely without explicit user approval.
