# Prefetched memory injected ephemerally into current user message

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/conversation_loop.py#L610-L627

Local clone source path: `agent/conversation_loop.py` (temporary clone; cleaned after research)

```python
0610:             # Inject ephemeral context into the current turn's user message.
0611:             # Sources: memory manager prefetch + plugin pre_llm_call hooks
0612:             # with target="user_message" (the default).  Both are
0613:             # API-call-time only — the original message in `messages` is
0614:             # never mutated, so nothing leaks into session persistence.
0615:             if idx == current_turn_user_idx and msg.get("role") == "user":
0616:                 _injections = []
0617:                 if _ext_prefetch_cache:
0618:                     _fenced = build_memory_context_block(_ext_prefetch_cache)
0619:                     if _fenced:
0620:                         _injections.append(_fenced)
0621:                 if _plugin_user_context:
0622:                     _injections.append(_plugin_user_context)
0623:                 if _injections:
0624:                     _base = api_msg.get("content", "")
0625:                     if isinstance(_base, str):
0626:                         api_msg["content"] = _base + "\n\n" + "\n\n".join(_injections)
0627: 
```
