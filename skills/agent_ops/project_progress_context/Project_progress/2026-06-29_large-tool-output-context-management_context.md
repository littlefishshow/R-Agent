# Project Progress Context — large-tool-output-context-management

Created: 2026-06-29 20:47:25
---

## Progress Entry — 2026-06-29 20:47:25

### Project

large-tool-output-context-management

### Summary

借鉴 hermes-agent 完成 R-Agent 大工具输出外置化：大 tool result 在写入 messages 前自动持久化到 sandbox/tool_outputs，并以 <persisted-output> 摘要/预览/路径回填；新增 artifact_inspect/search/slice 作为二次提取工具。

### Current Status

核心实现已完成并通过定向测试。内部上下文治理模块已按用户要求从 tools/ 移到 core/context/；artifact 三件套仍在 tools/artifact_tools.py 注册为模型可调用工具。当前还可继续补齐 Hermes 更完备能力：turn-level aggregate budget 接入、registry per-tool 阈值、历史旧 tool result prune、完整 ContextCompressor。

### Key Files / Code Locations

- `core/context/budget_config.py`
- `core/context/tool_result_storage.py`
- `core/agent.py`
- `tools/artifact_tools.py`
- `tools/sys_tools.py`
- `tests/test_tool_result_storage.py`
- `tests/test_artifact_tools.py`
- `tests/test_agent_large_tool_output.py`
- `README.md`

### Decisions / Context

关键设计：core/context/budget_config.py 和 core/context/tool_result_storage.py 是内部基础设施，不注册为工具，不由 Agent 决定调用；core/agent.py 在工具执行完成后、tool message append 前固定调用 maybe_persist_tool_result。摘要来自程序化 summarize_content，不是 LLM 总结；包含 chars/lines/detected_format/keyword_counts/JSON 概览。完整内容保存在 sandbox/tool_outputs。artifact_inspect/search/slice 与 read_file 的区别：它们面向 persisted 大输出做概览、检索、局部读取，避免机械分块整份读回上下文。

### Prior Context Considered

(no previous context, or previous context intentionally omitted)

### Verification

已运行 python3 -m pytest tests/test_tool_result_storage.py tests/test_artifact_tools.py tests/test_agent_large_tool_output.py tests/test_gui_context_events.py tests/test_tool_process_isolation.py，结果 14 passed。已执行 sys_reload，共加载 33 个工具。README.md 已在 2026-06-29 更新日志记录大工具输出外置化与 artifact 二次检索。

### Unfinished / Next Steps

如继续升级，建议按顺序：1) 在 Agent Loop 中接入 enforce_turn_budget，处理同一轮多个中等 tool result 聚合超预算；2) ToolRegistry.register 增加 max_result_size 元数据并接入 BudgetConfig；3) 增加旧 tool result prune，保护 tail 并替换历史冗长 tool 输出；4) 设计真正的 head+summary+tail ContextCompressor，并保护 tool_call/tool_result 对；5) 可选设计 ContextEngine 插件抽象。
