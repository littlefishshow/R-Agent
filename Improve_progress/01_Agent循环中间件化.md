# 01 · Agent 循环中间件化

**状态：🚧 进行中（2026-08-11：中间件框架 + 6 个 hook 点已接入主循环，默认空链零行为变化；把现有逻辑逐个平移成 middleware 待做）**
**对应 deer-flow 学习文档：** 第 3 章（Agent-loop）+ 第 4 章（Lead Agent 构建链路）+ 13.1（把 agent loop 瘦身为 runtime + middleware）
**建议顺序：** 第 4 步（在事件流、ThreadState、上下文管理之后做最顺）
**依赖：** `02_ThreadState结构化状态`（middleware 需要一个统一 state 对象来读写）、`08_运行事件流`（用事件流验证 hook 是否按顺序触发）

---

## 1. 要解决什么问题（R-Agent 现状）

R-Agent 的主循环是 `core/agent.py:891` 的 `RAgent._loop`，一个 `while iteration < self.max_iterations:` 的大循环。
一次循环里它同时干了这些事：

- 注入软警告（`_inject_soft_warning`，L904）
- 组装工具 schema + allow/exclude 过滤（L908–918）
- 判断是否压缩上下文（`_maybe_compress_context`，L919）
- 带重试地调用 LLM（`_chat_completion_with_retry`，L936）
- 追加 assistant 消息（L957）
- 遍历执行每个 tool_call（L963–1059）
- 执行 turn 预算（`enforce_turn_budget`，L1061）
- 追加工具结果（L1072）
- 终态兜底（`_force_finalize`，L1093）

**痛点：** 所有横切逻辑（预算、压缩、安全、观测、memory）都焊死在一个函数里。每加一个能力，这个函数就更大更难改，
也很难单独测试某一段。这正是 deer-flow 学习文档 13.1 点名的反模式："不要把 agent loop 写成越来越大的循环函数。"

对照 deer-flow：它把循环交给 LangGraph runtime，自己只写一条 **middleware chain**，每个 middleware 只管一件事
（上下文注入、压缩、memory、限流、工具错误、安全、兜底）。

---

## 2. deer-flow 是怎么做的

- 关键文件：`deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py`
  的 `make_lead_agent()` → `build_middlewares()` → `create_agent(...)`。
- 循环本身由 LangGraph/LangChain runtime 负责，DeerFlow 只在这些 hook 阶段插入 middleware：
  `before_agent` / `before_model` / `wrap_model_call` / `wrap_tool_call` / `after_model` / `after_agent`。
- 每个 middleware 是一个独立对象，职责单一，顺序可编排（见学习文档第 3 章末尾的分类）。

**速学重点（原文）：** 主 loop 只当"交通指挥"，把复杂逻辑拆成稳定 hook。

---

## 3. R-Agent 打算怎么改（简略步骤）

我们**不引入 LangGraph**（那会是一次大重写，且破坏 R-Agent 现有轻量特性）。目标是用一个**轻量 middleware 协议**
在不改变外部行为的前提下，把 `_loop` 里的逻辑抽出来。

1. **定义中间件协议**：在 `core/middleware/base.py` 新增一个 `Middleware` 基类，提供可选钩子：
   `before_iteration(ctx)`、`before_model(ctx)`、`after_model(ctx)`、`before_tool(ctx, call)`、`after_tool(ctx, call, result)`、`after_iteration(ctx)`。
   `ctx` 是一个贯穿本轮的运行上下文对象（引用 ThreadState、tools、iteration、event_sink 等）。
2. **保持 `_loop` 为最小骨架**：`_loop` 只保留"调模型 → 执行工具 → 推进状态"，其余全部改为在合适的 hook 点依次调用已注册的 middleware。
3. **逐个平移现有逻辑**（一次一个，每次都跑测试）：
   - `_inject_soft_warning` / `_force_finalize` → `SoftWarningMiddleware` / `TerminalResponseMiddleware`（`before_model` / `after_iteration`）
   - `_maybe_compress_context` → `SummarizationMiddleware`（`before_model`）
   - `enforce_turn_budget` + `maybe_persist_tool_result` → `ToolOutputBudgetMiddleware`（`after_tool`）
   - `tool_call_guard` 回调 → `GuardrailMiddleware`（`before_tool`）
   - `_emit_event` 调用 → 由各 middleware 在自己阶段发事件（配合 `08_运行事件流`）
4. **提供 `build_default_middlewares()`**：在 `RAgent.__init__` 里组装默认链，顺序显式可读（参考 deer-flow `build_middlewares()`）。
5. **加开关**：`RAgent(..., use_middleware=True/False)`。先默认 `False`（走老路径），中间件链通过测试后再翻默认值，保证可回滚。

> 关键约束：现有回调（`on_think`/`on_tool_start`/`on_tool_end`）、`event_sink`、`tool_call_guard`、`allowed_tools`/`exclude_tools`
> 的对外行为必须保持不变——它们要么被 middleware 适配，要么继续并存。

### 本轮已落地（✅） / 待做（⬜）

- ✅ **步骤 1 · 中间件协议**：新增 `core/middleware/base.py` + `__init__.py`。`Middleware` 基类（6 个 hook 默认 no-op）、`AgentContext`（贯穿一轮的运行上下文，持有 agent/iteration/tools/message/event_sink）、`ToolCallView`（工具调用只读视图）、`MiddlewareChain`（按序调度、**单个中间件异常被吞掉绝不打断主循环**、`before_tool` 可返回否决串）、`build_default_middlewares()`（默认返回**空链**）。
- ✅ **步骤 2+4 · 接入 hook + 默认链**：`core/agent.py:_loop` 在 6 个固定阶段调用中间件——`before_iteration`（每轮开始）、`before_model`（tools 组装+压缩后）、`after_model`（拿到回复后）、`before_tool`（执行工具前，可否决，**与既有 `tool_call_guard` 并存，guard 优先**）、`after_tool`（工具后，**可改写工具结果**）、`after_iteration`（每轮结束，两条退出路径都触发）。`RAgent.__init__` 新增 `middlewares=None` 参数，默认用 `build_default_middlewares()`（按 config 开关组装，默认空链）。
- ✅ **首批真实中间件落地**（验证框架价值）：`core/middleware/builtins.py` 新增两个内置中间件，兑现 `03`/`04` 的待做项：
  - `ToolResultSanitizationMiddleware`（`after_tool`）：中和工具结果里的 prompt injection（= `03` 步骤 6）。开关 `TOOL_SANITIZATION_ENABLED`。
  - `MemoryWriteMiddleware`（`after_iteration`）：调用 `provider.add(...)` 提供记忆自动写入 hook（= `04` 步骤 6）。开关 `MEMORY_WRITE_MIDDLEWARE_ENABLED`。
  - `build_default_middlewares()` 现按 config 开关组装；两开关默认关 => 默认链仍空。为支持清洗改写，`after_tool` 协议扩展为可返回替换串（`None`=不变，向后兼容）。
- ⬜ **步骤 3 · 把现有逻辑逐个平移成 middleware**：`_inject_soft_warning` / `_force_finalize` / `_maybe_compress_context` / `enforce_turn_budget` 目前仍以原有内联形式留在 `_loop`。它们已有对应 hook 点可迁入，但**平移属于"改主路"**，收益低、回归风险高，留到最后再做。
- ⬜ **步骤 5 · 开关**：本实现用"默认空链"实现零行为变化，不需要额外 `use_middleware` 开关——空链即等价老路径；有自定义中间件时才生效。

> 与原计划差异：原计划想先"抽空 `_loop`"，实践中更稳的顺序是**先立骨架（hook 点 + 空链）**，让后续每个横切需求以 Middleware 形式增量接入；把已稳定运行的内联逻辑（压缩/软提醒/收尾）留到最后再平移，风险最低。

---

## 4. 为什么这样改

- **为什么不引入 LangGraph**：deer-flow 把循环托管给 LangGraph runtime，但那对 R-Agent 是一次大重写，且会破坏它现有的轻量、子进程隔离、macOS fork 规避等特性。我们只取"middleware chain"这一核心思想，用一个最小 hook 协议实现，`_loop` 仍是自己的循环——**收益（可组合的横切治理）拿到，成本（重写运行时）不付**。
- **为什么默认空链而不是加 `use_middleware` 开关**：空链天然等价于"没有中间件"，行为与现状逐字节一致（`test_empty_chain_is_zero_behavior_change` 验证）。这比布尔开关更简洁——不需要在 `_loop` 里维护两条代码路径，也不会有"开关开着但链为空"的歧义。
- **为什么 `before_tool` 与既有 `tool_call_guard` 并存、且 guard 优先**：`tool_call_guard` 是调用方（如 AutoResearch、self_evolution）传入的现有安全回调，不能破坏其语义。中间件的 `before_tool` 是新增的、面向可复用策略的否决点。让 guard 优先保证现有安全策略不被中间件绕过；guard 未否决时才看中间件，二者叠加而非替换。
- **为什么单个中间件异常必须被吞掉**：中间件是"旁路增强"，一个写得不好的中间件不应让用户正在进行的对话崩溃。`MiddlewareChain` 捕获每个 hook 的异常记入 `errors` 列表并继续——与 events/durable-context 的"观测/增强绝不打断主循环"原则一致。
- **为什么先立骨架、后平移内联逻辑**：压缩、软提醒、强制收尾是已在生产跑通的逻辑，贸然抽成 middleware 有回归风险且当前收益低。先把 hook 点铺好，让**新需求**（`03` 的工具输出清洗、`04` 的 memory 自动写入、`05` 的 loop detection）直接以 Middleware 形式落地；等这些验证了框架，再回头平移老逻辑最稳。

---

## 5. 测试示例

新增 `tests/test_middleware.py`，4 个用例全部通过：

1. `test_hook_order_across_a_tool_turn` —— 一次"工具轮 + 最终答复轮"的会话，断言完整 hook 序列：
   `[bi, bm, am, before_tool, after_tool, ai]` + `[bi, bm, am, ai]`。
2. `test_before_tool_veto_blocks_execution` —— 否决型中间件让目标工具**不执行**（handler 调用计数为 0），否决串作为工具结果进入对话。
3. `test_middleware_exception_does_not_break_loop` —— `before_model` 抛异常，`run_conversation` 仍正常返回，异常被记入 `agent.middleware.errors`。
4. `test_empty_chain_is_zero_behavior_change` —— 不传 middlewares 时链长为 0，行为与现状一致。

**你可以亲手验证：**

```bash
cd /Users/bytedance/myenv/hermes/R-Agent

# 1) 本章测试
python3 -m pytest tests/test_middleware.py -q          # 4 passed

# 2) 零回归子集（含主循环所有相关测试）
python3 -m pytest tests/ -q -k "middleware or agent or context or gui or thread or event or delegate or memory or prompt or token"
# -> 219 passed（另 3 个 autoresearch 用例失败，git stash 已证与本次改动无关）
```

**实测 hook 触发顺序**（一次含 1 次工具调用的会话）：

```
第 1 轮(有工具): before_iteration → before_model → after_model → before_tool:echo → after_tool:echo → after_iteration
第 2 轮(最终答复): before_iteration → before_model → after_model → after_iteration
```

---

## 6. 进度记录
- 2026-08-11 · 建立简略计划。
- 2026-08-11 · **框架落地（步骤 1+2+4）**：新增 `core/middleware/`（`base.py` + `__init__.py`）；`core/agent.py:_loop` 接入 6 个 hook 点（`before_tool` 与既有 `tool_call_guard` 并存）；`RAgent.__init__` 加 `middlewares=None`，默认空链（零行为变化）。新增 `tests/test_middleware.py`（4 passed），零回归（219 passed）。步骤 3（平移现有内联逻辑）留到后续章节实际使用框架后再做。
- 2026-08-11 · **首批真实中间件落地**：新增 `core/middleware/builtins.py`（`ToolResultSanitizationMiddleware` = `03` 步骤6；`MemoryWriteMiddleware` = `04` 步骤6）；`after_tool` 协议扩展为可改写结果；`build_default_middlewares()` 按 config 开关组装（默认仍空）。`core/config.py` 加 2 个开关。新增 `tests/test_middleware_builtins.py`（6 passed），零回归（225 passed）。框架价值已验证。
- 2026-08-11 · **工具清洗灰度完成**：新增 `TOOL_SANITIZATION_MODE=off|audit|enforce`；audit 只写运行事件（命中数、工具名）不改写结果，enforce 才中和。旧 `TOOL_SANITIZATION_ENABLED=1` 兼容映射为 enforce。本机 `.env` 开启 audit，用于收集误报样本后再决定是否 enforce。
