# Project Progress Context — token-usage-display

Created: 2026-06-25 19:28:06
---

## Progress Entry — 2026-06-25 19:28:06

### Project

token-usage-display

### Summary

实现每次回复显示本次 Agent 启动以来累计 token 用量

### Current Status

已完成 core/agent.py usage 累计、main.py 输入框右侧与回复面板右下角显示、测试与 README 更新

### Key Files / Code Locations

- `core/agent.py`
- `main.py`
- `tests/test_token_usage_display.py`
- `README.md`

### Decisions / Context

采用 response.usage 作为可信来源；兼容 dict 与对象 usage；无 usage 时显示 tokens: unavailable；Rich Panel 使用 subtitle_align=right 实现右下角显示。

### Verification

PYTHONPATH=. pytest -q tests/test_token_usage_display.py tests/test_status_hint.py tests/test_agent_interrupt.py 通过；python3 -m py_compile core/agent.py main.py 通过。

### Unfinished / Next Steps

如需统计多个 RAgent 实例共享的进程级 token，可后续改为模块级全局累计器；当前 CLI 主实例已满足用户需求。
