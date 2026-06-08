# SQLite FTS5 and trigram session-message indexes

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/hermes_state.py#L321-L374

Local clone source path: `hermes_state.py` (temporary clone; cleaned after research)

```python
0321: FTS_SQL = """
0322: CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
0323:     content
0324: );
0325: 
0326: CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
0327:     INSERT INTO messages_fts(rowid, content) VALUES (
0328:         new.id,
0329:         COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
0330:     );
0331: END;
0332: 
0333: CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
0334:     DELETE FROM messages_fts WHERE rowid = old.id;
0335: END;
0336: 
0337: CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
0338:     DELETE FROM messages_fts WHERE rowid = old.id;
0339:     INSERT INTO messages_fts(rowid, content) VALUES (
0340:         new.id,
0341:         COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
0342:     );
0343: END;
0344: """
0345: 
0346: # Trigram FTS5 table for CJK substring search.  The default unicode61
0347: # tokenizer splits CJK characters into individual tokens, breaking phrase
0348: # matching.  The trigram tokenizer creates overlapping 3-byte sequences so
0349: # substring queries work natively for any script (CJK, Thai, etc.).
0350: FTS_TRIGRAM_SQL = """
0351: CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(
0352:     content,
0353:     tokenize='trigram'
0354: );
0355: 
0356: CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
0357:     INSERT INTO messages_fts_trigram(rowid, content) VALUES (
0358:         new.id,
0359:         COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
0360:     );
0361: END;
0362: 
0363: CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
0364:     DELETE FROM messages_fts_trigram WHERE rowid = old.id;
0365: END;
0366: 
0367: CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update AFTER UPDATE ON messages BEGIN
0368:     DELETE FROM messages_fts_trigram WHERE rowid = old.id;
0369:     INSERT INTO messages_fts_trigram(rowid, content) VALUES (
0370:         new.id,
0371:         COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
0372:     );
0373: END;
0374: """
```
