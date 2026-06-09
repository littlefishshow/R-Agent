# Agent Memory 项目维护进度

> 用途：记录当前 Agent Memory 系统迭代进展，便于重启 Agent 后快速恢复上下文。  
> 依据：`outputs/current_memory_system_improvement_plan.md`、当前 git diff、已完成的 P0 实现与修复。  
> 最近更新日期：2026-06-08

---

## 1. 当前迭代目标

当前正在重构 R-Agent 的 memory 系统，目标是从简单的 `memories/USER.md` / `memories/MEMORY.md` 文件追加机制，逐步演进为更安全、可维护、可检索、可压缩恢复的 Agent Memory 架构。

长期设计分层参考：

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

## 2. 当前总体状态

### 阶段状态

| 阶段 | 状态 | 说明 |
|---|---:|---|
| P0：文件 memory 安全稳定化 | 核心完成，剩余优化 | 已完成 atomic write、duplicate check、unique replace/remove、char limit、基础安全扫描、frozen snapshot 接入与热加载兼容修复；剩余 drift detection / entry id 等增强 |
| P1：memory_search / memory_get | 已实现 minimal 纯文本版 | 已在 Markdown source of truth 上实现行级搜索与分页读取，暂未引入 SQLite |
| P2：session summary / compaction flush | 未开始 | 后续用于复杂任务结束或上下文压缩前沉淀摘要 |
| P3：SQLite FTS / 可检索历史 | 未开始 | 中期扩展，Markdown 可继续作为 source of truth |
| P4：vector index / provider interface | 未开始 | 后续产品化方向 |

---

## 3. 已完成内容

### 2026-06-08：P0 文件 memory 安全加固

涉及文件：

```text
core/memory.py
tools/memory_tool.py
main.py
tests/test_memory_p0.py
```

已完成能力：

1. **Atomic write**
   - 使用 `tempfile.mkstemp()` 创建临时文件；
   - 写入后 `flush + os.fsync()`；
   - 使用 `os.replace()` 原子替换目标文件；
   - 尽量对目录执行 `fsync`，确保 rename 元数据落盘。

2. **并发保护**
   - 使用 `fcntl.flock()` 做进程间 advisory lock；
   - 在 `fcntl` 不可用时退化；
   - 增加 `threading.RLock()`，保证同一进程内线程安全。

3. **Duplicate check**
   - 新增 entry normalize 逻辑；
   - 兼容 bullet list 格式；
   - 重复内容会跳过，不再无限追加。

4. **Unique replace/remove**
   - `old_text` 必须唯一匹配；
   - 0 次匹配时报错；
   - 多次匹配时报错，要求提供更长、更精确的 substring；
   - 避免误替换或误删多个位置。

5. **Char limit**
   - `USER.md` 默认限制 4000 字符；
   - `MEMORY.md` 默认限制 6000 字符；
   - 超限时拒绝写入，要求先 remove/replace 旧 memory。

6. **基础安全扫描**
   - 拒绝明显 prompt injection 内容，例如：
     - `ignore previous instructions`
     - `reveal system prompt`
     - `exfiltrate`
   - 拒绝明显 secret/private key 内容，例如：
     - `api_key :=`
     - `secret_key :=`
     - `access_token :=`
     - `BEGIN PRIVATE KEY`

7. **修复 old_text 无法删除污染内容的问题**
   - 拆分校验逻辑：
     - `_validate_non_empty()`：只检查非空；
     - `_validate_new_content()`：检查非空 + suspicious scan；
   - `remove(old_text)` 与 `replace(old_text)` 不做 suspicious scan；
   - 避免历史中已经存在恶意内容时无法删除。

8. **Frozen Snapshot 语义接入**
   - `MemoryManager` 新增：
     - `read_memory_live()`
     - `load_snapshot()`
     - `read_memory_snapshot()`
   - `main.py` 在 system prompt 初始化时追加：
     - `memory_manager.load_snapshot()`
   - memory tool 写入后只影响落盘和未来 session，不修改当前 frozen system prompt。

9. **工具返回可见性提示**
   - memory tool 成功 add/replace/remove 后，会提示：
     - 已持久化到未来 session；
     - 当前 frozen system prompt 不会被修改。

10. **热加载兼容修复**
    - 曾出现：
      - `cannot import name 'MemoryError' from core.memory`
      - `cannot import name 'MemoryOperationError' from core.memory`
    - 原因：工具热加载时，`tools/memory_tool.py` 对异常类做硬 import，而运行时 `core.memory` 可能还是旧模块缓存。
    - 当前修复：
      - `tools/memory_tool.py` 不再硬 import `MemoryOperationError`；
      - 改为 `getattr(core_memory, "MemoryOperationError", getattr(core_memory, "MemoryError", Exception))`；
      - 保证异常类重命名期间工具模块不因 import 失败而无法注册。

11. **本地 `/mem` 命令读取改造**
    - `main.py` 中 `/mem USER`、`/mem MEMORY` 不再直接 open 文件；
    - 改为调用：
      - `memory_manager.read_target("user")`
      - `memory_manager.read_target("memory")`
    - 读取也走 MemoryManager 的锁和文件初始化逻辑。

12. **测试文件新增**
    - 新增 `tests/test_memory_p0.py`；
    - 覆盖：
      - duplicate skip；
      - replace/remove；
      - char limit；
      - suspicious old_text 可删除；
      - frozen snapshot 不随写入改变；
      - memory tool 返回 frozen visibility 提示。

---


### 2026-06-08：阶段性总结与 git push 准备

- 已按用户要求在 `README.md` 增加「更新日志」，记录本阶段 Agent Memory 系统升级内容与日期。
- 已整理 `.gitignore`，避免提交本地临时仓库、memory lock、TTS 输出等运行时文件，并允许正式 `tests/` 目录纳入版本控制。
- 准备阶段性提交，重点包含 P0/P1 memory 系统改造、维护进度文档、测试文件与 README 更新日志。

---


### 2026-06-08：Memory 目录规范化迁移

涉及文件：

```text
core/memory.py
.gitignore
memories/USER.md
memories/MEMORY.md
R-Agent/memories/USER.md
R-Agent/memories/MEMORY.md
outputs/current_memory_system_improvement_plan.md
outputs/memory_directory_migration_2026-06-08.md
```

迁移内容：

- 将默认活跃 memory 目录从 `R-Agent/memories/` 改为 `memories/`；
- 将旧活跃目录中的真实 `USER.md` / `MEMORY.md` 内容迁移到根目录 `memories/`；
- 原根目录 `memories/` 中的 `<br />` 占位内容已被替换；
- 删除旧嵌套目录下被 git 跟踪的 memory 文件，避免双目录混淆；
- 更新 `.gitignore`，忽略 `memories/.memory.lock`，并继续忽略旧路径 lock；
- 生成迁移备份文档，便于审计。

---


### 2026-06-08：delete_file 安全审批 A 方案修正

涉及文件：

```text
tools/file_tools.py
README.md
outputs/agent_memory_maintenance_progress.md
```

背景与结论：

- 审查 git diff 后发现当前实现实际是 B/token 方案：`delete_file_tool(path, confirm, approval_token)`，permission response 与 schema 中都包含 `approval_token`；
- 用户指出 token 字段可能触发接口安全审核，因此改回 A 方案：只使用 `confirm=true` 作为二次确认信号，不返回、不要求、不注册 `approval_token`；
- 删除沙盒外文件/目录时不再通过 `console.input()` 阻塞等待，统一返回结构化 `permission_required`，避免 rich.status spinner 覆盖隐藏输入。

已完成能力：

1. `delete_file_tool` 签名改为：

```python
def delete_file_tool(path: str, confirm: bool = False) -> str:
```

2. `delete_file` tool schema 仅保留：

```text
path
confirm
```

3. `permission_required` 返回体不含 `approval_token`，只提示用户明确同意后再次传入 `confirm=true`；
4. 工作区外删除也改为非阻塞审批返回，避免继续走 `check_outside_workspace_auth()` 的 `console.input()`；
5. `is_in_sandbox()` / `is_in_workspace()` 改为 `os.path.commonpath()` 边界判断，避免 `sandbox_evil` / 工作区同名前缀路径误判。

---

## 4. 已验证内容

### 2026-06-08

已执行：

```bash
python3 -m py_compile core/memory.py tools/memory_tool.py main.py
```

结果：通过。

已执行手动行为测试，覆盖：

- append 成功；
- duplicate skip；
- replace 唯一替换；
- remove 删除；
- suspicious old_text 可以删除；
- frozen snapshot 写入后不变；
- live memory 能看到新写入；
- memory tool 返回 future sessions / frozen system prompt 提示。

结果：

```text
manual memory P0 checks passed
```

pytest 情况：

```bash
python3 -m pytest tests/test_memory_p0.py -q
```

当前环境未安装 pytest：

```text
No module named pytest
```

因此 pytest 测试文件已写好，但尚未在 pytest 环境中执行。

---


### 2026-06-08：delete_file A 方案验证

已执行：

```bash
python3 -m py_compile tools/file_tools.py
```

结果：通过。

已执行手动行为测试，覆盖：

- `sandbox/tmp_delete_file_review.txt`：沙盒内文件无需确认，直接删除成功；
- `tmp_delete_file_review.txt`：工作区沙盒外文件首次返回 `permission_required=true`，文件仍存在；
- 同一工作区沙盒外文件再次 `confirm=true` 删除成功；
- `/tmp/r_agent_delete_file_review.txt`：工作区外文件首次返回 `permission_required=true`，不再阻塞等待 `console.input()`；
- 返回体与源码中无 `approval_token` / `DELETE_APPROVAL` / `secrets.token` 残留；
- `sandbox_evil/file.txt` 不再被误判为沙盒内，`sandbox/file.txt` 正确识别为沙盒内。

---

## 5. 当前未完成事项

### P0 剩余建议

1. **README 更新日志机制**
   - 用户要求：每次升级/重构/准备 git push 时，需要在 `README.md` 中按日期记录具体更新内容。
   - 当前尚未执行 README 更新，因为本次用户只是提出要求，还未明确进入 git push/升级发布步骤。

2. **决定是否提交运行时 memory 文件**
   - 已将活跃 memory 目录规范化为 `memories/`；旧 `R-Agent/memories/` 不再作为默认路径；
   - 建议后续决定是否将 `R-Agent/memories/*.md` 纳入 git 管理；
   - 如果该 repo 不应提交个人运行时状态，应加入 `.gitignore` 或在提交前 revert。

3. **drift detection**
   - 尚未实现；
   - 建议检测：
     - 文件是否超过硬限制；
     - 文件格式是否偏离预期；
     - 是否出现大量非 bullet 内容；
     - 是否出现异常控制字符；
   - 发现异常时拒绝自动写入，提示人工查看。

4. **更精细的 entry schema / id**
   - 当前 replace/remove 仍基于唯一 substring；
   - 后续可增加 entry id 或 metadata block，避免 substring 操作。

---

## 6. 推荐下一步

### 下一步优先做：P0 增强 drift detection 与 entry id 设计，或进入 P2 session summary

P1-minimal 纯文本版 `memory_search` / `memory_get` 已实现。接下来建议在两个方向中选择：

1. 完成 P0 增强：drift detection、entry id / metadata block；
2. 进入 P2：session summary / compaction flush。

目标工具：

```text
memory_search(query, target?, max_results?)
memory_get(target, from_line?, lines?)
```

建议实现范围：

1. `core/memory.py` 增加：

```python
search_memory(query: str, target: str = "all", max_results: int = 5)
get_memory(target: str, from_line: int = 1, lines: int = 50)
```

2. 新增或扩展工具：

```text
tools/memory_read_tool.py
```

或在 `tools/memory_tool.py` 内注册两个新工具：

```text
memory_search
memory_get
```

3. 测试覆盖：

- 搜索 USER；
- 搜索 MEMORY；
- target=all；
- max_results 限制；
- 空 query 错误；
- get 分页；
- from_line 越界；
- lines 上限保护。

4. 工具设计约束：

- `memory_search` 只返回摘要 snippet 和位置信息；
- `memory_get` 用于按行读取完整上下文；
- 为未来 SQLite FTS 保持接口稳定。

---

## 7. 重要用户约定

用户已明确要求：

1. 当前项目是一个 Agent 项目，正在维护升级。
2. 当用户要求升级换代、重构、准备 `git push` 时，需要把升级内容保存到 `README.md`。
3. 每次更新需要在 `README.md` 中给出：
   - 具体更新内容；
   - 对应日期；
   - 作为更新日志。
4. 当前 Agent memory 项目迭代需要在 `outputs/` 中维护进度文档，便于重启后恢复上下文。

---

## 8. 快速恢复提示

如果下次重启后继续推进，建议先看：

```text
outputs/current_memory_system_improvement_plan.md
outputs/agent_memory_maintenance_progress.md
git diff -- tools/file_tools.py README.md outputs/agent_memory_maintenance_progress.md
```

然后优先继续实现：

```text
继续评估 delete_file A 方案是否需要为 read/write/search 的工作区外阻塞 input 做同类 permission_required 改造；或推进 P0/P2 memory 后续事项
```
