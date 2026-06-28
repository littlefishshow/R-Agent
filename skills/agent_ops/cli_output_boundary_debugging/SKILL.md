---
name: "cli_output_boundary_debugging"
description: "修复 CLI/后台 Agent 输出串线与 stdout 污染"
---

# CLI Output Boundary Debugging

## When to Use

- R-Agent CLI 已显示 `You>` 输入提示后，后台线程又输出 `[Tool Call]`、`[Tool Result]`、`正在思考`、重试日志等内容。
- Rich `console.status(...)`、prompt_toolkit `rprompt`、后台 Agent 或子 Agent 的 stdout 互相串线。
- 需要明确“CLI 层负责用户可见输出，core/background Agent 默认静默或写日志”的边界。

## Procedure

1. **定位裸 stdout 输出**
   - 搜索：`print(`、`[Tool Call]`、`[Tool Result]`、`正在思考`、`模型瞬时错误`。
   - 重点检查 `core/agent.py` 的 fallback print，以及后台工具/子 Agent 是否未传 UI callback。

2. **收敛输出责任边界**
   - `core/agent.py` 默认不直接打印思考、工具调用、工具结果或重试状态。
   - 可见输出只通过 `on_think`、`on_tool_start`、`on_tool_end` 等回调交给 CLI 层处理。
   - 后台 Agent 必须显式传入 no-op callback，或把结果写入日志文件。

3. **修后台 Agent**
   - 在后台模块中定义：
     ```python
     def _noop_callback(*args, **kwargs) -> None:
         return None
     ```
   - 调用 `run_conversation(...)` 时传入：
     ```python
     on_think=_noop_callback,
     on_tool_start=_noop_callback,
     on_tool_end=_noop_callback,
     ```

4. **验证**
   - 搜索确认核心层无相关裸 `print`。
   - 跑相关单测，例如：`PYTHONPATH=. pytest tests/test_self_evolution_review.py tests/test_token_usage_display.py -q`。
   - 如改动影响 Agent 全局行为，跑 `PYTHONPATH=. pytest -q`。

5. **文档**
   - 维护 R-Agent 项目时，在 `README.md` 更新日志按日期记录 CLI 输出边界、后台静默、日志归档等改动。

## Notes

- prompt_toolkit 的 `rprompt` 与后台 stdout 同时写终端时，容易表现为 token 提示和 `[Tool Call]` 出现在同一行。
- Rich status 残留和后台裸 print 是不同机制，但都应通过“CLI 层唯一负责可见输出”来降低风险。
