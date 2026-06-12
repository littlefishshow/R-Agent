---
name: "agent_context_audit"
description: "审计 Agent 每轮上下文可见性并生成源码说明文档"
---

# Agent Context Audit

## When to Use

Use this skill when the user asks what an agent/model can see during each invocation, including system prompt, tools, skills, memory, conversation history, tool results, hidden reasoning, or subagent context isolation, and wants this grounded in project source files.

## Procedure

1. Initialize or inspect a task plan if the audit spans multiple files.
2. Locate the agent loop and request construction:
   - Search for chat completion calls, `messages`, `tools`, `tool_calls`, and registry/schema code.
   - Identify where the system prompt is built and where it is inserted into messages.
3. Trace each context channel separately:
   - System prompt/persona files.
   - User and assistant conversation history.
   - Tool schemas and tool execution results.
   - Skills listing/viewing/loading behavior.
   - Memory snapshot vs live memory tools.
   - UI/status callbacks vs actual model-visible messages.
   - Subagent/delegation context construction and excluded tools.
   - Any context compression/archive mechanism; verify implementation matches tool description.
4. Use read/search tools to cite exact files and line ranges. Do not rely on memory for file content.
5. Produce a Markdown document with:
   - A one-sentence model of each invocation.
   - A visibility table.
   - Call-chain explanation.
   - Source index with file/function/line references.
   - Notes about mismatches or risks.
   - Improvement suggestions if relevant.
6. Verify the document with line count and heading scan before final response.

## Notes

- Distinguish “available through tool schema” from “already present in messages.”
- Distinguish hidden model reasoning from assistant messages/tool calls actually saved in conversation history.
- Distinguish parent agent context from subagent context; never assume delegation inherits parent messages unless source proves it.
- If a tool claims to compress/archive context, verify whether the agent loop actually intercepts and mutates the message history.