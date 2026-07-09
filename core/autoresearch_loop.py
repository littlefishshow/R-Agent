from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from tools.web_tools import web_extract_tool, web_search_tool
from core.autoresearch_debug import inflight_finish, inflight_start

Decision = Literal["run", "read", "write", "apply_patch", "web_search", "web_extract", "note", "stop"]


@dataclass
class AutoResearchSettings:
    """Dedicated autoresearch loop settings.

    The loop is centered on program.md, keeps parent context bounded, and stores
    raw child/tool outputs outside the prompt as timestamped artifacts.
    """

    project_dir: str | Path
    project_id: str = "autoresearch"
    program_path: str | Path = "program.md"
    state_path: str | Path = ".autoresearch/state.json"
    artifact_dir: str | Path = ".autoresearch/artifacts"
    context_char_budget: int = 24_000
    program_char_budget: int = 12_000
    summary_char_budget: int = 6_000
    recent_observation_limit: int = 8
    command_timeout_seconds: int = 300
    max_rounds: int = 1
    trial_rationale: str = "manual"
    allowed_file_write_roots: tuple[str, ...] = (".",)
    bucket_item_char_budget: int = 900
    bucket_max_items: int = 3
    workflow: str = "default"
    use_llm_step_agents: bool = False
    llm_model: str | None = None
    llm_temperature: float = 0.0
    progress_path: str | Path = ".autoresearch/progress.md"
    auto_commit: bool = False
    max_experiments: int = 4
    max_active_context_chars: int = 8_000
    max_pareto_items: int = 8
    max_useful_failures: int = 3
    use_git_versioning: bool = True
    versioning_policy: str = "artifact_only"
    planner_kind: str = "fixed"
    llm_request_timeout: float = 60.0
    llm_retry_attempts: int = 0
    # --- v2: cost control + layered memory ---
    project_state_path: str | Path = "project.md"
    budget_path: str | Path = ".autoresearch/budget.json"
    monitor_path: str | Path = ".autoresearch/monitor.json"
    trace_rounds: bool = False
    trace_dir: str | Path = ".autoresearch/round_traces"
    max_usd: float = 0.0            # 0 => unlimited
    max_tokens: int = 0             # 0 => unlimited
    budget_degrade_ratio: float = 0.8
    model_tier_plan: str = ""
    model_tier_exec: str = ""
    model_tier_util: str = ""
    readonly_eval_globs: tuple[str, ...] = ("prepare.py", "eval.sh", "eval/**", "evaluation/**")
    plateau_patience: int = 3
    debug_mode: bool = False
    # --- v2 execute/run tuning ---
    # Cap LLM-backed actions per Execute phase visit so one step cannot burn the
    # whole time/token budget on a long todo list; remaining items advance on the
    # next Execute visit via a cursor.
    execute_max_actions_per_step: int = 1
    # When Execute wrote a self-iterating search driver, let Run execute it so it
    # performs many internal evaluations from a single LLM decision.
    run_search_driver: bool = True
    run_max_inner_seconds: float = 20.0
    run_max_inner_evals: int = 100
    run_cheap_eval_threshold_seconds: float = 2.0
    solved_metric_threshold: Optional[float] = None
    search_driver_globs: tuple[str, ...] = (
        "train/search.py", "train/search_driver.py", "train/*search*.py",
        "train/*driver*.py", "train/*exploration*.py", "search.py",
        "train/search.sh", "search.sh",
    )

    def __post_init__(self) -> None:
        # Normalize early so tools, background payloads, state, progress, and
        # active_context all report the same supported lifecycle policy.
        self.versioning_policy = normalize_versioning_policy(self.versioning_policy)
        self.planner_kind = normalize_planner_kind(self.planner_kind)

    def root(self) -> Path:
        return Path(self.project_dir).expanduser().resolve()

    def program_file(self) -> Path:
        p = Path(self.program_path)
        return p if p.is_absolute() else self.root() / p

    def project_state_file(self) -> Path:
        p = Path(self.project_state_path)
        return p if p.is_absolute() else self.root() / p

    def budget_file(self) -> Path:
        p = Path(self.budget_path)
        return p if p.is_absolute() else self.root() / p

    def monitor_file(self) -> Path:
        p = Path(self.monitor_path)
        return p if p.is_absolute() else self.root() / p

    def trace_root(self) -> Path:
        p = Path(self.trace_dir)
        return p if p.is_absolute() else self.root() / p

    def stop_file(self) -> Path:
        # Sentinel a watcher / esc handler can create to stop the loop cleanly.
        return self.root() / ".autoresearch" / "STOP"

    def debug_file(self) -> Path:
        return self.root() / ".autoresearch" / "DEBUG"

    def state_file(self) -> Path:
        p = Path(self.state_path)
        return p if p.is_absolute() else self.root() / p

    def artifacts_root(self) -> Path:
        p = Path(self.artifact_dir)
        return p if p.is_absolute() else self.root() / p

    def progress_file(self) -> Path:
        p = Path(self.progress_path)
        return p if p.is_absolute() else self.root() / p


@dataclass
class AutoResearchObservation:
    kind: str
    summary: str
    artifact_path: str = ""
    status: str = "ok"
    created_at: float = field(default_factory=time.time)

    def compact(self, max_chars: int = 900) -> dict:
        summary = self.summary
        if len(summary) > max_chars:
            summary = summary[: max_chars - 3].rstrip() + "..."
        return {
            "kind": self.kind,
            "status": self.status,
            "summary": summary,
            "artifact_path": self.artifact_path,
            "created_at": self.created_at,
        }


DEFAULT_CONTEXT_BUCKETS = (
    "project_understanding",
    "current_changes",
    "experiment_results",
    "conclusions",
    "modification_plans",
    "open_questions",
    "raw_observations",
)


@dataclass
class ContextBucket:
    name: str
    items: list[str] = field(default_factory=list)
    max_items: int = 3
    max_item_chars: int = 900

    def add(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        if len(text) > self.max_item_chars:
            text = text[: self.max_item_chars - 3].rstrip() + "..."
        self.items.append(text)
        self.items = self.items[-self.max_items :]

    def compact(self) -> list[str]:
        return list(self.items[-self.max_items :])


@dataclass
class AutoResearchWorkflowStep:
    name: str
    action_type: Decision
    rationale: str
    command: str = ""
    path: str = ""
    content: str = ""
    patch: str = ""
    query: str = ""
    urls: list[str] = field(default_factory=list)
    max_results: int = 5
    allowed_tools: tuple[Decision, ...] = field(default_factory=tuple)
    role: str = ""

    def to_action(self) -> "AutoResearchAction":
        return AutoResearchAction(
            type=self.action_type,
            rationale=self.rationale or self.name,
            command=self.command,
            path=self.path,
            content=self.content,
            patch=self.patch,
            query=self.query,
            urls=list(self.urls),
            max_results=self.max_results,
            role=self.role,
        )




@dataclass
class AutoResearchAction:
    type: Decision
    rationale: str
    command: str = ""
    path: str = ""
    content: str = ""
    patch: str = ""
    query: str = ""
    urls: list[str] = field(default_factory=list)
    max_results: int = 5
    role: str = ""


@dataclass
class AutoResearchStepResult:
    action: AutoResearchAction
    bucket_updates: dict[str, list[str]] = field(default_factory=dict)
    raw_response: str = ""
    used_fallback: bool = False
    error: str = ""
    # Full LLM I/O for post-hoc debugging (only populated when round tracing is on).
    system_prompt: str = ""
    user_payload: str = ""


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
    name_match = re.search(r"primary_metric_name\s*[:=]\s*([A-Za-z0-9_.-]+)", surface)
    if name_match:
        metric_name = name_match.group(1)
    metric_match = re.search(r"primary_metric\s*[:=]\s*(-?\d+(?:\.\d+)?)", surface)
    if metric_match:
        metric = float(metric_match.group(1))
    hib_match = re.search(r"higher_is_better\s*[:=]\s*(true|false|1|0|yes|no)", surface, re.I)
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


class AutoResearchArtifactStore:
    def __init__(self, settings: AutoResearchSettings):
        self.settings = settings
        self.root = settings.artifacts_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, *, kind: str, rationale: str, content: str, extension: str = "txt") -> str:
        import secrets

        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}" + f"-{secrets.token_hex(3)}"
        filename = "_".join([
            stamp,
            _safe_slug(self.settings.project_id),
            _safe_slug(rationale, "trial"),
            _safe_slug(kind),
        ]) + f".{_safe_slug(extension, 'txt', 12)}"
        path = self.root / filename
        path.write_text(content or "", encoding="utf-8")
        return str(path)


class ProjectConfinedCommandRunner:
    """Fast shell runner for project-local autoresearch commands.

    It does not call global run_command, so project-confined commands are not
    blocked by high-risk warnings.  Boundary checks reject obvious path escapes.
    """

    def __init__(self, project_dir: str | Path, timeout_seconds: int = 300):
        self.boundary = ProjectBoundary(project_dir)
        self.timeout_seconds = timeout_seconds

    def run(self, command: str, cwd: str | Path = ".") -> dict:
        workdir = self.boundary.resolve(cwd)
        self.boundary.validate_command_surface(command)
        started = time.time()
        inflight_start(self.boundary.project_dir, "shell", detail=command[:300], cwd=str(workdir))
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            result = {
                "command": command,
                "cwd": str(workdir),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "duration_seconds": round(time.time() - started, 3),
            }
            inflight_finish(
                self.boundary.project_dir,
                "shell",
                detail=command[:300],
                returncode=completed.returncode,
                duration_seconds=result["duration_seconds"],
            )
            return result
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": command,
                "cwd": str(workdir),
                "returncode": None,
                "stdout": exc.stdout or "",
                "stderr": (exc.stderr or "") + f"\nCommand timed out after {self.timeout_seconds}s",
                "duration_seconds": round(time.time() - started, 3),
                "timeout": True,
            }
            inflight_finish(
                self.boundary.project_dir,
                "shell",
                detail=command[:300],
                returncode=None,
                timeout=True,
                duration_seconds=result["duration_seconds"],
            )
            return result


class AutoResearchContextManager:
    def __init__(self, settings: AutoResearchSettings):
        self.settings = settings
        self.boundary = ProjectBoundary(settings.project_dir)

    def read_program(self) -> str:
        path = self.boundary.ensure_inside(self.settings.program_file())
        if not path.exists():
            return ""
        return self._truncate(path.read_text(encoding="utf-8"), self.settings.program_char_budget)

    def default_state(self) -> dict:
        return {
            "summary": "",
            "observations": [],
            "buckets": {name: [] for name in DEFAULT_CONTEXT_BUCKETS},
            "experiments": [],
            "pareto_front": [],
            "best_experiment": None,
            "useful_failures": [],
            "versioning_policy": normalize_versioning_policy(self.settings.versioning_policy),
            "use_git_versioning": bool(self.settings.use_git_versioning),
        }

    def load_state(self) -> dict:
        path = self.boundary.ensure_inside(self.settings.state_file())
        if not path.exists():
            return self.default_state()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self.default_state()
        except Exception:
            return self.default_state()
        data.setdefault("summary", "")
        data.setdefault("observations", [])
        data.setdefault("experiments", [])
        data.setdefault("pareto_front", [])
        data.setdefault("best_experiment", None)
        data.setdefault("useful_failures", [])
        data["versioning_policy"] = normalize_versioning_policy(self.settings.versioning_policy)
        data["use_git_versioning"] = bool(self.settings.use_git_versioning)
        buckets = data.setdefault("buckets", {})
        for name in DEFAULT_CONTEXT_BUCKETS:
            buckets.setdefault(name, [])
        return data

    def add_to_bucket(self, state: dict, bucket_name: str, text: str) -> None:
        bucket_name = bucket_name if bucket_name in DEFAULT_CONTEXT_BUCKETS else "raw_observations"
        bucket = ContextBucket(
            bucket_name,
            list((state.setdefault("buckets", {}).get(bucket_name) or [])),
            max_items=self.settings.bucket_max_items,
            max_item_chars=self.settings.bucket_item_char_budget,
        )
        bucket.add(text)
        state["buckets"][bucket_name] = bucket.compact()

    def save_state(self, state: dict) -> None:
        import os

        state["versioning_policy"] = normalize_versioning_policy(self.settings.versioning_policy)
        state["use_git_versioning"] = bool(self.settings.use_git_versioning)
        path = self.boundary.ensure_inside(self.settings.state_file())
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def build_parent_context(self, observations: Iterable[AutoResearchObservation] = ()) -> str:
        state = self.load_state()
        recent = list(state.get("observations") or [])[-self.settings.recent_observation_limit :]
        recent.extend([obs.compact() for obs in observations])
        bucket_payload = {}
        for name in DEFAULT_CONTEXT_BUCKETS:
            items = list((state.get("buckets", {}) or {}).get(name) or [])[-self.settings.bucket_max_items :]
            bucket_payload[name] = [self._truncate(str(item), self.settings.bucket_item_char_budget) for item in items]
        payload = {
            "project_id": self.settings.project_id,
            "program_md": self.read_program(),
            "modular_context": bucket_payload,
            "state_summary": self._truncate(str(state.get("summary") or ""), self.settings.summary_char_budget),
            "recent_observations": recent[-self.settings.recent_observation_limit :],
            "context_policy": {
                "max_chars": self.settings.context_char_budget,
                "raw_outputs": "archived separately; parent sees summaries and artifact paths only",
            },
            "versioning": {
                "policy": normalize_versioning_policy(self.settings.versioning_policy),
                "use_git_versioning": bool(self.settings.use_git_versioning),
                "best_experiment": state.get("best_experiment"),
                "pareto_count": len(state.get("pareto_front") or []),
            },
        }
        return self._truncate_middle(json.dumps(payload, ensure_ascii=False, indent=2), self.settings.context_char_budget)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    @staticmethod
    def _truncate_middle(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        keep_head = max_chars // 2
        keep_tail = max_chars - keep_head - 40
        return text[:keep_head].rstrip() + "\n...<context clipped>...\n" + text[-keep_tail:].lstrip()


Planner = Callable[[str, int], AutoResearchAction]
Summarizer = Callable[[AutoResearchAction, str], str]


class FixedAutoResearchPlanner:
    """Deterministic small-step workflow planner for autoresearch.

    Each step declares the only action/tool surface it may use.  R-Agent can run
    the whole loop as one isolated tool process, while this planner keeps the
    internal autoresearch workflow stable and low-variance.
    """

    DEFAULT_STEPS = (
        AutoResearchWorkflowStep(
            name="inspect_project",
            action_type="run",
            rationale="project_understanding_inspect",
            command="pwd && find . -maxdepth 2 -type f | sort | head -120",
            allowed_tools=("run", "read"),
        ),
        AutoResearchWorkflowStep(
            name="read_program",
            action_type="read",
            rationale="project_understanding_program",
            path="program.md",
            allowed_tools=("read",),
        ),
        AutoResearchWorkflowStep(
            name="plan_change",
            action_type="note",
            rationale="modification_plan_initial",
            content="Draft one minimal, reversible experiment hypothesis from program.md and current project understanding.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="baseline_eval",
            action_type="run",
            rationale="experiment_result_baseline",
            command="if [ -f eval.sh ]; then bash eval.sh; elif [ -f train/train.sh ]; then bash train/train.sh; else echo 'No eval.sh or train/train.sh found; baseline unavailable.'; fi",
            allowed_tools=("run",),
            role="baseline",
        ),
        AutoResearchWorkflowStep(
            name="summarize_baseline",
            action_type="note",
            rationale="conclusion_baseline_summary",
            content="Record baseline evidence, metrics if available, and whether a real experiment can proceed.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="propose_experiment",
            action_type="note",
            rationale="modification_plan_one_hypothesis",
            content="Propose exactly one minimal change hypothesis, expected metric direction, risk, and rollback condition. Do not edit yet.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="apply_change",
            action_type="note",
            rationale="current_change_apply_patch_or_skip",
            content="No safe patch has been produced by the step agent; record that apply-change was skipped.",
            allowed_tools=("apply_patch", "write", "note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="run_experiment_if_available",
            action_type="run",
            rationale="experiment_result_trial",
            command="if [ -f train/train.sh ]; then bash train/train.sh; elif [ -f eval.sh ]; then bash eval.sh; else echo 'No train/train.sh or eval.sh found; trial unavailable.'; fi",
            allowed_tools=("run",),
            role="trial",
        ),
        AutoResearchWorkflowStep(
            name="parse_metric_and_decide",
            action_type="note",
            rationale="conclusion_metric_decision",
            content="Parse latest metric evidence from observations/artifacts. Decide keep/discard/needs_metrics. Do not commit unless explicitly configured.",
            allowed_tools=("note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="record_decision",
            action_type="note",
            rationale="conclusion_record_decision",
            content="Record final decision, completed work, artifact paths, and next action. Commit is disabled by default; record what would be committed.",
            allowed_tools=("note", "read"),
        ),
    )

    def __init__(self, steps: Optional[list[AutoResearchWorkflowStep]] = None):
        self.steps = list(steps or self.DEFAULT_STEPS)

    def __call__(self, parent_context: str, round_index: int) -> AutoResearchAction:
        if round_index < len(self.steps):
            return self.steps[round_index].to_action()
        return AutoResearchAction(type="stop", rationale="fixed autoresearch workflow completed")

    def step_for_round(self, round_index: int) -> AutoResearchWorkflowStep | None:
        if round_index < len(self.steps):
            return self.steps[round_index]
        return None

    def allowed_tools_for_round(self, round_index: int) -> tuple[Decision, ...]:
        if round_index < len(self.steps):
            return self.steps[round_index].allowed_tools
        return ("stop",)


class EvolutionaryAutoResearchPlanner:
    """Wrap the fixed 10-step workflow with an inner propose/apply/run/decide loop.

    First runs the full fixed workflow once (baseline + first trial).  After that,
    while the loop has budget for more trials (measured by ``experiment_count``)
    and rounds remaining, keep replaying the four inner steps:

        propose_experiment -> apply_change -> run_experiment_if_available -> parse_metric_and_decide

    This is what makes ``max_experiments``, Pareto front, and per-trial versioning
    policies actually consume more than one trial per ``auto_research_run``.
    """

    INNER_STEP_NAMES = ("propose_experiment", "apply_change", "run_experiment_if_available", "parse_metric_and_decide")

    def __init__(self, base: Optional[FixedAutoResearchPlanner] = None):
        self._base = base or FixedAutoResearchPlanner()
        self._loop: "AutoResearchLoop | None" = None
        by_name = {step.name: step for step in self._base.steps}
        missing = [name for name in self.INNER_STEP_NAMES if name not in by_name]
        if missing:
            raise AutoResearchSafetyError(f"EvolutionaryAutoResearchPlanner base is missing steps: {missing}")
        self._inner_steps: tuple[AutoResearchWorkflowStep, ...] = tuple(by_name[name] for name in self.INNER_STEP_NAMES)
        self._final_step = by_name.get("record_decision")

    def bind_loop(self, loop: "AutoResearchLoop") -> None:
        self._loop = loop

    @property
    def base_step_count(self) -> int:
        return len(self._base.steps)

    def _step_for_round(self, round_index: int) -> AutoResearchWorkflowStep | None:
        base = self._base
        if round_index < len(base.steps):
            return base.steps[round_index]
        extra = round_index - len(base.steps)
        loop = self._loop
        experiments_done = int(getattr(loop, "_experiment_count", 0)) if loop is not None else 0
        max_experiments = int(getattr(loop.settings, "max_experiments", 0)) if loop is not None else 0
        if loop is not None and experiments_done >= max(0, max_experiments):
            if extra == 0 and self._final_step is not None:
                return self._final_step
            return None
        cycle_index = extra % len(self._inner_steps)
        return self._inner_steps[cycle_index]

    def step_for_round(self, round_index: int) -> AutoResearchWorkflowStep | None:
        return self._step_for_round(round_index)

    def allowed_tools_for_round(self, round_index: int) -> tuple[Decision, ...]:
        step = self._step_for_round(round_index)
        if step is None:
            return ("stop",)
        return step.allowed_tools

    def __call__(self, parent_context: str, round_index: int) -> AutoResearchAction:
        step = self._step_for_round(round_index)
        if step is None:
            return AutoResearchAction(type="stop", rationale="evolutionary autoresearch budget exhausted")
        return step.to_action()


class AutoResearchStepAgent:
    STEP_GUIDANCE = {
        "inspect_project": "Build concise project understanding: structure, likely entrypoints, existing eval/train files, and risks.",
        "read_program": "Extract research goal, success metric, allowed edits, fixed eval harness, budget, and stop conditions from program.md.",
        "plan_change": (
            "Propose one reversible experiment hypothesis. Prefer a plan that lets ONE edit do MANY evaluations: "
            "if the protocol allows editing files under train/, you MAY write a self-iterating search script "
            "(e.g. train/train.py runs a loop that itself calls the eval harness many times, reads the returned "
            "metric, and keeps the best candidate) instead of hand-editing a single constant per round. "
            "This is your choice; pick it when the task is a search/optimization loop, so you do not need to think "
            "once per evaluation. Specify target files, expected metric direction, risk, rollback."
        ),
        "baseline_eval": (
            "Run or prepare baseline evaluation; focus on machine-parseable metrics and failure diagnosis. "
            "Use python3 (never bare 'python') for any inline/summary script. Judge success by the parsed metric "
            "and the train/eval logs, NOT by the exit code of a summary wrapper — do not let a summary step's "
            "failure make you exit nonzero when train/eval actually produced a valid metric."
        ),
        "summarize_baseline": "Summarize baseline evidence and whether metrics are sufficient for comparison.",
        "propose_experiment": (
            "Produce a single minimal modification plan; do not combine unrelated ideas. For search/optimization "
            "tasks, a strong single plan is to (re)write an allowed train-side script that internally loops over "
            "many candidates and calls the eval harness each time, returning the best. That amortizes one LLM "
            "decision over many cheap evaluations instead of one candidate per round. "
            "IMPROVE ACROSS ROUNDS: read the previous round's best metric and search history from context; if the "
            "objective is not yet reached or still improving, REWRITE the search script to do better — widen the "
            "search range to cover the whole plausible domain from program.md, increase the sample budget, and/or "
            "switch algorithm (e.g. coarse global scan then local refinement). Do NOT anchor the search solely on "
            "the existing submission.json; always run an independent global search each round."
        ),
        "apply_change": (
            "Make the planned train-side change. Two safe options: (a) emit apply_patch with a unified diff when you "
            "know the exact current file contents; (b) if you do NOT have the exact contents, prefer a full-file "
            "'write' action (path + complete new content) rather than skipping — a self-contained search script is a "
            "good fit for 'write'. Only skip if no safe change can be expressed. Never touch forbidden eval files."
        ),
        "run_experiment_if_available": (
            "Run the configured experiment/eval command; prefer bounded commands and preserve logs. Use python3 for "
            "any inline summary; base the run's success on the parsed metric and logs, not on a summary script's exit "
            "code."
        ),
        "parse_metric_and_decide": (
            "Parse metrics, compare against baseline if present, and decide keep/discard/needs_metrics. If the target "
            "is not reached and the budget allows, prefer 'needs_metrics'/continue so the loop can propose an "
            "improved search script next round, rather than stopping at a mediocre local result."
        ),
        "record_decision": "Record final decision, completed parts, artifacts, next steps, and what would be committed.",
    }

    """One bounded LLM child agent for a single fixed autoresearch step.

    It receives only the step definition, allowed action surface, and the bounded
    parent context assembled from modular buckets.  It must return structured
    JSON.  The parent loop still validates and executes the selected action.
    """

    def __init__(self, settings: AutoResearchSettings, client=None, model: str | None = None, loop: "AutoResearchLoop | None" = None):
        self.settings = settings
        self.client = client
        self.model = model or settings.llm_model
        self.loop = loop
        self._tier = "plan"

    def _client(self):
        if self.client is None:
            from core import config

            inner = config.create_llm_client()
            ledger = getattr(self.loop, "budget", None)
            if ledger is not None:
                from core.autoresearch_budget import MeteredLLMClient

                self.client = MeteredLLMClient(
                    inner,
                    ledger,
                    get_phase=lambda: getattr(self.loop, "_current_phase", "") or "",
                    get_model=lambda: self._resolved_model(),
                )
            else:
                self.client = inner
        return self.client

    def _resolved_model(self) -> str:
        if self.model:
            return self.model
        tiers = getattr(self.loop, "model_tiers", None)
        if tiers is not None:
            return tiers.resolve(self._tier)
        return __import__("core.config", fromlist=["get_model"]).get_model()

    def plan_step(
        self,
        *,
        step: AutoResearchWorkflowStep,
        fallback_action: AutoResearchAction,
        parent_context: str,
        round_index: int,
    ) -> AutoResearchStepResult:
        allowed = list(step.allowed_tools or (fallback_action.type,))
        system = (
            "You are an isolated auto_research step agent. "
            "Return ONLY valid JSON. Do not include markdown fences. "
            "Choose exactly one action within allowed_tools, and optionally write "
            "short bucket_updates for modular context. Do not claim experimental "
            "improvements without metrics in context/artifacts."
        )
        user = {
            "round_index": round_index,
            "step": {
                "name": step.name,
                "fallback_action": fallback_action.__dict__,
                "allowed_tools": allowed,
                "guidance": self.STEP_GUIDANCE.get(step.name, "Perform this step conservatively and update relevant context buckets."),
            },
            "parent_context": parent_context,
            "output_schema": {
                "action": {
                    "type": "one of allowed_tools",
                    "rationale": "short reason",
                    "command": "for run",
                    "path": "for read/write",
                    "content": "for write/note",
                    "patch": "unified diff for apply_patch",
                    "query": "for web_search",
                    "urls": "for web_extract",
                    "max_results": "integer",
                },
                "bucket_updates": {name: ["short item"] for name in DEFAULT_CONTEXT_BUCKETS},
            },
        }
        response = self._chat_completion_with_retry(system, user)
        message = response.choices[0].message
        raw = getattr(message, "content", None) or ""
        data = extract_json_object(raw)
        action_data = data.get("action") or {}
        action_type = action_data.get("type") or fallback_action.type
        if action_type not in allowed:
            raise AutoResearchSafetyError(f"LLM step action {action_type!r} not in allowed_tools={allowed}")
        action = AutoResearchAction(
            type=action_type,
            rationale=str(action_data.get("rationale") or fallback_action.rationale),
            command=str(action_data.get("command") or ""),
            path=str(action_data.get("path") or ""),
            content=str(action_data.get("content") or ""),
            patch=str(action_data.get("patch") or ""),
            query=str(action_data.get("query") or ""),
            urls=list(action_data.get("urls") or []),
            max_results=int(action_data.get("max_results") or 5),
            role=str(action_data.get("role") or fallback_action.role or ""),
        )
        # Preserve deterministic defaults for omitted action fields.
        for field_name in ("command", "path", "content", "patch", "query"):
            if not getattr(action, field_name):
                setattr(action, field_name, getattr(fallback_action, field_name))
        if not action.urls:
            action.urls = list(fallback_action.urls)
        bucket_updates = data.get("bucket_updates") or {}
        if not isinstance(bucket_updates, dict):
            bucket_updates = {}
        normalized = {}
        for key, values in bucket_updates.items():
            if key not in DEFAULT_CONTEXT_BUCKETS:
                key = "raw_observations"
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                normalized.setdefault(key, []).extend(str(v) for v in values if str(v).strip())
        return AutoResearchStepResult(
            action=action,
            bucket_updates=normalized,
            raw_response=raw,
            system_prompt=system,
            user_payload=json.dumps(user, ensure_ascii=False),
        )

    def _chat_completion_with_retry(self, system: str, user: dict):
        client = self._client()
        model = self._resolved_model()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]
        attempts = max(1, 1 + int(self.settings.llm_retry_attempts))
        timeout = float(self.settings.llm_request_timeout or 60)
        last_exc: Exception | None = None

        # Some OpenAI-compatible providers only accept the model default
        # temperature and reject explicit temperature=0. Avoid sending the
        # parameter for the default deterministic setting; callers that really
        # need sampling can still set a non-zero temperature.
        completion_kwargs = {"model": model, "messages": messages}
        if self.settings.llm_temperature not in (None, 0, 0.0):
            completion_kwargs["temperature"] = self.settings.llm_temperature

        for attempt in range(attempts):
            root = self.settings.root()
            phase = getattr(self.loop, "_current_phase", "") if self.loop is not None else ""
            step = (user.get("step") or {}).get("name") if isinstance(user, dict) else ""
            inflight_start(
                root,
                "llm",
                phase=phase,
                detail=f"{step or 'chat'} attempt {attempt + 1}/{attempts}",
                model=model,
                timeout_seconds=timeout,
                prompt_chars=sum(len(str(m.get("content", ""))) for m in messages),
            )
            try:
                try:
                    response = client.chat.completions.create(
                        **completion_kwargs,
                        timeout=timeout,
                    )
                except TypeError:
                    # Older client shims may not accept timeout=
                    response = client.chat.completions.create(**completion_kwargs)
                inflight_finish(root, "llm", phase=phase, detail=f"{step or 'chat'} attempt {attempt + 1}/{attempts}", model=model)
                return response
            except Exception as exc:
                inflight_finish(root, "llm", phase=phase, detail=f"{step or 'chat'} attempt {attempt + 1}/{attempts}", model=model, error=str(exc)[:500])
                last_exc = exc
        raise last_exc if last_exc else RuntimeError("LLM completion failed with no exception")




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


def save_project_diff(project_dir: str | Path, artifacts: AutoResearchArtifactStore, rationale: str, *, git_available: bool) -> str:
    if git_available:
        diff = _run_git(project_dir, ["diff", "--no-ext-diff", "--binary"]).get("stdout", "")
        staged = _run_git(project_dir, ["diff", "--cached", "--no-ext-diff", "--binary"]).get("stdout", "")
        status = _run_git(project_dir, ["status", "--porcelain=v1"]).get("stdout", "")
        content = "# git status --porcelain\n" + status + "\n# git diff\n" + diff + "\n# git diff --cached\n" + staged
        if content.strip():
            return artifacts.save(kind="diff", rationale=rationale, content=content, extension="diff")
        return ""
    # Non-git safe degradation: record manifest only, do not invent repository history.
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




VERSIONING_POLICIES = {"artifact_only", "commit_pareto", "commit_all_trials", "branch_per_trial"}


def normalize_versioning_policy(policy: str | None) -> str:
    value = str(policy or "artifact_only").strip().lower()
    return value if value in VERSIONING_POLICIES else "artifact_only"


PLANNER_KINDS = {"fixed", "evolutionary"}


def normalize_planner_kind(kind: str | None) -> str:
    value = str(kind or "fixed").strip().lower()
    return value if value in PLANNER_KINDS else "fixed"


def _git_worktree_clean(snapshot: dict) -> bool:
    return bool(isinstance(snapshot, dict) and snapshot.get("git_available") and not str(snapshot.get("status") or "").strip())


def _sanitize_branch_component(value: str, default: str = "trial") -> str:
    slug = _safe_slug(value, default, 80).replace("_", "-")
    return slug.strip(".-/") or default


def git_commit_trial(project_dir: str | Path, experiment_id: str, rationale: str, *, branch_name: str = "") -> dict:
    """Commit current trial changes in an existing git repo; never initializes repos."""
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
    """Safely commit a trial on a per-trial branch and return to the original branch/ref."""
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
    """Rollback tracked/staged trial changes only; intentionally preserves untracked files."""
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


def extract_metrics_from_text(text: str, program_text: str = "") -> tuple[dict[str, float], dict[str, bool]]:
    """Best-effort multi-metric parser from JSON/log text plus program hints."""
    metrics: dict[str, float] = {}
    directions: dict[str, bool] = {}
    primary = parse_primary_metric(text)
    if primary.get("metric") is not None:
        name = str(primary.get("metric_name") or "primary_metric")
        metrics[name] = float(primary["metric"])
        directions[name] = bool(primary.get("higher_is_better", _metric_direction(name)))
    for pattern in (
        r"\b([A-Za-z][A-Za-z0-9_.-]*(?:accuracy|acc|f1|auc|score|loss|error|latency|time|cost|metric)[A-Za-z0-9_.-]*)\s*[:=]\s*(-?\d+(?:\.\d+)?)",
        r"\b(accuracy|acc|f1|auc|score|loss|error|latency|time|cost)\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    ):
        for name, value in re.findall(pattern, text or "", flags=re.I):
            try:
                metrics[str(name)] = float(value)
            except ValueError:
                continue
    # Parse simple JSON metrics/results files embedded as raw action output.
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
    common = [k for k in a_metrics if k in b_metrics and isinstance(a_metrics.get(k), (int, float)) and isinstance(b_metrics.get(k), (int, float))]
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

class AutoResearchProgressView:
    """Text-only visual progress dashboard for autoresearch."""

    def __init__(self, settings: AutoResearchSettings):
        self.settings = settings
        self.path = settings.progress_file()

    @staticmethod
    def _bar(percent: int, width: int = 20) -> str:
        percent = max(0, min(100, int(percent)))
        filled = round(width * percent / 100)
        return "█" * filled + "░" * (width - filled)

    def write(
        self,
        *,
        status: str,
        current_step: str,
        round_index: int,
        total_rounds: int,
        observations: list[AutoResearchObservation],
        state: dict,
        artifact_dir: str,
        step_agent_errors: list[str] | None = None,
    ) -> None:
        total = max(1, int(total_rounds or 1))
        overall = min(100, round(max(0, round_index) * 100 / total))
        recent_text = "\n".join(obs.summary for obs in observations[-3:])
        experiment_percent = extract_progress_percent(recent_text)
        if experiment_percent is None:
            experiment_percent = overall
        buckets = state.get("buckets", {}) if isinstance(state, dict) else {}
        plans = buckets.get("modification_plans") or []
        conclusions = buckets.get("conclusions") or []
        completed = [f"- [{obs.status}] {obs.kind}: {obs.summary[:180]}" for obs in observations[-8:]]
        errors = step_agent_errors or []
        eta = self._eta_text(observations, round_index, total)
        log_tail = self._log_tail(observations)
        recent_experiments = (state.get("experiments") or []) if isinstance(state, dict) else []
        last_version = recent_experiments[-1].get("version_summary", "") if recent_experiments else ""
        lines = [
            f"# auto_research Progress — {self.settings.project_id}",
            "",
            f"Updated: {time.strftime('%F %T')}",
            f"Status: **{status}**",
            f"Current step: `{current_step}`",
            f"Versioning policy: `{normalize_versioning_policy(self.settings.versioning_policy)}`",
            f"Last version action: {last_version or '(none yet)'}",
            "",
            f"Overall: {overall}% `{self._bar(overall)}`",
            f"Experiment/Train progress: {experiment_percent}% `{self._bar(experiment_percent)}`",
            f"ETA: {eta}",
            "",
            "## 当前修改计划",
        ]
        lines.extend([f"- {item}" for item in plans[-3:]] if plans else ["- (no modification plan recorded yet)"])
        lines.extend(["", "## 实验进度 / 结论"])
        lines.extend([f"- {item}" for item in conclusions[-3:]] if conclusions else ["- (no conclusions recorded yet)"])
        lines.extend(["", "## 已完成部分"])
        lines.extend(completed if completed else ["- (no completed step yet)"])
        lines.extend(["", "## 最近日志 Tail"])
        lines.extend([f"```text", log_tail or "(no log tail yet)", "```"])
        best = state.get("best_experiment") if isinstance(state, dict) else None
        pareto = state.get("pareto_front") if isinstance(state, dict) else []
        lines.extend(["", "## Evolution summary"])
        if best:
            lines.append(f"- Best: `{best.get('experiment_id')}` decision={best.get('decision')} metrics={json.dumps(best.get('metrics') or {}, ensure_ascii=False)}")
        else:
            lines.append("- Best: (no metric-bearing experiment yet)")
        lines.append(f"- Pareto candidates: {len(pareto or [])}")
        lines.extend(["", "## Artifacts", f"- `{artifact_dir}`"])
        if errors:
            lines.extend(["", "## Step Agent Fallback / Errors", *(f"- {e}" for e in errors[-5:])])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _eta_text(observations: list[AutoResearchObservation], round_index: int, total_rounds: int) -> str:
        if len(observations) < 2:
            return "estimating"
        elapsed = max(0.0, observations[-1].created_at - observations[0].created_at)
        avg = elapsed / max(1, len(observations) - 1)
        remaining = max(0, total_rounds - round_index)
        seconds = int(avg * remaining)
        return f"~{seconds}s remaining"

    @staticmethod
    def _log_tail(observations: list[AutoResearchObservation], max_lines: int = 20) -> str:
        for obs in reversed(observations):
            if not obs.artifact_path:
                continue
            path = Path(obs.artifact_path)
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    text = "\n".join(str(data.get(k, "")) for k in ("stdout", "stderr") if data.get(k)) or text
            except Exception:
                pass
            tail = "\n".join(text.splitlines()[-max_lines:])
            if tail.strip():
                return tail[-4000:]
        return ""


class AutoResearchLoop:
    """Lightweight loop for autoresearch projects.

    Each round: build bounded parent context -> choose one action -> execute in a
    project-confined child/tool surface -> archive raw output -> persist a compact
    observation into .autoresearch/state.json.
    """

    def __init__(self, settings: AutoResearchSettings, planner: Optional[Planner] = None, summarizer: Optional[Summarizer] = None, step_agent: AutoResearchStepAgent | None = None):
        self.settings = settings
        self.boundary = ProjectBoundary(settings.project_dir)
        self.context = AutoResearchContextManager(settings)
        self.artifacts = AutoResearchArtifactStore(settings)
        self.runner = ProjectConfinedCommandRunner(settings.project_dir, settings.command_timeout_seconds)
        self.budget = self._build_budget_ledger()
        self.model_tiers = self._build_model_tiers()
        self._current_phase = ""
        self.planner = planner or self._build_default_planner()
        self.summarizer = summarizer or self.default_summarizer
        self.step_agent = step_agent or (AutoResearchStepAgent(settings, loop=self) if settings.use_llm_step_agents else None)
        self._observations: list[AutoResearchObservation] = []
        self._step_agent_errors: list[str] = []
        self.progress = AutoResearchProgressView(settings)
        self._experiment_count = 0
        bind_loop = getattr(self.planner, "bind_loop", None)
        if callable(bind_loop):
            bind_loop(self)

    def _build_budget_ledger(self):
        from core.autoresearch_budget import BudgetLedger, BudgetLimits

        limits = BudgetLimits(
            max_usd=float(self.settings.max_usd or 0.0),
            max_tokens=int(self.settings.max_tokens or 0),
            degrade_ratio=float(self.settings.budget_degrade_ratio or 0.8),
        )
        return BudgetLedger(self.settings.budget_file(), limits)

    def _build_model_tiers(self):
        from core.autoresearch_budget import ModelTiers

        base = self.settings.llm_model or ""
        tiers = ModelTiers.from_env(base=base)
        # Explicit settings override env.
        if self.settings.model_tier_plan:
            tiers.plan = self.settings.model_tier_plan
        if self.settings.model_tier_exec:
            tiers.exec = self.settings.model_tier_exec
        if self.settings.model_tier_util:
            tiers.util = self.settings.model_tier_util
        return tiers

    def _build_default_planner(self) -> "Planner":
        if normalize_planner_kind(self.settings.planner_kind) == "evolutionary":
            return EvolutionaryAutoResearchPlanner()
        return FixedAutoResearchPlanner()

    def run(self, rounds: Optional[int] = None) -> dict:
        max_rounds = max(0, int(rounds if rounds is not None else self.settings.max_rounds))
        self._write_progress("running", "starting", 0, max_rounds)
        stopped_early = False
        for round_index in range(max_rounds):
            # Cooperative interrupt: a watcher (or the user's esc handler) can
            # drop a STOP sentinel in .autoresearch/ to end the loop cleanly at a
            # round boundary. All prior rounds are already persisted, so this
            # loses at most the not-yet-started round.
            if self._stop_requested():
                self._write_progress("stopped", "stopped_by_request", round_index, max_rounds)
                stopped_early = True
                break
            step = getattr(self.planner, "step_for_round", lambda _i: None)(round_index)
            step_name = getattr(step, "name", f"round_{round_index}")
            self._write_progress("running", step_name, round_index, max_rounds)
            parent_context = self.context.build_parent_context(self._observations)
            step_result = self._plan_step(parent_context, round_index)
            action = step_result.action
            self._capture_proposed_change_spec(step_name, action)
            action = self._maybe_hydrate_apply_change(step_name, action)
            step_result.action = action
            self._validate_step_tool_scope(action, round_index)
            self._apply_bucket_updates(step_result.bucket_updates)
            if self._is_experiment_action(action, step_name) and self._experiment_count >= max(0, int(self.settings.max_experiments)):
                observation = AutoResearchObservation(
                    "experiment_budget",
                    f"Skipped trial because max_experiments={self.settings.max_experiments} was reached",
                    "",
                    "skipped",
                )
                self._archive_useful_failure({"summary": observation.summary, "status": "skipped_budget"})
                base_git = {}
            else:
                base_git = git_snapshot(self.settings.root(), enabled=self.settings.use_git_versioning)
                observation = self.execute_action(action)
            self._observations.append(observation)
            self._persist_observation(observation)
            self._maybe_record_experiment(action, observation, base_git, step_name)
            self._write_round_trace(round_index, step_name, parent_context, step_result, observation)
            self._write_progress("running", step_name, round_index + 1, max_rounds)
            if action.type == "stop":
                break
        self._write_evolution_artifacts(self.context.load_state())
        final_status = "stopped" if stopped_early else "completed"
        self._write_progress(final_status, "stopped_by_request" if stopped_early else "done", len(self._observations) if stopped_early else max_rounds, max_rounds)
        return {
            "project_id": self.settings.project_id,
            "rounds_completed": len(self._observations),
            "stopped_early": stopped_early,
            "observations": [obs.compact(max_chars=1200) for obs in self._observations],
            "state_path": str(self.settings.state_file()),
            "artifact_dir": str(self.settings.artifacts_root()),
            "progress_path": str(self.settings.progress_file()),
            "best_path": str(self.settings.root() / ".autoresearch" / "best.json"),
            "pareto_front_path": str(self.settings.root() / ".autoresearch" / "pareto_front.json"),
            "active_context_path": str(self.settings.root() / ".autoresearch" / "active_context.md"),
            "versioning_policy": normalize_versioning_policy(self.settings.versioning_policy),
            "use_git_versioning": bool(self.settings.use_git_versioning),
            "step_agent_errors": list(self._step_agent_errors),
        }

    def _stop_requested(self) -> bool:
        """True if a STOP sentinel exists (cooperative interrupt / esc)."""
        try:
            return self.settings.stop_file().exists()
        except Exception:
            return False

    def _write_progress(self, status: str, current_step: str, round_index: int, total_rounds: int) -> None:
        try:
            self.progress.write(
                status=status,
                current_step=current_step,
                round_index=round_index,
                total_rounds=total_rounds,
                observations=self._observations,
                state=self.context.load_state(),
                artifact_dir=str(self.settings.artifacts_root()),
                step_agent_errors=self._step_agent_errors,
            )
        except Exception:
            pass

    def _plan_step(self, parent_context: str, round_index: int) -> AutoResearchStepResult:
        fallback_action = self.planner(parent_context, round_index)
        if not self.step_agent:
            return AutoResearchStepResult(action=fallback_action, used_fallback=True)
        step_getter = getattr(self.planner, "step_for_round", None)
        step = step_getter(round_index) if callable(step_getter) else None
        if step is None:
            return AutoResearchStepResult(action=fallback_action, used_fallback=True)
        try:
            result = self.step_agent.plan_step(
                step=step,
                fallback_action=fallback_action,
                parent_context=parent_context,
                round_index=round_index,
            )
            self._validate_step_tool_scope(result.action, round_index)
            return result
        except Exception as exc:
            msg = f"step_agent fallback at round {round_index} ({getattr(step, 'name', 'unknown')}): {exc}"
            self._step_agent_errors.append(msg)
            return AutoResearchStepResult(
                action=fallback_action,
                bucket_updates={"raw_observations": [msg]},
                used_fallback=True,
                error=str(exc),
            )

    def _write_round_trace(self, round_index, step_name, parent_context, step_result, observation) -> None:
        """Dump the full per-round LLM I/O + outcome for post-hoc debugging.

        Gated by settings.trace_rounds (default off) because it writes the entire
        parent context and prompt/response each round, which is verbose. When on,
        every round produces .autoresearch/round_traces/round_<NNN>_<step>.json so
        it is possible to see exactly what the LLM saw and replied, and why an
        action was chosen or fell back.
        """
        if not getattr(self.settings, "trace_rounds", False):
            return
        try:
            root = self.settings.trace_root()
            root.mkdir(parents=True, exist_ok=True)
            action = step_result.action
            trace = {
                "round_index": round_index,
                "step_name": step_name,
                "timestamp": time.strftime("%F %T"),
                "used_fallback": bool(step_result.used_fallback),
                "step_agent_error": step_result.error or "",
                "llm": {
                    "system_prompt": step_result.system_prompt or "",
                    "user_payload": step_result.user_payload or "",
                    "raw_response": step_result.raw_response or "",
                },
                "parent_context": parent_context,
                "chosen_action": {
                    "type": action.type,
                    "role": getattr(action, "role", ""),
                    "rationale": action.rationale,
                    "command": action.command,
                    "path": action.path,
                    "patch": action.patch,
                    "content_preview": (action.content or "")[:2000],
                },
                "bucket_updates": step_result.bucket_updates,
                "observation": {
                    "kind": observation.kind,
                    "status": observation.status,
                    "summary": observation.summary[:2000],
                    "artifact_path": observation.artifact_path,
                },
            }
            fname = f"round_{round_index:03d}_{_safe_slug(step_name)}.json"
            (root / fname).write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            # Tracing must never break the loop.
            pass

    def _apply_bucket_updates(self, bucket_updates: dict[str, list[str]]) -> None:
        if not bucket_updates:
            return
        state = self.context.load_state()
        for bucket_name, values in bucket_updates.items():
            if isinstance(values, str):
                values = [values]
            for value in values or []:
                self.context.add_to_bucket(state, bucket_name, str(value))
        self.context.save_state(state)

    _CHANGE_SPEC_STEPS = {"plan_change", "propose_experiment"}
    _APPLY_STEP_NAMES = {"apply_change"}

    def _proposed_change_path(self) -> Path:
        return self.settings.root() / ".autoresearch" / "proposed_change.json"

    def _capture_proposed_change_spec(self, step_name: str, action: AutoResearchAction) -> None:
        """If plan/propose step emitted a JSON change spec in note content, persist it.

        The spec is a lightweight escape hatch so deterministic and LLM step
        agents can queue a code change without producing a unified diff. Two
        forms are supported and validated in `_maybe_hydrate_apply_change`.
        """
        if step_name not in self._CHANGE_SPEC_STEPS or action.type != "note":
            return
        spec = _extract_change_spec(action.content or "")
        if not spec:
            return
        try:
            target_label = str(spec.get("path") or "")
            if not target_label:
                return
            # Reject obvious escapes before persisting; final resolution still
            # goes through ProjectBoundary in `_maybe_hydrate_apply_change`.
            self.boundary.resolve(target_label)
        except Exception:
            return
        path = self._proposed_change_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _maybe_hydrate_apply_change(self, step_name: str, action: AutoResearchAction) -> AutoResearchAction:
        """Upgrade apply_change note fallback into an apply_patch when a change spec is queued.

        Precedence:
        1. LLM emitted apply_patch with a real patch: pass through.
        2. LLM emitted note but proposed_change.json exists: synthesize a unified
           diff and switch to apply_patch.
        3. No spec available: keep the note (documents why no change was applied).
        """
        if step_name not in self._APPLY_STEP_NAMES:
            return action
        if action.type == "apply_patch" and (action.patch or action.content).strip():
            return action
        spec_path = self._proposed_change_path()
        if not spec_path.exists():
            return action
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            patch = self._change_spec_to_patch(spec)
        except Exception as exc:
            note_content = (action.content or "") + f"\napply_change: failed to synthesize patch from proposed_change.json: {exc}"
            return AutoResearchAction(
                type="note",
                rationale=action.rationale or "apply_change_synthesis_failed",
                content=note_content,
                role=action.role,
            )
        if not patch.strip():
            return action
        return AutoResearchAction(
            type="apply_patch",
            rationale=action.rationale or "apply_change_from_proposed_spec",
            patch=patch,
            role=action.role,
        )

    def _change_spec_to_patch(self, spec: dict) -> str:
        kind = str(spec.get("kind") or "").strip().lower()
        target_label = str(spec.get("path") or "").strip()
        if not target_label:
            raise AutoResearchSafetyError("proposed_change.json missing 'path'")
        target = self.boundary.resolve(target_label)
        if kind == "write":
            new_content = str(spec.get("content") or "")
            old_lines = target.read_text(encoding="utf-8").splitlines(keepends=True) if target.exists() else []
            new_lines = new_content.splitlines(keepends=True)
            return _make_unified_diff(target_label, old_lines, new_lines, is_new=not target.exists())
        if kind == "search_replace":
            if not target.exists():
                raise AutoResearchSafetyError(f"search_replace target does not exist: {target_label}")
            original = target.read_text(encoding="utf-8")
            old_snippet = str(spec.get("old") or "")
            new_snippet = str(spec.get("new") or "")
            if not old_snippet:
                raise AutoResearchSafetyError("search_replace requires non-empty 'old'")
            if old_snippet not in original:
                raise AutoResearchSafetyError(f"search_replace 'old' snippet not found in {target_label}")
            occurrences = original.count(old_snippet)
            if occurrences > 1:
                raise AutoResearchSafetyError(f"search_replace 'old' snippet is not unique in {target_label} ({occurrences} matches)")
            replaced = original.replace(old_snippet, new_snippet, 1)
            return _make_unified_diff(target_label, original.splitlines(keepends=True), replaced.splitlines(keepends=True), is_new=False)
        raise AutoResearchSafetyError(f"unsupported change spec kind: {kind!r}")

    def _validate_step_tool_scope(self, action: AutoResearchAction, round_index: int) -> None:
        allowed_getter = getattr(self.planner, "allowed_tools_for_round", None)
        if not callable(allowed_getter):
            return
        allowed = tuple(allowed_getter(round_index) or ())
        if allowed and action.type not in allowed:
            raise AutoResearchSafetyError(
                f"Workflow step {round_index} attempted action {action.type!r}, allowed={allowed}"
            )

    def execute_action(self, action: AutoResearchAction) -> AutoResearchObservation:
        try:
            if action.type == "run":
                result = self.runner.run(action.command)
                raw = json.dumps(result, ensure_ascii=False, indent=2)
                artifact = self.artifacts.save(kind="shell", rationale=action.rationale, content=raw, extension="json")
                status = "ok" if result.get("returncode") == 0 else "failed"
                # Robustness: a baseline/trial wrapper (often LLM-generated) may exit
                # nonzero because of a broken *summary* step even though train/eval
                # produced a valid metric. Do not let that mask a good experiment:
                # if a primary metric is parseable from the output, recover to ok.
                if status == "failed" and self._run_has_valid_metric(result, action):
                    status = "ok_metric_recovered"
                self._record_metric(action, raw, artifact, status)
                return AutoResearchObservation("shell", self.summarizer(action, raw), artifact, status)
            if action.type == "read":
                path = self.boundary.resolve(action.path)
                raw = path.read_text(encoding="utf-8")
                artifact = self.artifacts.save(kind="read", rationale=action.rationale, content=raw, extension="txt")
                self._record_metric(action, raw, artifact, "ok")
                return AutoResearchObservation("read", self.summarizer(action, raw), artifact, "ok")
            if action.type == "write":
                path = self.boundary.resolve(action.path)
                self._ensure_write_allowed(path)
                self._ensure_not_readonly_eval(path)
                old = path.read_text(encoding="utf-8") if path.exists() else ""
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(action.content, encoding="utf-8")
                raw = json.dumps({"path": str(path), "old_chars": len(old), "new_chars": len(action.content)}, ensure_ascii=False)
                artifact = self.artifacts.save(kind="write", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("write", self.summarizer(action, raw), artifact, "ok")
            if action.type == "apply_patch":
                result = apply_patch_with_git(self.settings.root(), action.patch or action.content, readonly_globs=self.settings.readonly_eval_globs)
                raw = json.dumps(result, ensure_ascii=False, indent=2)
                artifact = self.artifacts.save(kind="apply_patch", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("apply_patch", self.summarizer(action, raw), artifact, "ok")
            if action.type == "web_search":
                raw = web_search_tool(action.query, limit=action.max_results)
                artifact = self.artifacts.save(kind="web_search", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("web_search", self.summarizer(action, raw), artifact, "ok")
            if action.type == "web_extract":
                raw = web_extract_tool(action.urls)
                artifact = self.artifacts.save(kind="web_extract", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("web_extract", self.summarizer(action, raw), artifact, "ok")
            if action.type == "note":
                artifact = self.artifacts.save(kind="note", rationale=action.rationale, content=action.content, extension="md")
                self._record_metric(action, action.content, artifact, "ok")
                return AutoResearchObservation("note", self.summarizer(action, action.content), artifact, "ok")
            if action.type == "stop":
                return AutoResearchObservation("stop", action.rationale or "Stopped by planner", "", "ok")
            raise AutoResearchSafetyError(f"Unsupported action type: {action.type}")
        except Exception as exc:
            raw = json.dumps({"error": str(exc), "action": action.__dict__}, ensure_ascii=False, indent=2, default=str)
            artifact = self.artifacts.save(kind="error", rationale=action.rationale or action.type, content=raw, extension="json")
            return AutoResearchObservation(action.type, f"Action failed: {exc}", artifact, "failed")

    def _run_has_valid_metric(self, result: dict, action: AutoResearchAction) -> bool:
        """True if a baseline/trial run yielded a parseable primary metric.

        Used to recover a nonzero-exit run whose failure was only in a summary
        wrapper (e.g. a broken inline python) while train/eval + metrics were
        fine. Scoped to explicit baseline/trial roles so a generic run that
        exits nonzero still surfaces as failed even if it happened to print a
        metric-looking line.
        """
        role = getattr(action, "role", "")
        if role not in {"baseline", "trial"}:
            return False
        text = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
        if parse_primary_metric(text).get("metric") is not None:
            return True
        file_metrics, _ = self._collect_metric_files()
        return bool(file_metrics)

    def _record_metric(self, action: AutoResearchAction, raw: str, artifact_path: str, status: str) -> None:
        info = parse_primary_metric(raw)
        metric = info.get("metric")
        if metric is None:
            return
        state = self.context.load_state()
        baseline = state.get("baseline_metric")
        is_baseline = self._is_baseline_action(action)
        decision = decide_experiment(metric, None if is_baseline else baseline, bool(info.get("higher_is_better", True)))
        record = {
            "timestamp": time.strftime("%F %T"),
            "rationale": action.rationale,
            "metric_name": info.get("metric_name"),
            "metric": metric,
            "higher_is_better": info.get("higher_is_better"),
            "decision": decision,
            "artifact_path": artifact_path,
            "status": status,
        }
        state.setdefault("metrics", []).append(record)
        if is_baseline or baseline is None:
            state["baseline_metric"] = metric
        self.context.save_state(state)
        results_path = self.settings.root() / "results.tsv"
        header = "timestamp\trationale\tmetric_name\tmetric\thigher_is_better\tdecision\tartifact_path\tstatus\n"
        line = "\t".join(str(record[k]) for k in ["timestamp", "rationale", "metric_name", "metric", "higher_is_better", "decision", "artifact_path", "status"]) + "\n"
        if not results_path.exists():
            results_path.write_text(header + line, encoding="utf-8")
        else:
            with results_path.open("a", encoding="utf-8") as f:
                f.write(line)

    def _ensure_write_allowed(self, path: Path) -> None:
        for root in self.settings.allowed_file_write_roots or (".",):
            allowed = self.boundary.resolve(root)
            try:
                path.resolve().relative_to(allowed)
                return
            except ValueError:
                continue
        raise AutoResearchSafetyError(f"Write path is outside allowed roots: {path}")

    def _ensure_not_readonly_eval(self, path: Path) -> None:
        try:
            rel = str(Path(path).resolve().relative_to(self.settings.root()))
        except ValueError:
            return
        if _matches_readonly(rel, self.settings.readonly_eval_globs):
            raise AutoResearchSafetyError(
                f"write target is a read-only evaluation file: {rel} (requires user approval)"
            )

    def _persist_observation(self, obs: AutoResearchObservation) -> None:
        state = self.context.load_state()
        observations = list(state.get("observations") or [])
        observations.append(obs.compact(max_chars=1200))
        state["observations"] = observations[-100:]
        addition = f"- {time.strftime('%F %T')}: [{obs.status}/{obs.kind}] {obs.summary}"
        self.context.add_to_bucket(state, self._bucket_for_observation(obs), addition)
        existing = str(state.get("summary") or "").rstrip()
        state["summary"] = self.context._truncate((existing + "\n" + addition).strip(), self.settings.summary_char_budget)
        state["updated_at"] = time.time()
        self.context.save_state(state)

    @staticmethod
    def _is_experiment_action(action: AutoResearchAction, step_name: str = "") -> bool:
        if action.type != "run":
            return False
        if getattr(action, "role", "") == "trial":
            return True
        if getattr(action, "role", "") == "baseline":
            return False
        text = f"{step_name} {action.rationale} {action.command}".lower()
        return any(token in text for token in ("trial", "experiment", "run_experiment"))

    @staticmethod
    def _is_baseline_action(action: AutoResearchAction) -> bool:
        role = getattr(action, "role", "")
        if role == "baseline":
            return True
        if role == "trial":
            return False
        return "baseline" in (action.rationale or "").lower()

    def _maybe_record_experiment(self, action: AutoResearchAction, obs: AutoResearchObservation, base_git: dict, step_name: str) -> None:
        if not self._is_experiment_action(action, step_name):
            return
        if self._experiment_count >= max(0, int(self.settings.max_experiments)):
            self._archive_useful_failure({
                "summary": f"Skipped experiment record because max_experiments={self.settings.max_experiments} was reached",
                "action": action.__dict__,
                "status": "skipped_budget",
            })
            return
        self._experiment_count += 1
        state = self.context.load_state()
        existing = list(state.get("experiments") or [])
        experiment_id = f"exp-{len(existing) + 1:04d}-{int(time.time())}"
        raw = ""
        if obs.artifact_path and Path(obs.artifact_path).exists():
            raw = Path(obs.artifact_path).read_text(encoding="utf-8", errors="replace")
        program_text = self.context.read_program()
        metrics, directions = extract_metrics_from_text(raw, program_text)
        file_metrics, file_directions = self._collect_metric_files()
        metrics.update(file_metrics)
        directions.update(file_directions)
        primary = parse_primary_metric(raw)
        primary_name = primary.get("metric_name") if primary.get("metric") is not None else (next(iter(metrics), None))
        primary_metric = metrics.get(primary_name) if primary_name else None
        baseline = state.get("baseline_metric")
        decision = decide_experiment(primary_metric, baseline, directions.get(str(primary_name), True)) if primary_name else ("failed" if obs.status == "failed" else "needs_metrics")
        policy = normalize_versioning_policy(self.settings.versioning_policy)
        git_after = git_snapshot(self.settings.root(), enabled=self.settings.use_git_versioning)
        git_available = bool(git_after.get("git_available"))
        changed_files = git_changed_files(self.settings.root()) if git_available else []
        diff_path = save_project_diff(self.settings.root(), self.artifacts, experiment_id, git_available=git_available)
        base_commit = base_git.get("head", "") if isinstance(base_git, dict) else ""
        base_clean = _git_worktree_clean(base_git)
        record = {
            "experiment_id": experiment_id,
            "created_at": time.time(),
            "timestamp": time.strftime("%F %T"),
            "hypothesis": action.rationale,
            "summary": obs.summary[:1200],
            "metrics": metrics,
            "metric_directions": directions,
            "primary_metric_name": primary_name,
            "status": obs.status,
            "decision": decision,
            "changed_files": changed_files,
            "diff_path": diff_path,
            "artifact_path": obs.artifact_path,
            "git_commit": "",
            "commit_sha": "",
            "branch": "",
            "base_commit": base_commit,
            "git_available": git_available,
            "git_status_before": base_git.get("status", "") if isinstance(base_git, dict) else "",
            "git_status_after": git_after.get("status", "") if isinstance(git_after, dict) else "",
            "version_policy": policy,
            "version_action": "artifact_only",
            "rollback_status": "not_needed",
            "version_error": "",
        }
        existing.append(record)
        state["experiments"] = existing[-max(1, int(self.settings.max_experiments)) :]

        # Recompute multi-objective governance artifacts before version decisions so
        # commit_pareto can use the same best/Pareto view written to state.
        all_directions = {}
        for exp in state.get("experiments") or []:
            all_directions.update(exp.get("metric_directions") or {})
        front = pareto_front(state.get("experiments") or [], all_directions, self.settings.max_pareto_items)
        best = choose_best_experiment(state.get("experiments") or [], all_directions, primary_name)
        state["pareto_front"] = front
        state["best_experiment"] = best

        front_ids = {str(item.get("experiment_id")) for item in front or []}
        best_id = str((best or {}).get("experiment_id") or "")
        has_metrics = bool(metrics)
        invalid = obs.status == "failed" or decision in {"needs_metrics", "failed"} or not has_metrics
        # commit_pareto is intentionally strict: only the current best or
        # non-dominated Pareto candidates are committed. A merely improved but
        # dominated trial stays as a patch artifact and is rolled back below.
        pareto_kept = experiment_id in front_ids or experiment_id == best_id
        should_commit = False
        should_branch = False
        should_rollback = False
        if not git_available or not self.settings.use_git_versioning:
            record["version_action"] = "artifact_only_disabled" if not self.settings.use_git_versioning else "artifact_only_no_git"
            record["rollback_status"] = "skipped_no_git"
        elif policy == "artifact_only":
            record["version_action"] = "artifact_only"
            record["rollback_status"] = "skipped_artifact_only"
        elif not base_clean:
            record["version_action"] = "artifact_only_dirty_base"
            record["rollback_status"] = "skipped_dirty_base"
        else:
            if policy == "commit_all_trials":
                should_commit = not invalid
                should_rollback = invalid
            elif policy == "commit_pareto":
                should_commit = (not invalid) and pareto_kept
                should_rollback = not should_commit
            elif policy == "branch_per_trial":
                should_branch = not invalid
                should_rollback = invalid
            if should_commit:
                result = git_commit_trial(self.settings.root(), experiment_id, action.rationale)
                record["version_action"] = result.get("action", "commit_attempted")
                record["commit_sha"] = result.get("commit_sha", "")
                record["git_commit"] = record["commit_sha"]
                record["branch"] = result.get("branch", "")
                record["version_error"] = result.get("error", "")
                if result.get("action") in {"commit_failed", "committed_branch_failed"}:
                    should_rollback = True
            elif should_branch:
                result = git_branch_trial(self.settings.root(), experiment_id, action.rationale, base_commit)
                record["version_action"] = result.get("action", "branch_attempted")
                record["commit_sha"] = result.get("commit_sha", "")
                record["git_commit"] = record["commit_sha"]
                record["branch"] = result.get("branch", "")
                record["rollback_status"] = result.get("rollback_status", "not_needed")
                record["version_error"] = result.get("error", "")
                should_rollback = False
            else:
                record["version_action"] = "artifact_only_not_selected"
            if should_rollback:
                rollback = git_safe_rollback_to_base(self.settings.root(), base_commit)
                record["rollback_status"] = rollback.get("status", "unknown")
                record["version_error"] = record.get("version_error", "") or rollback.get("error", "")

        git_final = git_snapshot(self.settings.root(), enabled=self.settings.use_git_versioning)
        record["git_status_final"] = git_final.get("status", "") if isinstance(git_final, dict) else ""
        record["version_summary"] = f"policy={policy} action={record.get('version_action')} rollback={record.get('rollback_status')} commit={record.get('commit_sha','')} branch={record.get('branch','')}"

        if obs.status == "failed" or decision in {"discard", "needs_metrics", "failed"}:
            self._archive_useful_failure(record, state=state)
        self.context.add_to_bucket(state, "current_changes", f"Versioning: {record['version_summary']} diff={diff_path}")
        feedback = self._search_feedback_digest()
        if feedback:
            self.context.add_to_bucket(state, "experiment_results", feedback)
        self.context.save_state(state)
        self._write_evolution_artifacts(state)

    def _search_feedback_digest(self) -> str:
        """Compact digest of a train-side search's own summary/history, if present.

        A self-iterating search script (the pattern we now encourage) tends to
        write outputs/train_search_summary.json + train_search_history.jsonl.
        Surfacing best_z, best point, eval_count and the sampled range back into
        the experiment_results bucket is what lets the NEXT round's planner see
        "range too small / stuck at boundary" and rewrite a better script.
        """
        root = self.settings.root()
        summary_path = root / "outputs" / "train_search_summary.json"
        if not summary_path.exists():
            return ""
        try:
            s = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        parts = []
        for k in ("best_z", "best_x", "best_y", "eval_count", "status"):
            if k in s:
                parts.append(f"{k}={s[k]}")
        # Add sampled x/y range from history so the planner can judge coverage.
        hist_path = root / "outputs" / "train_search_history.jsonl"
        if hist_path.exists():
            try:
                xs, ys = [], []
                for line in hist_path.read_text(encoding="utf-8").splitlines()[-2000:]:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if isinstance(r.get("x"), (int, float)):
                        xs.append(float(r["x"]))
                    if isinstance(r.get("y"), (int, float)):
                        ys.append(float(r["y"]))
                if xs and ys:
                    parts.append(f"sampled_x_range=[{min(xs):g},{max(xs):g}]")
                    parts.append(f"sampled_y_range=[{min(ys):g},{max(ys):g}]")
            except Exception:
                pass
        if not parts:
            return ""
        return "Search feedback: " + " ".join(parts) + " (if not converged, widen range/increase budget/refine locally next round)"

    def _collect_metric_files(self) -> tuple[dict[str, float], dict[str, bool]]:
        root = self.settings.root()
        metrics: dict[str, float] = {}
        directions: dict[str, bool] = {}
        candidates = [root / "metrics.json", root / "results.json", root / ".autoresearch" / "metrics.json"]
        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
            except Exception:
                continue
            parsed, dirs = extract_metrics_from_text(text, self.context.read_program())
            metrics.update(parsed)
            directions.update(dirs)
        results_tsv = root / "results.tsv"
        if results_tsv.exists():
            try:
                parsed, dirs = extract_metrics_from_text(results_tsv.read_text(encoding="utf-8", errors="replace")[-100_000:], self.context.read_program())
                metrics.update(parsed); directions.update(dirs)
            except Exception:
                pass
        return metrics, directions

    def _archive_useful_failure(self, record: dict, state: dict | None = None) -> None:
        own_state = state if state is not None else self.context.load_state()
        failure = {
            "experiment_id": record.get("experiment_id", ""),
            "timestamp": record.get("timestamp", time.strftime("%F %T")),
            "summary": str(record.get("summary") or record.get("hypothesis") or record.get("status") or "")[:800],
            "decision": record.get("decision", record.get("status", "failed")),
            "artifact_path": record.get("artifact_path", ""),
            "diff_path": record.get("diff_path", ""),
            "version_policy": record.get("version_policy", ""),
            "version_action": record.get("version_action", ""),
            "commit_sha": record.get("commit_sha", record.get("git_commit", "")),
            "branch": record.get("branch", ""),
            "rollback_status": record.get("rollback_status", ""),
        }
        useful = list(own_state.get("useful_failures") or [])
        useful.append(failure)
        own_state["useful_failures"] = useful[-max(0, int(self.settings.max_useful_failures)) :]
        if state is None:
            self.context.save_state(own_state)

    def _write_evolution_artifacts(self, state: dict) -> None:
        root = self.settings.root() / ".autoresearch"
        root.mkdir(parents=True, exist_ok=True)
        best = state.get("best_experiment")
        front = state.get("pareto_front") or []
        (root / "best.json").write_text(json.dumps(best or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (root / "pareto_front.json").write_text(json.dumps(front, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._write_active_context(state)

    def _write_active_context(self, state: dict) -> None:
        root = self.settings.root() / ".autoresearch"
        budget = max(1000, int(self.settings.max_active_context_chars or 8000))
        recent_experiments = state.get("experiments") or []
        last_version = recent_experiments[-1].get("version_summary", "") if recent_experiments else ""
        lines = [
            f"# Active autoresearch context — {self.settings.project_id}",
            "",
            "This is a compressed working context. Raw logs and full history stay in artifacts/state.",
            "",
            "## Versioning",
            f"- policy: `{normalize_versioning_policy(self.settings.versioning_policy)}`",
            f"- last action: {last_version or '(none yet)'}",
            "",
        ]
        best = state.get("best_experiment") or {}
        if best:
            lines.extend([
                "## Best experiment",
                f"- id: {best.get('experiment_id')}",
                f"- decision/status: {best.get('decision')} / {best.get('status')}",
                f"- metrics: {json.dumps(best.get('metrics') or {}, ensure_ascii=False)}",
                f"- diff: {best.get('diff_path', '')}",
                "",
            ])
        lines.append("## Pareto front")
        for item in (state.get("pareto_front") or [])[: self.settings.max_pareto_items]:
            lines.append(f"- {item.get('experiment_id')}: decision={item.get('decision')} metrics={json.dumps(item.get('metrics') or {}, ensure_ascii=False)} diff={item.get('diff_path','')} version={item.get('version_summary') or item.get('version_action','')}")
        if not (state.get("pareto_front") or []):
            lines.append("- (no metric-bearing Pareto candidates yet)")
        lines.extend(["", "## Useful failures / discarded rounds"])
        for item in (state.get("useful_failures") or [])[-self.settings.max_useful_failures :]:
            lines.append(f"- {item.get('experiment_id','')}: {item.get('decision')} — {item.get('summary','')[:240]} artifact={item.get('artifact_path','')} version={item.get('version_action','')} rollback={item.get('rollback_status','')}")
        lines.extend(["", "## Recent conclusions"])
        for item in ((state.get("buckets") or {}).get("conclusions") or [])[-3:]:
            lines.append(f"- {item}")
        text = "\n".join(lines).strip() + "\n"
        if len(text) > budget:
            text = text[: budget - 40].rstrip() + "\n...<active context clipped>...\n"
        (root / "active_context.md").write_text(text, encoding="utf-8")

    @staticmethod
    def _bucket_for_observation(obs: AutoResearchObservation) -> str:
        text = f"{obs.kind} {obs.summary}".lower()
        if "conclusion" in text or "summary" in text:
            return "conclusions"
        if "modification_plan" in text or "plan" in text:
            return "modification_plans"
        if "experiment_result" in text or "eval" in text or "metric" in text or "train" in text:
            return "experiment_results"
        if "change" in text or "write" in text or "diff" in text:
            return "current_changes"
        if "question" in text or "unknown" in text:
            return "open_questions"
        if "project_understanding" in text or "inspect" in text or "program" in text:
            return "project_understanding"
        return "raw_observations"

    @staticmethod
    def default_summarizer(action: AutoResearchAction, raw: str) -> str:
        preview = raw.strip().replace("\r", "")
        metric_info = parse_primary_metric(raw)
        decision = decide_experiment(metric_info.get("metric"), None, bool(metric_info.get("higher_is_better", True)))
        progress = extract_progress_percent(raw)
        extras = []
        if metric_info.get("metric") is not None:
            extras.append(f"metric={metric_info['metric']} {metric_info['metric_name']} decision={decision}")
        if progress is not None:
            extras.append(f"progress={progress}%")
        if len(preview) > 900:
            preview = preview[:897].rstrip() + "..."
        suffix = ("; " + "; ".join(extras)) if extras else ""
        return f"{action.type} rationale={action.rationale!r}{suffix}; raw_preview={preview}"

    @staticmethod
    def default_planner(parent_context: str, round_index: int) -> AutoResearchAction:
        if round_index == 0:
            return AutoResearchAction(
                type="run",
                rationale="bootstrap_inspect_project",
                command="pwd && find . -maxdepth 2 -type f | sort | head -80",
            )
        return AutoResearchAction(type="stop", rationale="default planner completed bootstrap")


__all__ = [
    "AutoResearchAction",
    "AutoResearchArtifactStore",
    "AutoResearchContextManager",
    "AutoResearchLoop",
    "AutoResearchObservation",
    "AutoResearchStepAgent",
    "AutoResearchStepResult",
    "AutoResearchWorkflowStep",
    "ContextBucket",
    "DEFAULT_CONTEXT_BUCKETS",
    "AutoResearchProgressView",
    "FixedAutoResearchPlanner",
    "decide_experiment",
    "extract_json_object",
    "extract_progress_percent",
    "parse_primary_metric",
    "apply_unified_patch_limited",
    "apply_patch_with_git",
    "AutoResearchSafetyError",
    "AutoResearchSettings",
    "ProjectBoundary",
    "ProjectConfinedCommandRunner",
]
