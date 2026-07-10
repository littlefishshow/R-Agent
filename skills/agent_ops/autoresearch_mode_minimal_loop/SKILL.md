---
name: "autoresearch_mode_minimal_loop"
description: "实现 R-Agent 小型 autoresearch 最小闭环"
---

# Autoresearch Mode Minimal Loop

## When to Use

- 用户要求为 R-Agent 增加或维护小型 autoresearch mode。
- 需要实现受控研究闭环：Plan → Execute → Conclude。
- 需要接入 CLI 本地命令、状态目录、Esc 中断和最小验证。

## Procedure

1. **先定位 CLI 与中断边界**
   - `main.py` 是 CLI 入口，已有 `PromptSession`、本地斜杠命令、`_run_with_esc_interrupt()`。
   - `core/agent.py` 已有 `AgentInterrupted` 与 `cancel_event`，可复用取消语义。
   - autoresearch 不应默认进入普通 LLM Agent Loop，而应作为本地 mode 先跑受控状态机。

2. **核心实现位置**
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

7. **验证**
   - 运行 `python3 -m py_compile main.py core/autoresearch.py tests/test_autoresearch_mode.py`。
   - 定向运行 `pytest -q tests/test_autoresearch_mode.py tests/test_status_hint.py`。
   - 可用临时目录 smoke test 调 `run_autoresearch_cycle()`，确认 `.autoresearch/state.json` phase 为 `completed`。
   - 如果完整 `pytest -q` 因环境缺依赖失败，最终说明要标注失败原因和是否与本次改动无关。

## Notes

- 对非 git 临时目录，不要把 `git status` 固定为必跑且作为失败；可以只在 `.git/` 存在时加入该命令。
- 测试不要要求日志中完全不出现某个安全边界字符串；更稳妥的是检查 notes 明确说明没有自动修改代码/没有高风险动作。
