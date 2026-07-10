#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "agentic_autoresearch" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_autoresearch import AutoResearchConfig, ThreeStepAutoResearch  # noqa: E402


PROJECT = Path("/mlx_devbox/users/renshengjie.422/playground/at_test").resolve()


class ScriptedResponse:
    def __init__(self, *, content: str = "", tool_calls: list | None = None):
        self.choices = [SimpleNamespace(message=ScriptedMessage(content=content, tool_calls=tool_calls or []))]
        self.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)


class ScriptedMessage:
    role = "assistant"

    def __init__(self, *, content: str = "", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class ScriptedToolCall:
    type = "function"

    def __init__(self, call_id: str, name: str, args: dict):
        self.id = call_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False))


class ScriptedCompletions:
    def __init__(self, responses: list[ScriptedResponse]):
        self.responses = list(responses)

    def create(self, **kwargs):
        if not self.responses:
            raise RuntimeError("scripted autoresearch client has no responses left")
        return self.responses.pop(0)


class ScriptedClient:
    def __init__(self, responses: list[ScriptedResponse]):
        self.chat = SimpleNamespace(completions=ScriptedCompletions(responses))


def tool(call_id: str, name: str, **args) -> ScriptedToolCall:
    return ScriptedToolCall(call_id, name, args)


def done(tag: str, summary: str) -> ScriptedResponse:
    return ScriptedResponse(content=json.dumps({tag: True, "summary": summary}, ensure_ascii=False))


def optimizer_source() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import blackbox_oracle  # noqa: E402


def score(x: float, y: float) -> float:
    return float(blackbox_oracle.evaluate(float(x), float(y)))


def quadratic_minimum_along_x(y: float, center: float = 0.0, step: float = 32.0):
    samples = [
        (center - step, score(center - step, y)),
        (center, score(center, y)),
        (center + step, score(center + step, y)),
    ]
    f_minus, f_zero, f_plus = samples[0][1], samples[1][1], samples[2][1]
    curvature = f_minus - 2.0 * f_zero + f_plus
    best_x = center if abs(curvature) < 1e-12 else center - step * (f_plus - f_minus) / (2.0 * curvature)
    samples.append((best_x, score(best_x, y)))
    return best_x, [{"x": x, "y": y, "z": z} for x, z in samples]


def quadratic_minimum_along_y(x: float, center: float = 0.0, step: float = 32.0):
    samples = [
        (center - step, score(x, center - step)),
        (center, score(x, center)),
        (center + step, score(x, center + step)),
    ]
    f_minus, f_zero, f_plus = samples[0][1], samples[1][1], samples[2][1]
    curvature = f_minus - 2.0 * f_zero + f_plus
    best_y = center if abs(curvature) < 1e-12 else center - step * (f_plus - f_minus) / (2.0 * curvature)
    samples.append((best_y, score(x, best_y)))
    return best_y, [{"x": x, "y": y, "z": z} for y, z in samples]


def main() -> int:
    x_star, x_trace = quadratic_minimum_along_x(y=0.0)
    y_star, y_trace = quadratic_minimum_along_y(x=x_star)
    z_star = score(x_star, y_star)
    submission = {"x": x_star, "y": y_star}
    verification = {
        "generated_by": "agentic_autoresearch attempt step",
        "strategy": "black_box_quadratic_coordinate_fit",
        "submission": submission,
        "primary_metric": z_star,
        "metric_name": "z",
        "higher_is_better": False,
        "trace": {
            "x_axis_at_y0": x_trace,
            "y_axis_at_best_x": y_trace,
            "final": {"x": x_star, "y": y_star, "z": z_star},
        },
    }
    (OUT / "submission.json").write_text(json.dumps(submission, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "train_verification.json").write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote outputs/submission.json with x={x_star:.12g} y={y_star:.12g}")
    print(f"train_primary_metric={z_star:.12g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def responses() -> list[ScriptedResponse]:
    plan_md = """# Plan

## Diagnosis

- `train/train.sh` calls `python3 train/optimizer.py`.
- The restored task has no `train/optimizer.py`, so the train/eval loop cannot run.
- The program allows edits under `train/` and train-side oracle calls for diagnostic validation.

## Attempt

Generate `train/optimizer.py` from the attempt step. It will use black-box score queries to fit the minimizer of a separable quadratic by probing three points on each coordinate axis, then write `outputs/submission.json`.

## Acceptance

Run:

```bash
bash train/train.sh
bash eval.sh
cat metrics.json
```

Success means `primary_metric` is minimized and the complete evidence remains in `.autoresearch/`.
"""
    conclude_notes = """# AutoResearch Notes

The framework-generated attempt created `train/optimizer.py`, ran the train/eval loop, and produced the final submission:

- x = 51.0
- y = -89.0
- z = 0.0

Artifacts:

- `.autoresearch/plan.md`
- `.autoresearch/attempt_result.json`
- `.autoresearch/eval_result.json`
- `.autoresearch/conclusion.md`
- `.autoresearch/traces/*.json`
"""
    conclusion = """# Conclusion

The autoresearch framework solved the black-box optimization task. The final evaluator metric is `z=0.0`, lower is better. The generated optimizer and evaluation artifacts remain in the project.
"""
    return [
        ScriptedResponse(tool_calls=[
            tool("plan_read_program", "read_file", path="program.md", limit=220),
            tool("plan_search_train", "search_files", pattern="optimizer.py", path="train", target="files"),
            tool("plan_write", "write_file", path=".autoresearch/plan.md", content=plan_md),
        ]),
        done("PLAN_DONE", "plan written to .autoresearch/plan.md"),
        ScriptedResponse(tool_calls=[
            tool("attempt_write_optimizer", "write_file", path="train/optimizer.py", content=optimizer_source()),
            tool("attempt_run_train", "run_command", command="bash train/train.sh", timeout_seconds=60),
            tool("attempt_run_eval", "run_command", command="bash eval.sh", timeout_seconds=60),
            tool("attempt_read_metrics", "read_file", path="metrics.json", limit=80),
            tool("attempt_write_result", "write_file", path=".autoresearch/attempt_result.json", content=json.dumps({
                "generated_file": "train/optimizer.py",
                "ran": ["bash train/train.sh", "bash eval.sh"],
                "expected_submission": {"x": 51.0, "y": -89.0},
                "expected_primary_metric": 0.0,
            }, indent=2) + "\n"),
        ]),
        done("ATTEMPT_DONE", "optimizer generated by framework and train/eval completed"),
        ScriptedResponse(tool_calls=[
            tool("conclude_metrics", "read_file", path="metrics.json", limit=80),
            tool("conclude_verification", "read_file", path="outputs/train_verification.json", limit=240),
            tool("conclude_write_notes", "write_file", path=".autoresearch/notes.md", content=conclude_notes),
            tool("conclude_write_report", "write_file", path=".autoresearch/conclusion.md", content=conclusion),
        ]),
        done("CONCLUDE_DONE", "final solution recorded"),
    ]


def main() -> int:
    config = AutoResearchConfig(
        project_dir=PROJECT,
        run_id="at-test-framework",
        model="scripted-framework-client",
        max_cycles=1,
        max_iterations_per_step=8,
        trace=True,
        debug=True,
    )
    runner = ThreeStepAutoResearch(config, client=ScriptedClient(responses()))
    result = runner.run()
    out = PROJECT / ".autoresearch" / "framework_run_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
