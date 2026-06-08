# opencode-mem / opencode-mymem memory 实现调研笔记

## 结论摘要

- 相关主仓库定位为 `tickernelz/opencode-mem`：OpenCode plugin，提供本地持久化 memory、向量检索、自动捕获和用户画像。
- `epoch-chrono/opencode-mymem` 是 GitHub 显示的 `tickernelz/opencode-mem` fork。当前 clone 到的 fork package 仍叫 `opencode-mem`，版本为 `2.8.3`，主仓库为 `2.17.0`。
- 搜索未发现独立且明确的 `my-mem` + OpenCode 仓库；结合任务提示与搜索结果，`my-mem` 很可能是对 `opencode-mymem` 的简称/误写，或者指这个 fork。两者关系不是两个独立实现，而是同一项目的上游与 fork/旧版本关系。
- 重要演进：`opencode-mymem` 旧 fork 使用 `sqlite-vec` 虚拟表做向量检索；新版 `opencode-mem` 依赖 `usearch`，SQLite 负责持久化 memory/向量 BLOB，USearch 负责近似/内存索引检索，并提供 ExactScan 扫描 SQLite BLOB 的降级。

## 调研对象与来源

- 上游：`https://github.com/tickernelz/opencode-mem`
  - clone commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
  - package: `opencode-mem@2.17.0`
  - dependencies 关键项：`usearch`, `@huggingface/transformers`, `@opencode-ai/plugin`, `@opencode-ai/sdk`
- fork：`https://github.com/epoch-chrono/opencode-mymem`
  - clone commit: `410a7b26fc8860f2fc86bc684bcf0ca54b1de732`
  - package: `opencode-mem@2.8.3`
  - dependencies 关键项：`sqlite-vec`, `@xenova/transformers`, `@opencode-ai/plugin`

> 临时 clone 使用 `sandbox/tmp/opencode-mem-research-*`，本任务结束时已清理；项目根目录未留下 clone 仓库。

## 重点实现拆解

### 1. SQLite 存储

新版 `opencode-mem` 使用分片 SQLite：

- `metadata.db` 的 `shards` 表记录 scope/user/project、scope_hash、shard_index、db_path、vector_count、is_active。
- 每个 shard DB 创建 `memories` 表：`content`, `vector BLOB`, `tags_vector BLOB`, `container_tag`, `tags`, `type`, `metadata`, user/project/git 字段与 pinned 字段。
- profile 另存在 `user-profiles.db`：`user_profiles` 表使用 `profile_data TEXT` 保存 JSON，配套 `user_profile_changelogs` 记录版本快照。

参考：`01_sqlite_storage_schema.md`, `05_user_profile_sqlite.md`。

### 2. 向量检索

新版路径：

1. 插入 memory 时先把 Float32Array 转成 Uint8Array/BLOB 存入 SQLite。
2. 如果有 shard，则调用 `VectorBackend.insert()`，默认 backend 由配置选择。
3. 查询时分别搜索 content embedding 与 tags embedding，合并分数：content similarity 权重 0.7，tags similarity 权重 0.3。
4. 如果 USearch backend 出错，降级到 ExactScan：从 SQLite 读取所有 BLOB 向量，逐条 cosine similarity。

旧 fork 路径：

- 加载 `sqlite-vec` extension。
- 创建 `vec_memories` / `vec_tags` 虚拟表。
- 使用 `embedding MATCH ? AND k = ?` 查询距离。

参考：`02_vector_retrieval_backends.md`。

### 3. Auto-capture

`event` hook 监听 `session.idle`：

- 防抖/延迟 10 秒后调用 `performAutoCapture()`。
- 从 `userPromptManager` 取最后未捕获 prompt。
- 调 OpenCode session API 读取该 prompt 后的 AI messages，提取 text responses 与 tool calls。
- 构造 markdown context，调用模型生成结构化 summary。
- 保存为 project memory，source 标记为 `auto-capture`，metadata 记录 sessionID、prompt、timestamp 等。

参考：`03_auto_capture_and_profile_learning.md`。

### 4. User profile learning

同样在 `session.idle` 的后台流程中触发：

- 按 userId 拉取未分析 prompts。
- 将现有 profile + 新 prompts 构造成分析上下文。
- LLM 产出/更新用户偏好、沟通风格、工作流、技术栈等 JSON profile data。
- 保存到 `user-profiles.db`，递增 version，并写 changelog。

参考：`03_auto_capture_and_profile_learning.md`, `05_user_profile_sqlite.md`。

### 5. chat.message 注入

`chat.message` hook 在用户消息进入模型前修改 `output.parts`：

- 先保存用户 prompt 以供之后 auto-capture/profile learning 使用。
- 根据配置决定是否注入：`always`、首条真实用户消息、或 compaction 后恢复场景。
- 拉取 project memories，过滤当前 session / 过旧 memory。
- 调 `formatContextForPrompt()` 格式化为 memory context。
- 构造 `synthetic: true` 的 text part，并 `output.parts.unshift(contextPart)`。

参考：`04_chat_injection_compaction_restore.md`。

### 6. Compaction restore

`event` hook 监听 `session.compacted`：

- 用 sessionID 搜索相关 memories。
- `formatMemoriesForCompaction()` 生成恢复上下文。
- 调 `ctx.client.session.prompt({ noReply: true })` 将 memory context 注入回 session，不触发模型回复。
- 在 TUI 展示 `Memory Restored` toast。

参考：`04_chat_injection_compaction_restore.md`。

## 对当前 agent memory 系统的可借鉴点

1. **SQLite 持久层与向量索引解耦**：SQLite 保存事实数据和向量 BLOB，USearch/其他 backend 只做可重建索引；索引损坏时可从 SQLite rebuild。
2. **双向量检索**：content vector + tags vector 分开检索再加权，能兼顾语义内容和技术标签。
3. **可靠降级**：向量 backend 失败时自动 exact scan，牺牲性能但保持功能可用。
4. **事件驱动捕获**：chat.message 只记录 prompt，session.idle 后异步总结，减少阻塞主聊天路径。
5. **用户画像独立库与 changelog**：profile 与 memories 分库，profile_data JSON + version/changelog 便于审计和回滚。
6. **上下文注入标记 synthetic**：注入内容作为 synthetic part，可避免被误判为真实用户输入，也便于过滤。
7. **compaction 后恢复**：监听压缩事件，将本 session 关键 memories noReply 注入，解决压缩后上下文遗失。

## 输出文件清单

- `notes.md`：本调研笔记。
- `01_sqlite_storage_schema.md`：SQLite shard schema、新旧 sqlite-vec 对比片段。
- `02_vector_retrieval_backends.md`：新版 VectorBackend/USearch/ExactScan 与旧版 sqlite-vec 检索片段。
- `03_auto_capture_and_profile_learning.md`：session.idle、auto-capture、用户画像学习片段。
- `04_chat_injection_compaction_restore.md`：chat.message synthetic 注入与 session.compacted restore 片段。
- `05_user_profile_sqlite.md`：user profile SQLite schema、更新与 changelog 片段。
