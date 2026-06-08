# memory(action=add): scan, lock, reload, char-limit, persist

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/memory_tool.py#L297-L347

Local clone source path: `tools/memory_tool.py` (temporary clone; cleaned after research)

```python
0297:     def add(self, target: str, content: str) -> Dict[str, Any]:
0298:         """Append a new entry. Returns error if it would exceed the char limit."""
0299:         content = content.strip()
0300:         if not content:
0301:             return {"success": False, "error": "Content cannot be empty."}
0302: 
0303:         # Scan for injection/exfiltration before accepting
0304:         scan_error = _scan_memory_content(content)
0305:         if scan_error:
0306:             return {"success": False, "error": scan_error}
0307: 
0308:         with self._file_lock(self._path_for(target)):
0309:             # Re-read from disk under lock to pick up writes from other sessions.
0310:             # If external drift was detected, the file was backed up to .bak.<ts>
0311:             # — refuse the mutation so we don't clobber the un-roundtrippable
0312:             # content the patch tool / shell append / sister session wrote.
0313:             bak = self._reload_target(target)
0314:             if bak:
0315:                 return _drift_error(self._path_for(target), bak)
0316: 
0317:             entries = self._entries_for(target)
0318:             limit = self._char_limit(target)
0319: 
0320:             # Reject exact duplicates
0321:             if content in entries:
0322:                 return self._success_response(target, "Entry already exists (no duplicate added).")
0323: 
0324:             # Calculate what the new total would be
0325:             new_entries = entries + [content]
0326:             new_total = len(ENTRY_DELIMITER.join(new_entries))
0327: 
0328:             if new_total > limit:
0329:                 current = self._char_count(target)
0330:                 return {
0331:                     "success": False,
0332:                     "error": (
0333:                         f"Memory at {current:,}/{limit:,} chars. "
0334:                         f"Adding this entry ({len(content)} chars) would exceed the limit. "
0335:                         f"Consolidate now: use 'replace' to merge overlapping entries into "
0336:                         f"shorter ones or 'remove' stale or less important entries (see "
0337:                         f"current_entries below), then retry this add — all in this turn."
0338:                     ),
0339:                     "current_entries": entries,
0340:                     "usage": f"{current:,}/{limit:,}",
0341:                 }
0342: 
0343:             entries.append(content)
0344:             self._set_entries(target, entries)
0345:             self.save_to_disk(target)
0346: 
0347:         return self._success_response(target, "Entry added.")
```
