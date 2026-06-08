# MemoryManager compression hook, memory write mirror, initialize_all

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/memory_manager.py#L566-L683

Local clone source path: `agent/memory_manager.py` (temporary clone; cleaned after research)

```python
0566:     def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
0567:         """Notify all providers before context compression.
0568: 
0569:         Returns combined text from providers to include in the compression
0570:         summary prompt. Empty string if no provider contributes.
0571:         """
0572:         parts = []
0573:         for provider in self._providers:
0574:             try:
0575:                 result = provider.on_pre_compress(messages)
0576:                 if result and result.strip():
0577:                     parts.append(result)
0578:             except Exception as e:
0579:                 logger.debug(
0580:                     "Memory provider '%s' on_pre_compress failed: %s",
0581:                     provider.name, e,
0582:                 )
0583:         return "\n\n".join(parts)
0584: 
0585:     @staticmethod
0586:     def _provider_memory_write_metadata_mode(provider: MemoryProvider) -> str:
0587:         """Return how to pass metadata to a provider's memory-write hook."""
0588:         try:
0589:             signature = inspect.signature(provider.on_memory_write)
0590:         except (TypeError, ValueError):
0591:             return "keyword"
0592: 
0593:         params = list(signature.parameters.values())
0594:         if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
0595:             return "keyword"
0596:         if "metadata" in signature.parameters:
0597:             return "keyword"
0598: 
0599:         accepted = [
0600:             p for p in params
0601:             if p.kind in {
0602:                 inspect.Parameter.POSITIONAL_ONLY,
0603:                 inspect.Parameter.POSITIONAL_OR_KEYWORD,
0604:                 inspect.Parameter.KEYWORD_ONLY,
0605:             }
0606:         ]
0607:         if len(accepted) >= 4:
0608:             return "positional"
0609:         return "legacy"
0610: 
0611:     def on_memory_write(
0612:         self,
0613:         action: str,
0614:         target: str,
0615:         content: str,
0616:         metadata: Optional[Dict[str, Any]] = None,
0617:     ) -> None:
0618:         """Notify external providers when the built-in memory tool writes.
0619: 
0620:         Skips the builtin provider itself (it's the source of the write).
0621:         """
0622:         for provider in self._providers:
0623:             if provider.name == "builtin":
0624:                 continue
0625:             try:
0626:                 metadata_mode = self._provider_memory_write_metadata_mode(provider)
0627:                 if metadata_mode == "keyword":
0628:                     provider.on_memory_write(
0629:                         action, target, content, metadata=dict(metadata or {})
0630:                     )
0631:                 elif metadata_mode == "positional":
0632:                     provider.on_memory_write(action, target, content, dict(metadata or {}))
0633:                 else:
0634:                     provider.on_memory_write(action, target, content)
0635:             except Exception as e:
0636:                 logger.debug(
0637:                     "Memory provider '%s' on_memory_write failed: %s",
0638:                     provider.name, e,
0639:                 )
0640: 
0641:     def on_delegation(self, task: str, result: str, *,
0642:                       child_session_id: str = "", **kwargs) -> None:
0643:         """Notify all providers that a subagent completed."""
0644:         for provider in self._providers:
0645:             try:
0646:                 provider.on_delegation(
0647:                     task, result, child_session_id=child_session_id, **kwargs
0648:                 )
0649:             except Exception as e:
0650:                 logger.debug(
0651:                     "Memory provider '%s' on_delegation failed: %s",
0652:                     provider.name, e,
0653:                 )
0654: 
0655:     def shutdown_all(self) -> None:
0656:         """Shut down all providers (reverse order for clean teardown)."""
0657:         for provider in reversed(self._providers):
0658:             try:
0659:                 provider.shutdown()
0660:             except Exception as e:
0661:                 logger.warning(
0662:                     "Memory provider '%s' shutdown failed: %s",
0663:                     provider.name, e,
0664:                 )
0665: 
0666:     def initialize_all(self, session_id: str, **kwargs) -> None:
0667:         """Initialize all providers.
0668: 
0669:         Automatically injects ``hermes_home`` into *kwargs* so that every
0670:         provider can resolve profile-scoped storage paths without importing
0671:         ``get_hermes_home()`` themselves.
0672:         """
0673:         if "hermes_home" not in kwargs:
0674:             from hermes_constants import get_hermes_home
0675:             kwargs["hermes_home"] = str(get_hermes_home())
0676:         for provider in self._providers:
0677:             try:
0678:                 provider.initialize(session_id=session_id, **kwargs)
0679:             except Exception as e:
0680:                 logger.warning(
0681:                     "Memory provider '%s' initialize failed: %s",
0682:                     provider.name, e,
0683:                 )
```
