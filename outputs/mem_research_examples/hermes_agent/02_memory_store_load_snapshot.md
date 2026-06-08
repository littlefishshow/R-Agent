# MemoryStore loads MEMORY.md/USER.md and captures sanitized snapshot

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/memory_tool.py#L113-L170

Local clone source path: `tools/memory_tool.py` (temporary clone; cleaned after research)

```python
0113: class MemoryStore:
0114:     """
0115:     Bounded curated memory with file persistence. One instance per AIAgent.
0116: 
0117:     Maintains two parallel states:
0118:       - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
0119:         Never mutated mid-session. Keeps prefix cache stable.
0120:       - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
0121:         Tool responses always reflect this live state.
0122:     """
0123: 
0124:     def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
0125:         self.memory_entries: List[str] = []
0126:         self.user_entries: List[str] = []
0127:         self.memory_char_limit = memory_char_limit
0128:         self.user_char_limit = user_char_limit
0129:         # Frozen snapshot for system prompt -- set once at load_from_disk()
0130:         self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}
0131: 
0132:     def load_from_disk(self):
0133:         """Load entries from MEMORY.md and USER.md, capture system prompt snapshot.
0134: 
0135:         The frozen snapshot is what enters the system prompt. We scan each
0136:         entry for injection/promptware patterns at snapshot-build time —
0137:         ANY hit replaces the entry text in the snapshot with a placeholder
0138:         like ``[BLOCKED: …]``, so a poisoned-on-disk memory file (supply
0139:         chain, compromised tool, sister-session write) cannot inject into
0140:         the system prompt.
0141: 
0142:         The live ``memory_entries`` / ``user_entries`` lists keep the
0143:         original text so the user can still SEE poisoned entries via
0144:         ``memory(action=read)`` and remove them — silently dropping them
0145:         would hide the attack from the user.
0146: 
0147:         Scanning is deterministic from disk bytes, so the snapshot remains
0148:         stable for the entire session (prefix-cache invariant holds).
0149:         """
0150:         mem_dir = get_memory_dir()
0151:         mem_dir.mkdir(parents=True, exist_ok=True)
0152: 
0153:         self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
0154:         self.user_entries = self._read_file(mem_dir / "USER.md")
0155: 
0156:         # Deduplicate entries (preserves order, keeps first occurrence)
0157:         self.memory_entries = list(dict.fromkeys(self.memory_entries))
0158:         self.user_entries = list(dict.fromkeys(self.user_entries))
0159: 
0160:         # Sanitize entries for the system-prompt snapshot only.  Live state
0161:         # (memory_entries / user_entries) keeps the raw text so the user
0162:         # can see + remove poisoned entries via the memory tool.
0163:         sanitized_memory = self._sanitize_entries_for_snapshot(self.memory_entries, "MEMORY.md")
0164:         sanitized_user = self._sanitize_entries_for_snapshot(self.user_entries, "USER.md")
0165: 
0166:         # Capture frozen snapshot for system prompt injection
0167:         self._system_prompt_snapshot = {
0168:             "memory": self._render_block("memory", sanitized_memory),
0169:             "user": self._render_block("user", sanitized_user),
0170:         }
```
