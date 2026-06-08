# Official docs: session_search vs memory

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/website/docs/user-guide/features/memory.md#L175-L201

Local clone source path: `website/docs/user-guide/features/memory.md` (temporary clone; cleaned after research)

```markdown
0175: ## Session Search
0176: 
0177: Beyond MEMORY.md and USER.md, the agent can search its past conversations using the `session_search` tool:
0178: 
0179: - All CLI and messaging sessions are stored in SQLite (`~/.hermes/state.db`) with FTS5 full-text search
0180: - Search queries return actual messages from the DB — no LLM summarization, no truncation
0181: - The agent can find things it discussed weeks ago, even if they're not in its active memory
0182: - The agent can also scroll forward/backward inside any session it finds
0183: 
0184: ```bash
0185: hermes sessions list    # Browse past sessions
0186: ```
0187: 
0188: See [Session Search Tool](/user-guide/sessions#session-search-tool) for the three calling shapes (discovery / scroll / browse) and the response format.
0189: 
0190: ### session_search vs memory
0191: 
0192: | Feature | Persistent Memory | Session Search |
0193: |---------|------------------|----------------|
0194: | **Capacity** | ~1,300 tokens total | Unlimited (all sessions) |
0195: | **Speed** | Instant (in system prompt) | ~20ms FTS5 query, ~1ms scroll |
0196: | **Cost** | Token cost in every prompt | Free — no LLM calls |
0197: | **Use case** | Key facts always available | Finding specific past conversations |
0198: | **Management** | Manually curated by agent | Automatic — all sessions stored |
0199: | **Token cost** | Fixed per session (~1,300 tokens) | On-demand (searched when needed) |
0200: 
0201: **Memory** is for critical facts that should always be in context. **Session search** is for "did we discuss X last week?" queries where the agent needs to recall specifics from past conversations.
```
