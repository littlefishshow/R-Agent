# Project Progress Context — hermes-self-evolution

Created: 2026-06-25 17:34:43
---

## Progress Entry — 2026-06-25 17:34:43

### Project

hermes-self-evolution

### Summary

本轮完成 Hermes Agent 自演进机制融入 R-Agent 的最小闭环：skill_manage、skill_view supporting files、usage telemetry、archive_subtask 真压缩、self_evolution_review dry-run、deterministic curator，并更新 README 与 outputs 进度文档。

### Current Status

代码已修改并通过新增测试；待最终 git commit。

### Key Files / Code Locations

- `core/skills.py`
- `tools/skills_tool.py`
- `core/skill_usage.py`
- `tools/self_evolution_tool.py`
- `tools/skill_curator_tool.py`
- `core/agent.py`
- `tests/test_skill_curator_tool.py`
- `README.md`
- `outputs/hermes_self_evolution_upgrade_progress_2026-06-25.md`

### Decisions / Context

参考 ../repos/hermes-agent 的 tools/skill_usage.py、tools/skill_manager_tool.py、hermes_cli/curator.py；R-Agent 采用更保守的 dry-run self_evolution_review，不自动写 memory/skill；curator 只处理 created_by 为 foreground_agent/background_review/agent 的记录，pinned 跳过。

### Verification

python3 -m pytest tests/test_self_evolution_skill_manage.py tests/test_archive_subtask_compression.py tests/test_skill_curator_tool.py -q => 4 passed；registry.reload_all 可看到 self_evolution_review、skill_manage、skill_curator_*。

### Unfinished / Next Steps

提交后下一阶段：把 self_evolution_review 升级为受限子 Agent；curator 增加 report/backup/rollback；接入 compaction flush 与 active memory recall。
