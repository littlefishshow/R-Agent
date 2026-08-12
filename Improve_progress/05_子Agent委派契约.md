# 05 · 子 Agent 委派契约

**状态：✅ 已完成（2026-08-11）**
**对应 deer-flow 学习文档：** 第 8 章（Sub-agent 系统）+ 13.4（子 Agent 要有结构化 contract）
**建议顺序：** 第 6 步（R-Agent 这块已最成熟，属收尾增强）
**依赖：** `02_ThreadState结构化状态`（结果写入 `delegation_ledger`）、`08_运行事件流`（`delegate.start/step/end` 事件）。

---

## 1. 要解决什么问题（R-Agent 现状）

这是 R-Agent **最成熟**的子系统，核对过代码，已经相当完整：

已有（`tools/delegate_tool.py:delegate_task` L519）：
- 每个子任务结果带：`task_id`、`status`(completed/blocked/error/timeout)、`result`、`truncated`、`blocked_update`、
  `context_artifact_path`、`token_usage`、`task_index`。
- 预算：每任务 `max_iterations`（1–200 clamp）、`default_wall_timeout_seconds`（`config.get_delegate_task_wall_timeout()`）、`max_workers` 并行。
- 子 agent 是每任务全新 `RAgent`（`_run_subagent`）；截断自动标 `blocked`；超时标 `timeout`；有模型失败检测。
- 每个子任务必须绑定 todo-list `task_id`；in-process 执行（规避 macOS 嵌套 fork 崩溃，见 `core/agent.py:998`）。
- `return_mode` compact/full 控制 payload；step 事件通过 `event_sink`（`_emit_delegate_event`）单独走。

缺口（对照 deer-flow / 13.4）：
1. **`status` 与 `stop_reason` 混在一起**：现在 `blocked/timeout` 既是状态也是原因；deer-flow 建议单列 `stop_reason`（`token_capped/turn_capped/loop_capped`）。
2. **`step_events` 没内嵌进结果**：step 事件只在 `event_sink` 里飘，结果对象里没有可回放的 `step_events` 数组。
3. **无显式 loop detection**：目前靠 `max_iterations` + turn 预算兜底，没有"检测到重复动作就早停"的原语。
4. **结果未沉淀进 state channel**：子任务上下文落在 `sandbox/delegate_contexts/`，但没有统一的内存 `delegation_ledger`（依赖 `02`）。

---

## 2. deer-flow 是怎么做的

- 文件：`subagents/registry.py`（注册 built-in/custom）、`subagents/executor.py`（`SubagentExecutor` 状态机 + 后台执行）、`tools/builtins/task_tool.py`（父 → 子委派）、`agents/middlewares/subagent_limit_middleware.py`（运行时强制限流）。
- 结果是**结构化 contract**（13.4 原文）：
  ```text
  task_id / subagent_type / status(pending|running|completed|failed|cancelled|timed_out)
  result / error / stop_reason(token_capped|turn_capped|loop_capped)
  usage / started_at / completed_at / step_events
  ```
- 每次委派会产生：live custom events、run event store（`subagent.start/step/end`）、token usage 回写父 journal、`delegations` ledger（压缩后仍可通过 durable context 回忆）。
- 子 Agent 不允许递归调用 task（防无限套娃，见 15 章 checklist 第 6 条）。

---

## 3. R-Agent 打算怎么改（简略步骤）

R-Agent 已经 80% 到位，这里是"补齐契约 + 沉淀 ledger"，不重写。

1. **拆分 `stop_reason`**：在结果里新增 `stop_reason` 字段，从现有 `status`+截断/超时信息映射出 `turn_capped`（达 max_iterations）/`timeout`（wall clock）/`token_capped`（预算）/`loop_capped`（见第 3 步）。`status` 只表达最终态。
2. **内嵌 `step_events`**：把本来只走 `event_sink` 的 step 事件同时收集进结果对象的 `step_events`（可截断/采样），便于父 Agent 回放，无需依赖 GUI。
3. **加轻量 loop detection**：在子 agent 循环里检测"连续 N 次相同 tool_call（同名+同参）"→ 早停并置 `stop_reason=loop_capped`。可复用主循环，未来与 `01` 的 middleware 共享。
4. **沉淀 `delegation_ledger`**（依赖 `02`）：把每次委派的结构化结果写进 `ThreadState.delegation_ledger`，压缩后通过 durable context（`03`）回注。
5. **补时间戳**：`started_at`/`completed_at`，对齐 deer-flow contract，也方便 `08` 事件流关联。
6. **确认防递归**：核对子 agent 的工具集是否已排除 `delegate`（不允许子 Agent 再委派）；若没有，补上。

> 关键约束：`delegate_task` 现有对外返回结构要**向后兼容**——新字段是增量添加，老字段语义不变，避免打断现有调用方（如 AutoResearch）。

### 本轮已落地（✅） / 待做（⬜）

> 实测发现 R-Agent 真实 `status` 取值为 `success` / `truncated` / `error` / `timeout`（计划里的 `blocked` 猜测不准，已按真实值实现）。

- ✅ **步骤 1 · stop_reason（附加字段，不改 status）**：`tools/delegate_tool.py` 新增 `_derive_stop_reason()` + `_normalize_result_contract()`，在 `delegate_task` 结果排序后统一补齐 `stop_reason`：`success→completed` / `truncated→turn_capped` / `timeout→timeout` / `error→error` / 循环命中→`loop_capped`（显式优先）。`status`/`truncated` 等既有字段一律不动。`_compact_delegate_item` 也带出 `stop_reason`（compact/full 两种 return_mode 都有）。
- ✅ **步骤 3 · loop detection（loop_capped）**：`core/middleware/builtins.py:LoopDetectionMiddleware`（复用 `01` 框架）在 `before_tool` 检测"连续 N 次相同工具调用（同名+同参）"，达阈值即否决该次调用并在子 agent 上打 `_loop_capped` 标记；`_run_subagent` 据此把 `stop_reason` 记为 `loop_capped`。委派子 Agent **默认启用**循环保护（`LOOP_DETECTION_ENABLED` 默认开，阈值 `LOOP_DETECTION_THRESHOLD` 默认 3）。
- ✅ **步骤 5 · 时间戳**：`_run_subagent` 在子任务开始/结束记录 `started_at`/`completed_at`，加入结果 item（对齐 deer-flow contract，也方便与 `08` 事件流关联）。
- ✅ **步骤 6 · 防递归**：核对确认 `DELEGATE_CHILD_EXCLUDED_TOOLS` 已含 `delegate_task`——子 Agent 无法再委派，本轮无需改动。
- 🔨 **步骤 4 · delegation_ledger 沉淀**：父 Agent 侧已在 `02` 章落地——`core/agent.py` 主循环把 `delegate_task` 返回结果写入 `ThreadState.delegation_ledger`，`03` 章的 durable context 会回注。本章的 `stop_reason` 会随结果一并进入 ledger。
- ✅ **步骤 2 · step_events 有界内嵌**：`_run_subagent` 现在采样 `llm.step / tool.start / tool.end`，记录轮次、工具名、参数/结果短预览和时间戳；默认每个子任务最多 32 条（`DELEGATE_STEP_EVENTS_LIMIT`，0 可关闭，最大 200）。compact/full 返回与 `delegation_ledger` 都保留这些事件，但不返回完整子 Agent transcript。

---

## 4. 为什么这样改

- **为什么 `stop_reason` 与 `status` 分家、且只增不改**：`status` 是既有对外契约（AutoResearch、todo 流转、GUI 都读它），语义必须冻结。`stop_reason` 是 deer-flow 风格的**附加**诊断维度——同一个 `truncated` 状态，用 `stop_reason=turn_capped` 说清"为什么停"。用集中式 `_normalize_result_contract` 在返回前统一补齐（而不是改 5 个 item 构建点），既减少改动面又保证每条结果都有。
- **为什么按真实 status 实现而非计划猜测**：精读代码发现真实取值是 `success/truncated/error/timeout`，计划文档里的 `blocked` 是对 todo 状态的误记。以真实代码为准，避免映射错位——这也是"每次动手前先读真实代码"的价值。
- **为什么 loop detection 用中间件实现、且子 Agent 默认开**：它天然是 `before_tool` 的横切逻辑，正好落在 `01` 的框架上，父/子循环可共享同一实现。子任务是**自动执行、无人盯着**的，最容易陷入"同一工具反复调用"的死循环，所以对子 Agent 默认开启（阈值 3：偶发两次重试是正常的，连续第 3 次同名同参才判定循环）；父 Agent（交互式、有人看着）默认不装，避免误伤正常的重复操作。
- **为什么否决而非直接杀进程**：命中循环时返回一段说明作为工具结果，让模型"看到"自己在打转并有机会改变策略；同时打 `_loop_capped` 标记供上层如实报告。这比硬中断更温和，也保留了子 agent 自我纠偏的可能。
- **为什么先不做 step_events**：它需要在子 agent 内收集事件流并采样回传，改动面和数据量都比其余步骤大，收益偏"锦上添花"。当前 `08` 的事件流已能落盘子 agent 的 `delegate.start/end`，回放需求已基本覆盖，所以把 step_events 内嵌排到最后。

---

## 5. 测试示例

新增 `tests/test_delegate_contract.py`，5 个用例全部通过：

1. `test_derive_stop_reason_mapping` —— 五种映射 + 显式 stop_reason 优先。
2. `test_normalize_only_adds_fields` —— 补 `stop_reason` 但 `status/truncated/task_id` 不变。
3. `test_truncated_task_reports_turn_capped` —— **端到端**：截断任务 compact 返回里 `status=truncated` 且 `stop_reason=turn_capped`。
4. `test_loop_detection_reports_loop_capped` —— **端到端**：陷入循环的子任务返回 `stop_reason=loop_capped`。
5. `test_timestamps_present_full_mode` —— full 模式含 `started_at`/`completed_at` 且 `completed_at >= started_at`。

另有 `LoopDetectionMiddleware` 的单元覆盖（连续 3 次同签名被否决、不同签名重置计数）。

**你可以亲手验证：**

```bash
cd /Users/bytedance/myenv/hermes/R-Agent

# 1) 本章测试
python3 -m pytest tests/test_delegate_contract.py -q          # 5 passed

# 2) 既有委派行为不变
python3 -m pytest tests/test_delegate_progress.py -q          # 8 passed

# 3) 零回归子集
python3 -m pytest tests/ -q -k "delegate or middleware or agent or todo or memory or context or event"
# -> 230 passed（另 3 个 autoresearch 用例失败，git stash 已证与本次改动无关）
```

**契约字段示例**（一条截断子任务的 compact 结果）：

```json
{
  "task_index": 0,
  "task_id": "t1",
  "status": "truncated",
  "truncated": true,
  "stop_reason": "turn_capped",
  "max_iterations": 1,
  "token_usage": { "...": "..." }
}
```

---

## 6. 进度记录
- 2026-08-11 · 建立简略计划。
- 2026-08-11 · **落地步骤 1/3/5/6**：`tools/delegate_tool.py` 加 `stop_reason`（`_derive_stop_reason`+`_normalize_result_contract`，集中补齐、只增不改）、`started_at`/`completed_at`；`core/middleware/builtins.py` 加 `LoopDetectionMiddleware`（子 Agent 默认开，命中记 `loop_capped`）；`core/config.py` 加 2 个 loop 开关；确认防递归已在 `DELEGATE_CHILD_EXCLUDED_TOOLS`。新增 `tests/test_delegate_contract.py`（5 passed），既有 `test_delegate_progress.py`（8 passed）零回归，整体 230 passed。step_events 内嵌（步骤 2）待后续；delegation_ledger 沉淀（步骤 4）已由 `02` 承担。
- 2026-08-11 · **步骤 2 落地，本章完成**：delegate 结果新增有界 `step_events`（默认最多 32 条），采样模型轮次、工具开始/结束短预览；异常与 timeout 路径也尽力保留已采样事件。父 Agent 的 delegation ledger 同步保留 stop_reason、时间戳和 step_events。委派定向 + 基础设施回归 35 passed。
