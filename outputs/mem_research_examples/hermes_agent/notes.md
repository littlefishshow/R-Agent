# Hermes Agent memory 实现调研笔记

调研对象：NousResearch/Hermes Agent 官方仓库  
仓库：<https://github.com/NousResearch/hermes-agent>  
调研 commit：`4d18717b6c798d4f6bab9e736c6ed10c5a8365f4`  
临时 clone：`sandbox/tmp/hermes-agent-r3`（任务完成后清理）

本目录保存了可参考的代码片段文件。每个片段文件顶部都标明了 GitHub source URL、commit 与本地源文件路径。

## 总览：Hermes memory 的分层

Hermes Agent 的 memory 不是单一机制，而是几层互补：

1. **内置文件 memory**：`~/.hermes/memories/MEMORY.md` 与 `USER.md`，由 `tools/memory_tool.py` 管理。
2. **Frozen Snapshot 注入**：会话启动时读取文件并渲染进 system prompt；会话中写入会立即落盘，但不会改变本会话 system prompt。
3. **memory tool**：模型通过单一 `memory` 工具对文件 memory 执行 `add / replace / remove`。
4. **SQLite session search**：所有历史 session 存在 `~/.hermes/state.db`，用 SQLite FTS5 / trigram FTS 检索历史消息，再按 session lineage 去重、返回命中附近窗口和开头/结尾 bookends。
5. **MemoryProvider / MemoryManager 插件层**：内置 memory 之外可接一个外部 provider；通过 lifecycle hooks 实现 prefetch、post-turn sync、compression 前抽取、内置 memory 写入镜像等。
6. **prefetch ephemeral 注入**：外部 provider 的 recall 在每轮开始前 prefetch，一次性包进 `<memory-context>` fenced block 注入当前用户消息，不写入持久 transcript。
7. **compression hooks**：上下文压缩前通知 provider `on_pre_compress(messages)`，但当前核心调用处没有把返回文本接入压缩 prompt（见“注意点”）。

## 重点发现

### 1. MEMORY.md / USER.md 文件 memory

来源片段：

- `01_memory_tool_overview_frozen_snapshot.md`
- `02_memory_store_load_snapshot.md`
- `03_memory_add_persist_limits.md`
- `04_memory_replace_remove_snapshot_read.md`
- `05_memory_atomic_write_dispatch.md`
- `23_docs_memory_files_frozen_snapshot.md`

实现要点：

- 存储目录通过 `get_hermes_home() / "memories"` 动态解析，支持 profile 隔离。
- 两个 store：
  - `MEMORY.md`：agent 的 personal notes，例如环境事实、项目约定、工具 quirks。
  - `USER.md`：用户画像，例如偏好、沟通风格、工作习惯。
- 条目用 `\n§\n` 分隔，支持多行 entry。
- 默认 char limits：`MEMORY.md` 2200 chars，`USER.md` 1375 chars。
- 每次 mutation 前：
  - strict threat scan，防 prompt injection / exfiltration pattern 进入 system prompt。
  - 文件 lock + 从磁盘 reload，降低并发 session 覆盖风险。
  - drift detection：如果磁盘文件无法按 memory tool 格式 round-trip，备份 `.bak.<ts>` 并拒绝写入，避免覆盖手工/patch/sister-session 写入。
- 写文件使用 temp file + fsync + atomic replace，避免 reader 看到半写状态。

可借鉴点：

- 小而强的 curated memory，不用数据库也能可靠工作。
- “写入前重新读取 + 格式 drift 保护 + atomic replace”适合多 agent/多会话共享同一 memory 文件的场景。
- char limit 用字符而非 token，跨模型稳定，便于工具层直接校验。

### 2. Frozen Snapshot 设计

来源片段：

- `01_memory_tool_overview_frozen_snapshot.md`
- `02_memory_store_load_snapshot.md`
- `04_memory_replace_remove_snapshot_read.md`
- `23_docs_memory_files_frozen_snapshot.md`

实现要点：

- `MemoryStore.load_from_disk()` 在会话启动时读取 MEMORY/USER 后，构建 `_system_prompt_snapshot`。
- `format_for_system_prompt()` 只返回 `_system_prompt_snapshot`，不会返回 live entries。
- 会话中 memory tool 写入立刻持久化到文件，但不会修改 system prompt snapshot；下一会话才刷新。
- 目的：保持 system prompt/prefix 稳定，利于 LLM prefix cache，也降低 mid-session system prompt 漂移。

可借鉴点：

- 对“长期事实”采用 frozen snapshot，可把“可持久化”和“本轮上下文可见性”解耦。
- 如果需要本会话立即可见，靠 tool response/live state 或 ephemeral recall，而不是改 system prompt。

### 3. memory tool

来源片段：

- `03_memory_add_persist_limits.md`
- `04_memory_replace_remove_snapshot_read.md`
- `06_memory_tool_schema.md`

实现要点：

- 单一工具 `memory`，参数：`action`, `target`, `content`, `old_text`。
- actions：`add`, `replace`, `remove`。官方文档说没有 `read` action。
- `replace/remove` 通过短唯一 substring (`old_text`) 匹配，而非暴露 ID。
- schema description 写得很强：明确何时保存、优先级、不要保存临时任务状态、技能应保存为 skill、两类 target 区分等。

注意：代码 schema 中明确“Do NOT save task progress, session outcomes, completed-work logs... use session_search”，但网站文档的 save examples 仍有 completed work 例子；如果复用设计，建议以代码 schema 为准，避免把 session diary 污染到长期 memory。

### 4. MemoryProvider 抽象

来源片段：

- `07_memory_provider_core_abc.md`
- `08_memory_provider_hooks.md`
- `25_docs_provider_abc_hooks.md`

核心接口：

- `name`
- `is_available()`：只做 config/deps 检查，不做网络调用。
- `initialize(session_id, **kwargs)`：传入 `hermes_home`, `platform`, `agent_context`, `agent_identity`, `agent_workspace`, `parent_session_id`, `user_id` 等。
- `system_prompt_block()`：只放静态 provider 信息。
- `prefetch(query, *, session_id="")`：每轮 API call 前返回 recall context。
- `queue_prefetch(query, *, session_id="")`：turn 后为下一轮后台预热。
- `sync_turn(user_content, assistant_content, *, session_id="", messages=None)`：完成 turn 后非阻塞持久化。
- `get_tool_schemas()` / `handle_tool_call()`：外部 provider 自己的工具面。
- hooks：`on_turn_start`, `on_session_end`, `on_session_switch`, `on_pre_compress`, `on_memory_write`, `on_delegation`, `shutdown`。

可借鉴点：

- Provider ABC 明确区分：静态 system prompt、动态 prefetch recall、post-turn sync、工具扩展、compression/session lifecycle。
- `sync_turn()` 要求非阻塞，外部 memory 不应阻塞用户路径。
- `hermes_home` 参数避免 provider 硬编码 `~/.hermes`，利于 profile 隔离。

### 5. MemoryManager 编排

来源片段：

- `09_prefetch_context_fence.md`
- `10_memory_manager_registration.md`
- `11_memory_manager_prompt_prefetch.md`
- `12_memory_manager_sync_tools.md`
- `13_memory_manager_hooks_init.md`

实现要点：

- 管理 provider list 和 tool-name routing。
- 允许 built-in provider + 最多一个 external provider，避免 tool schema 膨胀和多个 memory backend 冲突。
- 注册 provider 时拒绝 shadow core tool names。
- `build_system_prompt()` 聚合 provider 静态 prompt block。
- `prefetch_all()` 聚合 provider recall；失败 best-effort，不阻塞。
- `sync_all()` 调用各 provider 的 `sync_turn`，兼容有/无 `messages` 参数的旧签名。
- `get_all_tool_schemas()` 聚合 provider 工具 schema。
- `handle_tool_call()` 根据 tool name 路由给 provider。
- hooks 聚合：`on_turn_start`, `on_session_end`, `on_session_switch`, `on_pre_compress`, `on_memory_write`, `on_delegation`, `shutdown_all`, `initialize_all`。

可借鉴点：

- MemoryManager 作为唯一集成点，避免 agent 主流程散落 provider-specific 逻辑。
- hook 调用全部 try/except，外部 memory 错误不影响主对话。
- 注册时做 tool name 冲突检查，尤其是防止 provider 覆盖核心工具。

### 6. prefetch 注入

来源片段：

- `09_prefetch_context_fence.md`
- `14_prefetch_before_tool_loop.md`
- `15_prefetch_injected_into_user_message.md`
- `16_post_turn_sync_queue_prefetch.md`

流程：

1. turn start：调用 `memory_manager.on_turn_start()`。
2. 同一位置调用 `memory_manager.prefetch_all(original_user_message)`，得到 `ext_prefetch_cache`。
3. 构造 API messages 时，只对当前 turn 的 user message 追加 injections。
4. memory prefetch 会先包进：

   ```text
   <memory-context>
   [System note: The following is recalled memory context, NOT new user input...]

   ...provider recall...
   </memory-context>
   ```

5. 这只是 API-call-time 的 ephemeral 注入；原始 `messages` 不被修改，避免落入 session persistence。
6. turn 完成后 `_sync_memory_after_turn()` 调 `sync_all()`，再 `queue_prefetch_all()` 预热下一轮。

可借鉴点：

- recall context 注入到当前 user message 而非 system prompt，可降低 prefix cache 破坏。
- fenced block + system note 防止把 recall 当成用户新输入。
- `sanitize_context()` 会去掉 provider 返回中已有的 `<memory-context>` tags 和 system note，防 provider 预包装/嵌套注入。

### 7. SQLite FTS session search

来源片段：

- `18_sqlite_fts_schema.md`
- `19_session_search_windows_bookends.md`
- `20_session_search_fts_query.md`
- `21_session_search_discovery_shape.md`
- `22_session_search_schema_guidance.md`
- `24_docs_memory_vs_session_search.md`

实现要点：

- session DB：`~/.hermes/state.db`。
- `messages_fts`：FTS5 表，content 包含 `message.content + tool_name + tool_calls`。
- `messages_fts_trigram`：FTS5 trigram tokenizer，改善 CJK/多脚本 substring search。
- triggers 在 messages insert/delete/update 时同步 FTS 表。
- `SessionDB.search_messages()`：
  - 支持 FTS5 syntax：keywords, quoted phrases, OR/NOT, prefix wildcard。
  - 支持 role/source filters、active row filter、newest/oldest sort。
  - CJK 3+ 字优先走 trigram；短 CJK fallback 到 LIKE。
- `session_search` tool 的 calling shapes：
  1. discovery：`query`，FTS 检索，按 lineage 去重，返回每个 session 的 snippet、±5 window、bookend_start 前 3 条、bookend_end 后 3 条。
  2. scroll：`session_id + around_message_id`，返回 anchor 附近 ±window。
  3. read：仅 `session_id`，dump 小 session 或首 20 + 尾 10。
  4. browse：无参数，列最近 sessions。
- `get_anchored_view()` 建在 `get_messages_around()` 上，bookends 帮助“一次调用看到目标、命中上下文、结论”。

可借鉴点：

- 将 session search 明确区分于 memory：memory 保存关键 facts；session_search 保存所有历史且按需检索。
- FTS 检索不走 LLM summarization，成本低且返回原文消息。
- discovery 返回 bookends + anchored window，比只返回 snippet 更可用。

### 8. compression hooks

来源片段：

- `08_memory_provider_hooks.md`
- `13_memory_manager_hooks_init.md`
- `17_compression_on_pre_compress_call.md`

设计意图：

- `MemoryProvider.on_pre_compress(messages) -> str`：上下文压缩丢弃旧消息前，provider 可抽取 insights，并返回文本供 compression summary prompt 保留。
- `MemoryManager.on_pre_compress()` 聚合各 provider 返回文本。

注意点：

- 当前 `agent/conversation_compression.py` 调用了 `agent._memory_manager.on_pre_compress(messages)`，但返回值没有被保存或传给 `context_compressor.compress()`。
- 这可能表示：
  - 设计已在 ABC/manager 中准备好，但核心压缩路径尚未接线；或
  - provider 主要靠 side effects 写入外部 backend，而不是靠返回文本进入 compression prompt。
- 如果要借鉴，建议明确接线：将 hook 返回内容作为 compression prompt 的 dedicated context，或将 hook contract 改成 side-effect-only，避免返回值被忽略。

## 可参考代码片段索引

- `01_memory_tool_overview_frozen_snapshot.md` — 文件 memory 与 frozen snapshot 顶层说明。
- `02_memory_store_load_snapshot.md` — 读取 MEMORY/USER、去重、threat scan、构建 snapshot。
- `03_memory_add_persist_limits.md` — add 写入流程：scan、lock、reload、limit、persist。
- `04_memory_replace_remove_snapshot_read.md` — replace/remove substring matching；snapshot read。
- `05_memory_atomic_write_dispatch.md` — atomic write 与 memory_tool dispatcher。
- `06_memory_tool_schema.md` — memory tool schema 与行为指导。
- `07_memory_provider_core_abc.md` — MemoryProvider core lifecycle。
- `08_memory_provider_hooks.md` — provider hooks：pre-compress/memory-write/delegation。
- `09_prefetch_context_fence.md` — memory context fenced block。
- `10_memory_manager_registration.md` — MemoryManager provider 注册与工具冲突控制。
- `11_memory_manager_prompt_prefetch.md` — prompt block 与 prefetch aggregation。
- `12_memory_manager_sync_tools.md` — post-turn sync 与 provider tool routing。
- `13_memory_manager_hooks_init.md` — manager hooks 与 initialize_all。
- `14_prefetch_before_tool_loop.md` — turn 开始 prefetch。
- `15_prefetch_injected_into_user_message.md` — ephemeral 注入当前 user message。
- `16_post_turn_sync_queue_prefetch.md` — turn 完成 sync + queue_prefetch。
- `17_compression_on_pre_compress_call.md` — compression 前 hook 调用。
- `18_sqlite_fts_schema.md` — SQLite FTS/trigram schema 与 triggers。
- `19_session_search_windows_bookends.md` — anchored window / bookends。
- `20_session_search_fts_query.md` — FTS/trigram/LIKE search 实现。
- `21_session_search_discovery_shape.md` — session_search discovery 聚合。
- `22_session_search_schema_guidance.md` — session_search tool schema guidance。
- `23_docs_memory_files_frozen_snapshot.md` — 官方 docs：文件 memory / frozen snapshot。
- `24_docs_memory_vs_session_search.md` — 官方 docs：memory vs session search。
- `25_docs_provider_abc_hooks.md` — 官方 docs：provider plugin ABC/hooks。

## 对当前 agent memory 系统的改进建议

1. **采用 frozen snapshot + live disk writes**：长期 memory 进入 system prompt 时固定在 session start；会话中写入只更新持久层，避免频繁破坏 prefix cache。
2. **将 memory 与 session_search 分工制度化**：
   - memory：稳定、短、总是有用的 facts/preferences。
   - session_search：任务进度、历史决定、具体对话证据。
3. **给 memory tool schema 写强约束**：尤其强调不要保存临时 TODO、完成日志、原始大块数据；把“何时保存/何时跳过”写进工具描述。
4. **增加 drift detection/atomic writes**：如果仍使用文件 memory，建议加 lock、reload-before-write、round-trip drift backup、atomic replace。
5. **引入 provider interface**：把外部 memory 后端通过统一 ABC 接入，至少支持：`initialize`, `prefetch`, `sync_turn`, `on_pre_compress`, `on_memory_write`, `shutdown`。
6. **prefetch 使用 fenced ephemeral 注入**：把外部 recall 包在专用标签和 system note 中，注入当前 user message；不要写入 transcript。
7. **session search 返回 anchored window + bookends**：只返回 snippet 容易不够；bookend_start/bookend_end 对恢复“目标→过程→结论”很有帮助。
8. **compression hook 明确语义**：如果 hook 返回文本，务必接入 compression prompt；如果只允许 side effect，则接口返回 `None`，减少误解。
