# session_search tool schema user-facing guidance

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/session_search_tool.py#L627-L679

Local clone source path: `tools/session_search_tool.py` (temporary clone; cleaned after research)

```python
0627: SESSION_SEARCH_SCHEMA = {
0628:     "name": "session_search",
0629:     "description": (
0630:         "Search past sessions stored in the local session DB, or scroll inside one. "
0631:         "FTS5-backed retrieval over the SQLite message store. No LLM calls — every "
0632:         "shape returns actual messages from the DB.\n\n"
0633:         "FOUR CALLING SHAPES\n\n"
0634:         "  1) DISCOVERY — pass `query`:\n"
0635:         "     session_search(query=\"auth refactor\", limit=3)\n"
0636:         "     Runs FTS5, dedupes hits by session lineage, returns the top N sessions. "
0637:         "Each result carries:\n"
0638:         "       - session_id, title, when, source\n"
0639:         "       - snippet: FTS5-highlighted match excerpt\n"
0640:         "       - bookend_start: first 3 user+assistant messages of the session "
0641:         "(the goal / kickoff)\n"
0642:         "       - messages: ±5 messages around the FTS5 match, with the anchor message "
0643:         "flagged (the hit in context)\n"
0644:         "       - bookend_end: last 3 user+assistant messages of the session "
0645:         "(the resolution / decisions)\n"
0646:         "       - match_message_id, messages_before, messages_after\n"
0647:         "     Bookends + window together let you reconstruct goal → match → resolution "
0648:         "without paying for the whole transcript.\n\n"
0649:         "  2) SCROLL — pass `session_id` + `around_message_id`:\n"
0650:         "     session_search(session_id=\"...\", around_message_id=12345, window=10)\n"
0651:         "     Returns a window of ±`window` messages centered on the anchor. No FTS5, "
0652:         "no bookends — just the slice. Use after a discovery call when you need more "
0653:         "context than the ±5 default window.\n"
0654:         "       - To scroll FORWARD: pass messages[-1].id back as around_message_id.\n"
0655:         "       - To scroll BACKWARD: pass messages[0].id back as around_message_id.\n"
0656:         "       - The boundary message appears in both windows — orientation marker.\n"
0657:         "       - When messages_before or messages_after is < window, you're at the "
0658:         "start or end of the session.\n\n"
0659:         "  3) READ — pass `session_id` only (no around_message_id):\n"
0660:         "     session_search(session_id=\"...\", profile=\"work\")\n"
0661:         "     Dumps the whole session by id (first 20 + last 10 messages when "
0662:         "large). This is how you resolve an `@session:<profile>/<id>` link the "
0663:         "user dropped into the chat: split the value on `/` into profile + id "
0664:         "and call session_search(session_id=id, profile=profile).\n\n"
0665:         "  4) BROWSE — no args:\n"
0666:         "     session_search()\n"
0667:         "     Returns recent sessions chronologically: titles, previews, timestamps. "
0668:         "Use when the user asks \"what was I working on\" without naming a topic.\n\n"
0669:         "FTS5 SYNTAX\n\n"
0670:         "  AND is the default — multi-word queries require all terms. Use OR explicitly "
0671:         "for broader recall (`alpha OR beta OR gamma`), quoted phrases for exact match "
0672:         "(`\"docker networking\"`), boolean (`python NOT java`), or prefix wildcards "
0673:         "(`deploy*`).\n\n"
0674:         "WHEN TO USE\n\n"
0675:         "  Reach for this on any \"what did we do about X\" / \"where did we leave Y\" / "
0676:         "\"find the session where Z\" question — before gh, web search, or filesystem "
0677:         "inspection. The session DB carries what was said when; external tools show "
0678:         "current world state."
0679:     ),
```
