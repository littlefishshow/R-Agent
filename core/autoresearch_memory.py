"""AutoResearch v2 — layered memory helpers (L0 constitution / L1 belief / L2 project / L3 .auto).

Implements the file contracts from AUTORESEARCH_DESIGN_v2.md §1:

- ``program.md`` is split into an immutable CONSTITUTION (L0, user-only) and a
  mutable BELIEF (L1, loop-editable) section via HTML comment markers. When the
  markers are absent the whole file is treated as read-only constitution.
- ``project.md`` (L2) is the human-readable project state, single-writer =
  parent. It carries the ``phase`` / ``phase_reason`` used by the state machine.
- ``.auto/`` (L3) holds per-directory implementation detail owned by sub-agents,
  with a size-bounded GC helper so it cannot grow without bound.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CONSTITUTION_OPEN = "<!-- CONSTITUTION -->"
CONSTITUTION_CLOSE = "<!-- /CONSTITUTION -->"
BELIEF_OPEN = "<!-- BELIEF -->"
BELIEF_CLOSE = "<!-- /BELIEF -->"


# ---------------------------------------------------------------------------
# program.md — L0 constitution / L1 belief
# ---------------------------------------------------------------------------


@dataclass
class ProgramSections:
    constitution: str
    belief: str
    has_markers: bool

    def render(self) -> str:
        if not self.has_markers:
            # No markers: preserve the original as a pure constitution.
            return self.constitution.rstrip() + "\n"
        return (
            f"{CONSTITUTION_OPEN}\n{self.constitution.strip()}\n{CONSTITUTION_CLOSE}\n\n"
            f"{BELIEF_OPEN}\n{self.belief.strip()}\n{BELIEF_CLOSE}\n"
        )


def _extract_between(text: str, open_tag: str, close_tag: str) -> Optional[str]:
    pattern = re.escape(open_tag) + r"(.*?)" + re.escape(close_tag)
    m = re.search(pattern, text, flags=re.DOTALL)
    return m.group(1).strip() if m else None


def split_program(text: str) -> ProgramSections:
    """Split program.md into constitution (L0) and belief (L1).

    If markers are missing, the entire text becomes the constitution and belief
    is empty; callers must treat such a program as fully read-only.
    """
    text = text or ""
    constitution = _extract_between(text, CONSTITUTION_OPEN, CONSTITUTION_CLOSE)
    belief = _extract_between(text, BELIEF_OPEN, BELIEF_CLOSE)
    if constitution is None and belief is None:
        return ProgramSections(constitution=text.strip(), belief="", has_markers=False)
    return ProgramSections(
        constitution=(constitution or "").strip(),
        belief=(belief or "").strip(),
        has_markers=True,
    )


def update_belief(text: str, new_belief: str) -> str:
    """Return program.md with only the L1 belief section replaced.

    Raises ValueError if the program has no belief markers (i.e. is read-only),
    so the loop can never silently overwrite a user's constitution.
    """
    sections = split_program(text)
    if not sections.has_markers:
        raise ValueError("program.md has no BELIEF markers; it is read-only (constitution-only).")
    sections.belief = (new_belief or "").strip()
    return sections.render()


def ensure_program_scaffold(text: str) -> str:
    """Ensure program.md has L0/L1 markers, migrating a legacy flat file in place.

    A legacy program (no markers) keeps all its content as constitution and gets
    an empty belief section appended, so future belief updates are possible
    without touching the user's original text.
    """
    sections = split_program(text)
    if sections.has_markers:
        return text
    return ProgramSections(constitution=sections.constitution, belief="", has_markers=True).render()


# ---------------------------------------------------------------------------
# project.md — L2 project state + phase
# ---------------------------------------------------------------------------


PHASES = ("init", "plan", "execute", "run", "evaluate", "compress", "pause")
DEFAULT_PROJECT_TEMPLATE = """# Project State

## 梗概
(project overview not yet generated)

## 当前计划
(no plan yet)

## 改动记录
(none yet)

## 短期结论
(none yet)

## 经验账本索引
(see .autoresearch/lessons.jsonl)

<!-- PHASE: init -->
<!-- PHASE_REASON: project not started -->
"""

_PHASE_RE = re.compile(r"<!--\s*PHASE:\s*([a-zA-Z_]+)\s*-->")
_PHASE_REASON_RE = re.compile(r"<!--\s*PHASE_REASON:\s*(.*?)\s*-->")


def normalize_phase(phase: str) -> str:
    value = (phase or "").strip().lower()
    return value if value in PHASES else "init"


def read_phase(project_text: str) -> tuple[str, str]:
    m = _PHASE_RE.search(project_text or "")
    phase = normalize_phase(m.group(1)) if m else "init"
    rm = _PHASE_REASON_RE.search(project_text or "")
    reason = rm.group(1).strip() if rm else ""
    return phase, reason


def write_phase(project_text: str, phase: str, reason: str = "") -> str:
    """Return project.md text with PHASE / PHASE_REASON markers set (idempotent)."""
    phase = normalize_phase(phase)
    reason = (reason or "").strip()
    text = project_text or DEFAULT_PROJECT_TEMPLATE
    if _PHASE_RE.search(text):
        text = _PHASE_RE.sub(f"<!-- PHASE: {phase} -->", text, count=1)
    else:
        text = text.rstrip() + f"\n\n<!-- PHASE: {phase} -->\n"
    if _PHASE_REASON_RE.search(text):
        text = _PHASE_REASON_RE.sub(f"<!-- PHASE_REASON: {reason} -->", text, count=1)
    else:
        text = text.rstrip() + f"\n<!-- PHASE_REASON: {reason} -->\n"
    return text


# ---------------------------------------------------------------------------
# .auto/ — L3 sub-agent detail with GC
# ---------------------------------------------------------------------------


def auto_dir(root: str | Path) -> Path:
    return Path(root) / ".auto"


def write_auto_note(root: str | Path, name: str, content: str) -> Path:
    d = auto_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "note"
    if not safe.endswith(".md"):
        safe += ".md"
    path = d / safe
    path.write_text(content or "", encoding="utf-8")
    return path


def read_auto_notes(root: str | Path, *, max_files: int = 20, max_chars_per_file: int = 4000) -> dict:
    d = auto_dir(root)
    notes: dict[str, str] = {}
    if not d.exists():
        return notes
    for path in sorted(d.glob("*.md"))[:max_files]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(text) > max_chars_per_file:
            text = text[: max_chars_per_file - 3].rstrip() + "..."
        notes[path.name] = text
    return notes


def gc_auto_dir(root: str | Path, *, max_files: int = 20, max_total_chars: int = 60_000) -> dict:
    """Bound .auto/ growth: drop oldest notes past file-count / total-size caps.

    Returns a small report of what was removed.  Never touches non-.md files.
    """
    d = auto_dir(root)
    if not d.exists():
        return {"removed": [], "kept": 0}
    files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime)
    removed: list[str] = []
    # File-count cap: remove oldest beyond the cap.
    while len(files) > max(1, max_files):
        victim = files.pop(0)
        removed.append(victim.name)
        victim.unlink(missing_ok=True)
    # Total-size cap: keep newest until under budget.
    def total_size(paths: list[Path]) -> int:
        return sum(p.stat().st_size for p in paths if p.exists())

    while files and total_size(files) > max(1000, max_total_chars):
        victim = files.pop(0)
        removed.append(victim.name)
        victim.unlink(missing_ok=True)
    return {"removed": removed, "kept": len(files)}


# ---------------------------------------------------------------------------
# lessons.jsonl — experience ledger that survives git rollback
# ---------------------------------------------------------------------------


def lessons_path(root: str | Path) -> Path:
    return Path(root) / ".autoresearch" / "lessons.jsonl"


def append_lesson(root: str | Path, *, kind: str, summary: str, detail: str = "", experiment_id: str = "") -> Path:
    """Append one lesson (directional/operational insight) to lessons.jsonl.

    This file is intentionally gitignored so that a ``git rollback`` of a failed
    trial does NOT erase the lesson learned from that failure.  ``kind`` is a
    free label such as ``directional_error`` / ``operational_error`` /
    ``insight`` / ``dead_end``.
    """
    path = lessons_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": time.strftime("%F %T"),
        "created_at": time.time(),
        "kind": str(kind or "insight"),
        "experiment_id": str(experiment_id or ""),
        "summary": str(summary or "")[:2000],
        "detail": str(detail or "")[:8000],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_lessons(root: str | Path, *, limit: int = 50) -> list[dict]:
    path = lessons_path(root)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows[-max(1, int(limit)):]


__all__ = [
    "ProgramSections",
    "split_program",
    "update_belief",
    "ensure_program_scaffold",
    "read_phase",
    "write_phase",
    "normalize_phase",
    "PHASES",
    "DEFAULT_PROJECT_TEMPLATE",
    "auto_dir",
    "write_auto_note",
    "read_auto_notes",
    "gc_auto_dir",
    "append_lesson",
    "read_lessons",
    "CONSTITUTION_OPEN",
    "CONSTITUTION_CLOSE",
    "BELIEF_OPEN",
    "BELIEF_CLOSE",
]
