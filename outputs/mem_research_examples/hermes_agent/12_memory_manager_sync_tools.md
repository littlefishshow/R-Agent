# MemoryManager sync_all and provider tool dispatch

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/memory_manager.py#L402-L489

Local clone source path: `agent/memory_manager.py` (temporary clone; cleaned after research)

```python
0402:     def sync_all(
0403:         self,
0404:         user_content: str,
0405:         assistant_content: str,
0406:         *,
0407:         session_id: str = "",
0408:         messages: Optional[List[Dict[str, Any]]] = None,
0409:     ) -> None:
0410:         """Sync a completed turn to all providers."""
0411:         for provider in self._providers:
0412:             try:
0413:                 if messages is not None and self._provider_sync_accepts_messages(provider):
0414:                     provider.sync_turn(
0415:                         user_content,
0416:                         assistant_content,
0417:                         session_id=session_id,
0418:                         messages=messages,
0419:                     )
0420:                 else:
0421:                     provider.sync_turn(
0422:                         user_content,
0423:                         assistant_content,
0424:                         session_id=session_id,
0425:                     )
0426:             except Exception as e:
0427:                 logger.warning(
0428:                     "Memory provider '%s' sync_turn failed: %s",
0429:                     provider.name, e,
0430:                 )
0431: 
0432:     # -- Tools ---------------------------------------------------------------
0433: 
0434:     def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
0435:         """Collect tool schemas from all providers.
0436: 
0437:         Reserved core tool names (``clarify``, ``delegate_task``, etc.) are
0438:         skipped — they are rejected from the routing table in
0439:         :meth:`add_provider`, so the manager must not advertise a schema it
0440:         will never route. Built-ins always win (#40466).
0441:         """
0442:         from toolsets import _HERMES_CORE_TOOLS
0443: 
0444:         _core_tool_names = set(_HERMES_CORE_TOOLS)
0445:         schemas = []
0446:         seen = set()
0447:         for provider in self._providers:
0448:             try:
0449:                 for schema in provider.get_tool_schemas():
0450:                     name = schema.get("name", "")
0451:                     if name in _core_tool_names:
0452:                         continue
0453:                     if name and name not in seen:
0454:                         schemas.append(schema)
0455:                         seen.add(name)
0456:             except Exception as e:
0457:                 logger.warning(
0458:                     "Memory provider '%s' get_tool_schemas() failed: %s",
0459:                     provider.name, e,
0460:                 )
0461:         return schemas
0462: 
0463:     def get_all_tool_names(self) -> set:
0464:         """Return set of all tool names across all providers."""
0465:         return set(self._tool_to_provider.keys())
0466: 
0467:     def has_tool(self, tool_name: str) -> bool:
0468:         """Check if any provider handles this tool."""
0469:         return tool_name in self._tool_to_provider
0470: 
0471:     def handle_tool_call(
0472:         self, tool_name: str, args: Dict[str, Any], **kwargs
0473:     ) -> str:
0474:         """Route a tool call to the correct provider.
0475: 
0476:         Returns JSON string result. Raises ValueError if no provider
0477:         handles the tool.
0478:         """
0479:         provider = self._tool_to_provider.get(tool_name)
0480:         if provider is None:
0481:             return tool_error(f"No memory provider handles tool '{tool_name}'")
0482:         try:
0483:             return provider.handle_tool_call(tool_name, args, **kwargs)
0484:         except Exception as e:
0485:             logger.error(
0486:                 "Memory provider '%s' handle_tool_call(%s) failed: %s",
0487:                 provider.name, tool_name, e,
0488:             )
0489:             return tool_error(f"Memory tool '{tool_name}' failed: {e}")
```
