# OpenAI tool schema and behavioral guidance for memory

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/tools/memory_tool.py#L659-L708

Local clone source path: `tools/memory_tool.py` (temporary clone; cleaned after research)

```python
0659: MEMORY_SCHEMA = {
0660:     "name": "memory",
0661:     "description": (
0662:         "Save durable information to persistent memory that survives across sessions. "
0663:         "Memory is injected into future turns, so keep it compact and focused on facts "
0664:         "that will still matter later.\n\n"
0665:         "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
0666:         "- User corrects you or says 'remember this' / 'don't do that again'\n"
0667:         "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
0668:         "- You discover something about the environment (OS, installed tools, project structure)\n"
0669:         "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
0670:         "- You identify a stable fact that will be useful again in future sessions\n\n"
0671:         "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
0672:         "The most valuable memory prevents the user from having to repeat themselves.\n\n"
0673:         "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
0674:         "state to memory; use session_search to recall those from past transcripts.\n"
0675:         "If you've discovered a new way to do something, solved a problem that could be "
0676:         "necessary later, save it as a skill with the skill tool.\n\n"
0677:         "TWO TARGETS:\n"
0678:         "- 'user': who the user is -- name, role, preferences, communication style, pet peeves\n"
0679:         "- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned\n\n"
0680:         "ACTIONS: add (new entry), replace (update existing -- old_text identifies it), "
0681:         "remove (delete -- old_text identifies it).\n\n"
0682:         "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state."
0683:     ),
0684:     "parameters": {
0685:         "type": "object",
0686:         "properties": {
0687:             "action": {
0688:                 "type": "string",
0689:                 "enum": ["add", "replace", "remove"],
0690:                 "description": "The action to perform."
0691:             },
0692:             "target": {
0693:                 "type": "string",
0694:                 "enum": ["memory", "user"],
0695:                 "description": "Which memory store: 'memory' for personal notes, 'user' for user profile."
0696:             },
0697:             "content": {
0698:                 "type": "string",
0699:                 "description": "The entry content. Required for 'add' and 'replace'."
0700:             },
0701:             "old_text": {
0702:                 "type": "string",
0703:                 "description": "Short unique substring identifying the entry to replace or remove."
0704:             },
0705:         },
0706:         "required": ["action", "target"],
0707:     },
0708: }
```
