"""AutoResearch v2 — deterministic phase handlers (no-LLM baseline).

These give the phase machine real behavior without requiring a model, so the
whole loop is testable and has a safe fallback.  LLM-backed handlers (personas,
delegate execution) are layered on top in later phases and simply replace the
entries in the handler map.

Design ref: AUTORESEARCH_DESIGN_v2.md §5.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.autoresearch_memory import (
    split_program,
    update_belief,
    write_auto_note,
    gc_auto_dir,
    append_lesson,
)
from core.autoresearch_phases import PhaseContext, PhaseResult


# --------------------------------------------------------------------------- #
# P1 Init — cheap codebase survey (schema/head only, never full dataset)
# --------------------------------------------------------------------------- #

_SURVEY_MAX_FILES = 40
_SURVEY_HEAD_LINES = 12
_INTEREST_SUFFIXES = (".py", ".sh", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".txt")
_SKIP_DIRS = {".git", ".autoresearch", "__pycache__", "node_modules", ".auto", ".venv", "venv"}


def survey_project(root: Path) -> str:
    """Produce a bounded overview: file tree + head of key files (no full data)."""
    root = Path(root)
    lines: list[str] = ["# Project Overview (auto-generated survey)", ""]
    listed = 0
    tree: list[str] = []
    for path in sorted(root.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        tree.append(str(rel))
        listed += 1
        if listed >= 200:
            break
    lines.append("## File tree (truncated)")
    lines.extend(f"- {t}" for t in tree[:120])
    lines.append("")
    lines.append("## Key file heads")
    sampled = 0
    for rel in tree:
        p = root / rel
        if p.suffix.lower() not in _INTEREST_SUFFIXES:
            continue
        if sampled >= _SURVEY_MAX_FILES:
            break
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        head = "\n".join(text.splitlines()[:_SURVEY_HEAD_LINES])
        lines.append(f"### {rel}")
        lines.append("```")
        lines.append(head)
        lines.append("```")
        sampled += 1
    return "\n".join(lines) + "\n"


def make_init_handler():
    def handler(ctx: PhaseContext) -> PhaseResult:
        overview = survey_project(ctx.root)
        write_auto_note(ctx.root, "survey", overview)
        gc_auto_dir(ctx.root)
        # Seed project.md overview section (keep the rest of the template intact).
        project = ctx.project_text
        marker = "## 梗概"
        if marker in project:
            head, _, rest = project.partition(marker)
            # replace up to the next section header
            after = rest.split("\n## ", 1)
            body = f"{marker}\n项目已完成初始 survey，详见 .auto/survey.md。\n"
            if len(after) == 2:
                project = head + body + "\n## " + after[1]
            else:
                project = head + body
        return PhaseResult(project_text=project, summary="init: codebase survey written to .auto/survey.md")

    return handler


# --------------------------------------------------------------------------- #
# P5 Evaluate — reuse loop's Pareto/versioning + lessons ledger
# --------------------------------------------------------------------------- #

def make_evaluate_handler():
    """Deterministic evaluate: read machine state, record lesson, note conclusion.

    The heavy lifting (metrics parse, Pareto, git commit/rollback) already runs
    inside ``AutoResearchLoop._maybe_record_experiment`` during Run.  Here we
    read the resulting ``state.json`` to classify the outcome and persist a
    rollback-surviving lesson.
    """

    def handler(ctx: PhaseContext) -> PhaseResult:
        root = Path(ctx.root)
        state_path = root / ".autoresearch" / "state.json"
        best = None
        pareto = []
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                best = state.get("best_experiment")
                pareto = state.get("pareto_front") or []
            except Exception:
                pass

        major = bool(ctx.signals.major_error)
        pareto_changed = bool(ctx.signals.pareto_changed)

        if major:
            append_lesson(
                root,
                kind="operational_error" if not ctx.signals.plan_still_valid else "directional_error",
                summary="major error during execute/run; jumped to evaluate",
                detail=ctx.signals.__dict__.get("summary", "") if isinstance(ctx.signals.__dict__, dict) else "",
            )
            summary = "evaluate: major error recorded to lessons.jsonl"
        elif best:
            append_lesson(
                root,
                kind="insight" if pareto_changed else "dead_end",
                summary=(f"best={best.get('experiment_id')} metrics={best.get('metrics')}" if pareto_changed
                         else "trial did not improve Pareto front"),
                experiment_id=str(best.get("experiment_id") or ""),
            )
            summary = f"evaluate: best={best.get('experiment_id')} pareto_changed={pareto_changed}"
        else:
            summary = "evaluate: no metric-bearing experiment yet"

        # Append a short conclusion into project.md.
        project = ctx.project_text
        conclusion_line = f"- {summary} (pareto_candidates={len(pareto)})"
        if "## 短期结论" in project:
            head, _, rest = project.partition("## 短期结论")
            after = rest.split("\n## ", 1)
            body = f"## 短期结论\n{conclusion_line}\n"
            if len(after) == 2:
                project = head + body + "\n## " + after[1]
            else:
                project = head + body
        return PhaseResult(project_text=project, summary=summary)

    return handler


# --------------------------------------------------------------------------- #
# P6 Compress — semantic-lite compression placeholder (deterministic)
# --------------------------------------------------------------------------- #

def make_compress_handler(max_belief_chars: int = 4000):
    """Deterministic compression: trim belief section if oversized, keep lessons.

    A real LLM compressor replaces this handler; the deterministic version only
    guards against unbounded growth of the belief section and never touches the
    lessons ledger.
    """

    def handler(ctx: PhaseContext) -> PhaseResult:
        sections = split_program(ctx.program_text)
        if not sections.has_markers or len(sections.belief) <= max_belief_chars:
            return PhaseResult(summary="compress: nothing to trim")
        trimmed = sections.belief[: max_belief_chars - 3].rstrip() + "..."
        new_program = update_belief(ctx.program_text, trimmed)
        return PhaseResult(program_text=new_program, summary=f"compress: belief trimmed to {max_belief_chars} chars")

    return handler


def default_handlers() -> dict:
    """Deterministic handler map covering the phases that have real no-LLM work."""
    return {
        "init": make_init_handler(),
        "evaluate": make_evaluate_handler(),
        "compress": make_compress_handler(),
    }


__all__ = [
    "survey_project",
    "make_init_handler",
    "make_evaluate_handler",
    "make_compress_handler",
    "default_handlers",
]
