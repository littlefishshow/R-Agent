# MemoryManager registration, one external provider, tool routing

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/memory_manager.py#L244-L321

Local clone source path: `agent/memory_manager.py` (temporary clone; cleaned after research)

```python
0244: class MemoryManager:
0245:     """Orchestrates the built-in provider plus at most one external provider.
0246: 
0247:     The builtin provider is always first. Only one non-builtin (external)
0248:     provider is allowed.  Failures in one provider never block the other.
0249:     """
0250: 
0251:     def __init__(self) -> None:
0252:         self._providers: List[MemoryProvider] = []
0253:         self._tool_to_provider: Dict[str, MemoryProvider] = {}
0254:         self._has_external: bool = False  # True once a non-builtin provider is added
0255: 
0256:     # -- Registration --------------------------------------------------------
0257: 
0258:     def add_provider(self, provider: MemoryProvider) -> None:
0259:         """Register a memory provider.
0260: 
0261:         Built-in provider (name ``"builtin"``) is always accepted.
0262:         Only **one** external (non-builtin) provider is allowed — a second
0263:         attempt is rejected with a warning.
0264:         """
0265:         is_builtin = provider.name == "builtin"
0266: 
0267:         if not is_builtin:
0268:             if self._has_external:
0269:                 existing = next(
0270:                     (p.name for p in self._providers if p.name != "builtin"), "unknown"
0271:                 )
0272:                 logger.warning(
0273:                     "Rejected memory provider '%s' — external provider '%s' is "
0274:                     "already registered. Only one external memory provider is "
0275:                     "allowed at a time. Configure which one via memory.provider "
0276:                     "in config.yaml.",
0277:                     provider.name, existing,
0278:                 )
0279:                 return
0280:             self._has_external = True
0281: 
0282:         self._providers.append(provider)
0283: 
0284:         # Core tool names are reserved — a memory provider must never register
0285:         # a tool that shadows a built-in (e.g. ``clarify``, ``delegate_task``).
0286:         # Built-ins always win, so such a tool is dropped at agent init and
0287:         # would otherwise linger in ``_tool_to_provider`` and hijack dispatch
0288:         # (#40466). Reject it here, at the door, so it never enters the routing
0289:         # table at all — matching the built-ins-always-win invariant used by
0290:         # the TTS/browser/search provider registries.
0291:         from toolsets import _HERMES_CORE_TOOLS
0292: 
0293:         _core_tool_names = set(_HERMES_CORE_TOOLS)
0294: 
0295:         # Index tool names → provider for routing
0296:         for schema in provider.get_tool_schemas():
0297:             tool_name = schema.get("name", "")
0298:             if tool_name in _core_tool_names:
0299:                 logger.warning(
0300:                     "Memory provider '%s' tool '%s' shadows a reserved core "
0301:                     "tool name; registration ignored. Core tools always win — "
0302:                     "rename the provider's tool to something unique.",
0303:                     provider.name, tool_name,
0304:                 )
0305:                 continue
0306:             if tool_name and tool_name not in self._tool_to_provider:
0307:                 self._tool_to_provider[tool_name] = provider
0308:             elif tool_name in self._tool_to_provider:
0309:                 logger.warning(
0310:                     "Memory tool name conflict: '%s' already registered by %s, "
0311:                     "ignoring from %s",
0312:                     tool_name,
0313:                     self._tool_to_provider[tool_name].name,
0314:                     provider.name,
0315:                 )
0316: 
0317:         logger.info(
0318:             "Memory provider '%s' registered (%d tools)",
0319:             provider.name,
0320:             len(provider.get_tool_schemas()),
0321:         )
```
