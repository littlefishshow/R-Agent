# Project Progress Context — auto-research-agent-loop

Created: 2026-07-07 11:22:10
Last compacted: 2026-07-07 11:49:29
---

## Progress Entry — 2026-07-07 11:49:29

### Project

auto-research-agent-loop

### Summary

继续增强 auto_research：加入安全 apply_patch action、结构化 metrics/results.tsv 记录、progress.md 日志 tail 与 ETA。

### Current Status

已完成：Decision/AutoResearchAction 增加 apply_patch/patch；新增 apply_unified_patch_limited 项目内最小 unified diff 应用；默认 workflow 增加 apply_change step；execute_action 支持 apply_patch；新增 _record_metric 将 primary_metric 写入 state.metrics/state.baseline_metric 与 results.tsv；AutoResearchProgressView 增加 ETA 与最近日志 Tail；README 已更新。

### Key Files / Code Locations

- `core/autoresearch_loop.py`
- `tests/test_autoresearch_tool.py`
- `README.md`
- `skills/agent_ops/project_progress_context/Project_progress/2026-07-07_auto-research-agent-loop_context.md`

### Decisions / Context

安全边界：apply_patch 只支持项目内文本 unified diff 的创建/修改，拒绝删除、binary、绝对路径和 ../；默认 auto_commit 仍关闭。metrics 解析是 best-effort，当前从 shell/read/note raw 中解析 primary_metric 等字段，baseline rationale 会设置 baseline_metric，后续 experiment 与 baseline 比较生成 keep/discard/neutral/needs_metrics。progress.md 从最近 artifact 的 stdout/stderr 或文本中抽取 tail，并用 step 平均耗时估算 ETA。

### Prior Context Considered

Previous saved context was compacted before this save; full old entries were not appended verbatim.
- **Summary**: 继续完成 auto_research 升级：补齐专业 step prompt、JSON fence/内嵌 JSON 提取、实验循环骨架、文字进度界面和后台非阻塞运行。
- **Current Status**: 已完成：1) Fixed workflow 扩展到 baseline/propose/run/parse/record 九步；2) AutoResearchStepAgent 增加 STEP_GUIDANCE；3) 新增 extract_json_object/parse_primary_metric/decide_experiment/extract_progress_percent；4) 新增 AutoResearchProgressView 写 .autoresearch/progress.md；5) auto_research_run 支持 background=true，以独立 Python 子进程后台运行；6) 新增 auto_research_status 查询 progress 预览；7) README 2026-07-07 日志已更新。
- **Key Files / Code Locations**: - `core/autoresearch_loop.py` - `tools/autoresearch_tool.py` - `tests/test_autoresearch_tool.py` - `README.md` - `skills/agent_ops/project_progress_context/Project_progress/2026-07-07_auto-research-agent-loop_context.md`
- **Decisions / Context**: 用户要求前三点功能并要求 auto_research 不阻塞 R-Agent，且提供直观文字可视化。实现决策：后台模式使用 subprocess.Popen 启动独立 Python 子进程而不是 daemon thread，避免 R-Agent 工具调用返回后后台线程随隔离工具进程退出；status 通过 .autoresearch/run_<run_id>.json 和 progress.md 跨进程读取。可视化完全使用 Markdown 文本进度条。实验 keep/discard 目前为解析/记录骨架，auto_commit 默认关闭，避免自动提交或破坏性回滚。
- **Verification**: 已运行：python3 -m py_compile core/autoresearch_loop.py tools/autoresearch_tool.py；python3 -m pytest -q tests/test_autoresearch_loop.py tests/test_autoresearch_tool.py tests/test_tool_process_isolation.py => 17 passed；python3 -m pytest -q => 235 passed, 8 skipped, 1 warning（既有 pytest.mark.cloud warning）。
- **Unfinished / Next Steps**: 后续可做：1) 增强真实 apply-change/edit patch 能力与 rollback；2) 将 metric baseline/experiment 历史结构化保存到 results.tsv 或 state.metrics；3) 为 progress.md 增加更细训练日志 tail/ETA；4) 增加 CLI/slash command；5) 提交前确认未跟踪 skills/productivity/autoresearch/ 是否纳入 git。

### Verification

已运行：python3 -m py_compile core/autoresearch_loop.py tools/autoresearch_tool.py；python3 -m pytest -q tests/test_autoresearch_loop.py tests/test_autoresearch_tool.py tests/test_tool_process_isolation.py => 20 passed；python3 -m pytest -q => 238 passed, 8 skipped, 1 warning（既有 pytest.mark.cloud warning）。

### Unfinished / Next Steps

后续可做：1) 更完整的 patch apply（多文件复杂 hunk、dry-run diff 预览）；2) 真实 rollback/commit 策略；3) 训练日志 tail 实时流式刷新和 ETA 基于日志 step/epoch；4) CLI/slash command；5) 准备提交前确认未跟踪 skills/productivity/autoresearch/。
