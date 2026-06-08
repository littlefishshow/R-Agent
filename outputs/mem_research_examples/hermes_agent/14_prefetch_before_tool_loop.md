# Turn start calls on_turn_start and prefetch_all once

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/turn_context.py#L359-L388

Local clone source path: `agent/turn_context.py` (temporary clone; cleaned after research)

```python
0359:     # Notify memory providers of the new turn (BEFORE prefetch_all).
0360:     if agent._memory_manager:
0361:         try:
0362:             _turn_msg = original_user_message if isinstance(original_user_message, str) else ""
0363:             agent._memory_manager.on_turn_start(agent._user_turn_count, _turn_msg)
0364:         except Exception:
0365:             pass
0366: 
0367:     # External memory provider: prefetch once before the tool loop.
0368:     ext_prefetch_cache = ""
0369:     if agent._memory_manager:
0370:         try:
0371:             _query = original_user_message if isinstance(original_user_message, str) else ""
0372:             ext_prefetch_cache = agent._memory_manager.prefetch_all(_query) or ""
0373:         except Exception:
0374:             pass
0375: 
0376:     return TurnContext(
0377:         user_message=user_message,
0378:         original_user_message=original_user_message,
0379:         messages=messages,
0380:         conversation_history=conversation_history,
0381:         active_system_prompt=active_system_prompt,
0382:         effective_task_id=effective_task_id,
0383:         turn_id=turn_id,
0384:         current_turn_user_idx=current_turn_user_idx,
0385:         should_review_memory=should_review_memory,
0386:         plugin_user_context=plugin_user_context,
0387:         ext_prefetch_cache=ext_prefetch_cache,
0388:     )
```
