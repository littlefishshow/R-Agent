---
name: "cli_runtime_interrupt"
description: "为 CLI Agent 增加运行期中断与上下文回退"
---

# CLI Runtime Interrupt

## When to Use

- 用户希望命令行 Agent 在思考、工具执行或续跑期间支持按键中断。
- 需要在 Rich/prompt_toolkit 风格 CLI 中显示“按 Esc 中断”等提示。
- 需要中断后回滚本轮 assistant/tool 中间消息，避免污染 `RAgent.messages`。
- 当前 Agent Loop 是同步 LLM/工具调用，但希望先实现边界级取消、工具进程隔离与上下文回退。

## Procedure

1. **定位阻塞点**
   - 找到 CLI 主循环中同步调用 Agent 的位置，例如 `console.status(...)` 内直接执行 `agent.run_conversation(...)`。
   - 找到核心消息历史容器，例如 `RAgent.messages`。
   - 找到 LLM 请求、工具调用、强制收尾、截断续跑等边界。

2. **新增取消语义**
   - 在核心 Agent 模块新增显式中断异常，例如 `AgentInterrupted`。
   - 给 `run_conversation()`、`continue_after_truncation()`、内部 loop、LLM retry wrapper、force finalize 增加可选 `cancel_event`。
   - 用兼容函数判断 `cancel_event.is_set()`，不要假设一定是 `threading.Event`。

3. **设计回滚 checkpoint**
   - 普通对话：追加本轮 user message 后记录 `rollback_index = len(messages)`，中断时裁剪到该位置，保留用户输入、丢弃后续 assistant/tool/system 中间消息。
   - 续跑：追加续跑 user 指令前记录 checkpoint，中断时回滚续跑指令及其后的中间消息。
   - 中断时清理截断标记、软提醒标记等运行状态，避免后续误判。

4. **隔离工具执行**
   - 保留原同步 `execute_tool()` 作为兼容入口，新增 `execute_tool_isolated()` 供 Agent Loop 调用。
   - 工具 handler 放入子进程执行；父进程用 pipe/queue 接收兼容旧协议的 JSON 字符串。
   - 父进程等待期间短间隔轮询 `cancel_event`；置位后先 `terminate()`，必要时 `kill()`，再抛出 `AgentInterrupted`。
   - 工具异常、超时、无返回和不可 JSON 序列化结果应转成 JSON error，而不是破坏 Agent Loop。
   - 优先使用 `fork` 可保留动态注册 handler；不支持 fork 时回退到模块工具重载，需接受动态/不可 pickle handler 兼容性下降。

5. **CLI 层并发执行与状态提示**
   - 用后台线程运行 Agent callable。
   - 主线程维持 Rich status，并在 TTY 下监听 Esc。
   - 检测到 Esc 时立即打印用户可见反馈（如 `esc 中断`）并 set cancel_event。
   - 捕获 `AgentInterrupted` 后给出明确提示并返回主输入循环。
   - 用统一 helper 给默认等待、思考中、模型重试、工具执行状态追加“按 Esc 中断”，并避免重复追加。

6. **终端按键监听注意事项**
   - 若不在 prompt_toolkit prompt 中，给 prompt key binding 无效。
   - 在普通 TTY 下可用 `select` + `termios`/`tty.setcbreak` 做简单单键监听，并在 finally 中恢复终端属性。
   - 非 TTY 环境应降级为不监听按键，但不能影响正常执行。

7. **验证**
   - 先运行 `python3 -m py_compile` 覆盖修改文件。
   - 用 fake LLM client 模拟请求期间 set cancel_event，断言抛出 `AgentInterrupted` 且 `messages` 已回滚。
   - 续跑也要测试回滚追加的续跑 user 指令。
   - 用长耗时 fake tool 验证工具在子进程执行、cancel_event 会终止子进程并抛出 `AgentInterrupted`。
   - 测试状态提示 helper：自动追加“按 Esc 中断”且不会重复追加。
   - 若环境缺少 pytest，可手动导入测试函数或 smoke test 执行，但需在最终说明中标注完整 pytest 未运行的原因。

## Known Limitations

- 已隔离的长耗时工具可以由父进程 terminate/kill；同步阻塞的 LLM HTTP 请求仍通常只能在请求返回后的边界响应，不能真正底层 abort。
- 如果需要即时取消模型请求，应进一步引入异步/流式请求、短 timeout 或支持取消的 HTTP client。
- 进程隔离在 fork 平台对动态注册工具兼容性较好；spawn 平台更依赖模块级工具重载，动态闭包 handler 可能不可用。
- 后台线程方案要避免在用户中断后继续写入 `messages`；核心层必须在写入 assistant/tool 前后检查取消信号并集中回滚。