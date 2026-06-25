---
name: "delegate_terminal_progress_debugging"
description: "诊断修复 delegate 看板显示与并发状态问题"
---

# Delegate Terminal Progress Debugging

## When to Use

- `delegate_task` 终端看板与 Rich spinner 黏在同一行，尤其是 CLI 精简模式下。
- `delegate_task` 返回 `Tool process ended without returning a result`。
- 多个子 Agent 并发 `claim/update` 后，Todo 看板最终进度不稳定，例如子任务已打印 completed，但最终快照仍显示 `0/N`、任务消失或状态回退。
- 需要为 delegate/todo 看板修复补充回归测试。

## Procedure

1. **区分快照语义**
   - `Delegate 启动前任务快照` 本来就发生在子 Agent 执行前，显示 `0/N` 可能是正常语义。
   - 如果 `Sub-Agent 结束后` 或 `最终任务快照` 仍显示旧进度，再排查状态写入与并发问题。

2. **修复 Rich status 与看板输出冲突**
   - CLI 层维护当前活动的 Rich status 引用，例如 `ACTIVE_STATUS`。
   - 工具打印 Rich Panel 或子 Agent 日志前临时 `status.stop()`，打印后再 `status.start()`。
   - 在 `__main__` 运行时不要只 `import main` 查状态；优先从 `sys.modules['__main__']` 和 `sys.modules['main']` 查找。

3. **避免把 delegate_task 放进隔离工具子进程执行**
   - `delegate_task` 本身是调度器，会启动线程/子 Agent；子 Agent 还会调用工具。
   - 若走 `execute_tool_isolated()`，容易形成“隔离工具进程 -> 线程池 -> 子 Agent -> 工具子进程”的嵌套 fork/线程结构，在 macOS 下可能无结果退出。
   - 在 Agent loop 中对 `delegate_task` 特判为父进程内 `registry.execute_tool()`，普通工具仍走隔离执行。

4. **修复 Todo 并发写覆盖**
   - 对 `todo_manage` 的完整 action 加锁，而不是只锁 `_save_state()`。
   - 同一进程内使用 `threading.RLock()`，跨隔离工具进程时使用文件锁（Unix/macOS 可用 `fcntl.flock`）。
   - 保存 JSON 时写入临时文件，再用 `os.replace()` 原子替换，避免快照读到半写文件。

5. **补充回归测试**
   - 用 fake `RAgent` 模拟子 Agent 并发 claim/update，不依赖真实 LLM。
   - 覆盖截断子 Agent 自动把 `in_progress` 标记为 `blocked`。
   - 覆盖最终快照显示 `完成进度：N/N (100.0%)`。
   - 覆盖 Todo Progress 面板明确展示“✅ 已完成任务”和“🕓 未完成任务”明细；未完成列表应列出 pending/in_progress/blocked/needs_split/failed/cancelled 等具体任务 id、描述、状态和分配信息，不能只显示总数、状态计数或 ready id。
   - 覆盖 status-safe print 会调用 `stop()` 与 `start()`。

## Verification

建议运行：

```bash
python3 -m py_compile core/agent.py main.py tools/delegate_tool.py tools/todo_tool.py
PYTHONPATH=. pytest -q tests/test_delegate_progress.py tests/test_status_hint.py tests/test_tool_process_isolation.py
```

## Notes

- `0/N` 只在启动前快照中出现不一定是 bug；最终快照不正确才是状态同步问题。
- `todo_manage` 的并发一致性要考虑两种来源：同进程 ThreadPoolExecutor 和隔离工具子进程。