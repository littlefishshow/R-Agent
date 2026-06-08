# Built-in file memory overview and frozen snapshot contract

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/memory_tool.py#L1-L24

Local clone source path: `tools/memory_tool.py` (temporary clone; cleaned after research)

```python
0001: #!/usr/bin/env python3
0002: """
0003: Memory Tool Module - Persistent Curated Memory
0004: 
0005: Provides bounded, file-backed memory that persists across sessions. Two stores:
0006:   - MEMORY.md: agent's personal notes and observations (environment facts, project
0007:     conventions, tool quirks, things learned)
0008:   - USER.md: what the agent knows about the user (preferences, communication style,
0009:     expectations, workflow habits)
0010: 
0011: Both are injected into the system prompt as a frozen snapshot at session start.
0012: Mid-session writes update files on disk immediately (durable) but do NOT change
0013: the system prompt -- this preserves the prefix cache for the entire session.
0014: The snapshot refreshes on the next session start.
0015: 
0016: Entry delimiter: § (section sign). Entries can be multiline.
0017: Character limits (not tokens) because char counts are model-independent.
0018: 
0019: Design:
0020: - Single `memory` tool with action parameter: add, replace, remove, read
0021: - replace/remove use short unique substring matching (not full text or IDs)
0022: - Behavioral guidance lives in the tool schema description
0023: - Frozen snapshot pattern: system prompt is stable, tool responses show live state
0024: """
```
