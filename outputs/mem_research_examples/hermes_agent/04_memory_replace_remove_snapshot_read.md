# replace/remove via substring; format_for_system_prompt returns frozen snapshot

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/memory_tool.py#L349-L461

Local clone source path: `tools/memory_tool.py` (temporary clone; cleaned after research)

```python
0349:     def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
0350:         """Find entry containing old_text substring, replace it with new_content."""
0351:         old_text = old_text.strip()
0352:         new_content = new_content.strip()
0353:         if not old_text:
0354:             return {"success": False, "error": "old_text cannot be empty."}
0355:         if not new_content:
0356:             return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}
0357: 
0358:         # Scan replacement content for injection/exfiltration
0359:         scan_error = _scan_memory_content(new_content)
0360:         if scan_error:
0361:             return {"success": False, "error": scan_error}
0362: 
0363:         with self._file_lock(self._path_for(target)):
0364:             bak = self._reload_target(target)
0365:             if bak:
0366:                 return _drift_error(self._path_for(target), bak)
0367: 
0368:             entries = self._entries_for(target)
0369:             matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
0370: 
0371:             if not matches:
0372:                 return {"success": False, "error": f"No entry matched '{old_text}'."}
0373: 
0374:             if len(matches) > 1:
0375:                 # If all matches are identical (exact duplicates), operate on the first one
0376:                 unique_texts = {e for _, e in matches}
0377:                 if len(unique_texts) > 1:
0378:                     previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
0379:                     return {
0380:                         "success": False,
0381:                         "error": f"Multiple entries matched '{old_text}'. Be more specific.",
0382:                         "matches": previews,
0383:                     }
0384:                 # All identical -- safe to replace just the first
0385: 
0386:             idx = matches[0][0]
0387:             limit = self._char_limit(target)
0388: 
0389:             # Check that replacement doesn't blow the budget
0390:             test_entries = entries.copy()
0391:             test_entries[idx] = new_content
0392:             new_total = len(ENTRY_DELIMITER.join(test_entries))
0393: 
0394:             if new_total > limit:
0395:                 current = self._char_count(target)
0396:                 return {
0397:                     "success": False,
0398:                     "error": (
0399:                         f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
0400:                         f"Shorten the new content, or 'remove' other stale or less important "
0401:                         f"entries to make room (see current_entries below), then retry — all "
0402:                         f"in this turn."
0403:                     ),
0404:                     "current_entries": entries,
0405:                     "usage": f"{current:,}/{limit:,}",
0406:                 }
0407: 
0408:             entries[idx] = new_content
0409:             self._set_entries(target, entries)
0410:             self.save_to_disk(target)
0411: 
0412:         return self._success_response(target, "Entry replaced.")
0413: 
0414:     def remove(self, target: str, old_text: str) -> Dict[str, Any]:
0415:         """Remove the entry containing old_text substring."""
0416:         old_text = old_text.strip()
0417:         if not old_text:
0418:             return {"success": False, "error": "old_text cannot be empty."}
0419: 
0420:         with self._file_lock(self._path_for(target)):
0421:             bak = self._reload_target(target)
0422:             if bak:
0423:                 return _drift_error(self._path_for(target), bak)
0424: 
0425:             entries = self._entries_for(target)
0426:             matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
0427: 
0428:             if not matches:
0429:                 return {"success": False, "error": f"No entry matched '{old_text}'."}
0430: 
0431:             if len(matches) > 1:
0432:                 # If all matches are identical (exact duplicates), remove the first one
0433:                 unique_texts = {e for _, e in matches}
0434:                 if len(unique_texts) > 1:
0435:                     previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
0436:                     return {
0437:                         "success": False,
0438:                         "error": f"Multiple entries matched '{old_text}'. Be more specific.",
0439:                         "matches": previews,
0440:                     }
0441:                 # All identical -- safe to remove just the first
0442: 
0443:             idx = matches[0][0]
0444:             entries.pop(idx)
0445:             self._set_entries(target, entries)
0446:             self.save_to_disk(target)
0447: 
0448:         return self._success_response(target, "Entry removed.")
0449: 
0450:     def format_for_system_prompt(self, target: str) -> Optional[str]:
0451:         """
0452:         Return the frozen snapshot for system prompt injection.
0453: 
0454:         This returns the state captured at load_from_disk() time, NOT the live
0455:         state. Mid-session writes do not affect this. This keeps the system
0456:         prompt stable across all turns, preserving the prefix cache.
0457: 
0458:         Returns None if the snapshot is empty (no entries at load time).
0459:         """
0460:         block = self._system_prompt_snapshot.get(target, "")
0461:         return block if block else None
```
