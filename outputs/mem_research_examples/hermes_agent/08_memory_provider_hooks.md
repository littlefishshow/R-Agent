# MemoryProvider compression/memory-write/delegation hooks

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/memory_provider.py#L219-L296

Local clone source path: `agent/memory_provider.py` (temporary clone; cleaned after research)

```python
0219:     def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
0220:         """Called before context compression discards old messages.
0221: 
0222:         Use to extract insights from messages about to be compressed.
0223:         messages is the list that will be summarized/discarded.
0224: 
0225:         Return text to include in the compression summary prompt so the
0226:         compressor preserves provider-extracted insights. Return empty
0227:         string for no contribution (backwards-compatible default).
0228:         """
0229:         return ""
0230: 
0231:     def on_delegation(self, task: str, result: str, *,
0232:                       child_session_id: str = "", **kwargs) -> None:
0233:         """Called on the PARENT agent when a subagent completes.
0234: 
0235:         The parent's memory provider gets the task+result pair as an
0236:         observation of what was delegated and what came back. The subagent
0237:         itself has no provider session (skip_memory=True).
0238: 
0239:         task: the delegation prompt
0240:         result: the subagent's final response
0241:         child_session_id: the subagent's session_id
0242:         """
0243: 
0244:     def get_config_schema(self) -> List[Dict[str, Any]]:
0245:         """Return config fields this provider needs for setup.
0246: 
0247:         Used by 'hermes memory setup' to walk the user through configuration.
0248:         Each field is a dict with:
0249:           key:         config key name (e.g. 'api_key', 'mode')
0250:           description: human-readable description
0251:           secret:      True if this should go to .env (default: False)
0252:           required:    True if required (default: False)
0253:           default:     default value (optional)
0254:           choices:     list of valid values (optional)
0255:           url:         URL where user can get this credential (optional)
0256:           env_var:     explicit env var name for secrets (default: auto-generated)
0257: 
0258:         Return empty list if no config needed (e.g. local-only providers).
0259:         """
0260:         return []
0261: 
0262:     def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
0263:         """Write non-secret config to the provider's native location.
0264: 
0265:         Called by 'hermes memory setup' after collecting user inputs.
0266:         ``values`` contains only non-secret fields (secrets go to .env).
0267:         ``hermes_home`` is the active HERMES_HOME directory path.
0268: 
0269:         Providers with native config files (JSON, YAML) should override
0270:         this to write to their expected location. Providers that use only
0271:         env vars can leave the default (no-op).
0272: 
0273:         All new memory provider plugins MUST implement either:
0274:         - save_config() for native config file formats, OR
0275:         - use only env vars (in which case get_config_schema() fields
0276:           should all have ``env_var`` set and this method stays no-op).
0277:         """
0278: 
0279:     def on_memory_write(
0280:         self,
0281:         action: str,
0282:         target: str,
0283:         content: str,
0284:         metadata: Optional[Dict[str, Any]] = None,
0285:     ) -> None:
0286:         """Called when the built-in memory tool writes an entry.
0287: 
0288:         action: 'add', 'replace', or 'remove'
0289:         target: 'memory' or 'user'
0290:         content: the entry content
0291:         metadata: structured provenance for the write, when available. Common
0292:           keys include ``write_origin``, ``execution_context``, ``session_id``,
0293:           ``parent_session_id``, ``platform``, and ``tool_name``.
0294: 
0295:         Use to mirror built-in memory writes to your backend.
0296:         """
```
