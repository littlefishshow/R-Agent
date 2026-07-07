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

    def root(self) -> Path:
        return Path(self.project_dir).expanduser().resolve()

    def program_file(self) -> Path:
        p = Path(self.program_path)
        return p if p.is_absolute() else self.root() / p

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


@dataclass
class AutoResearchStepResult:
    action: AutoResearchAction
    bucket_updates: dict[str, list[str]] = field(default_factory=dict)
    raw_response: str = ""
    used_fallback: bool = False
    error: str = ""


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
                if line.startswith("\ No newline"):
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
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
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
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            return {
                "command": command,
                "cwd": str(workdir),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "duration_seconds": round(time.time() - started, 3),
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "command": command,
                "cwd": str(workdir),
                "returncode": None,
                "stdout": exc.stdout or "",
                "stderr": (exc.stderr or "") + f"\nCommand timed out after {self.timeout_seconds}s",
                "duration_seconds": round(time.time() - started, 3),
                "timeout": True,
            }


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
        path = self.boundary.ensure_inside(self.settings.state_file())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
            allowed_tools=("apply_patch", "note", "read"),
        ),
        AutoResearchWorkflowStep(
            name="run_experiment_if_available",
            action_type="run",
            rationale="experiment_result_trial",
            command="if [ -f train/train.sh ]; then bash train/train.sh; elif [ -f eval.sh ]; then bash eval.sh; else echo 'No train/train.sh or eval.sh found; trial unavailable.'; fi",
            allowed_tools=("run",),
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


class AutoResearchStepAgent:
    STEP_GUIDANCE = {
        "inspect_project": "Build concise project understanding: structure, likely entrypoints, existing eval/train files, and risks.",
        "read_program": "Extract research goal, success metric, allowed edits, fixed eval harness, budget, and stop conditions from program.md.",
        "plan_change": "Propose one reversible experiment hypothesis only; specify target files, expected metric direction, risk, rollback.",
        "baseline_eval": "Run or prepare baseline evaluation; focus on machine-parseable metrics and failure diagnosis.",
        "summarize_baseline": "Summarize baseline evidence and whether metrics are sufficient for comparison.",
        "propose_experiment": "Produce a single minimal modification plan; do not combine unrelated ideas.",
        "apply_change": "If and only if there is a safe minimal patch, emit apply_patch with a unified diff. Otherwise emit note explaining why no patch is safe yet.",
        "run_experiment_if_available": "Run the configured experiment/eval command; prefer bounded commands and preserve logs.",
        "parse_metric_and_decide": "Parse metrics, compare against baseline if present, and decide keep/discard/needs_metrics.",
        "record_decision": "Record final decision, completed parts, artifacts, next steps, and what would be committed.",
    }

    """One bounded LLM child agent for a single fixed autoresearch step.

    It receives only the step definition, allowed action surface, and the bounded
    parent context assembled from modular buckets.  It must return structured
    JSON.  The parent loop still validates and executes the selected action.
    """

    def __init__(self, settings: AutoResearchSettings, client=None, model: str | None = None):
        self.settings = settings
        self.client = client
        self.model = model or settings.llm_model

    def _client(self):
        if self.client is None:
            from core import config

            self.client = config.create_llm_client()
        return self.client

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
        response = self._client().chat.completions.create(
            model=self.model or __import__("core.config", fromlist=["get_model"]).get_model(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            temperature=self.settings.llm_temperature,
        )
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
        return AutoResearchStepResult(action=action, bucket_updates=normalized, raw_response=raw)


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
        lines = [
            f"# auto_research Progress — {self.settings.project_id}",
            "",
            f"Updated: {time.strftime('%F %T')}",
            f"Status: **{status}**",
            f"Current step: `{current_step}`",
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
        self.planner = planner or FixedAutoResearchPlanner()
        self.summarizer = summarizer or self.default_summarizer
        self.step_agent = step_agent or (AutoResearchStepAgent(settings) if settings.use_llm_step_agents else None)
        self._observations: list[AutoResearchObservation] = []
        self._step_agent_errors: list[str] = []
        self.progress = AutoResearchProgressView(settings)

    def run(self, rounds: Optional[int] = None) -> dict:
        max_rounds = max(0, int(rounds if rounds is not None else self.settings.max_rounds))
        self._write_progress("running", "starting", 0, max_rounds)
        for round_index in range(max_rounds):
            step = getattr(self.planner, "step_for_round", lambda _i: None)(round_index)
            step_name = getattr(step, "name", f"round_{round_index}")
            self._write_progress("running", step_name, round_index, max_rounds)
            parent_context = self.context.build_parent_context(self._observations)
            step_result = self._plan_step(parent_context, round_index)
            action = step_result.action
            self._validate_step_tool_scope(action, round_index)
            self._apply_bucket_updates(step_result.bucket_updates)
            observation = self.execute_action(action)
            self._observations.append(observation)
            self._persist_observation(observation)
            self._write_progress("running", step_name, round_index + 1, max_rounds)
            if action.type == "stop":
                break
        self._write_progress("completed", "done", max_rounds, max_rounds)
        return {
            "project_id": self.settings.project_id,
            "rounds_completed": len(self._observations),
            "observations": [obs.compact(max_chars=1200) for obs in self._observations],
            "state_path": str(self.settings.state_file()),
            "artifact_dir": str(self.settings.artifacts_root()),
            "progress_path": str(self.settings.progress_file()),
            "step_agent_errors": list(self._step_agent_errors),
        }

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
                old = path.read_text(encoding="utf-8") if path.exists() else ""
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(action.content, encoding="utf-8")
                raw = json.dumps({"path": str(path), "old_chars": len(old), "new_chars": len(action.content)}, ensure_ascii=False)
                artifact = self.artifacts.save(kind="write", rationale=action.rationale, content=raw, extension="json")
                return AutoResearchObservation("write", self.summarizer(action, raw), artifact, "ok")
            if action.type == "apply_patch":
                result = apply_unified_patch_limited(self.settings.root(), action.patch or action.content)
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

    def _record_metric(self, action: AutoResearchAction, raw: str, artifact_path: str, status: str) -> None:
        info = parse_primary_metric(raw)
        metric = info.get("metric")
        if metric is None:
            return
        state = self.context.load_state()
        baseline = state.get("baseline_metric")
        is_baseline = "baseline" in (action.rationale or "").lower()
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
    "AutoResearchSafetyError",
    "AutoResearchSettings",
    "ProjectBoundary",
    "ProjectConfinedCommandRunner",
]
