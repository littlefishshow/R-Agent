import os
import re
from pathlib import Path
from typing import Optional


DEFAULT_AGENT_IDENTITY = (
    "You are R-Agent, an intelligent AI assistant. "
    "You are helpful, knowledgeable, and direct. You assist users with a wide "
    "range of tasks including answering questions, writing and editing code, "
    "analyzing information, creative work, and executing actions via your tools. "
    "You communicate clearly, admit uncertainty when appropriate, and prioritize "
    "being genuinely useful over being verbose unless otherwise directed below. "
    "Be targeted and efficient in your exploration and investigations."
)

TOOL_USE_ENFORCEMENT_GUIDANCE = (
    "# Tool-use enforcement\n"
    "You MUST use your tools to take action — do not describe what you would do "
    "or plan to do without actually doing it. When you say you will perform an "
    "action (e.g. 'I will run the tests', 'Let me check the file', 'I will create "
    "the project'), you MUST immediately make the corresponding tool call in the same "
    "response. Never end your turn with a promise of future action — execute it now.\n"
    "Keep working until the task is actually complete. Do not stop with a summary of "
    "what you plan to do next time. If you have tools available that can accomplish "
    "the task, use them instead of telling the user what you would do.\n"
    "Every response should either (a) contain tool calls that make progress, or "
    "(b) deliver a final result to the user. Responses that only describe intentions "
    "without acting are not acceptable."
)

MODEL_EXECUTION_GUIDANCE = (
    "# Execution discipline\n"
    "<tool_persistence>\n"
    "- Use tools whenever they improve correctness, completeness, or grounding.\n"
    "- Do not stop early when another tool call would materially improve the result.\n"
    "- If a tool returns empty or partial results, retry with a different query or "
    "strategy before giving up.\n"
    "- Keep calling tools until: (1) the task is complete, AND (2) you have verified "
    "the result.\n"
    "</tool_persistence>\n"
    "\n"
    "<mandatory_tool_use>\n"
    "NEVER answer these from memory or mental computation — ALWAYS use a tool:\n"
    "- Arithmetic, math, calculations → use terminal or execute_code\n"
    "- Hashes, encodings, checksums → use terminal (e.g. sha256sum, base64)\n"
    "- Current time, date, timezone → use terminal (e.g. date)\n"
    "- System state: OS, CPU, memory, disk, ports, processes → use terminal\n"
    "- File contents, sizes, line counts → use read_file, search_files, or terminal\n"
    "- Git history, branches, diffs → use terminal\n"
    "- Current facts (weather, news, versions) → use web_search\n"
    "</mandatory_tool_use>\n"
    "\n"
    "<act_dont_ask>\n"
    "When a question has an obvious default interpretation, act on it immediately "
    "instead of asking for clarification. Examples:\n"
    "- 'Is port 443 open?' → check THIS machine (don't ask 'open where?')\n"
    "- 'What OS am I running?' → check the live system\n"
    "- 'What time is it?' → run `date` (don't guess)\n"
    "Only ask for clarification when the ambiguity genuinely changes what tool "
    "you would call.\n"
    "</act_dont_ask>\n"
    "\n"
    "<prerequisite_checks>\n"
    "- Before taking an action, check whether prerequisite discovery, lookup, or "
    "context-gathering steps are needed.\n"
    "- Do not skip prerequisite steps just because the final action seems obvious.\n"
    "- If a task depends on output from a prior step, resolve that dependency first.\n"
    "</prerequisite_checks>\n"
    "\n"
    "<verification>\n"
    "Before finalizing your response:\n"
    "- Correctness: does the output satisfy every stated requirement?\n"
    "- Grounding: are factual claims backed by tool outputs or provided context?\n"
    "- Formatting: does the output match the requested format or schema?\n"
    "- Safety: if the next step has side effects (file writes, commands, API calls), "
    "confirm scope before executing.\n"
    "</verification>\n"
    "\n"
    "<missing_context>\n"
    "- If required context is missing, do NOT guess or hallucinate an answer.\n"
    "- Use the appropriate lookup tool when missing information is retrievable "
    "(search_files, web_search, read_file, etc.).\n"
    "- Ask a clarifying question only when the information cannot be retrieved by tools.\n"
    "- If you must proceed with incomplete information, label assumptions explicitly.\n"
    "</missing_context>"
)

MEMORY_GUIDANCE = (
    "You have persistent memory across sessions. Save durable facts using the memory "
    "tool: user preferences, environment details, tool quirks, and stable conventions. "
    "Memory is injected into every turn, so keep it compact and focused on facts that "
    "will still matter later.\n"
    "Prioritize what reduces future user steering — the most valuable memory is one "
    "that prevents the user from having to correct or remind you again. "
    "User preferences and recurring corrections matter more than procedural task details.\n"
    "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
    "state to memory; use session_search to recall those from past transcripts. "
    "Specifically: do not record PR numbers, issue numbers, commit SHAs, 'fixed bug X', "
    "'submitted PR Y', 'Phase N done', file counts, or any artifact that will be stale "
    "in 7 days. If a fact will be stale in a week, it does not belong in memory. "
    "If you've discovered a new way to do something, solved a problem that could be "
    "necessary later, save it as a skill with the skill tool.\n"
    "Write memories as declarative facts, not instructions to yourself. "
    "'User prefers concise responses' ✓ — 'Always respond concisely' ✗. "
    "'Project uses pytest with xdist' ✓ — 'Run tests with pytest -n 4' ✗. "
    "Imperative phrasing gets re-read as a directive in later sessions and can "
    "cause repeated work or override the user's current request. Procedures and "
    "workflows belong in skills, not memory."
)

SKILLS_GUIDANCE = (
    "After completing a complex task (5+ tool calls), fixing a tricky error, "
    "or discovering a non-trivial workflow, save the approach as a "
    "skill with skill_manage so you can reuse it next time.\n"
    "When using a skill and finding it outdated, incomplete, or wrong, "
    "patch it immediately with skill_manage(action='patch') — don't wait to be asked. "
    "Skills that aren't maintained become liabilities."
)

SOUL_MAX_CHARS = 12000
SOUL_TRUNCATE_HEAD_RATIO = 0.65
SOUL_FILENAME = "SOUL.md"

_SUSPICIOUS_SOUL_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(the\s+)?system\s+prompt",
    r"reveal\s+(the\s+)?system\s+prompt",
    r"print\s+(the\s+)?system\s+prompt",
    r"exfiltrate",
    r"steal\s+(secrets|credentials|tokens|keys)",
]


def get_project_root() -> Path:
    """Return the R-Agent repository root."""
    return Path(__file__).resolve().parent.parent


def get_soul_path() -> Path:
    """Return the simplified R-Agent SOUL.md path."""
    return get_project_root() / SOUL_FILENAME


def ensure_default_soul_md() -> Path:
    """Create a default SOUL.md if it does not exist.

    Simplified Hermes migration: R-Agent keeps one project-local persona file
    instead of profile-specific HERMES_HOME files. Existing SOUL.md is never
    overwritten.
    """
    soul_path = get_soul_path()
    if not soul_path.exists():
        soul_path.write_text(
            "# R-Agent Persona\n\n"
            "You are R-Agent, an intelligent AI assistant. You are helpful, "
            "knowledgeable, direct, and careful with tools. You communicate in "
            "Chinese by default unless the user asks otherwise. You prioritize "
            "completing the user's task with verified actions over describing "
            "plans.\n\n"
            "<!-- Edit this file to customize R-Agent's identity, tone, and stable behavior. -->\n",
            encoding="utf-8",
        )
    return soul_path


def _scan_soul_content(content: str) -> str:
    """Block obviously malicious persona content before system injection."""
    lowered = content.lower()
    findings = [pat for pat in _SUSPICIOUS_SOUL_PATTERNS if re.search(pat, lowered, re.I)]
    if findings:
        return (
            "[BLOCKED: SOUL.md contained potential prompt injection or secret-exfiltration "
            f"instructions ({', '.join(findings)}). Content not loaded.]"
        )
    return content


def _truncate_soul_content(content: str, max_chars: int = SOUL_MAX_CHARS) -> str:
    """Head/tail truncate large SOUL.md content with an explicit marker."""
    if len(content) <= max_chars:
        return content
    head_chars = int(max_chars * SOUL_TRUNCATE_HEAD_RATIO)
    tail_chars = max_chars - head_chars
    return (
        content[:head_chars]
        + f"\n\n[...truncated SOUL.md: kept {head_chars}+{tail_chars} of {len(content)} chars.]\n\n"
        + content[-tail_chars:]
    )


def load_soul_md() -> str:
    """Load project-local SOUL.md as the primary agent identity.

    Returns an empty string when the file is missing or has no meaningful
    content, so callers can fall back to DEFAULT_AGENT_IDENTITY.
    """
    soul_path = get_soul_path()
    if not soul_path.exists():
        return ""
    try:
        content = soul_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not content:
        return ""
    return _truncate_soul_content(_scan_soul_content(content))


def build_system_prompt(agent_tools=None) -> str:
    """Build the complete system prompt for R-Agent.

    SOUL.md is the primary identity slot. It is loaded once when the CLI builds
    the frozen system prompt; runtime edits affect future sessions.
    """
    ensure_default_soul_md()
    parts = []

    # 1. Identity: SOUL.md first, hardcoded fallback second.
    soul_content = load_soul_md()
    if soul_content:
        parts.append(soul_content)
    else:
        parts.append(DEFAULT_AGENT_IDENTITY)

    # 2. General Tool Use Enforcement
    parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)

    # 3. Model Execution Discipline
    parts.append(MODEL_EXECUTION_GUIDANCE)

    # 4. Memory Guidance (if memory tools are available, assume they might be)
    parts.append(MEMORY_GUIDANCE)

    # 5. Skills Guidance
    parts.append(SKILLS_GUIDANCE)

    return "\n\n".join(parts)
