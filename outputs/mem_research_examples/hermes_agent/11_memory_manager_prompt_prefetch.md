# MemoryManager system prompt and prefetch aggregation

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/memory_manager.py#L337-L383

Local clone source path: `agent/memory_manager.py` (temporary clone; cleaned after research)

```python
0337:     def build_system_prompt(self) -> str:
0338:         """Collect system prompt blocks from all providers.
0339: 
0340:         Returns combined text, or empty string if no providers contribute.
0341:         Each non-empty block is labeled with the provider name.
0342:         """
0343:         blocks = []
0344:         for provider in self._providers:
0345:             try:
0346:                 block = provider.system_prompt_block()
0347:                 if block and block.strip():
0348:                     blocks.append(block)
0349:             except Exception as e:
0350:                 logger.warning(
0351:                     "Memory provider '%s' system_prompt_block() failed: %s",
0352:                     provider.name, e,
0353:                 )
0354:         return "\n\n".join(blocks)
0355: 
0356:     # -- Prefetch / recall ---------------------------------------------------
0357: 
0358:     def prefetch_all(self, query: str, *, session_id: str = "") -> str:
0359:         """Collect prefetch context from all providers.
0360: 
0361:         Returns merged context text labeled by provider. Empty providers
0362:         are skipped. Failures in one provider don't block others.
0363:         """
0364:         parts = []
0365:         for provider in self._providers:
0366:             try:
0367:                 result = provider.prefetch(query, session_id=session_id)
0368:                 if result and result.strip():
0369:                     parts.append(result)
0370:             except Exception as e:
0371:                 logger.debug(
0372:                     "Memory provider '%s' prefetch failed (non-fatal): %s",
0373:                     provider.name, e,
0374:                 )
0375:         return "\n\n".join(parts)
0376: 
0377:     def queue_prefetch_all(self, query: str, *, session_id: str = "") -> None:
0378:         """Queue background prefetch on all providers for the next turn."""
0379:         for provider in self._providers:
0380:             try:
0381:                 provider.queue_prefetch(query, session_id=session_id)
0382:             except Exception as e:
0383:                 logger.debug(
```
