# Official docs: MEMORY.md/USER.md and frozen snapshot

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/website/docs/user-guide/features/memory.md#L13-L48

Local clone source path: `website/docs/user-guide/features/memory.md` (temporary clone; cleaned after research)

```markdown
0013: Two files make up the agent's memory:
0014: 
0015: | File | Purpose | Char Limit |
0016: |------|---------|------------|
0017: | **MEMORY.md** | Agent's personal notes — environment facts, conventions, things learned | 2,200 chars (~800 tokens) |
0018: | **USER.md** | User profile — your preferences, communication style, expectations | 1,375 chars (~500 tokens) |
0019: 
0020: Both are stored in `~/.hermes/memories/` and are injected into the system prompt as a frozen snapshot at session start. The agent manages its own memory via the `memory` tool — it can add, replace, or remove entries.
0021: 
0022: :::info
0023: Character limits keep memory focused. When memory is full, the agent consolidates or replaces entries to make room for new information.
0024: :::
0025: 
0026: ## How Memory Appears in the System Prompt
0027: 
0028: At the start of every session, memory entries are loaded from disk and rendered into the system prompt as a frozen block:
0029: 
0030: ```
0031: ══════════════════════════════════════════════
0032: MEMORY (your personal notes) [67% — 1,474/2,200 chars]
0033: ══════════════════════════════════════════════
0034: User's project is a Rust web service at ~/code/myapi using Axum + SQLx
0035: §
0036: This machine runs Ubuntu 22.04, has Docker and Podman installed
0037: §
0038: User prefers concise responses, dislikes verbose explanations
0039: ```
0040: 
0041: The format includes:
0042: - A header showing which store (MEMORY or USER PROFILE)
0043: - Usage percentage and character counts so the agent knows capacity
0044: - Individual entries separated by `§` (section sign) delimiters
0045: - Entries can be multiline
0046: 
0047: **Frozen snapshot pattern:** The system prompt injection is captured once at session start and never changes mid-session. This is intentional — it preserves the LLM's prefix cache for performance. When the agent adds/removes memory entries during a session, the changes are persisted to disk immediately but won't appear in the system prompt until the next session starts. Tool responses always show the live state.
0048: 
```
