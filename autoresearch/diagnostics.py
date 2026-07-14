"""Project diagnostics used by AutoResearch execute/run phases."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from autoresearch.state.memory import write_auto_note
from autoresearch.state.regression import write_regression_cases


_TARGET_LOAD_RE = re.compile(r"spec_from_file_location\([^\n]*?Path\(['\"]([^'\"]+)['\"]\)", re.DOTALL)
_DEF_RE = re.compile(r"^def\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE)

RESULT_ARTIFACT_PATHS = (
    "outputs/submission.json",
    "outputs/train_verification.json",
    "outputs/predictions.json",
    "submission/predictions.json",
    "predictions.json",
    "metrics.json",
    "results.json",
    "train/candidate.json",
)


def eval_contract_digest(root: str | Path, *, max_chars: int = 5000) -> str:
    """Create a compact, factual digest of how eval.py evaluates the project."""
    root = Path(root)
    eval_path = root / "eval.py"
    if not eval_path.exists():
        return ""
    try:
        eval_text = eval_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    metrics = read_json(root / "metrics.json")
    targets = sorted(set(_TARGET_LOAD_RE.findall(eval_text)))
    function_names = []
    for target in targets:
        path = root / target
        if path.exists() and path.is_file():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                function_names.extend(_DEF_RE.findall(source)[:12])
            except Exception:
                pass
    for name in ("solve_case", "solve", "rank", "normalize", "repair_json", "decode_text", "is_anomaly", "clean_row"):
        if re.search(r"\." + re.escape(name) + r"\b|\b" + re.escape(name) + r"\(", eval_text):
            function_names.append(name)
    function_names = sorted(set(function_names))
    failures = metrics.get("failures") if isinstance(metrics, dict) else []
    lines = [
        "# Eval Contract Digest",
        "",
        "Facts extracted mechanically from eval.py and metrics.json. Use these facts to decide what to edit; the framework is not prescribing a target file.",
        "",
        f"- eval_file: eval.py ({len(eval_text)} chars)",
    ]
    if targets:
        lines.append(f"- eval_import_targets: {', '.join(targets)}")
    if function_names:
        lines.append(f"- referenced_or_available_functions: {', '.join(function_names[:16])}")
    if isinstance(metrics, dict) and metrics:
        metric_name = metrics.get("primary_metric_name") or metrics.get("metric_name") or "primary_metric"
        metric_value = metrics.get("primary_metric", metrics.get(metric_name))
        lines.append(f"- current_metric: {metric_name}={metric_value} higher_is_better={metrics.get('higher_is_better')}")
        for key in ("accuracy", "row_accuracy", "cell_accuracy", "exact_accuracy", "top1_accuracy", "runtime_sec", "runtime_seconds", "score"):
            if key in metrics:
                lines.append(f"- {key}: {metrics.get(key)}")
    lines.extend(["", "## Eval head", "```python", eval_text[:1800].rstrip(), "```"])
    if isinstance(failures, list) and failures:
        lines.extend(["", "## Current failures from metrics.json"])
        for item in failures[:10]:
            lines.append("- " + json.dumps(item, ensure_ascii=False, sort_keys=True)[:800])
    text = "\n".join(lines).rstrip() + "\n"
    if len(text) > max_chars:
        text = text[: max_chars - 40].rstrip() + "\n...<eval contract clipped>...\n"
    return text


def write_eval_contract_digest(root: str | Path) -> str:
    text = eval_contract_digest(root)
    if not text:
        return ""
    return str(write_auto_note(root, "eval_contract", text))


def failure_digest(root: str | Path, *, max_chars: int = 5000) -> str:
    root = Path(root)
    # If the last run crashed, its stderr/traceback is the most important fact —
    # a stale metrics.json would otherwise hide it and the LLM would keep tuning
    # a metric while the eval is actually failing to run.
    run_failure = ""
    rf_path = root / ".auto" / "run_failure.md"
    if rf_path.exists():
        try:
            rf_text = rf_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            rf_text = ""
        # Only treat it as an active failure if it was not marked resolved.
        if rf_text and "(resolved:" not in rf_text:
            run_failure = rf_text
    metrics = read_json(root / "metrics.json")
    if not isinstance(metrics, dict) or not metrics:
        if run_failure:
            return (
                "# Failure Digest\n\n"
                "The latest official run FAILED before producing metrics. Fix the error below first.\n\n"
                + run_failure
            )
        return ""
    metric_name = metrics.get("primary_metric_name") or metrics.get("metric_name") or "primary_metric"
    metric_value = metrics.get("primary_metric", metrics.get(metric_name))
    failures = metrics.get("failures")
    lines = [
        "# Failure Digest",
        "",
        "Facts extracted mechanically after the latest official evaluation. Use this to make the next patch narrow and evidence-driven.",
        "",
    ]
    if run_failure:
        # metrics.json exists but the latest run crashed, so the metric below is
        # stale. Make the crash the headline and keep the (stale) metric labeled.
        lines.extend([
            "> WARNING: the latest run FAILED; the metric below is STALE. Fix the error first.",
            "",
            run_failure.strip(),
            "",
        ])
    lines.append(
        f"- metric ({'STALE — run failed' if run_failure else 'latest'}): "
        f"{metric_name}={metric_value} higher_is_better={metrics.get('higher_is_better')}"
    )
    for key in ("accuracy", "row_accuracy", "cell_accuracy", "exact_accuracy", "top1_accuracy", "runtime_sec", "runtime_seconds", "score"):
        if key in metrics:
            lines.append(f"- {key}: {metrics.get(key)}")
    if isinstance(failures, list) and failures:
        lines.extend(["", "## Failed cases"])
        for item in failures[:12]:
            lines.append("- " + json.dumps(item, ensure_ascii=False, sort_keys=True)[:900])
    else:
        lines.append("- failures: none listed in metrics.json")
    diagnostics = derived_failure_diagnostics(root, failures=failures if isinstance(failures, list) else [])
    if diagnostics:
        lines.extend(["", "## Derived diagnostics"])
        lines.extend(f"- {line}" for line in diagnostics[:24])
    # Executable per-case regression check: re-run the recorded failing cases
    # against the current solution and show input/expected/actual, so the next
    # patch is driven by concrete cases rather than only an aggregate score.
    try:
        from autoresearch.state.regression_check import run_regression_check

        regression_out = run_regression_check(root)
    except Exception:
        regression_out = ""
    if regression_out:
        lines.extend([
            "",
            "## Per-case regression check (current solution vs recorded failures)",
            "```",
            regression_out.rstrip(),
            "```",
        ])
    text = "\n".join(lines).rstrip() + "\n"
    if len(text) > max_chars:
        text = text[: max_chars - 40].rstrip() + "\n...<failure digest clipped>...\n"
    return text


def write_failure_digest(root: str | Path) -> str:
    text = failure_digest(root)
    if not text:
        return ""
    write_regression_cases(root)
    return str(write_auto_note(root, "failure_digest", text))


def derived_failure_diagnostics(root: str | Path, failures: list | None = None) -> list[str]:
    root = Path(root)
    metrics = read_json(root / "metrics.json")
    lines: list[str] = []
    if not isinstance(metrics, dict):
        metrics = {}
    if metrics.get("accuracy") == 1.0 and metrics.get("runtime_sec") is not None:
        lines.append(
            "correctness is already 1.0; this is now a performance optimization problem "
            f"(runtime_sec={metrics.get('runtime_sec')}, score={metrics.get('score')})"
        )
    if failures:
        lines.extend(_failure_case_hints(failures))
    lines.extend(json_mapping_diffs(root / "data" / "truth.json", root / "submission" / "predictions.json"))
    return lines


def json_mapping_diffs(expected_path: Path, actual_path: Path, *, max_rows: int = 12) -> list[str]:
    expected = read_json(expected_path)
    actual = read_json(actual_path)
    if not isinstance(expected, dict) or not isinstance(actual, dict) or not expected:
        return []
    lines = []
    field_counts: dict[str, int] = {}
    pair_counts: dict[str, int] = {}
    for key in sorted(expected)[:500]:
        exp = expected.get(key)
        act = actual.get(key)
        if exp == act:
            continue
        if isinstance(exp, dict) and isinstance(act, dict):
            bad_fields = {
                field: {"expected": exp.get(field), "actual": act.get(field)}
                for field in sorted(exp)
                if exp.get(field) != act.get(field)
            }
            for field, values in bad_fields.items():
                field_counts[field] = field_counts.get(field, 0) + 1
                pair_key = json.dumps({field: values}, ensure_ascii=False, sort_keys=True)
                pair_counts[pair_key] = pair_counts.get(pair_key, 0) + 1
            lines.append(json.dumps({"id": key, "field_diffs": bad_fields}, ensure_ascii=False, sort_keys=True)[:900])
        else:
            lines.append(json.dumps({"id": key, "expected": exp, "actual": act}, ensure_ascii=False, sort_keys=True)[:900])
        if len(lines) >= max_rows:
            break
    if lines:
        lines.insert(0, f"derived mismatch rows from {expected_path.name} vs {actual_path.name}: {len(lines)} shown")
        if field_counts:
            summary = ", ".join(f"{field}={count}" for field, count in sorted(field_counts.items(), key=lambda x: (-x[1], x[0])))
            lines.insert(1, f"field mismatch counts: {summary}")
        for pair, count in reversed(sorted(pair_counts.items(), key=lambda x: (-x[1], x[0]))[:5]):
            lines.insert(2, f"repeated field diff x{count}: {pair}")
    return lines


def _failure_case_hints(failures: list) -> list[str]:
    hints: list[str] = []
    false_negative_patterns: dict[str, int] = {}
    for item in failures[:20]:
        if not isinstance(item, dict):
            continue
        line = str(item.get("line") or "")
        expected = item.get("expected")
        pred = item.get("pred")
        if line and expected is True and pred is False:
            for token in _log_rule_tokens(line):
                false_negative_patterns[token] = false_negative_patterns.get(token, 0) + 1
        for field in ("input", "expected", "pred", "repaired", "parsed"):
            value = item.get(field)
            if isinstance(value, str) and _needs_repr_hint(value):
                hints.append(f"{field} repr={value!r} codepoints={_codepoints(value)}")
    if false_negative_patterns:
        summary = ", ".join(f"{token}={count}" for token, count in sorted(false_negative_patterns.items(), key=lambda x: (-x[1], x[0])))
        hints.insert(0, f"log false-negative rule candidates: {summary}")
    return hints[:16]


def _log_rule_tokens(line: str) -> list[str]:
    lowered = line.lower()
    tokens = []
    for pattern in (
        r"status=(5\d\d)", r"latency_ms=(\d+)", r"p95_latency_ms=(\d+)",
        r"disk usage (\d+) percent", r"retry_count=(\d+)", r"repeated=(\d+)", r"memory_mb=(\d+)",
    ):
        match = re.search(pattern, lowered)
        if match:
            tokens.append(match.group(0))
    for marker in ("oom_kill_risk=true", "timeout storm", "fatal", "panic", "exception", "failed"):
        if marker in lowered:
            tokens.append(marker)
    return tokens


def _needs_repr_hint(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) > 126 for ch in value) or "\xa0" in value or "\\t" in value or "\\f" in value


def _codepoints(value: str, limit: int = 32) -> str:
    points = [f"U+{ord(ch):04X}" for ch in value[:limit]]
    suffix = " ..." if len(value) > limit else ""
    return " ".join(points) + suffix


def eval_import_targets(root: str | Path) -> list[str]:
    root = Path(root)
    eval_path = root / "eval.py"
    if not eval_path.exists():
        return []
    try:
        text = eval_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    return sorted(set(_TARGET_LOAD_RE.findall(text)))


def is_direct_eval_target(root: str | Path, rel_path: str) -> bool:
    rel = str(rel_path or "").lstrip("./")
    return rel in set(eval_import_targets(root))


def direct_eval_import_check(loop, rel_path: str) -> dict:
    from autoresearch.legacy.loop import AutoResearchAction

    rel = str(rel_path or "").lstrip("./")
    module_name = "_autoresearch_smoke_" + re.sub(r"[^A-Za-z0-9_]+", "_", Path(rel).stem)
    code = (
        "import importlib.util\n"
        "from pathlib import Path\n"
        f"path = Path({rel!r})\n"
        f"spec = importlib.util.spec_from_file_location({module_name!r}, path)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print('import_ok')\n"
    )
    action = AutoResearchAction(type="run", rationale="execute direct eval import smoke", command="python3 -c " + shlex.quote(code))
    return loop.runner.run(action.command)


def project_fingerprint(root: str | Path) -> dict:
    root = Path(root)
    payload: dict[str, dict] = {}
    for rel in RESULT_ARTIFACT_PATHS:
        path = root / rel
        if not path.exists() or not path.is_file():
            payload[rel] = {"exists": False}
            continue
        try:
            data = path.read_bytes()
        except Exception:
            payload[rel] = {"exists": True, "readable": False}
            continue
        payload[rel] = {
            "exists": True,
            "size": len(data),
            "sha1": __import__("hashlib").sha1(data).hexdigest()[:12],
        }
    return payload


def changed_result_artifacts(before: dict, after: dict) -> list[str]:
    return [
        rel for rel, value in (after or {}).items()
        if value != (before or {}).get(rel) and value.get("exists")
    ]


def has_visible_result_artifacts(*snapshots: dict) -> bool:
    for snapshot in snapshots:
        for value in (snapshot or {}).values():
            if isinstance(value, dict) and value.get("exists"):
                return True
    return False


def read_project_json(root: str | Path, rel: str) -> dict:
    path = Path(root) / rel
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def parse_higher_is_better(value, default: bool = True) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"false", "0", "no", "lower", "min", "minimize"}:
            return False
        if text in {"true", "1", "yes", "higher", "max", "maximize"}:
            return True
        return default
    if value is None:
        return default
    return bool(value)


def compact_json_value(data: dict, *, max_chars: int = 900) -> dict:
    """Return a JSON-safe small dict for prompt carry-forward."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[str(key)] = value
        elif isinstance(value, list):
            out[str(key)] = value[:8]
        elif isinstance(value, dict):
            out[str(key)] = {str(k): v for k, v in list(value.items())[:8] if isinstance(v, (str, int, float, bool)) or v is None}
        else:
            out[str(key)] = str(value)[:200]
    surface = json.dumps(out, ensure_ascii=False, default=str)
    if len(surface) <= max_chars:
        return out
    return {"truncated": surface[: max_chars - 3].rstrip() + "..."}


# --------------------------------------------------------------------------- #
# Submission artifact schema pre-validation
# --------------------------------------------------------------------------- #

# Prediction artifacts an eval typically indexes by case id.
_SUBMISSION_ARTIFACTS = (
    "submission/predictions.json",
    "predictions.json",
    "outputs/predictions.json",
    "outputs/submission.json",
)
# Reference files that define the expected case-id key set.
_REFERENCE_FILES = (
    "data/truth.json",
    "data/test_cases.json",
    "data/cases.json",
)


def _reference_ids(root: Path) -> tuple[str, set[str]]:
    """Return (reference_file, id_set) from truth/test_cases if present.

    Supports a dict keyed by id (truth.json) or a list of {"id": ...} records
    (test_cases.json). Returns ("", set()) when no reference is available.
    """
    for rel in _REFERENCE_FILES:
        path = root / rel
        if not path.exists() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(data, dict) and data:
            return rel, {str(k) for k in data.keys()}
        if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
            ids = {str(x.get("id")) for x in data if x.get("id") is not None}
            if ids:
                return rel, ids
    return "", set()


def validate_submission_artifacts(root: str | Path) -> dict:
    """Check that a prediction artifact has the shape the fixed eval expects.

    Many evals do ``preds.get(case_id)`` on a JSON that must be a dict keyed by
    case id. When the model writes a list (or misses ids), the fixed eval.py
    crashes with an opaque traceback and the whole run fails. Catching the shape
    mismatch *before* running eval turns "先跑崩再靠 traceback 事后补救" into an
    explicit, actionable diagnostic.

    Returns {"ok": bool, "checked": bool, "artifact": str, "problems": [str],
    "diagnostic": str}. ``checked`` is False when there is nothing to validate
    (no prediction artifact / no reference id set) so callers can no-op safely.
    """
    root = Path(root)
    ref_file, ref_ids = _reference_ids(root)
    artifact = ""
    for rel in _SUBMISSION_ARTIFACTS:
        if (root / rel).exists():
            artifact = rel
            break
    if not artifact or not ref_ids:
        return {"ok": True, "checked": False, "artifact": artifact, "problems": [], "diagnostic": ""}

    path = root / artifact
    problems: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        problems.append(f"could not read {artifact}: {exc}")
        return _submission_result(False, artifact, ref_file, problems, ref_ids, None)
    try:
        data = json.loads(raw)
    except Exception as exc:
        problems.append(f"{artifact} is not valid JSON: {type(exc).__name__}: {exc}")
        return _submission_result(False, artifact, ref_file, problems, ref_ids, None)

    if not isinstance(data, dict):
        problems.append(
            f"{artifact} must be a JSON object keyed by case id (the eval does "
            f"preds.get(id)), but it is a {type(data).__name__}. Rewrite it as "
            f'{{"<id>": <prediction>, ...}} using ids from {ref_file}.'
        )
        return _submission_result(False, artifact, ref_file, problems, ref_ids, data)

    pred_ids = {str(k) for k in data.keys()}
    missing = ref_ids - pred_ids
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        problems.append(
            f"{artifact} is missing {len(missing)}/{len(ref_ids)} required case ids "
            f"(e.g. {sample}). Every id in {ref_file} must have a prediction."
        )
    ok = not problems
    return _submission_result(ok, artifact, ref_file, problems, ref_ids, data)


def _submission_result(ok: bool, artifact: str, ref_file: str, problems: list, ref_ids: set, data) -> dict:
    diagnostic = ""
    if not ok:
        lines = [
            "# Submission Schema Problem",
            "",
            "The prediction artifact does not match the shape the fixed evaluator expects. "
            "Fix the artifact structure BEFORE re-running eval; otherwise eval.py will crash.",
            "",
            f"- artifact: {artifact}",
            f"- reference: {ref_file} ({len(ref_ids)} expected case ids)",
            "",
            "## Problems",
        ]
        lines.extend(f"- {p}" for p in problems)
        example_id = sorted(ref_ids)[0] if ref_ids else "row_000"
        lines.extend([
            "",
            "## Required shape",
            "```json",
            f'{{"{example_id}": <prediction for that case>, "...": "..."}}',
            "```",
        ])
        diagnostic = "\n".join(lines).rstrip() + "\n"
    return {"ok": ok, "checked": True, "artifact": artifact, "problems": problems, "diagnostic": diagnostic}


def metric_payload(root: str | Path) -> dict:
    train_v = read_project_json(root, "outputs/train_verification.json")
    metrics = read_project_json(root, "metrics.json")
    submission = read_project_json(root, "outputs/submission.json")
    predictions = read_project_json(root, "submission/predictions.json") or read_project_json(root, "predictions.json")
    metric_source = train_v or metrics
    direction_source = metrics or train_v
    metric = metric_source.get("z", metric_source.get("primary_metric"))
    try:
        metric_value = float(metric)
    except Exception:
        metric_value = None
    higher = parse_higher_is_better(direction_source.get("higher_is_better", True) if direction_source else True)
    return {
        "metric": metric_value,
        "metric_name": metric_source.get("metric_name", "primary_metric") if metric_source else "",
        "higher_is_better": higher,
        "submission": compact_json_value(submission, max_chars=500),
        "predictions": compact_json_value(predictions, max_chars=500),
        "train_verification": compact_json_value(train_v, max_chars=900),
        "metrics": compact_json_value(metrics, max_chars=900),
    }


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


__all__ = [
    "changed_result_artifacts",
    "compact_json_value",
    "direct_eval_import_check",
    "eval_contract_digest",
    "eval_import_targets",
    "failure_digest",
    "has_visible_result_artifacts",
    "is_direct_eval_target",
    "json_mapping_diffs",
    "metric_payload",
    "parse_higher_is_better",
    "project_fingerprint",
    "read_json",
    "read_project_json",
    "validate_submission_artifacts",
    "write_eval_contract_digest",
    "write_failure_digest",
]
