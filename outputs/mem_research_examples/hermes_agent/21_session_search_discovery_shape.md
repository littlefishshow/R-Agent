# session_search discovery: FTS hits plus anchored views

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/session_search_tool.py#L393-L491

Local clone source path: `tools/session_search_tool.py` (temporary clone; cleaned after research)

```python
0393: def _discover(
0394:     db,
0395:     query: str,
0396:     role_filter: Optional[List[str]],
0397:     limit: int,
0398:     sort: Optional[str],
0399:     current_session_id: str = None,
0400: ) -> str:
0401:     """Discovery shape: FTS5 + anchored window + bookends per hit. Single call."""
0402:     role_list = role_filter if role_filter else ["user", "assistant"]
0403: 
0404:     try:
0405:         raw_results = db.search_messages(
0406:             query=query,
0407:             role_filter=role_list,
0408:             exclude_sources=list(_HIDDEN_SESSION_SOURCES),
0409:             limit=50,  # widen so dedup-by-lineage can find distinct sessions
0410:             offset=0,
0411:             sort=sort,
0412:         )
0413:     except Exception as e:
0414:         logging.error("FTS5 search failed: %s", e, exc_info=True)
0415:         return tool_error(f"Search failed: {e}", success=False)
0416: 
0417:     if not raw_results:
0418:         return json.dumps({
0419:             "success": True,
0420:             "mode": "discover",
0421:             "query": query,
0422:             "results": [],
0423:             "count": 0,
0424:             "message": "No matching sessions found.",
0425:         }, ensure_ascii=False)
0426: 
0427:     current_lineage_root = _resolve_to_parent(db, current_session_id) if current_session_id else None
0428: 
0429:     # Dedupe by lineage. Keep the raw owning session_id on the surviving
0430:     # row — only that pairs validly with the FTS5 match id for the anchored
0431:     # window. parent_session_id is exposed separately when different.
0432:     seen_sessions = {}
0433:     for r in raw_results:
0434:         raw_sid = r["session_id"]
0435:         resolved_sid = _resolve_to_parent(db, raw_sid)
0436:         # Skip the current session lineage
0437:         if current_lineage_root and resolved_sid == current_lineage_root:
0438:             continue
0439:         if current_session_id and raw_sid == current_session_id:
0440:             continue
0441:         if resolved_sid not in seen_sessions:
0442:             row = dict(r)
0443:             row["_lineage_root"] = resolved_sid
0444:             seen_sessions[resolved_sid] = row
0445:         if len(seen_sessions) >= limit:
0446:             break
0447: 
0448:     results = []
0449:     for lineage_root, match_info in seen_sessions.items():
0450:         hit_sid = match_info.get("session_id") or lineage_root
0451:         msg_id = match_info.get("id")
0452:         try:
0453:             view = db.get_anchored_view(hit_sid, msg_id, window=5, bookend=3)
0454:         except Exception as e:
0455:             logging.warning("get_anchored_view failed for %s/%s: %s", hit_sid, msg_id, e, exc_info=True)
0456:             continue
0457: 
0458:         try:
0459:             session_meta = db.get_session(lineage_root) or {}
0460:         except Exception:
0461:             session_meta = {}
0462: 
0463:         entry = {
0464:             "session_id": hit_sid,
0465:             "when": _format_timestamp(
0466:                 session_meta.get("started_at") or match_info.get("session_started")
0467:             ),
0468:             "source": session_meta.get("source") or match_info.get("source", "unknown"),
0469:             "model": session_meta.get("model") or match_info.get("model") or "unknown",
0470:             "title": session_meta.get("title") or None,
0471:             "matched_role": match_info.get("role"),
0472:             "match_message_id": msg_id,
0473:             "snippet": match_info.get("snippet") or "",
0474:             "bookend_start": [_shape_message(m) for m in (view.get("bookend_start") or [])],
0475:             "messages": [_shape_message(m, anchor_id=msg_id) for m in (view.get("window") or [])],
0476:             "bookend_end": [_shape_message(m) for m in (view.get("bookend_end") or [])],
0477:             "messages_before": view.get("messages_before", 0),
0478:             "messages_after": view.get("messages_after", 0),
0479:         }
0480:         if lineage_root and lineage_root != hit_sid:
0481:             entry["parent_session_id"] = lineage_root
0482:         results.append(entry)
0483: 
0484:     return json.dumps({
0485:         "success": True,
0486:         "mode": "discover",
0487:         "query": query,
0488:         "results": results,
0489:         "count": len(results),
0490:         "sessions_searched": len(seen_sessions),
0491:     }, ensure_ascii=False)
```
