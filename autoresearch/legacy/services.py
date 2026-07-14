"""Shared low-level services for the legacy AutoResearch loop."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

class AutoResearchSafetyError(RuntimeError):
    pass


def _safe_slug(value: str, default: str = "item", max_len: int = 80) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or default)).strip("._")
    return (value or default)[:max_len]


def _contains_parent_escape(value: str) -> bool:
    return ".." in Path(value).parts


def extract_json_object(text: str) -> dict:
    """Extract a JSON object from raw model text, including markdown fences."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty JSON response")
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start: idx + 1])
    raise ValueError("unterminated JSON object")


_CHANGE_SPEC_KINDS = {"write", "search_replace"}


def _extract_change_spec(text: str) -> dict | None:
    """Best-effort extract a change spec dict from note content.

    Accepts either a raw JSON object, a fenced block, or a JSON snippet
    embedded in prose.  Only returns dicts whose 'kind' is a known change
    spec form -- silently ignores unrelated JSON so plain notes still work.
    """
    if not text or not text.strip():
        return None
    try:
        data = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in _CHANGE_SPEC_KINDS:
        return None
    return data


def _make_unified_diff(target_label: str, old_lines: list[str], new_lines: list[str], *, is_new: bool) -> str:
    import difflib

    label = target_label.lstrip("./")
    from_label = "/dev/null" if is_new else f"a/{label}"
    to_label = f"b/{label}"
    header_prefix = f"diff --git a/{label} b/{label}\n"
    if is_new:
        header_prefix += "new file mode 100644\n"
    diff = difflib.unified_diff(old_lines, new_lines, fromfile=from_label, tofile=to_label, n=3)
    body = "".join(diff)
    if not body.strip():
        return ""
    return header_prefix + body


def parse_primary_metric(text: str) -> dict:
    """Best-effort parse primary metric blocks from logs/JSON text."""
    surface = text or ""
    metric_name = "primary_metric"
    metric = None
    higher_is_better = True

    # Shell artifacts are persisted as JSON wrappers:
    # {"returncode": 0, "stdout": "...primary_metric...", "stderr": "..."}.
    # Parse the project-owned output first so wrapper metadata such as
    # returncode/duration_seconds cannot become the primary metric.
    try:
        data = json.loads(surface)
        if isinstance(data, dict):
            nested = []
            if isinstance(data.get("stdout"), str):
                nested.append(data.get("stdout") or "")
            if isinstance(data.get("stderr"), str):
                nested.append(data.get("stderr") or "")
            for nested_text in nested:
                parsed = parse_primary_metric(nested_text)
                if parsed.get("metric") is not None:
                    return parsed
            direct = data.get("primary_metric")
            if isinstance(direct, (int, float)) and not isinstance(direct, bool):
                metric = float(direct)
                metric_name = str(data.get("primary_metric_name") or data.get("metric_name") or metric_name)
                higher_is_better = bool(data.get("higher_is_better", higher_is_better))
                return {"metric": metric, "metric_name": metric_name, "higher_is_better": higher_is_better}
    except Exception:
        pass

    name_match = re.search(r'"?(?:primary_metric_name|metric_name)"?\s*[:=]\s*"?([A-Za-z0-9_.-]+)"?', surface)
    if name_match:
        metric_name = name_match.group(1)
    metric_match = re.search(r'"?primary_metric"?\s*[:=]\s*(-?\d+(?:\.\d+)?)', surface)
    if metric_match:
        metric = float(metric_match.group(1))
    hib_match = re.search(r'"?higher_is_better"?\s*[:=]\s*(true|false|1|0|yes|no)', surface, re.I)
    if hib_match:
        higher_is_better = hib_match.group(1).lower() in {"true", "1", "yes"}
    if metric is None:
        generic = re.search(r"\b(accuracy|f1|score|loss|val_loss|metric)\s*[:=]\s*(-?\d+(?:\.\d+)?)", surface, re.I)
        if generic:
            metric_name = generic.group(1)
            metric = float(generic.group(2))
            higher_is_better = "loss" not in metric_name.lower()
    return {"metric": metric, "metric_name": metric_name, "higher_is_better": higher_is_better}


def decide_experiment(metric: float | None, baseline: float | None = None, higher_is_better: bool = True) -> str:
    if metric is None:
        return "needs_metrics"
    if baseline is None:
        return "baseline_recorded"
    if metric == baseline:
        return "neutral"
    improved = metric > baseline if higher_is_better else metric < baseline
    return "keep" if improved else "discard"


def extract_progress_percent(text: str) -> int | None:
    matches = re.findall(r"(\d{1,3})\s*%", text or "")
    values = [int(m) for m in matches if 0 <= int(m) <= 100]
    return max(values) if values else None


def _patch_line_payload(line: str) -> str:
    payload = line[1:]
    return payload if payload.endswith("\n") else payload + "\n"


def apply_unified_patch_limited(project_dir: str | Path, patch_text: str) -> dict:
    """Apply a small unified diff inside project_dir.

    Supports creating/modifying text files.  Rejects deletion, rename, binary
    patches, absolute paths and ../ escapes.  This is intentionally limited for
    autonomous autoresearch changes.
    """
    if not patch_text or not patch_text.strip():
        raise AutoResearchSafetyError("empty patch")
    boundary = ProjectBoundary(project_dir)
    lines = patch_text.splitlines(keepends=True)
    if any(line.lower().startswith("binary files") or line.startswith("GIT binary patch") for line in lines):
        raise AutoResearchSafetyError("binary patches are not allowed")
    changed = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("--- "):
            i += 1
            continue
        old_label = lines[i][4:].strip()
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++ "):
            raise AutoResearchSafetyError("invalid unified diff: missing +++ line")
        new_label = lines[i][4:].strip()
        i += 1
        if new_label == "/dev/null":
            raise AutoResearchSafetyError("deleting files is not allowed")
        target_label = new_label[2:] if new_label.startswith("b/") else new_label
        if target_label.startswith("/") or _contains_parent_escape(target_label):
            raise AutoResearchSafetyError(f"patch target escapes project: {target_label}")
        target = boundary.resolve(target_label)
        original = [] if old_label == "/dev/null" or not target.exists() else target.read_text(encoding="utf-8").splitlines(keepends=True)
        out = []
        cursor = 0
        saw_hunk = False
        while i < len(lines) and lines[i].startswith("@@ "):
            saw_hunk = True
            header = lines[i]
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
            if not match:
                raise AutoResearchSafetyError(f"invalid hunk header: {header.strip()}")
            old_start = int(match.group(1))
            hunk_index = max(0, old_start - 1)
            if hunk_index < cursor:
                raise AutoResearchSafetyError("overlapping hunks are not allowed")
            out.extend(original[cursor:hunk_index])
            cursor = hunk_index
            i += 1
            while i < len(lines) and not lines[i].startswith("@@ ") and not lines[i].startswith("--- "):
                line = lines[i]
                if line.startswith(r"\ No newline"):
                    i += 1
                    continue
                if not line:
                    i += 1
                    continue
                tag = line[0]
                payload = _patch_line_payload(line)
                if tag == " ":
                    if cursor >= len(original) or original[cursor].rstrip("\n") != payload.rstrip("\n"):
                        raise AutoResearchSafetyError(f"patch context mismatch in {target_label}")
                    out.append(original[cursor])
                    cursor += 1
                elif tag == "-":
                    if cursor >= len(original) or original[cursor].rstrip("\n") != payload.rstrip("\n"):
                        raise AutoResearchSafetyError(f"patch deletion mismatch in {target_label}")
                    cursor += 1
                elif tag == "+":
                    out.append(payload)
                else:
                    raise AutoResearchSafetyError(f"unsupported patch line: {line[:20]!r}")
                i += 1
        if not saw_hunk:
            raise AutoResearchSafetyError("patch file section has no hunks")
        out.extend(original[cursor:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(out), encoding="utf-8")
        changed.append(str(target.relative_to(boundary.project_dir)))
    if not changed:
        raise AutoResearchSafetyError("no files changed by patch")
    return {"changed_files": changed}


def _normalize_patch_path(label: str) -> str | None:
    label = (label or "").strip()
    if not label or label == "/dev/null":
        return None
    # Drop optional timestamps after paths in ---/+++ lines.
    label = label.split("	", 1)[0].split(" ", 1)[0]
    if label.startswith("a/") or label.startswith("b/"):
        label = label[2:]
    return label


def _scan_patch_paths_for_safety(patch_text: str) -> list[str]:
    paths = []
    for line in patch_text.splitlines():
        candidates = []
        if line.startswith("diff --git "):
            parts = shlex.split(line)
            if len(parts) >= 4:
                candidates.extend([parts[2], parts[3]])
        elif line.startswith("--- ") or line.startswith("+++ "):
            candidates.append(line[4:].strip())
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            candidates.append(line.split(" ", 2)[2].strip())
        for raw in candidates:
            normalized = _normalize_patch_path(raw)
            if normalized is None:
                continue
            if normalized.startswith("/") or normalized.startswith("~") or _contains_parent_escape(normalized):
                raise AutoResearchSafetyError(f"patch path escapes project: {normalized}")
            paths.append(normalized)
    if not paths:
        raise AutoResearchSafetyError("patch contains no file paths")
    return sorted(set(paths))


def _matches_readonly(rel_path: str, readonly_globs) -> bool:
    """True if a project-relative path matches any read-only (eval) glob."""
    import fnmatch

    rel = str(rel_path or "").lstrip("./")
    for pattern in readonly_globs or ():
        pat = str(pattern).lstrip("./")
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.rstrip("/") + "/*"):
            return True
        # Support "dir/**" style directory guards.
        if pat.endswith("/**") and (rel == pat[:-3] or rel.startswith(pat[:-2])):
            return True
    return False


def apply_patch_with_git(project_dir: str | Path, patch_text: str, readonly_globs=()) -> dict:
    """Apply a full git-compatible patch inside project_dir using git apply.

    ``readonly_globs`` protects evaluation files (e.g. prepare.py): a patch that
    touches any matching path is rejected before it can bias the benchmark.

    LLM-authored diffs frequently carry wrong hunk line counts (``@@ -a,b +c,d @@``)
    or slightly stale context, which makes a strict ``git apply`` reject them with
    "corrupt patch". We therefore try a small ladder of increasingly tolerant
    flag sets (recount + context fuzz) before giving up, so a semantically correct
    edit is not lost to a cosmetic header mistake.
    """
    if not patch_text or not patch_text.strip():
        raise AutoResearchSafetyError("empty patch")
    if "GIT binary patch" in patch_text:
        raise AutoResearchSafetyError("binary git patches are not allowed")
    boundary = ProjectBoundary(project_dir)
    changed_files = _scan_patch_paths_for_safety(patch_text)
    for rel in changed_files:
        if _matches_readonly(rel, readonly_globs):
            raise AutoResearchSafetyError(
                f"patch modifies read-only evaluation file: {rel} (requires user approval)"
            )
    workdir = boundary.ensure_inside(boundary.project_dir)

    # Ladder of flag sets: strict first, then recount (fixes wrong @@ counts),
    # then recount + context fuzz (tolerates slightly stale surrounding lines).
    flag_ladder = (
        ["--whitespace=nowarn"],
        ["--whitespace=nowarn", "--recount"],
        ["--whitespace=nowarn", "--recount", "-C1"],
    )
    last_err = ""
    for flags in flag_ladder:
        check = subprocess.run(
            ["git", "apply", "--check", *flags, "-"],
            input=patch_text, cwd=str(workdir), capture_output=True, text=True, timeout=60,
        )
        if check.returncode != 0:
            last_err = (check.stderr or check.stdout).strip()
            continue
        applied = subprocess.run(
            ["git", "apply", *flags, "-"],
            input=patch_text, cwd=str(workdir), capture_output=True, text=True, timeout=60,
        )
        if applied.returncode != 0:
            last_err = (applied.stderr or applied.stdout).strip()
            continue
        return {
            "changed_files": changed_files,
            "apply_engine": "git apply",
            "apply_flags": flags,
            "recovered": flags != list(flag_ladder[0]),
        }
    raise AutoResearchSafetyError("git apply failed (tried strict/recount/fuzz): " + last_err)


class ProjectBoundary:
    """Project-directory confinement helper."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).expanduser().resolve()

    def resolve(self, value: str | Path = ".") -> Path:
        p = Path(value).expanduser()
        if not p.is_absolute():
            p = self.project_dir / p
        return self.ensure_inside(p)

    def ensure_inside(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.project_dir)
        except ValueError as exc:
            raise AutoResearchSafetyError(f"Path escapes project_dir: {resolved}") from exc
        return resolved

    def validate_command_surface(self, command: str) -> None:
        """Reject obvious workspace escapes for a fast project-local shell runner.

        This is intentionally lighter than the global run_command approval gate:
        autoresearch experiments may freely run project-local commands, while
        absolute paths outside the project and '../' escapes are rejected.
        """
        if not command or not command.strip():
            raise AutoResearchSafetyError("Empty command is not allowed")
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError as exc:
            raise AutoResearchSafetyError(f"Cannot parse command safely: {exc}") from exc
        for token in tokens:
            if token in {";", "&&", "||", "|", ">", ">>", "<", "2>", "2>>", "1>", "1>>"}:
                continue
            if token.startswith(("http://", "https://", "git@", "-")):
                continue
            if token in {"/dev/null", "/dev/stdout", "/dev/stderr"}:
                continue
            if token.startswith("~"):
                raise AutoResearchSafetyError(f"Home-relative path is not allowed: {token}")
            if token.startswith("/"):
                self.ensure_inside(token)
            elif _contains_parent_escape(token):
                raise AutoResearchSafetyError(f"Relative path escape is not allowed: {token}")




VERSIONING_POLICIES = {"artifact_only", "commit_pareto", "commit_all_trials", "branch_per_trial"}
PLANNER_KINDS = {"fixed", "evolutionary"}


def normalize_versioning_policy(policy: str | None) -> str:
    value = str(policy or "artifact_only").strip().lower()
    return value if value in VERSIONING_POLICIES else "artifact_only"


def normalize_planner_kind(kind: str | None) -> str:
    value = str(kind or "fixed").strip().lower()
    return value if value in PLANNER_KINDS else "fixed"


def _run_git(project_dir: str | Path, args: list[str], timeout: int = 30) -> dict:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(Path(project_dir).expanduser().resolve()),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
    except Exception as exc:
        return {"returncode": None, "stdout": "", "stderr": str(exc)}


def git_snapshot(project_dir: str | Path, *, enabled: bool = True) -> dict:
    """Return safe git repository metadata; never initializes or mutates repos."""
    if not enabled:
        return {"git_available": False, "reason": "disabled"}
    probe = _run_git(project_dir, ["rev-parse", "--is-inside-work-tree"])
    if probe.get("returncode") != 0 or probe.get("stdout", "").strip() != "true":
        return {"git_available": False, "reason": "not_git_repo"}
    root = _run_git(project_dir, ["rev-parse", "--show-toplevel"]).get("stdout", "").strip()
    head = _run_git(project_dir, ["rev-parse", "HEAD"])
    status = _run_git(project_dir, ["status", "--porcelain=v1"])
    return {
        "git_available": True,
        "repo_root": root,
        "head": head.get("stdout", "").strip() if head.get("returncode") == 0 else "",
        "status": status.get("stdout", "") if status.get("returncode") == 0 else "",
    }


def git_changed_files(project_dir: str | Path) -> list[str]:
    status = _run_git(project_dir, ["status", "--porcelain=v1"])
    if status.get("returncode") != 0:
        return []
    files = []
    for line in status.get("stdout", "").splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path)
    return sorted(set(files))


def save_project_diff(project_dir: str | Path, artifacts, rationale: str, *, git_available: bool) -> str:
    if git_available:
        diff = _run_git(project_dir, ["diff", "--no-ext-diff", "--binary"]).get("stdout", "")
        staged = _run_git(project_dir, ["diff", "--cached", "--no-ext-diff", "--binary"]).get("stdout", "")
        status = _run_git(project_dir, ["status", "--porcelain=v1"]).get("stdout", "")
        content = "# git status --porcelain\n" + status + "\n# git diff\n" + diff + "\n# git diff --cached\n" + staged
        if content.strip():
            return artifacts.save(kind="diff", rationale=rationale, content=content, extension="diff")
        return ""
    root = Path(project_dir).expanduser().resolve()
    rows = []
    for path in sorted(root.rglob("*")):
        if ".autoresearch" in path.parts or path.is_dir():
            continue
        try:
            rel = str(path.relative_to(root))
            rows.append({"path": rel, "size": path.stat().st_size, "mtime": path.stat().st_mtime})
        except Exception:
            continue
        if len(rows) >= 500:
            break
    return artifacts.save(kind="manifest", rationale=rationale, content=json.dumps({"files": rows}, ensure_ascii=False, indent=2), extension="json")


def _git_worktree_clean(snapshot: dict) -> bool:
    return bool(isinstance(snapshot, dict) and snapshot.get("git_available") and not str(snapshot.get("status") or "").strip())


def _sanitize_branch_component(value: str, default: str = "trial") -> str:
    slug = _safe_slug(value, default, 80).replace("_", "-")
    return slug.strip(".-/") or default


def git_commit_trial(project_dir: str | Path, experiment_id: str, rationale: str, *, branch_name: str = "") -> dict:
    status = _run_git(project_dir, ["status", "--porcelain=v1"])
    if status.get("returncode") != 0:
        return {"action": "commit_failed", "commit_sha": "", "branch": branch_name, "error": status.get("stderr") or status.get("stdout")}
    if not str(status.get("stdout") or "").strip():
        return {"action": "no_changes", "commit_sha": "", "branch": branch_name, "error": ""}
    add = _run_git(project_dir, ["add", "-A", "--", "."])
    if add.get("returncode") != 0:
        return {"action": "commit_failed", "commit_sha": "", "branch": branch_name, "error": add.get("stderr") or add.get("stdout")}
    staged = _run_git(project_dir, ["diff", "--cached", "--quiet"])
    if staged.get("returncode") == 0:
        return {"action": "no_staged_changes", "commit_sha": "", "branch": branch_name, "error": ""}
    message = f"auto_research {experiment_id}: {str(rationale or '')[:120]}"
    commit = _run_git(project_dir, ["-c", "user.name=auto_research", "-c", "user.email=auto_research@example.invalid", "commit", "-m", message], timeout=60)
    if commit.get("returncode") != 0:
        return {"action": "commit_failed", "commit_sha": "", "branch": branch_name, "error": commit.get("stderr") or commit.get("stdout")}
    sha = _run_git(project_dir, ["rev-parse", "HEAD"]).get("stdout", "").strip()
    if branch_name:
        branch = _run_git(project_dir, ["branch", "-f", branch_name, sha])
        if branch.get("returncode") != 0:
            return {"action": "committed_branch_failed", "commit_sha": sha, "branch": branch_name, "error": branch.get("stderr") or branch.get("stdout")}
    return {"action": "committed" if not branch_name else "committed_branch_recorded", "commit_sha": sha, "branch": branch_name, "error": ""}


def git_branch_trial(project_dir: str | Path, experiment_id: str, rationale: str, base_ref: str = "") -> dict:
    branch_name = "autoresearch/" + _sanitize_branch_component(experiment_id)
    current = _run_git(project_dir, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    original_ref = current.get("stdout", "").strip() if current.get("returncode") == 0 else (base_ref or "HEAD")
    create = _run_git(project_dir, ["checkout", "-b", branch_name], timeout=60)
    if create.get("returncode") != 0:
        return {"action": "branch_failed", "commit_sha": "", "branch": branch_name, "rollback_status": "not_needed", "error": create.get("stderr") or create.get("stdout")}
    result = git_commit_trial(project_dir, experiment_id, rationale, branch_name="")
    sha = result.get("commit_sha", "")
    back = _run_git(project_dir, ["checkout", original_ref], timeout=60)
    rollback_status = "returned_to_base" if back.get("returncode") == 0 else "return_failed"
    if sha:
        _run_git(project_dir, ["branch", "-f", branch_name, sha])
    action = "branched" if sha and rollback_status == "returned_to_base" else result.get("action", "branch_failed")
    error = result.get("error", "") or (back.get("stderr") or back.get("stdout") if back.get("returncode") != 0 else "")
    return {"action": action, "commit_sha": sha, "branch": branch_name, "rollback_status": rollback_status, "error": error}


def git_safe_rollback_to_base(project_dir: str | Path, base_commit: str) -> dict:
    if not base_commit:
        return {"status": "skipped_no_base", "error": ""}
    restore_staged = _run_git(project_dir, ["restore", "--staged", "--", "."], timeout=60)
    restore_worktree = _run_git(project_dir, ["restore", "--worktree", "--source", base_commit, "--", "."], timeout=60)
    status = _run_git(project_dir, ["status", "--porcelain=v1"])
    if restore_staged.get("returncode") != 0 or restore_worktree.get("returncode") != 0:
        return {"status": "failed", "error": (restore_staged.get("stderr") or "") + (restore_worktree.get("stderr") or "")}
    remaining = str(status.get("stdout") or "")
    untracked = [line for line in remaining.splitlines() if line.startswith("??")]
    tracked = [line for line in remaining.splitlines() if line and not line.startswith("??")]
    if tracked:
        return {"status": "partial_tracked_remaining", "error": "tracked changes remain"}
    if untracked:
        return {"status": "rolled_back_tracked_untracked_preserved", "error": ""}
    return {"status": "rolled_back", "error": ""}


def _metric_direction(name: str, default_higher: bool = True) -> bool:
    lowered = (name or "").lower()
    if any(token in lowered for token in ("loss", "error", "wer", "cer", "latency", "time", "cost", "perplexity")):
        return False
    if any(token in lowered for token in ("accuracy", "acc", "f1", "auc", "score", "precision", "recall", "success", "pass")):
        return True
    return default_higher


# Wall-clock / bookkeeping fields that eval harnesses emit alongside the real
# objective. They are pure telemetry: their jitter must never drive Pareto
# dominance or reset the plateau brake, otherwise a converged task keeps
# minting "new non-dominated" points from runtime noise and never stalls.
_TELEMETRY_METRIC_KEYS = frozenset({
    "runtime_seconds",
    "runtime_sec",
    "runtime",
    "duration_seconds",
    "duration_sec",
    "duration",
    "returncode",
    "elapsed",
    "elapsed_seconds",
    "wall_time",
    "wall_seconds",
    "timestamp",
    "num_cases",
    "total",
    "total_rows",
})

# Substrings that mark a key as non-objective bookkeeping even when a task uses
# its own naming (e.g. "eval_runtime_sec", "step_duration").
_TELEMETRY_METRIC_TOKENS = ("runtime", "duration", "elapsed", "wall_time", "returncode")


def is_objective_metric(name: str) -> bool:
    """True when ``name`` is a real optimization objective (not telemetry).

    Confusion-matrix counts (tp/fp/fn/tn) and raw case totals are excluded too:
    they co-vary with the primary metric and only add redundant, noisy axes to
    the Pareto comparison.
    """
    lowered = (name or "").strip().lower()
    if not lowered:
        return False
    if lowered in _TELEMETRY_METRIC_KEYS:
        return False
    if lowered in {"tp", "fp", "fn", "tn"}:
        return False
    if any(token in lowered for token in _TELEMETRY_METRIC_TOKENS):
        return False
    return True


def objective_metrics(metrics: dict) -> dict:
    """Filter a metrics dict down to real objectives for dominance/plateau."""
    if not isinstance(metrics, dict):
        return {}
    return {k: v for k, v in metrics.items() if is_objective_metric(k)}


def extract_metrics_from_text(text: str, program_text: str = "") -> tuple[dict[str, float], dict[str, bool]]:
    metrics: dict[str, float] = {}
    directions: dict[str, bool] = {}
    primary = parse_primary_metric(text)
    if primary.get("metric") is not None:
        name = str(primary.get("metric_name") or "primary_metric")
        metrics[name] = float(primary["metric"])
        directions[name] = bool(primary.get("higher_is_better", _metric_direction(name)))
    # Neutralize JSON-escaped whitespace (literal "\n"/"\t"/"\r") before the
    # regex fallback. Without this, a metrics blob that embeds a JSON string
    # preview leaks the escape letter into the captured name (e.g. the "\n"
    # before "primary_metric" produced a bogus "nprimary_metric" axis).
    scan_text = re.sub(r"\\[nrt]", " ", text or "")
    for pattern in (
        r"\b([A-Za-z][A-Za-z0-9_.-]*(?:accuracy|acc|f1|auc|score|loss|error|latency|time|cost|metric)[A-Za-z0-9_.-]*)\s*[:=]\s*(-?\d+(?:\.\d+)?)",
        r"\b(accuracy|acc|f1|auc|score|loss|error|latency|time|cost)\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    ):
        for name, value in re.findall(pattern, scan_text, flags=re.I):
            try:
                metrics[str(name)] = float(value)
            except ValueError:
                continue
    try:
        data = json.loads(text)
        candidates = []
        if isinstance(data, dict):
            candidates.append(data)
            for key in ("metrics", "results"):
                if isinstance(data.get(key), dict):
                    candidates.append(data[key])
        for candidate in candidates:
            for key, value in candidate.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metrics[str(key)] = float(value)
    except Exception:
        pass
    default_higher = not re.search(r"\b(minimi[sz]e|lower is better|reduce|loss|error|cost)\b", program_text or "", flags=re.I)
    for name in list(metrics):
        directions.setdefault(name, _metric_direction(name, default_higher))
    return metrics, directions


def _dominates(a: dict, b: dict, directions: dict[str, bool]) -> bool:
    a_metrics = a.get("metrics") or {}
    b_metrics = b.get("metrics") or {}
    # Only real objectives drive dominance. Wall-clock telemetry and raw counts
    # are excluded so runtime jitter cannot fabricate non-dominated points.
    common = [
        k for k in a_metrics
        if k in b_metrics
        and is_objective_metric(k)
        and isinstance(a_metrics.get(k), (int, float))
        and isinstance(b_metrics.get(k), (int, float))
    ]
    if not common:
        return False
    better_or_equal = True
    strictly_better = False
    for key in common:
        higher = directions.get(key, _metric_direction(key))
        av = float(a_metrics[key]); bv = float(b_metrics[key])
        if higher:
            better_or_equal = better_or_equal and av >= bv
            strictly_better = strictly_better or av > bv
        else:
            better_or_equal = better_or_equal and av <= bv
            strictly_better = strictly_better or av < bv
    return better_or_equal and strictly_better


def pareto_front(experiments: list[dict], directions: dict[str, bool], max_items: int) -> list[dict]:
    candidates = [e for e in experiments if e.get("metrics") and e.get("status") != "failed"]
    front = []
    for candidate in candidates:
        if any(_dominates(other, candidate, directions) for other in candidates if other is not candidate):
            continue
        front.append(candidate)
    front.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    return front[: max(1, int(max_items or 1))]


def choose_best_experiment(experiments: list[dict], directions: dict[str, bool], primary_name: str | None = None) -> dict | None:
    candidates = [e for e in experiments if e.get("metrics") and e.get("status") != "failed"]
    if not candidates:
        return None
    if not primary_name:
        for e in reversed(candidates):
            if e.get("primary_metric_name"):
                primary_name = e.get("primary_metric_name")
                break
    if not primary_name:
        primary_name = next(iter(candidates[-1].get("metrics") or {}), None)
    if not primary_name:
        return candidates[-1]
    higher = directions.get(primary_name, _metric_direction(primary_name))
    with_metric = [e for e in candidates if isinstance((e.get("metrics") or {}).get(primary_name), (int, float))]
    if not with_metric:
        return candidates[-1]
    return sorted(with_metric, key=lambda e: float(e["metrics"][primary_name]), reverse=higher)[0]
