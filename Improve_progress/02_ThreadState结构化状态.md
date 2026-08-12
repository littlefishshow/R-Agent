# 02 · ThreadState 结构化状态

**状态：✅ 已完成（2026-08-11）**
**对应 deer-flow 学习文档：** 第 5 章（ThreadState：不要把所有上下文塞进 messages）+ 13.2（把上下文拆成 state channel）
**建议顺序：** 第 2 步（很多后续改动的地基）
**依赖：** 无强前置。但 `01`（middleware）、`03`（上下文管理）、`05`（委派）都会往这个 state 里读写，所以越早做越好。

---

## 1. 要解决什么问题（R-Agent 现状）

R-Agent 目前**没有统一的状态对象**。状态散落在两处：

- `core/agent.py:348` 起的一堆实例属性：`self.messages`（唯一的扁平列表）、`self.token_usage`、`self.delegated_token_usage`、`self.context_usage`、截断/软警告标志。
- **磁盘文件**充当"事实上的 channel"：todo 在 `sandbox/todo_lists/`、子任务上下文在 `sandbox/delegate_contexts/`、大工具输出在 `sandbox/tool_outputs/`、memory 在 `memories/`。

**痛点：**
- 关键结构化信息（摘要、产物索引、委派结果、已加载 skill）没有被当成"状态"统一管理，而是要么塞进 `messages`、要么散在磁盘。
- 一旦压缩 `messages`（`core/context_control.py`），存在旧消息里的子任务结果、skill 引用就可能丢失。
- 想在 middleware / 事件流里读写状态，没有一个干净的入口。

对照 deer-flow：它用 `ThreadState`（扩展 `AgentState`）把 `messages` 之外的东西拆成**带 reducer 的独立 channel**，
压缩对话时不会丢结构化事实。

---

## 2. deer-flow 是怎么做的

- 关键文件：`deer-flow/backend/packages/harness/deerflow/agents/thread_state.py`（`ThreadState`、各 reducer、`DeltaThreadState`）。
- 核心字段：`messages / sandbox / thread_data / title / artifacts / todos / goal / uploaded_files / viewed_images / promoted / delegations / skill_context / summary_text`。
- 三个值得抄的点：
  1. 每个非简单字段有 **reducer**（`merge_artifacts`、`merge_delegations`、`merge_skill_context`），规定并发更新怎么合并，不会乱覆盖。
  2. `summary_text` 是**独立 channel**，压缩后通过 durable context 注入，而不是硬写回对话正文。
  3. `delegations` 保留子 Agent 的**结构化结果**，不只靠 tool message 文本。

**可迁移结论（原文）：** messages 适合对话，不能承担所有系统记忆和运行元数据。

---

## 3. R-Agent 怎么改的（已落地）

1. **新建 `core/state.py`，定义 `ThreadState` dataclass**，先落地这些 channel（对齐 R-Agent 已有磁盘概念）：
   - `messages`（沿用现有列表）
   - `summary_text`（压缩摘要，配合 `03`）
   - `artifact_index`（产物索引：现在大工具输出落在 `sandbox/tool_outputs/`，把路径+摘要收进这里）
   - `delegation_ledger`（子任务记录：现在在 `sandbox/delegate_contexts/`，把结构化结果收进这里，配合 `05`）
   - `skill_context`（已加载 skill 摘要，配合 `07`）
   - `todos`（现在在 `sandbox/todo_lists/`）
   - `token_usage` / `context_usage`（从散落属性收编）
2. **为每个 channel 写 reducer/merge 函数**（`merge_artifacts` 等），保证多来源更新可预测。
3. **`RAgent` 内部改为持有 `self.state: ThreadState`**，老属性（如 `self.messages`）改为 property 代理到 `state.messages`，保证**对外零破坏**。
4. **磁盘仍是落盘归档，state 是内存事实**：state channel 与磁盘目录保持双向一致（读时可从磁盘 rehydrate，写时同步落盘），先内存后落盘。
5. **暂不做 `DeltaThreadState` 增量 checkpoint**（deer-flow 的优化项），列为后续；R-Agent 目前无 checkpoint 压力。

> 关键约束：这一步是"重构 + 收编"，不改变任何可见行为。判定成功的标准是——现有所有测试不改一行仍然通过。

---

## 4. 为什么这样改

- **为什么先收编成内存 state，而不是一上来就 channel 化压缩**：`messages` 之外的信息现在散在 4 个实例属性 + 4 个磁盘目录，没有统一入口。先把它们收进一个 `ThreadState` 对象，后续的压缩（`03`）、memory（`04`）、委派（`05`）才有干净的读写点。这一步刻意做成"纯重构、零行为变化"，把风险降到最低。
- **为什么用 property 代理保证兼容**：审计发现 `agent.messages = [...]` 这种**整体重赋值**在内部、`app_gui/runtime.py`（8 处）和测试里都大量存在；`agent.token_usage["x"] += 1` 这种**原地修改**也很多。property 的 getter 返回 `state` 里的真实对象（原地修改自动生效），setter 把重赋值写回 `state`——两种用法都无需改一行外部代码。审计也确认没有对 agent 实例做 pickle/deepcopy，`delegate_tool` 读取用的是 `getattr`（property 安全），所以代理方案完全可行。
- **为什么 reducer 要对脏数据宽容**：reducer 会被主循环里的埋点代码调用（`delegate_task` 返回结构在 compact/full 模式下不同）。如果传入非预期结构就抛异常，会打断用户正在进行的对话。所以 reducer 遇到非 dict/None 一律跳过——这与 `08` 事件流"观测绝不打断主循环"的原则一致。
- **哪些字段暂不纳入 / 为什么**：
  - `summary_text` / `skill_context` 已建好 channel 但**本章不主动写入**——分别留给 `03`（LLM 摘要）和 `07`（skill 延迟加载）落地，避免本次改动面过大。
  - `todos` 保留字段但仍以 `sandbox/todo_lists/` 为准，不在本章双向同步。
  - `DeltaThreadState` 增量 checkpoint（deer-flow 的写入优化）暂不做：R-Agent 目前没有 checkpoint 压力，属过度设计。
- **本章实际写入的 channel**：`artifact_index`（大工具输出落盘时）和 `delegation_ledger`（`delegate_task` 返回时），因为这两处正好有现成的埋点位置（复用 `08` 的 `artifact.created` / `delegate.end` 事件点），顺手就能填，且能立刻验证 channel 有效。

---

## 5. 测试示例

新增 `tests/test_thread_state.py`，6 个用例全部通过：

1. `test_merge_artifacts_dedupes_by_path` —— 同 `path` 覆盖、新 `path` 追加。
2. `test_merge_delegations_dedupes_by_task_id` —— 同 `task_id` 合并成最新状态（running→completed）。
3. `test_merge_skill_context_dedupes_by_skill` —— 同 skill 去重。
4. `test_reducers_tolerate_junk` —— 传 `None`/字符串/数字都不抛异常、不写入。
5. `test_agent_state_backward_compat_properties` —— 读 / 原地修改（append、`+=`）/ 整体重赋值 都正确代理到 `self.state`。
6. `test_loop_populates_artifact_index` —— 跑一次产生大工具输出的会话，断言 `state.artifact_index` 记录了产物且 `path` 指向真实落盘文件。

**你可以亲手验证：**

```bash
cd /Users/bytedance/myenv/hermes/R-Agent

# 1) 跑本章 + 上一章测试
python3 -m pytest tests/test_thread_state.py tests/test_run_event_stream.py -q   # 10 passed

# 2) 零回归验证（现有测试一行没改，仍全绿）
python3 -m pytest tests/ -q -k "agent or context or gui or memory or delegate or token or todo or archive or sanitize or voice"
# -> 194 passed（另有 3 个 autoresearch 用例失败，经 git stash 确认与本次改动无关）
```

**实测 `delegation_ledger` 端到端填充样例**（模拟 delegate_task 返回两条子任务）：

```
RESULT: final
delegation_ledger: [
  {"task_id": "todo-1", "status": "completed", "token_usage": {"total_tokens": 42}},
  {"task_id": "todo-2", "status": "blocked", "truncated": true}
]
```

> 说明：`delegation_ledger` 只抽取结构化关键字段（task_id/status/truncated/token_usage 等），`05` 章会补齐 `stop_reason`、`step_events`、时间戳等完整契约。

---

## 6. 进度记录
- 2026-08-11 · 建立简略计划。
- 2026-08-11 · **完成落地**：新增 `core/state.py`（`ThreadState` + 3 个 reducer）；`core/agent.py` 改为持有 `self.state`，`messages`/`token_usage`/`delegated_token_usage`/`context_usage` 全部改为 property 代理（零行为变化）；主循环把大工具产物写入 `artifact_index`、把 `delegate_task` 结果写入 `delegation_ledger`。新增 `tests/test_thread_state.py`（6 passed），零回归（194 passed）。`summary_text`/`skill_context`/`todos` channel 已建但留给 `03`/`07` 写入。
