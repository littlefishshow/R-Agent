---
name: "autoresearch_mode_minimal_loop"
description: "历史说明：旧 R-Agent 小型 autoresearch 最小闭环已被顶层 autoresearch 包替代"
---

# Autoresearch Mode Minimal Loop

## Status

这个 skill 是历史记录，不再作为当前实现指南使用。

当前 autoresearch 实现已经迁移到顶层 `autoresearch/` 包：

- `autoresearch/tool.py`：真实工具注册和 handler。
- `autoresearch/phases.py`：V3 `run_phase_loop` 入口。
- `autoresearch/controller.py`：`plan -> attempt -> conclude` 控制器。
- `tools/autoresearch_tool.py`：仅保留为工具注册 shim。

当前 CLI 控制面：

- `/autoresearch run <项目目录>`
- `/autoresearch show [项目目录]`
- `/autoresearch debug [on|off|show] [项目目录]`
- `/autoresearch kill`

不要再新增或维护 `core/autoresearch.py` 小闭环作为主入口；旧 `run_autoresearch_cycle()` 路径只应作为历史参考。

## Historical Notes

以下内容描述的是早期最小闭环方案，仅用于理解历史演进。

## Procedure

1. **先定位 CLI 与中断边界**
   - `main.py` 是 CLI 入口，已有 `PromptSession`、本地斜杠命令、`_run_with_esc_interrupt()`。
   - `core/agent.py` 已有 `AgentInterrupted` 与 `cancel_event`，可复用取消语义。
   - autoresearch 不应默认进入普通 LLM Agent Loop，而应作为本地 mode 先跑受控状态机。

2. **旧核心实现位置**
   - 新增或维护 `core/autoresearch.py`。
   - 暴露 `run_autoresearch_cycle(project_path, objective, cancel_event, on_status)`。
   - 第一版只做串行 `Plan → Execute → Conclude`。

3. **状态目录约定**
   - 在目标项目下创建 `.autoresearch/`。
   - 至少维护：
     - `state.json`
     - `plan.json`
     - `execute_result.json`
     - `conclude_result.json`
     - `memory.md`
     - `lessons.md`
     - `results.tsv`
     - `runs/exp_xxx/`
   - Main/CLI 只展示摘要，不读取大段日志。
   - 调试归档应包含 `.autoresearch/traces/trace.jsonl`、按 worker 分类的 `plan.jsonl` / `execute.jsonl` / `conclude.jsonl`、人类可读 `flow.md`、以及 `traces/contexts/` 中的 Main/Plan/Execute/Conclude 上下文快照；每轮 `runs/exp_xxx/` 也保留 trace/flow/context 副本，便于回放单轮问题。

4. **第一版安全边界**
   - Plan 只规划，不改代码。
   - Execute 只执行短时、安全、只读命令。
   - Conclude 解析日志和 returncode，输出 `keep/crash`，写 lessons/results。
   - 不自动大改代码、不长训练、不下载大文件、不自动 `git reset --hard`、不无限循环。

5. **CLI 接入方式**
   - 在 `main.py` 增加 `/autoresearch` 补全和 `/help` 文案。
   - 支持 `/autoresearch` 后提示输入项目路径，以及 `/autoresearch /path/to/project` 直接启动。
   - 运行时使用 `_run_with_esc_interrupt()`，使输入锁定且 Esc 可中断。
   - 捕获 autoresearch 专用中断异常并提示用户。

6. **文档要求**
   - 维护 `autoresearch.md`：用中文、小学生也能懂的方式解释 mode、worker、文件和安全边界。
   - 维护 R-Agent 项目时同步更新 `README.md` 日期更新日志，写清本次升级内容。

7. **当前验证参考**
   - 运行 `python3 -m compileall -q main.py autoresearch tools/autoresearch_tool.py tests/test_autoresearch_mode.py`。
   - 定向运行 `python3 -m pytest tests/test_autoresearch*.py -q`。
   - 如果完整 `pytest -q` 因环境缺依赖失败，最终说明要标注失败原因和是否与本次改动无关。

## Notes

- 对非 git 临时目录，不要把 `git status` 固定为必跑且作为失败；可以只在 `.git/` 存在时加入该命令。
- 测试不要要求日志中完全不出现某个安全边界字符串；更稳妥的是检查 notes 明确说明没有自动修改代码/没有高风险动作。
