---
name: "cli_voice_and_background_shutdown_debugging"
description: "修复 CLI 语音输入与后台任务退出卡死"
---

# CLI Voice and Background Shutdown Debugging

## When to Use
- `/bbb` 语音输入偶发没有录音、按 Enter 后卡住、转写空 WAV 或半写入 WAV。
- 用户输入 `exit` / `quit` 后 CLI 卡死或退出很慢。
- CLI 后台线程中运行 self-evolution review、子 Agent 或工具隔离子进程，怀疑与 macOS fork / multiprocessing / Rich prompt_toolkit 竞争有关。

## Procedure
1. 先审计 `main.py` 的 `/bbb` 链路：`_record_audio_with_command()`、`_record_audio_until_keypress()`、`capture_voice_input()`。
2. 对系统录音命令避免使用未消费的 `stderr=subprocess.PIPE`；若不持续 drain stderr，优先改为 `subprocess.DEVNULL`，否则 ffmpeg/sox 日志可能填满 pipe 导致录音进程阻塞。
3. 录音停止时必须完整兜底：
   - 设置 `stop_event`。
   - 恢复 tty。
   - `thread.join(timeout=...)` 后检查 `thread.is_alive()`；仍存活时明确报错，不要继续转写。
   - `terminate()` / `kill()` 后的 `wait(timeout=...)` 也要捕获二次 `TimeoutExpired` 并转成可读错误。
4. 审计 `core/config.py:get_self_evolution_review_interval()` 与 `core/agent.py` 调度条件；如果配置注释是 `<=0 表示关闭`，调度条件必须包含 `interval > 0`。
5. 为 `RAgent` 增加后台任务生命周期管理：线程列表、锁、shutdown event、`shutdown_background_tasks(timeout)`；调度前检查 shutdown event，退出时短暂 join。
6. CLI 的 `exit` / `quit` / `KeyboardInterrupt` / `EOFError` 路径要调用 agent shutdown，避免只 break 主循环。
7. 自动后台 self-evolution review 若只是 CLI 空闲复盘，优先降级为 heuristic dry-run，避免在后台线程中再启动 review Agent 或隔离工具子进程；如需 forked review，建议由显式 tool call 或独立进程触发。
8. 补充回归测试：
   - interval=0 不触发 self review。
   - interval>0 可调度后台复盘。
   - shutdown_background_tasks 设置 shutdown 并 join。
   - 录音命令 stderr 使用 DEVNULL。
   - kill 后 wait 超时能报清晰 RuntimeError。
   - 录音线程 join 后仍存活会报错。
   - CLI 退出 helper 会调用 agent shutdown。
9. 运行 `PYTHONPATH=. pytest` 和 `git diff --check` 验证。

## Notes
- 后台线程中的异常不要裸 print 到 CLI；可保存到内部 `_background_errors` 或日志，避免污染 prompt_toolkit 输入行。
- macOS 多线程环境中 fork 子进程风险较高；尽量避免“后台线程 -> Agent -> isolated tool process”的嵌套结构。
