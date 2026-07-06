# Project Progress Context — delegated-todo-context-management

Created: 2026-07-06 14:59:18
---

## Progress Entry — 2026-07-06 14:59:18

### Project

delegated-todo-context-management

### Summary

正在重构父子任务调度与上下文管理：已先提交检查点 bfeb470；当前未提交改动实现 delegate_task 不再回灌子 Agent messages、todo_manage digest、子上下文 artifact、整体 todo 成功后统一清理上下文，以及系统提示中的父进程只调度/只读 digest 策略。

### Current Status

未最终提交；代码与测试已更新，当前全量测试通过 211 passed, 8 skipped, 1 warning。用户刚纠正关键语义：并非子进程成功就丢弃上下文，而是整个 todo tree 全部 completed 后才统一清理所有子进程上下文；代码和 README/prompt 已按此修正。

### Key Files / Code Locations

- `tools/delegate_tool.py`
- `tools/todo_tool.py`
- `core/prompt_builder.py`
- `tests/test_delegate_progress.py`
- `tests/test_todo_session_isolation.py`
- `README.md`

### Decisions / Context

关键设计：父进程默认只维护自己的对话上下文和 todo_digest，不读取子进程完整运行信息；delegate_task 返回 {tasks,todo_digest,note}，不再返回 sub_agent_messages。每个子 Agent 完成后将 messages 保存到 sandbox/delegate_contexts/<session>/...json，并在 todo metadata/digest 中以 context_artifact_path 引用；父进程仅在失败/超时/需要诊断时显式读取 artifact。整体 todo 全部 completed 时，delegate_task 调用 _cleanup_all_completed_contexts(session_id) 删除该 session 的上下文 artifact 并清理 metadata。todo_manage 新增 digest action，返回状态、ready、摘要、blocked_reason、split_proposal 和 context_artifact_path。core/prompt_builder.py 新增 Delegated todo context policy，要求复杂/需工具任务优先 todo_manage + delegate_task，父进程只调度/审批/汇总 digest。

### Prior Context Considered

(no previous context, or previous context intentionally omitted)

### Verification

已运行 python3 -m pytest tests/test_delegate_progress.py tests/test_todo_session_isolation.py -q，结果 12 passed；已运行 python3 -m pytest -q，结果 211 passed, 8 skipped, 1 warning（PytestUnknownMarkWarning: comfyui cloud）。

### Unfinished / Next Steps

继续时先检查 git diff；重点复核整体完成后统一清理上下文的实现是否完全符合预期，包括成功但 todo tree 未完成时 context_artifact_path 是否应对父进程可见、整体 completed 后返回结果中的 context_artifact_path 是否应置空、是否需要为 _cleanup_all_completed_contexts 增加更精确测试；随后可考虑补充父进程自动使用 todo_manage/digest 的更强约束或调度辅助。最后如本轮改动确认完成，需要更新 README（已初步更新）并 commit。
