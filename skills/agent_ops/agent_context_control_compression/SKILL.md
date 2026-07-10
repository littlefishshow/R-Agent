---
name: "agent_context_control_compression"
description: "为 R-Agent 实现上下文窗口判别与完整消息压缩"
---

# Agent Context Control Compression

## When to Use

- 用户要求 R-Agent 增加上下文控制、上下文压缩、token/window 阈值判别。
- 需要审计每轮 LLM 请求中的 system/messages/tools/tool results/skills/memory 可见性。
- 需要避免长对话中 tool result、assistant tool_calls 和 user messages 无限累积导致超过模型上下文窗口。
- 需要保证压缩时保留完整 message，不从单条保留 message 中间截断。

## Procedure

1. 先审计 `core/agent.py` 的请求构造与消息追加路径：
   - `run_conversation()` 注入 system/user message。
   - `_loop()` 每轮获取 `registry.get_all_schemas()`，构造 `kwargs={model,messages}`，有 tools 时附加 tools。
   - assistant message 和 tool result 都会写入 `self.messages`。
2. 检查配置层：OpenAI/Azure ChatCompletion response usage 通常只提供本次 prompt/completion/total tokens，不提供最大 context window；需要新增本地模型窗口映射和环境变量覆盖。
3. 将可复用逻辑放入 `core/context_control.py`：
   - `resolve_context_window(model, configured)`：环境变量优先，其次模型名映射，最后默认值。
   - `estimate_request_tokens(messages, tools)`：无外部依赖的快速估算。
   - `should_compress_context(...)`：默认 80% 阈值判定。
   - `compress_messages(...)`：按完整 message / assistant tool_calls + 后续 tool messages 分组压缩。
4. 压缩策略：
   - 保留第一条 system。
   - 将较早历史合并为一条 system 摘要，摘要包含用户重点、助手决策、工具结果要点和系统控制信息。
   - 保留最近完整 messages；不要从保留 message 中间截断。
   - 不拆散 assistant tool_calls 与后续 tool result，避免 OpenAI 消息链不合法。
5. 在 `tools/context_tool.py` 将完整 messages 压缩能力合并进既有 `archive_subtask`，避免额外暴露重复 `context_compress` schema 干扰工具选择。`archive_subtask` 需兼容两种用法：只传 `summary/next_steps` 时由 Agent 主循环压缩当前 `self.messages`；传入 `messages/tools` 时直接返回 `compressed_messages`、摘要和统计。
6. 在 `core/agent.py` 每次 LLM 请求前调用 `_maybe_compress_context(tools)`：
   - 根据 `config.get_llm_context_window()` 或模型映射取 max context。
   - 默认 80% 触发，默认压到 55%。
   - 更新 `context_usage`，供 GUI/状态查看。
7. 在 GUI runtime state 中暴露 `agent.get_context_usage()`。
8. 补测试：
   - 完整 message 保留。
   - tool result 摘要。
   - 低于阈值且 force=false 不压缩。
   - 80% 阈值判定。
   - Agent 请求前自动压缩。
9. 更新 README 日期日志，记录上下文控制升级内容。

## Verification

推荐最小验证：

```bash
python3 -m pytest tests/test_context_control.py tests/test_archive_subtask_compression.py tests/test_token_usage_display.py tests/test_gui_runtime.py
python3 -m py_compile core/context_control.py core/agent.py core/config.py tools/context_tool.py app_gui/runtime.py
```

## Pitfalls

- 不要把 response.usage 当成模型最大上下文窗口；它只是本次请求 token 用量。
- 压缩时不要留下 orphan tool message；assistant tool_calls 后必须跟对应 tool result。
- 不要只按字符截断整个 messages JSON；应以完整 message 或完整 tool-call unit 为边界。
- 不要同时暴露 `archive_subtask` 和 `context_compress` 两个语义接近的工具 schema；优先把能力合并进 `archive_subtask`，旧参数向后兼容，新参数提供完整压缩能力。
- 如果测试 fake response 用 dict message，`core/agent.py` 可能访问 `message.tool_calls` 失败；测试 fake response 应模拟 SDK object，或生产代码需兼容 dict。