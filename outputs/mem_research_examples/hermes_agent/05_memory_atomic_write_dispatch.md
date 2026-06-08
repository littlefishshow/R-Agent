# Atomic file writes and memory_tool dispatch

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/memory_tool.py#L577-L647

Local clone source path: `tools/memory_tool.py` (temporary clone; cleaned after research)

```python
0577:     @staticmethod
0578:     def _write_file(path: Path, entries: List[str]):
0579:         """Write entries to a memory file using atomic temp-file + rename.
0580: 
0581:         Previous implementation used open("w") + flock, but "w" truncates the
0582:         file *before* the lock is acquired, creating a race window where
0583:         concurrent readers see an empty file. Atomic rename avoids this:
0584:         readers always see either the old complete file or the new one.
0585:         """
0586:         content = ENTRY_DELIMITER.join(entries) if entries else ""
0587:         try:
0588:             # Write to temp file in same directory (same filesystem for atomic rename)
0589:             fd, tmp_path = tempfile.mkstemp(
0590:                 dir=str(path.parent), suffix=".tmp", prefix=".mem_"
0591:             )
0592:             try:
0593:                 with os.fdopen(fd, "w", encoding="utf-8") as f:
0594:                     f.write(content)
0595:                     f.flush()
0596:                     os.fsync(f.fileno())
0597:                 atomic_replace(tmp_path, path)
0598:             except BaseException:
0599:                 # Clean up temp file on any failure
0600:                 try:
0601:                     os.unlink(tmp_path)
0602:                 except OSError:
0603:                     pass
0604:                 raise
0605:         except (OSError, IOError) as e:
0606:             raise RuntimeError(f"Failed to write memory file {path}: {e}")
0607: 
0608: 
0609: def memory_tool(
0610:     action: str,
0611:     target: str = "memory",
0612:     content: str = None,
0613:     old_text: str = None,
0614:     store: Optional[MemoryStore] = None,
0615: ) -> str:
0616:     """
0617:     Single entry point for the memory tool. Dispatches to MemoryStore methods.
0618: 
0619:     Returns JSON string with results.
0620:     """
0621:     if store is None:
0622:         return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)
0623: 
0624:     if target not in {"memory", "user"}:
0625:         return tool_error(f"Invalid target '{target}'. Use 'memory' or 'user'.", success=False)
0626: 
0627:     if action == "add":
0628:         if not content:
0629:             return tool_error("Content is required for 'add' action.", success=False)
0630:         result = store.add(target, content)
0631: 
0632:     elif action == "replace":
0633:         if not old_text:
0634:             return tool_error("old_text is required for 'replace' action.", success=False)
0635:         if not content:
0636:             return tool_error("content is required for 'replace' action.", success=False)
0637:         result = store.replace(target, old_text, content)
0638: 
0639:     elif action == "remove":
0640:         if not old_text:
0641:             return tool_error("old_text is required for 'remove' action.", success=False)
0642:         result = store.remove(target, old_text)
0643: 
0644:     else:
0645:         return tool_error(f"Unknown action '{action}'. Use: add, replace, remove", success=False)
0646: 
0647:     return json.dumps(result, ensure_ascii=False)
```
