# Official docs: MemoryProvider ABC and hooks

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/website/docs/developer-guide/memory-provider-plugin.md#L26-L84

Local clone source path: `website/docs/developer-guide/memory-provider-plugin.md` (temporary clone; cleaned after research)

```markdown
0026: ## The MemoryProvider ABC
0027: 
0028: Your plugin implements the `MemoryProvider` abstract base class from `agent/memory_provider.py`:
0029: 
0030: ```python
0031: from agent.memory_provider import MemoryProvider
0032: 
0033: class MyMemoryProvider(MemoryProvider):
0034:     @property
0035:     def name(self) -> str:
0036:         return "my-provider"
0037: 
0038:     def is_available(self) -> bool:
0039:         """Check if this provider can activate. NO network calls."""
0040:         return bool(os.environ.get("MY_API_KEY"))
0041: 
0042:     def initialize(self, session_id: str, **kwargs) -> None:
0043:         """Called once at agent startup.
0044: 
0045:         kwargs always includes:
0046:           hermes_home (str): Active HERMES_HOME path. Use for storage.
0047:         """
0048:         self._api_key = os.environ.get("MY_API_KEY", "")
0049:         self._session_id = session_id
0050: 
0051:     # ... implement remaining methods
0052: ```
0053: 
0054: ## Required Methods
0055: 
0056: ### Core Lifecycle
0057: 
0058: | Method | When Called | Must Implement? |
0059: |--------|-----------|-----------------|
0060: | `name` (property) | Always | **Yes** |
0061: | `is_available()` | Agent init, before activation | **Yes** — no network calls |
0062: | `initialize(session_id, **kwargs)` | Agent startup | **Yes** |
0063: | `get_tool_schemas()` | After init, for tool injection | **Yes** |
0064: | `handle_tool_call(tool_name, args, **kwargs)` | When agent uses your tools | **Yes** (if you have tools) |
0065: 
0066: ### Config
0067: 
0068: | Method | Purpose | Must Implement? |
0069: |--------|---------|-----------------|
0070: | `get_config_schema()` | Declare config fields for `hermes memory setup` | **Yes** |
0071: | `save_config(values, hermes_home)` | Write non-secret config to native location | **Yes** (unless env-var-only) |
0072: 
0073: ### Optional Hooks
0074: 
0075: | Method | When Called | Use Case |
0076: |--------|-----------|----------|
0077: | `system_prompt_block()` | System prompt assembly | Static provider info |
0078: | `prefetch(query, *, session_id="")` | Before each API call | Return recalled context |
0079: | `queue_prefetch(query)` | After each turn | Pre-warm for next turn |
0080: | `sync_turn(user, assistant, *, session_id="")` | After each completed turn | Persist conversation |
0081: | `on_session_end(messages)` | Conversation ends | Final extraction/flush |
0082: | `on_pre_compress(messages)` | Before context compression | Save insights before discard |
0083: | `on_memory_write(action, target, content)` | Built-in memory writes | Mirror to your backend |
0084: | `shutdown()` | Process exit | Clean up connections |
```
