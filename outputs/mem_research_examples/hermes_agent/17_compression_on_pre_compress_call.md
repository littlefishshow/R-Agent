# Compression path invokes memory provider on_pre_compress

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/agent/conversation_compression.py#L428-L436

Local clone source path: `agent/conversation_compression.py` (temporary clone; cleaned after research)

```python
0428:     # Notify external memory provider before compression discards context
0429:     if agent._memory_manager:
0430:         try:
0431:             agent._memory_manager.on_pre_compress(messages)
0432:         except Exception:
0433:             pass
0434: 
0435:     try:
0436:         compressed = agent.context_compressor.compress(messages, current_tokens=approx_tokens, focus_topic=focus_topic, force=force)
```
