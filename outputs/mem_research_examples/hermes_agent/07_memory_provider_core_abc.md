# MemoryProvider core lifecycle, prefetch, sync, tools

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/memory_provider.py#L42-L149

Local clone source path: `agent/memory_provider.py` (temporary clone; cleaned after research)

```python
0042: class MemoryProvider(ABC):
0043:     """Abstract base class for memory providers."""
0044: 
0045:     @property
0046:     @abstractmethod
0047:     def name(self) -> str:
0048:         """Short identifier for this provider (e.g. 'builtin', 'honcho', 'hindsight')."""
0049: 
0050:     # -- Core lifecycle (implement these) ------------------------------------
0051: 
0052:     @abstractmethod
0053:     def is_available(self) -> bool:
0054:         """Return True if this provider is configured, has credentials, and is ready.
0055: 
0056:         Called during agent init to decide whether to activate the provider.
0057:         Should not make network calls — just check config and installed deps.
0058:         """
0059: 
0060:     @abstractmethod
0061:     def initialize(self, session_id: str, **kwargs) -> None:
0062:         """Initialize for a session.
0063: 
0064:         Called once at agent startup. May create resources (banks, tables),
0065:         establish connections, start background threads, etc.
0066: 
0067:         kwargs always include:
0068:           - hermes_home (str): The active HERMES_HOME directory path. Use this
0069:             for profile-scoped storage instead of hardcoding ``~/.hermes``.
0070:           - platform (str): "cli", "telegram", "discord", "cron", etc.
0071: 
0072:         kwargs may also include:
0073:           - agent_context (str): "primary", "subagent", "cron", or "flush".
0074:             Providers should skip writes for non-primary contexts (cron system
0075:             prompts would corrupt user representations).
0076:           - agent_identity (str): Profile name (e.g. "coder"). Use for
0077:             per-profile provider identity scoping.
0078:           - agent_workspace (str): Shared workspace name (e.g. "hermes").
0079:           - parent_session_id (str): For subagents, the parent's session_id.
0080:           - user_id (str): Platform user identifier (gateway sessions).
0081:           - user_id_alt (str): Optional alternate stable platform user identifier.
0082:         """
0083: 
0084:     def system_prompt_block(self) -> str:
0085:         """Return text to include in the system prompt.
0086: 
0087:         Called during system prompt assembly. Return empty string to skip.
0088:         This is for STATIC provider info (instructions, status). Prefetched
0089:         recall context is injected separately via prefetch().
0090:         """
0091:         return ""
0092: 
0093:     def prefetch(self, query: str, *, session_id: str = "") -> str:
0094:         """Recall relevant context for the upcoming turn.
0095: 
0096:         Called before each API call. Return formatted text to inject as
0097:         context, or empty string if nothing relevant. Implementations
0098:         should be fast — use background threads for the actual recall
0099:         and return cached results here.
0100: 
0101:         session_id is provided for providers serving concurrent sessions
0102:         (gateway group chats, cached agents). Providers that don't need
0103:         per-session scoping can ignore it.
0104:         """
0105:         return ""
0106: 
0107:     def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
0108:         """Queue a background recall for the NEXT turn.
0109: 
0110:         Called after each turn completes. The result will be consumed
0111:         by prefetch() on the next turn. Default is no-op — providers
0112:         that do background prefetching should override this.
0113:         """
0114: 
0115:     def sync_turn(
0116:         self,
0117:         user_content: str,
0118:         assistant_content: str,
0119:         *,
0120:         session_id: str = "",
0121:         messages: Optional[List[Dict[str, Any]]] = None,
0122:     ) -> None:
0123:         """Persist a completed turn to the backend.
0124: 
0125:         Called after each turn. Should be non-blocking — queue for
0126:         background processing if the backend has latency.
0127: 
0128:         ``messages`` is the OpenAI-style conversation message list as of the
0129:         completed turn, including any assistant tool calls and tool results.
0130:         Providers that do not need raw turn context can ignore it.
0131:         """
0132: 
0133:     @abstractmethod
0134:     def get_tool_schemas(self) -> List[Dict[str, Any]]:
0135:         """Return tool schemas this provider exposes.
0136: 
0137:         Each schema follows the OpenAI function calling format:
0138:         {"name": "...", "description": "...", "parameters": {...}}
0139: 
0140:         Return empty list if this provider has no tools (context-only).
0141:         """
0142: 
0143:     def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
0144:         """Handle a tool call for one of this provider's tools.
0145: 
0146:         Must return a JSON string (the tool result).
0147:         Only called for tool names returned by get_tool_schemas().
0148:         """
0149:         raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")
```
