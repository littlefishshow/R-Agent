# Anchored window and bookends for session search

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/hermes_state.py#L2214-L2377

Local clone source path: `hermes_state.py` (temporary clone; cleaned after research)

```python
2214:     def get_messages_around(
2215:         self,
2216:         session_id: str,
2217:         around_message_id: int,
2218:         window: int = 5,
2219:     ) -> Dict[str, Any]:
2220:         """Load a window of messages anchored on a specific message id.
2221: 
2222:         Returns a dict with:
2223:           - ``window``: up to ``window`` messages before the anchor, the anchor
2224:             itself, and up to ``window`` messages after, ordered by id ascending.
2225:           - ``messages_before``: count of messages strictly before the anchor
2226:             still in the session (== window unless we hit the start).
2227:           - ``messages_after``: count of messages strictly after the anchor
2228:             still in the session (== window unless we hit the end).
2229: 
2230:         Used by ``session_search`` for both the discovery shape (anchored on the
2231:         FTS5 match) and the scroll shape (anchored on any message id). The
2232:         ``messages_before`` / ``messages_after`` counts let the caller detect
2233:         session boundaries: when either is less than ``window``, the agent has
2234:         reached one end of the session.
2235: 
2236:         Returns an empty window when ``around_message_id`` is not a real id in
2237:         ``session_id`` — callers decide how to surface that.
2238:         """
2239:         if window < 0:
2240:             window = 0
2241:         with self._lock:
2242:             # Confirm the anchor exists in this session.
2243:             anchor_exists = self._conn.execute(
2244:                 "SELECT 1 FROM messages WHERE id = ? AND session_id = ? LIMIT 1",
2245:                 (around_message_id, session_id),
2246:             ).fetchone()
2247:             if not anchor_exists:
2248:                 return {"window": [], "messages_before": 0, "messages_after": 0}
2249: 
2250:             # Two queries: anchor + before (DESC, take window+1), and after
2251:             # (ASC, take window). Final order is id ASC.
2252:             before_rows = self._conn.execute(
2253:                 "SELECT * FROM messages "
2254:                 "WHERE session_id = ? AND id <= ? "
2255:                 "ORDER BY id DESC LIMIT ?",
2256:                 (session_id, around_message_id, window + 1),
2257:             ).fetchall()
2258:             after_rows = self._conn.execute(
2259:                 "SELECT * FROM messages "
2260:                 "WHERE session_id = ? AND id > ? "
2261:                 "ORDER BY id ASC LIMIT ?",
2262:                 (session_id, around_message_id, window),
2263:             ).fetchall()
2264: 
2265:         # before_rows is DESC; reverse so it's ASC, then concatenate after_rows.
2266:         rows = list(reversed(before_rows)) + list(after_rows)
2267:         result = []
2268:         for row in rows:
2269:             msg = dict(row)
2270:             if "content" in msg:
2271:                 msg["content"] = self._decode_content(msg["content"])
2272:             if msg.get("tool_calls"):
2273:                 try:
2274:                     msg["tool_calls"] = json.loads(msg["tool_calls"])
2275:                 except (json.JSONDecodeError, TypeError):
2276:                     logger.warning(
2277:                         "Failed to deserialize tool_calls in get_messages_around, falling back to []"
2278:                     )
2279:                     msg["tool_calls"] = []
2280:             result.append(msg)
2281: 
2282:         # before_rows includes the anchor itself; subtract 1 for the count of
2283:         # messages strictly before the anchor in the returned slice.
2284:         messages_before = max(0, len(before_rows) - 1)
2285:         messages_after = len(after_rows)
2286:         return {
2287:             "window": result,
2288:             "messages_before": messages_before,
2289:             "messages_after": messages_after,
2290:         }
2291: 
2292:     def get_anchored_view(
2293:         self,
2294:         session_id: str,
2295:         around_message_id: int,
2296:         window: int = 5,
2297:         bookend: int = 3,
2298:         keep_roles: Optional[Tuple[str, ...]] = ("user", "assistant"),
2299:     ) -> Dict[str, Any]:
2300:         """Return an anchored window plus session bookends.
2301: 
2302:         Built on top of ``get_messages_around``. Three slices:
2303: 
2304:           - ``window``: messages immediately surrounding the anchor. Filtered
2305:             to ``keep_roles`` (tool-response noise dropped by default), EXCEPT
2306:             the anchor itself is always preserved regardless of role.
2307:           - ``bookend_start``: first ``bookend`` user/assistant messages of the
2308:             session — but only those whose id is strictly before the window's
2309:             first message id. Empty when the window already overlaps the
2310:             session head. Empty-content messages (tool-call-only assistant
2311:             turns) are skipped so they don't crowd out actual prose openings.
2312:           - ``bookend_end``: last ``bookend`` user/assistant messages of the
2313:             session, same non-overlap rule at the tail.
2314: 
2315:         Bookends let an FTS5 hit anywhere in a long session yield the goal
2316:         (opening) and the resolution (closing) on a single call — without
2317:         loading the whole transcript.
2318: 
2319:         Returns ``{"window": [], "messages_before": 0, "messages_after": 0,
2320:         "bookend_start": [], "bookend_end": []}`` when the anchor isn't in
2321:         the session.
2322: 
2323:         ``keep_roles=None`` disables role filtering (raw window + raw
2324:         bookends).
2325:         """
2326:         if bookend < 0:
2327:             bookend = 0
2328: 
2329:         # Reuse the primitive — handles anchor-existence, content decoding,
2330:         # tool_calls deserialisation, and boundary counts.
2331:         primitive = self.get_messages_around(
2332:             session_id, around_message_id, window=window
2333:         )
2334:         window_rows = primitive["window"]
2335:         if not window_rows:
2336:             return {
2337:                 "window": [],
2338:                 "messages_before": 0,
2339:                 "messages_after": 0,
2340:                 "bookend_start": [],
2341:                 "bookend_end": [],
2342:             }
2343: 
2344:         # Apply role filter to the window, but never drop the anchor itself.
2345:         if keep_roles is not None:
2346:             keep_set = set(keep_roles)
2347:             filtered_window = [
2348:                 m for m in window_rows
2349:                 if m.get("id") == around_message_id or m.get("role") in keep_set
2350:             ]
2351:         else:
2352:             filtered_window = window_rows
2353: 
2354:         window_min_id = window_rows[0]["id"]
2355:         window_max_id = window_rows[-1]["id"]
2356: 
2357:         # Fetch bookends only when there's room outside the window. SQL filters
2358:         # by id range, role, and non-empty content — tool-call-only assistant
2359:         # turns (content='' with tool_calls populated) are excluded so they
2360:         # don't crowd out actual prose openings/closings.
2361:         bookend_start_rows: List[Any] = []
2362:         bookend_end_rows: List[Any] = []
2363:         if bookend > 0:
2364:             with self._lock:
2365:                 role_clause = ""
2366:                 role_params: list = []
2367:                 if keep_roles is not None:
2368:                     role_placeholders = ",".join("?" for _ in keep_roles)
2369:                     role_clause = f" AND role IN ({role_placeholders})"
2370:                     role_params = list(keep_roles)
2371: 
2372:                 bookend_start_rows = self._conn.execute(
2373:                     f"SELECT * FROM messages "
2374:                     f"WHERE session_id = ? AND id < ?{role_clause} "
2375:                     f"AND length(content) > 0 "
2376:                     f"ORDER BY id ASC LIMIT ?",
2377:                     (session_id, window_min_id, *role_params, bookend),
```
