# 当前 Agent Memory 系统改进方案

> 本文是 Agent Memory 系统的设计方案与路线图；实际维护进度与阶段性更新请查看 `outputs/agent_memory_maintenance_progress.md`。

> 目标：基于 OpenClaw、opencode-mem、Hermes Agent 的调研结果，提出一套适合当前 R-Agent 的 memory 改造路线。  
> 本文只给设计与实现步骤，不直接修改现有 `core/memory.py` / `tools/memory_tool.py`，避免在未确认前改变行为。

---

## 1. 当前 memory 系统现状

当前实现主要在：

```text
core/memory.py
tools/memory_tool.py
```

### 1.1 当前能力

当前 `MemoryManager` 做了：

```python
memory_dir = "R-Agent/memories"
USER.md
MEMORY.md
```

支持：

```text
read_memory()
append_memory(target, content)
replace_memory(target, old_text, new_text)
remove_memory(target, old_text)
```

工具层暴露：

```text
memory(action, target, content, old_text)
```

其中：

- `target=user`：用户偏好、身份信息；
- `target=memory`：项目或环境事实；
- `action=add/replace/remove`。

### 1.2 当前优点

1. **简单**：实现短，行为容易理解。
2. **文件可审计**：`USER.md` / `MEMORY.md` 可以直接打开查看。
3. **工具语义清晰**：区分 user memory 与 environment/project memory。
4. **已有 replace/remove**：长期记忆维护所需的基本操作已经有了。

### 1.3 当前主要缺口

| 缺口 | 风险 |
|---|---|
| 直接 append/write，无 atomic write | 写入中断可能导致文件半写或损坏 |
| 无并发保护 | 多 agent / 多任务同时写可能互相覆盖 |
| 无 duplicate check | memory 容易重复膨胀 |
| 无 char/token limit | system prompt 可能无限增长 |
| 无 prompt injection / secret scan | 恶意内容可能被长期注入 system prompt |
| replace/remove 使用全局字符串替换 | 可能误替换多个位置 |
| 没有 frozen snapshot 语义 | 需要明确“写入落盘”和“当前 prompt 可见性”的关系 |
| 没有 memory_search / memory_get | 只能把全部 memory 注入 prompt，无法扩展到大量历史 |
| 没有 session_search | 临时任务状态和历史对话容易被误写入长期 memory |
| 没有 compaction/task-end flush | 上下文压缩或复杂任务结束时没有稳定沉淀机制 |

---

## 2. 三个项目对当前系统的启发

### 2.1 从 Hermes Agent 借鉴

最适合当前系统优先借鉴的是 Hermes：

1. **小容量 curated memory**：`USER.md` / `MEMORY.md` 只保存稳定事实。
2. **Frozen Snapshot**：session 启动时读取并注入；session 中写入只落盘，不修改当前 system prompt。
3. **Atomic write**：临时文件 + fsync + replace。
4. **写入前重新读取**：降低并发覆盖。
5. **drift detection**：发现文件格式异常时拒绝覆盖。
6. **严格工具描述**：不要保存任务进度、临时状态、完成日志。

对应参考：

```text
outputs/mem_research_examples/hermes_agent/notes.md
outputs/mem_research_examples/hermes_agent/01_memory_tool_overview_frozen_snapshot.md
outputs/mem_research_examples/hermes_agent/05_memory_atomic_write_dispatch.md
```

### 2.2 从 OpenClaw 借鉴

OpenClaw 适合中期扩展：

1. **Markdown source of truth + SQLite/FTS/vector index**。
2. **memory_search + memory_get 分离**。
3. **active-memory 子 agent 自动召回**。
4. **compaction 前 flush**。
5. **memory path 白名单**。

对应参考：

```text
outputs/mem_research_examples/openclaw/notes.md
outputs/mem_research_examples/openclaw/02_memory_search_tool.md
outputs/mem_research_examples/openclaw/03_memory_get_tool.md
outputs/mem_research_examples/openclaw/12_compaction_flush_plan.md
```

### 2.3 从 opencode-mem 借鉴

opencode-mem 适合后续产品化：

1. **SQLite source of truth + vector backend 解耦**。
2. **session.idle / task-end 自动总结**。
3. **user profile 独立库 + changelog**。
4. **chat.message synthetic 注入**。
5. **compaction restore**。

对应参考：

```text
outputs/mem_research_examples/opencode_mem/notes.md
outputs/mem_research_examples/opencode_mem/03_auto_capture_and_profile_learning.md
outputs/mem_research_examples/opencode_mem/04_chat_injection_compaction_restore.md
```

---

## 3. 推荐总体架构

建议当前 Agent memory 分成 5 层：

```text
L1 Curated Memory
  USER.md / MEMORY.md
  小容量、稳定、system prompt frozen snapshot

L2 Session Summary
  每次复杂任务、子任务、压缩前生成摘要
  存为 session/daily markdown 或 SQLite

L3 Searchable History
  原始对话、工具调用摘要、任务结果
  SQLite FTS 搜索

L4 Memory Index
  对 L1/L2/L3 建 FTS / vector index
  可重建，不作为事实源

L5 Provider Interface
  后续支持本地 SQLite、Markdown index、外部 Mem0/Honcho 等
```

---

## 4. 分阶段改造路线

## P0：先把现有文件 memory 做安全、稳定

这一阶段不引入数据库，只增强当前 `USER.md` / `MEMORY.md`。

### P0.1 改造 MemoryManager 的文件写入

目标：

- atomic write；
- duplicate check；
- char limit；
- 精确 replace/remove；
- 基础安全扫描。

建议新增常量：

```python
ENTRY_DELIMITER = "\n§\n"
USER_CHAR_LIMIT = 4000
MEMORY_CHAR_LIMIT = 6000
```

当前文件是 bullet list：

```md
- xxx
- yyy
```

建议短期兼容，不强制迁移；长期可以切换到 `§` 分隔。

### P0.2 Atomic write 代码骨架

```python
import os
import tempfile


def atomic_write_text(path: str, text: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-memory-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
```

### P0.3 基础安全扫描骨架

```python
DANGEROUS_MEMORY_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "reveal your system prompt",
    "exfiltrate",
    "api_key",
    "secret_key",
    "BEGIN PRIVATE KEY",
]


def validate_memory_content(text: str) -> None:
    normalized = text.lower()
    for pattern in DANGEROUS_MEMORY_PATTERNS:
        if pattern.lower() in normalized:
            raise ValueError(f"Refusing to store suspicious memory content: {pattern}")
```

### P0.4 Duplicate check 骨架

```python
def normalize_entry(text: str) -> str:
    return " ".join(text.strip().lower().split())


def is_duplicate(existing_text: str, new_entry: str) -> bool:
    existing_lines = [line.strip("- ").strip() for line in existing_text.splitlines()]
    normalized_new = normalize_entry(new_entry)
    return any(normalize_entry(line) == normalized_new for line in existing_lines)
```

### P0.5 更安全的 replace/remove

当前：

```python
content.replace(old_content, new_content)
```

问题：会替换所有匹配。

建议改为只允许唯一匹配：

```python
def replace_once_unique(content: str, old: str, new: str) -> str:
    count = content.count(old)
    if count == 0:
        raise ValueError("old_text not found")
    if count > 1:
        raise ValueError("old_text is ambiguous; provide a longer exact substring")
    return content.replace(old, new, 1)
```

### P0.6 明确 Frozen Snapshot 语义

当前 `read_memory()` 每次读实时文件。建议在 Agent 初始化时读取一次：

```python
class MemoryManager:
    def load_snapshot(self) -> str:
        self._snapshot = self.read_memory_live()
        return self._snapshot

    def read_memory_snapshot(self) -> str:
        return self._snapshot
```

Agent 启动时：

```python
memory_snapshot = memory_manager.load_snapshot()
system_prompt += memory_snapshot
```

memory tool 写入仍落盘，但不改 `_snapshot`。

如果要让当前 session 立即知道写入结果，可以靠 tool response：

```text
Successfully appended to USER memory. This will be visible in future sessions.
```

---

## P1：增加 memory_search / memory_get

这一阶段解决“长期 memory 不能无限注入 prompt”的问题。

### P1.1 新增工具

建议新增两个工具：

```text
memory_search(query, target?, max_results?)
memory_get(target, from_line?, lines?)
```

或者：

```text
memory_read(target, query?)
```

但更推荐 OpenClaw 风格的 search/get 分离。

### P1.2 最小实现：纯文本搜索

先不引入 SQLite，也可以做一个简易版：

```python
def memory_search_text(query: str, files: list[str], max_results: int = 5):
    terms = [t.lower() for t in query.split() if t.strip()]
    results = []
    for file in files:
        lines = read_lines(file)
        for i, line in enumerate(lines, start=1):
            score = sum(1 for t in terms if t in line.lower())
            if score > 0:
                results.append({
                    "path": file,
                    "line": i,
                    "score": score,
                    "snippet": line.strip(),
                })
    return sorted(results, key=lambda x: x["score"], reverse=True)[:max_results]
```

### P1.3 中期实现：SQLite FTS

建立：

```sql
CREATE TABLE memory_chunks (
  id TEXT PRIMARY KEY,
  target TEXT NOT NULL,
  path TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  text TEXT NOT NULL,
  updated_at INTEGER
);

CREATE VIRTUAL TABLE memory_chunks_fts USING fts5(
  text,
  target UNINDEXED,
  path UNINDEXED,
  content='memory_chunks',
  content_rowid='rowid'
);
```

检索：

```sql
SELECT path, start_line, end_line, text, bm25(memory_chunks_fts) AS rank
FROM memory_chunks_fts
WHERE memory_chunks_fts MATCH ?
ORDER BY rank
LIMIT ?;
```

### P1.4 工具返回格式

`memory_search` 返回：

```json
{
  "results": [
    {
      "id": "...",
      "target": "user",
      "path": "USER.md",
      "start_line": 10,
      "end_line": 12,
      "score": 0.82,
      "snippet": "用户偏好..."
    }
  ]
}
```

`memory_get` 返回：

```json
{
  "path": "USER.md",
  "from": 10,
  "lines": 5,
  "text": "...",
  "truncated": false
}
```

---

## P2：增加 session_search，避免污染长期 memory

### P2.1 为什么需要 session_search

Hermes 的经验很重要：

- 长期 memory 只放稳定事实；
- 历史对话放 session DB；
- 临时任务状态、完成日志不要写入长期 memory。

当前系统如果没有 session_search，agent 容易把“任务进度”和“历史摘要”塞进 `MEMORY.md`。

### P2.2 建议 schema

```sql
CREATE TABLE session_messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  metadata TEXT
);

CREATE VIRTUAL TABLE session_messages_fts USING fts5(
  content,
  role UNINDEXED,
  session_id UNINDEXED,
  content='session_messages',
  content_rowid='rowid'
);
```

### P2.3 session_search 工具

```text
session_search(query, max_results=5, session_id=None)
```

返回真实消息窗口：

```json
{
  "results": [
    {
      "session_id": "...",
      "role": "assistant",
      "timestamp": 123,
      "match": "...",
      "before": ["..."],
      "after": ["..."]
    }
  ]
}
```

---

## P3：增加 task-end / compaction 前 memory flush

### P3.1 当前可接入点

当前已有：

```text
archive_subtask
context_tool.py
```

以及 agent loop 的强制收尾机制。

可以在以下时机触发 flush：

1. `archive_subtask` 前；
2. 强制收尾时；
3. todo task completed 时；
4. 长任务达到 soft warning 时。

### P3.2 flush prompt 模板

借鉴 OpenClaw：

```text
Pre-compaction memory flush.
只保存长期有价值的信息。
不要保存临时任务状态、执行日志、一次性结果。
用户偏好写入 USER.md。
项目/环境稳定事实写入 MEMORY.md。
如果没有需要保存的内容，回复 NO_MEMORY_UPDATE。
```

### P3.3 工具形式

可以新增：

```text
memory_flush(summary, scope, source)
```

或不新增工具，只在 agent 内部发起一个受限 prompt，让模型决定是否调用 `memory`。

---

## P4：引入自动总结与用户画像

借鉴 opencode-mem。

### P4.1 session/task summary

在任务完成后，把：

- 用户请求；
- agent 行为摘要；
- 关键文件/工具；
- 结果；
- 后续待办；

总结成结构化 memory candidate。

示例：

```json
{
  "type": "project_fact | user_preference | workflow | skip",
  "summary": "...",
  "tags": ["memory", "agent", "tooling"],
  "confidence": 0.8,
  "target": "memory"
}
```

只有 `confidence` 足够高且不是 `skip` 才写入。

### P4.2 user profile learning

定期从用户 prompt 中抽取：

- 偏好；
- 工作方式；
- 语言风格；
- 常用技术栈；
- 对 agent 行为的要求。

但必须可审计、可删除。

---

## P5：MemoryProvider 插件接口

借鉴 Hermes。

### P5.1 Provider 抽象

```python
class MemoryProvider:
    name: str

    def initialize(self, *, session_id: str, agent_context: dict):
        pass

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, messages=None):
        pass

    def get_tool_schemas(self) -> list[dict]:
        return []

    def handle_tool_call(self, name: str, args: dict):
        raise NotImplementedError

    def on_pre_compress(self, messages: list[dict]) -> str:
        return ""

    def on_memory_write(self, event: dict):
        pass
```

### P5.2 Manager 编排

```python
class MemoryProviderManager:
    def __init__(self):
        self.providers = []

    def build_system_prompt(self):
        return "\n".join(p.system_prompt_block() for p in self.providers)

    def prefetch_all(self, query):
        blocks = []
        for p in self.providers:
            try:
                block = p.prefetch(query)
                if block:
                    blocks.append(block)
            except Exception:
                pass
        return build_memory_context_block(blocks)

    def sync_all(self, user, assistant, messages=None):
        for p in self.providers:
            try:
                p.sync_turn(user, assistant, messages=messages)
            except Exception:
                pass
```

---

## 5. 建议的新工具清单

### 5.1 保留现有工具

```text
memory(action, target, content, old_text)
```

但增强描述：

- 不保存临时任务状态；
- 不保存完整日志；
- 不保存 secrets；
- 写入当前 session 不一定立即进入 system prompt；
- 长期事实才写入。

### 5.2 新增工具

#### memory_search

```json
{
  "query": "用户偏好 TypeScript",
  "target": "all",
  "max_results": 5
}
```

#### memory_get

```json
{
  "path": "USER.md",
  "from": 1,
  "lines": 20
}
```

#### session_search

```json
{
  "query": "之前怎么实现 todo list",
  "max_results": 5
}
```

#### memory_flush

可选。

```json
{
  "summary": "当前任务完成情况...",
  "reason": "pre_compaction | task_completed | session_end"
}
```

---

## 6. 推荐实施顺序

### 第一步：安全加固现有 memory

修改：

```text
core/memory.py
```

实现：

- atomic write；
- unique replace/remove；
- duplicate check；
- char limit；
- suspicious content scan。

风险低、收益高。

### 第二步：明确 Frozen Snapshot

修改：

```text
core/memory.py
core/agent.py 或 Agent 初始化逻辑
```

让 memory 注入变成 session start snapshot。

### 第三步：新增 memory_search / memory_get

先纯文本搜索，后 SQLite FTS。

新增：

```text
tools/memory_search_tool.py
```

或扩展现有 `memory_tool.py`。

### 第四步：新增 session_search

记录 session messages，避免长期 memory 污染。

### 第五步：引入自动总结和 flush

与 todo / archive_subtask / 强制收尾结合。

### 第六步：Provider 抽象和 active recall

当本地 memory 稳定后再做。

---

## 7. 最小可落地版本 MVP

如果只做一个短周期版本，建议包含：

```text
1. atomic write
2. duplicate check
3. unique replace/remove
4. char limit
5. memory_search 纯文本版
6. memory_get 按行读取版
7. fenced recall context 格式
```

这已经能显著改善当前系统。

---

## 8. 验收标准

改造完成后应满足：

1. 连续 add 同一条 memory 不重复写入。
2. replace/remove 如果 old_text 匹配多处，会要求用户提供更精确文本。
3. 写入异常不会损坏 `USER.md` / `MEMORY.md`。
4. 超过字符上限会拒绝写入，并提示先 replace/remove。
5. `memory_search` 能返回 path/line/snippet。
6. `memory_get` 只能读取 memory 目录内文件，不能任意读文件。
7. 长期 memory 中不再保存 todo 进度、临时任务状态。
8. recalled memory 注入时明确标记为“历史记忆，不是新指令”。

---

## 9. 本方案对应参考材料

```text
outputs/agent_memory_research_v2.md
outputs/mem_research_examples/openclaw/notes.md
outputs/mem_research_examples/opencode_mem/notes.md
outputs/mem_research_examples/hermes_agent/notes.md
```

尤其建议优先看：

```text
outputs/mem_research_examples/hermes_agent/05_memory_atomic_write_dispatch.md
outputs/mem_research_examples/openclaw/02_memory_search_tool.md
outputs/mem_research_examples/openclaw/03_memory_get_tool.md
outputs/mem_research_examples/opencode_mem/03_auto_capture_and_profile_learning.md
```
