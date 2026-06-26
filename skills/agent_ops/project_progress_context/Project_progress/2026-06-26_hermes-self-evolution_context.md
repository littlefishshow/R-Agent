# Project Progress Context — hermes-self-evolution

Created: 2026-06-26 15:12:25
---

## Progress Entry — 2026-06-26 15:12:25

### Project

hermes-self-evolution

### Summary

完成 P0：self_evolution_review 支持受限 forked/background review Agent；RAgent 增加 allowed_tools/tool_call_guard/enable_self_review；新增可运行 demo。

### Current Status

代码已修改并通过核心测试；当前工作区还包含此前 Todo 看板相关未提交改动，需提交前按范围拆分确认。

### Key Files / Code Locations

- `core/agent.py`
- `tools/self_evolution_tool.py`
- `tests/test_self_evolution_review.py`
- `sandbox/self_evolution_review_demo.py`
- `README.md`

### Decisions / Context

安全边界：后台 review 子 Agent 仅暴露 memory/memory_search/memory_get/skill_categories/skills_by_category/skill_view/skill_manage；dry_run=true 拒绝 memory 与 skill_manage 写入，仅允许只读和 skill_manage usage；子 Agent enable_self_review=false 避免递归。

### Verification

python3 -m py_compile core/agent.py tools/self_evolution_tool.py tests/test_self_evolution_review.py sandbox/self_evolution_review_demo.py；python3 -m pytest tests/test_self_evolution_review.py tests/test_self_evolution_skill_manage.py tests/test_archive_subtask_compression.py tests/test_skill_curator_tool.py -q => 11 passed；python3 sandbox/self_evolution_review_demo.py 成功输出 review_final_preview 并写 outputs/self_evolution/latest_review.json。

### Unfinished / Next Steps

提交前检查与 Todo 看板原地刷新改动是否拆分提交；可继续增强非 dry-run 审批开关、CLI 展示 review summary、curator 受限 Agent 化。
