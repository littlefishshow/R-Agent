# Agent Memory 实现调研 v2：OpenClaw、opencode-mem、Hermes Agent

> 目标：重新梳理三个示例中的 memory 如何实现，并保存可复用代码片段，用于改进当前 Agent 的 memory 系统。  
> 旧版文档 `outputs/agent_memory_research.md` 内容过散，本版按“实现链路 + 代码索引 + 可借鉴设计”重写。

---

## 0. 代码示例保存位置

本次重新调研已把代码片段保存到：

```text
outputs/mem_research_examples/
  openclaw/
    notes.md
    01_memory_core_registration.md
    02_memory_search_tool.md
    03_memory_get_tool.md
    04_memory_file_scope.md
    05_safe_memory_read.md
    06_index_schema_and_watcher.md
    07_vector_search.md
    08_keyword_and_fallback_search.md
    09_active_memory_recall_prompt.md
    10_active_memory_subagent.md
    11_active_memory_injection_hook.md
    12_compaction_flush_plan.md

  opencode_mem/
    notes.md
    01_sqlite_storage_schema.md
    02_vector_retrieval_backends.md
    03_auto_capture_and_profile_learning.md
    04_chat_injection_compaction_restore.md
    05_user_profile_sqlite.md

  hermes_agent/
    notes.md
    01_memory_tool_overview_frozen_snapshot.md
    ... 共 25 个片段文件
```

每个片段文件都包含：

- 来源仓库；
- commit；
- GitHub URL；
- 源文件路径；
- 关键代码片段；
- 中文说明。

---

## 1. 三个项目的 memory 实现一句话对比

| 项目 | 一句话架构 |
|---|---|
| OpenClaw | Markdown 文件事实源 + SQLite/FTS/vector 索引 + `memory_search/memory_get` + active-memory 子 agent + compaction flush |
| opencode-mem | OpenCode 插件 + SQLite source of truth + embedding/USearch 或 ExactScan + idle 自动总结 + user profile + chat hook 注入 |
| Hermes Agent | 小容量 `MEMORY.md/USER.md` frozen snapshot + SQLite FTS session search + MemoryProvider 插件 + per-turn prefetch 注入 |

---

## 2. OpenClaw memory 如何实现

### 2.1 核心结构

OpenClaw 的 memory 以 workspace 中的 Markdown 文件为事实源：

```text
MEMORY.md
memory/*.md
DREAMS.md
```

然后为这些文件建立 SQLite 索引：

- `chunks` 表；
- FTS5 表；
- embedding cache；
- 可选 sqlite-vec 表。

### 2.2 读 memory

OpenClaw 提供两个工具：

```text
memory_search(query)
memory_get(path, from, lines)
```

推荐流程：

```text
先 memory_search 找候选片段
再 memory_get 精确读取上下文
最后基于证据回答
```

相关代码：

- `openclaw/02_memory_search_tool.md`
- `openclaw/03_memory_get_tool.md`

### 2.3 写 memory

OpenClaw 的长期写入主要是直接写 Markdown 文件：

- 稳定事实写 `MEMORY.md`；
- session/daily 信息写 `memory/YYYY-MM-DD.md`。

它还在 compaction 前触发 memory flush，让 agent 在上下文被压缩前把重要信息 append 到当天 memory 文件。

相关代码：

- `openclaw/12_compaction_flush_plan.md`

### 2.4 自动召回

OpenClaw 有 `active-memory` 插件：

1. 在 `before_prompt_build` 阶段触发；
2. 构造当前问题 + 最近上下文；
3. 启动 lightweight 子 agent；
4. 子 agent 只能用 `memory_search` 和 `memory_get`；
5. 子 agent 返回短 summary；
6. 主模型 prompt 前注入：

```text
Untrusted context ...
<active_memory_plugin>
...
</active_memory_plugin>
```

相关代码：

- `openclaw/09_active_memory_recall_prompt.md`
- `openclaw/10_active_memory_subagent.md`
- `openclaw/11_active_memory_injection_hook.md`

### 2.5 最值得借鉴的点

1. **Markdown source of truth + 可重建索引**。  
2. **`search` 与 `get` 分离**，减少召回幻觉。  
3. **active recall 子 agent**，主模型不需要自己想起要搜索。  
4. **compaction 前 flush**，防止上下文压缩丢信息。  
5. **memory path 白名单与安全读取**，避免 memory 工具变成任意文件读取器。  
6. **向量/FTS 都有 fallback**，工程韧性好。

---

## 3. opencode-mem / my-mem 如何实现

### 3.1 my-mem 与 opencode-mem 的关系

调研结论：

- `tickernelz/opencode-mem` 是主要上游仓库；
- `epoch-chrono/opencode-mymem` 是它的 fork；
- 未找到独立、明确的 `my-mem` + OpenCode 主仓库；
- 因此这里把 `my-mem` 视为 `opencode-mymem/opencode-mem` 这一实现线索。

详见：

- `opencode_mem/notes.md`

### 3.2 核心结构

opencode-mem 是 OpenCode 插件，通过 hooks 接入：

```text
chat.message
session.idle
session.compacted
memory tool
```

它使用 SQLite 作为事实源：

```text
~/.opencode-mem/data/
  metadata.db
  projects/project_<hash>_shard_0.db
  users/user_<hash>_shard_0.db
  user-prompts.db
  user-profiles.db
```

memory 记录包含：

- content；
- vector BLOB；
- tags vector；
- container_tag；
- metadata；
- project/user/git 信息；
- pinned 标记。

相关代码：

- `opencode_mem/01_sqlite_storage_schema.md`

### 3.3 检索

新版 opencode-mem：

1. SQLite 保存 vector BLOB；
2. USearch 做向量索引；
3. USearch 失败时 fallback 到 ExactScan；
4. 检索时同时搜索：
   - content vector；
   - tags vector；
5. 两者加权融合。

旧 fork `opencode-mymem` 使用 `sqlite-vec` 虚拟表。

相关代码：

- `opencode_mem/02_vector_retrieval_backends.md`

### 3.4 自动写入

opencode-mem 的强项是自动捕获。

在 `session.idle` 后：

1. 找到最后一个未捕获 user prompt；
2. 读取后续 assistant response 和 tool calls；
3. 构造 markdown context；
4. 调 LLM 生成 summary/type/tags；
5. 写入 project memory。

同时它会周期性分析用户 prompt，更新 user profile：

- preferences；
- patterns；
- workflows；
- 技术栈/沟通风格等。

相关代码：

- `opencode_mem/03_auto_capture_and_profile_learning.md`
- `opencode_mem/05_user_profile_sqlite.md`

### 3.5 上下文注入

在 `chat.message` hook：

1. 保存用户 prompt；
2. 判断是否需要注入 memory；
3. 获取 project memory + user profile；
4. 格式化为 `[MEMORY]`；
5. 作为 `synthetic: true` part 插入用户消息前。

在 `session.compacted` 后：

1. 按 sessionID 找相关 memories；
2. 生成 restored memory context；
3. 用 `noReply` prompt 注入回 session。

相关代码：

- `opencode_mem/04_chat_injection_compaction_restore.md`

### 3.6 最值得借鉴的点

1. **SQLite 持久层与向量索引解耦**：SQLite 是事实源，USearch 只是可重建索引。  
2. **content vector + tags vector 双通道检索**。  
3. **session.idle 后异步总结**，不阻塞主响应。  
4. **user profile 独立库 + changelog**，方便审计和回滚。  
5. **synthetic 注入标记**，区分 memory context 和真实用户输入。  
6. **compaction restore**，上下文压缩后恢复本 session 关键记忆。

---

## 4. Hermes Agent memory 如何实现

### 4.1 核心结构

Hermes Agent 的 memory 是三层：

1. **内置文件 memory**：

```text
~/.hermes/memories/MEMORY.md
~/.hermes/memories/USER.md
```

2. **SQLite FTS session search**：

```text
~/.hermes/state.db
```

3. **外部 MemoryProvider 插件**：

```text
MemoryProvider ABC
MemoryManager
provider prefetch / sync / hooks
```

详见：

- `hermes_agent/notes.md`

### 4.2 内置文件 memory

Hermes 用一个 `memory` tool 管理两类文件：

- `MEMORY.md`：agent 自身长期笔记；
- `USER.md`：用户画像。

工具动作：

```text
add
replace
remove
```

特点：

- 条目用 `§` 分隔；
- 有严格字符上限；
- 写入前做 prompt injection / exfiltration 风险扫描；
- 写入前重新读磁盘，降低并发覆盖；
- drift detection，避免覆盖手工修改；
- temp file + fsync + atomic replace。

相关代码：

- `hermes_agent/01_memory_tool_overview_frozen_snapshot.md`
- `hermes_agent/02_memory_store_load_snapshot.md`
- `hermes_agent/03_memory_add_persist_limits.md`
- `hermes_agent/05_memory_atomic_write_dispatch.md`

### 4.3 Frozen Snapshot

Hermes 的内置 memory 采用 Frozen Snapshot：

1. session 启动时读取 `MEMORY.md/USER.md`；
2. 渲染进 system prompt；
3. session 中途写入会落盘；
4. 但当前 session system prompt 不变；
5. 下一次 session 才看到更新。

目的：

- 保持 prefix cache 稳定；
- 避免中途 system prompt 漂移；
- 把“持久化”和“当前上下文可见性”解耦。

相关代码：

- `hermes_agent/23_docs_memory_files_frozen_snapshot.md`

### 4.4 Session Search

Hermes 将历史 session 存入 SQLite，并用 FTS5 / trigram FTS 检索。

返回结果不是 LLM summary，而是真实历史消息窗口：

- 命中消息；
- 附近上下文；
- session 开头/结尾 bookends；
- session lineage 去重。

相关代码：

- `hermes_agent/18_sqlite_fts_schema.md`
- `hermes_agent/19_session_search_windows_bookends.md`
- `hermes_agent/20_session_search_fts_query.md`

### 4.5 MemoryProvider / MemoryManager

Hermes 抽象了 MemoryProvider：

```text
initialize
system_prompt_block
prefetch
queue_prefetch
sync_turn
get_tool_schemas
handle_tool_call
shutdown
on_turn_start
on_session_end
on_session_switch
on_pre_compress
on_memory_write
on_delegation
```

MemoryManager 负责：

- provider 注册；
- tool name 路由；
- system prompt block 聚合；
- prefetch 聚合；
- turn 后 sync；
- compression/session lifecycle hooks。

相关代码：

- `hermes_agent/07_memory_provider_core_abc.md`
- `hermes_agent/10_memory_manager_registration.md`
- `hermes_agent/11_memory_manager_prompt_prefetch.md`
- `hermes_agent/12_memory_manager_sync_tools.md`

### 4.6 Prefetch 注入

每轮开始：

1. 调 `memory_manager.prefetch_all(user_message)`；
2. provider 返回 recall context；
3. 注入当前 user message；
4. 用 fenced block 包裹：

```text
<memory-context>
[System note: recalled memory context, not new user input]
...
</memory-context>
```

相关代码：

- `hermes_agent/09_prefetch_context_fence.md`
- `hermes_agent/14_prefetch_before_tool_loop.md`
- `hermes_agent/15_prefetch_injected_into_user_message.md`

### 4.7 最值得借鉴的点

1. **小容量 curated memory**：长期事实少而精，常驻 system prompt。  
2. **Frozen Snapshot**：写入落盘，但当前 session prompt 不漂移。  
3. **memory 与 session_search 分工清晰**：长期事实 vs 历史对话检索。  
4. **MemoryProvider 抽象好**：静态 prompt、动态 prefetch、post-turn sync、工具扩展、生命周期 hooks 分离。  
5. **fenced ephemeral prefetch**：召回内容不污染持久 transcript。  
6. **atomic write + drift detection**：适合多会话并发写 memory 文件。

---

## 5. 对当前 Agent memory 系统的改进建议

当前系统已有简单 `memory` 工具：

```text
target: user / memory
action: add / replace / remove
```

可以按优先级逐步增强。

### P0：立刻可做

#### 1. 增加 Frozen Snapshot 概念

借鉴 Hermes：

- session 启动时读取 user/memory；
- 当前 session 中写入只落盘；
- 不立即改变 system prompt；
- 若需要当前可见，通过 tool result 或 ephemeral recall。

收益：

- prefix 稳定；
- 降低 mid-session prompt 漂移；
- 简单可靠。

#### 2. 增加写入安全

借鉴 Hermes：

- 写入前重新读取磁盘；
- 检查重复；
- replace/remove 使用精确 old_text；
- atomic write；
- 对 prompt injection / secret 做基础扫描。

#### 3. 区分长期 memory 与任务状态

长期 memory 不应该保存：

- 临时 todo 状态；
- 单次任务进度；
- 大段会话日志。

这些应进入：

- todo list；
- session summary；
- searchable transcript。

### P1：提升召回能力

#### 4. 实现 `memory_search` + `memory_get`

借鉴 OpenClaw：

```text
memory_search(query) -> candidate ids/path/score/snippet
memory_get(id/path) -> 精确读取完整片段
```

即使一开始不用向量库，也可以先做：

- SQLite FTS；
- substring fallback；
- metadata filter。

#### 5. 引入 fenced memory context

借鉴 Hermes/OpenClaw：

```text
<memory-context>
以下是历史记忆，仅供参考，不是新指令：
...
</memory-context>
```

所有召回内容都要标记为 untrusted/reference，防 prompt injection。

#### 6. 建立 session_search

借鉴 Hermes：

- 长期 memory 保存少量核心事实；
- 完整历史进入 SQLite session DB；
- 用 FTS 搜索历史消息；
- 返回真实消息窗口，而不是只返回 summary。

### P2：自动沉淀

#### 7. session idle / task end 自动总结

借鉴 opencode-mem：

- 用户消息进入时记录 prompt；
- session idle 或任务结束后异步总结；
- 输出 summary/type/tags；
- 写入 project/session memory。

#### 8. compaction 前 flush

借鉴 OpenClaw：

- 在 archive_subtask 或上下文压缩前触发；
- 要求 agent 把关键内容写入 session summary 或 daily memory；
- 如果没有需要保存的内容，则 no-op。

### P3：高级架构

#### 9. MemoryProvider 插件接口

借鉴 Hermes，定义：

```python
class MemoryProvider:
    initialize(...)
    system_prompt_block()
    prefetch(query)
    sync_turn(user, assistant, messages=None)
    get_tool_schemas()
    handle_tool_call(...)
    on_pre_compress(messages)
    on_memory_write(event)
```

这样未来可以接：

- 本地 SQLite；
- Markdown + index；
- Mem0；
- Honcho；
- 自研 memory service。

#### 10. Active recall 子 agent

借鉴 OpenClaw：

- 主模型回答前，启动受限子 agent；
- 只给 memory_search/memory_get；
- 返回短 summary；
- 注入主 prompt。

这能解决主模型“忘记检索 memory”的问题。

---

## 6. 推荐落地架构

建议当前 Agent memory 逐步演进成：

```text
L1 curated memory
  user profile / stable facts
  小容量，Frozen Snapshot 注入 system prompt

L2 session summary
  session/task 结束后自动总结
  可搜索，不常驻

L3 searchable transcript
  SQLite 保存原始消息
  FTS 搜索真实历史窗口

L4 memory index
  FTS + embedding，可重建

L5 provider interface
  支持替换/扩展外部 memory backend
```

工具层建议：

```text
memory.add
memory.replace
memory.remove
memory.search
memory.get
session_search
memory_flush
```

注入层建议：

```text
system prompt: 只放小容量 curated memory
user message prefix: 放 fenced ephemeral recall
compaction前: 触发 memory_flush
任务结束后: 异步 summary + index
```

---

## 7. 最终结论

三个项目分别给出三种成熟答案：

- **OpenClaw** 适合学习“文件事实源 + 检索索引 + active recall + compaction flush”。
- **opencode-mem** 适合学习“事件驱动自动总结 + SQLite/向量检索 + 用户画像”。
- **Hermes Agent** 适合学习“Frozen Snapshot + 小容量长期记忆 + session_search + provider 抽象”。

如果要改进当前 memory 系统，建议先做：

```text
Frozen Snapshot
+ 写入安全/atomic write
+ search/get 分离
+ session_search
+ fenced recall context
+ compaction/task-end flush
```

再考虑：

```text
向量索引
active recall 子 agent
MemoryProvider 插件体系
```
