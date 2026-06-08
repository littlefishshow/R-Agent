# OpenClaw memory 实现调研笔记

调研对象：OpenClaw 官方仓库  
仓库：<https://github.com/openclaw/openclaw>  
调研 commit：`538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`

## 一句话总结

OpenClaw 的 memory 是 **Markdown 文件事实源 + SQLite/FTS/vector 派生索引 + 显式工具检索 + active-memory 子 agent 自动召回 + compaction 前 flush**。

## 分层结构

1. **事实源**：workspace 下的 `MEMORY.md`、`memory/*.md`、可选 `DREAMS.md`。
2. **索引层**：SQLite `chunks` 表、FTS5 表、embedding cache、可选 sqlite-vec `chunks_vec`。
3. **工具层**：`memory_search` 做语义/关键词/hybrid recall；`memory_get` 做精确片段读取。
4. **自动召回层**：`active-memory` 在 `before_prompt_build` 阶段运行 lightweight subagent，只允许 memory 工具，把短 summary 作为 untrusted prefix 注入主 prompt。
5. **压缩保护层**：compaction 前构造 memory flush turn，要求 append 到 `memory/YYYY-MM-DD.md`，防止上下文被压缩后丢失。

## 关键实现

### 1. memory-core 注册

见 `01_memory_core_registration.md`。核心是：

- `api.registerMemoryCapability()` 注册 promptBuilder、flushPlanResolver 和 runtime。
- `api.registerTool()` 注册 `memory_search`、`memory_get`。

### 2. 文件范围与安全读取

见 `04_memory_file_scope.md`、`05_safe_memory_read.md`。

可借鉴点：

- 不允许任意读文件，只允许 memory path。
- 读取前做 workspace containment / symlink escape 检查。
- 文件缺失返回空而不是报错，降低 race condition。
- 输出按行和字符预算截断。

### 3. 索引与检索

见 `06_index_schema_and_watcher.md`、`07_vector_search.md`、`08_keyword_and_fallback_search.md`。

可借鉴点：

- Markdown 是 source of truth；SQLite 是可重建索引。
- vector 优先走 sqlite-vec KNN；不可用时分批 exact scan。
- keyword 走 FTS5/BM25；MATCH 失败 fallback LIKE。
- watcher 监听 memory 文件变动，标记 dirty 并异步 sync。

### 4. 工具使用模式

见 `02_memory_search_tool.md`、`03_memory_get_tool.md`。

推荐模式：

```text
memory_search(query) -> 返回 path/startLine/endLine/score/snippet
memory_get(path, from, lines) -> 精确读取上下文
基于来源证据回答
```

### 5. Active Memory

见 `09_active_memory_recall_prompt.md`、`10_active_memory_subagent.md`、`11_active_memory_injection_hook.md`。

可借鉴点：

- 不依赖主模型主动想起要搜索 memory。
- recall subagent 只能使用 memory tools，不能发消息、不能执行其他高风险工具。
- 只返回短 summary，作为 untrusted metadata 注入。
- 支持 timeout、cache、circuit breaker 等工程保护。

### 6. Compaction Flush

见 `12_compaction_flush_plan.md`。

可借鉴点：

- compaction 前专门触发一次“写 memory”机会。
- 只允许 append 到日记文件，避免污染/覆盖长期 bootstrap 文件。
- 如果无可记内容，允许 silent/no-reply。

## 对当前 agent memory 系统的改进启发

1. **文件事实源 + 可重建索引**：如果 memory 要可审计，优先用 Markdown 保存长期事实，用 SQLite/向量库做派生索引。
2. **search + get 双工具**：search 返回候选，get 做精读，减少 hallucinated recall。
3. **active recall 子 agent**：在主回答前自动召回，并把结果 fenced/untrusted 注入。
4. **compaction 前 flush**：上下文压缩前让 agent 把关键状态写到 session/daily memory。
5. **安全路径白名单**：memory_get/read 必须限制在 memory 目录，防止 memory 工具变成任意文件读取器。
6. **可靠降级**：向量索引失败时 fallback exact scan；FTS 失败时 fallback LIKE。

## 输出文件清单

- `01_memory_core_registration.md`
- `02_memory_search_tool.md`
- `03_memory_get_tool.md`
- `04_memory_file_scope.md`
- `05_safe_memory_read.md`
- `06_index_schema_and_watcher.md`
- `07_vector_search.md`
- `08_keyword_and_fallback_search.md`
- `09_active_memory_recall_prompt.md`
- `10_active_memory_subagent.md`
- `11_active_memory_injection_hook.md`
- `12_compaction_flush_plan.md`
