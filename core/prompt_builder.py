import os
import re
from datetime import datetime
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
    "state to memory; use memory_search to recall prior durable memory or current-session "
    "episodic details when the answer depends on history not visible in the current context. "
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
    "Use skills as reusable workflow assets, but keep the skill set compact. "
    "Prefer patching an existing relevant skill with skill_manage(action='patch') when "
    "you discover a stable improvement. Create a new skill only when the user asks for "
    "it or when a broadly reusable workflow cannot fit any existing skill; do not create "
    "a new skill after every complex task by default.\n"
    "When using a skill and finding it outdated, incomplete, or wrong, patch it rather "
    "than creating a duplicate. Skills that aren't maintained become liabilities."
)

DELEGATED_TODO_GUIDANCE = (
    "# Delegated todo context policy\n"
    "When the user presents a problem or task that benefits from tools, tracking, "
    "or verification, initialize/use todo_manage and delegate executable leaf tasks "
    "with delegate_task instead of keeping all work in the parent context. The parent "
    "is the scheduler only: it should create tasks, inspect todo_manage digest/ready/"
    "status, approve/reject splits, and synthesize from the todo digest. It must not "
    "pull full child-agent transcripts by default. Child agents should receive only "
    "task-relevant context, write progress/errors/split proposals into todo_manage, "
    "and avoid leaking their full internal context back to the parent. If a child fails, "
    "times out, or leaves its task incomplete, its context may be saved as a bounded "
    "artifact referenced from todo metadata; the parent may explicitly inspect that "
    "artifact only when needed. Child context artifacts should be retained until the "
    "entire todo tree succeeds, then cleaned together; before that, even successful "
    "leaf-task context may be needed for debugging or sibling/parent synthesis. The "
    "parent should still retain only the todo digest and its own user-facing conversation "
    "context unless it explicitly chooses to inspect an artifact.\n"
    "For trivial conversational replies that require no tools or state, answer directly."
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


def build_runtime_context_block() -> str:
    """构建动态运行时上下文块（当前只含日期）。

    对齐 deer-flow 的 DynamicContextMiddleware（见学习文档第 6.1 节）：当前日期
    属于**框架权限**信息，放在 system 层。它每次构建 system prompt 时刷新，弥补
    R-Agent 此前"模型不知道今天几号"的缺口。

    时区默认 Asia/Shanghai，可用环境变量 R_AGENT_TIMEZONE 覆盖；zoneinfo 不可用
    时退回本地时间，绝不因此报错。
    """
    tz_name = os.environ.get("R_AGENT_TIMEZONE", "Asia/Shanghai")
    now = None
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz_name))
    except Exception:
        try:
            now = datetime.now()
            tz_name = "local"
        except Exception:
            return ""
    weekday_cn = "一二三四五六日"[now.weekday()]
    return (
        "# Runtime context\n"
        f"Current date: {now.strftime('%Y-%m-%d')} (星期{weekday_cn}), timezone {tz_name}.\n"
        "This is framework-provided ground truth. Use it for any relative-date reasoning "
        "(\"today\", \"this week\", \"latest\"). For exact current time, still run a tool."
    )


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

    # 1.5 Runtime context: 当前日期（框架权限，每次构建刷新）。
    runtime_block = build_runtime_context_block()
    if runtime_block:
        parts.append(runtime_block)

    # 2. General Tool Use Enforcement
    parts.append(TOOL_USE_ENFORCEMENT_GUIDANCE)

    # 3. Model Execution Discipline
    parts.append(MODEL_EXECUTION_GUIDANCE)

    # 4. Memory Guidance (if memory tools are available, assume they might be)
    parts.append(MEMORY_GUIDANCE)

    # 5. Skills Guidance
    parts.append(SKILLS_GUIDANCE)

    # 6. Delegated todo policy
    parts.append(DELEGATED_TODO_GUIDANCE)

    return "\n\n".join(parts)
