# Post-turn sync_all and queue_prefetch_all

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/run_agent.py#L2941-L2984

Local clone source path: `run_agent.py` (temporary clone; cleaned after research)

```python
2941:         """Mirror a completed turn into external memory providers.
2942: 
2943:         Called at the end of ``run_conversation`` with the cleaned user
2944:         message (``original_user_message``) and the finalised assistant
2945:         response.  The external memory backend gets both ``sync_all`` (to
2946:         persist the exchange) and ``queue_prefetch_all`` (to start
2947:         warming context for the next turn) in one shot.
2948: 
2949:         Uses ``original_user_message`` rather than ``user_message``
2950:         because the latter may carry injected skill content that bloats
2951:         or breaks provider queries.
2952: 
2953:         Interrupted turns are skipped entirely (#15218).  A partial
2954:         assistant output, an aborted tool chain, or a mid-stream reset
2955:         is not durable conversational truth — mirroring it into an
2956:         external memory backend pollutes future recall with state the
2957:         user never saw completed.  The prefetch is gated on the same
2958:         flag: the user's next message is almost certainly a retry of
2959:         the same intent, and a prefetch keyed on the interrupted turn
2960:         would fire against stale context.
2961: 
2962:         Normal completed turns still sync as before.  The whole body is
2963:         wrapped in ``try/except Exception`` because external memory
2964:         providers are strictly best-effort — a misconfigured or offline
2965:         backend must not block the user from seeing their response.
2966:         """
2967:         if interrupted:
2968:             return
2969:         if not (self._memory_manager and final_response and original_user_message):
2970:             return
2971:         try:
2972:             sync_kwargs = {"session_id": self.session_id or ""}
2973:             if messages is not None:
2974:                 sync_kwargs["messages"] = messages
2975:             self._memory_manager.sync_all(
2976:                 original_user_message,
2977:                 final_response,
2978:                 **sync_kwargs,
2979:             )
2980:             self._memory_manager.queue_prefetch_all(
2981:                 original_user_message,
2982:                 session_id=self.session_id or "",
2983:             )
2984:         except Exception:
```
