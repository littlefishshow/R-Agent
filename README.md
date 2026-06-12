# R-Agent 架构与构建计划

## 1. Hermes-agent 架构分析

通过分析 `hermes-agent` 的项目结构和开发文档，我们可以看出一个成熟的 AI Agent 包含以下几个核心模块：

1. **核心对话循环 (Core Agent Loop - `run_agent.py`)**：
   这是 Agent 的“大脑”。它负责维护一个完整的对话循环（`while` 循环），将用户的输入发送给 LLM（如 OpenAI、Anthropic），检查 LLM 的返回是否包含工具调用（Tool Calls）。如果有，则暂停对话，执行工具，将结果拼接到上下文中并再次请求 LLM，直到 LLM 给出最终的文本回复。

2. **工具注册与调度系统 (Tool Orchestration - `model_tools.py`, `tools/registry.py`)**：
   Agent 需要“手和脚”来操作环境。该系统负责将普通的 Python 函数转换成 LLM 能理解的 JSON Schema，并在 LLM 要求调用时，通过路由找到对应的函数并执行。

3. **用户交互入口 (CLI / Gateway - `cli.py`, `gateway/`)**：
   Agent 的“嘴巴和耳朵”。负责接收用户的指令，并以友好的方式（如终端流式输出、进度条）将 Agent 的思考和操作过程展示给用户。

4. **状态与记忆管理 (State & Memory - `hermes_state.py`)**：
   用于持久化存储对话历史、工具执行结果和系统配置，保证多轮对话的连贯性。

## 2. 构建 R-Agent：第一步需要完成什么？

要从零构建属于我们自己的 `R-Agent`，**最先需要完成的是“核心对话循环”和“工具调度系统”**。
没有交互界面，我们可以用简单的 `input()` 替代；没有持久化记忆，我们可以先在内存中维护一个 `messages` 列表。但如果没有对话循环和工具调用机制，它就只是一个普通的 LLM 包装器，而不是 Agent。

### R-Agent 逐步演进计划 (从简单到复杂)

- **Phase 1: 基础骨架 (MVP)** ✅
  - 实现最简工具注册表（将 Python 函数包装为 Tool Schema）。
  - 实现核心的 `AIAgent` 类和 `while` 循环逻辑。
  - 实现一个极其简单的命令行交互脚本。
- **Phase 2: 基础工具接入**
  - 添加文件读取、文件写入工具。
  - 添加简单的终端命令执行工具。
- **Phase 3: 状态与记忆**
  - 实现多轮对话的历史记录管理。
- **Phase 4: 完善体验** ✅
  - 添加流式输出、终端 UI 美化（对标 hermes-agent 的 CLI，已通过 `rich` 库实现）。

---

我们已经完成了基础骨架和终端 UI 美化（Phase 1 & Phase 4）。
请通过 `pip install -r requirements.txt` 安装依赖，然后运行 `python main.py` 体验美观的 CLI 交互！

## 3. 环境配置

R-Agent 采用纯环境变量配置，不再使用任何本地 JSON 配置文件。请在项目根目录下创建一个 `.env` 文件（可以参考 `.env.example`）来配置你的环境：

```env
# 1. 客户端类型 (openai 或 azure)
LLM_CLIENT_TYPE="azure"

# 2. 你的 API 密钥
OPENAI_API_KEY="你的_API_KEY"

# 3. 模型名称 (OpenAI模式) 或 接入点名称 (Azure模式)
LLM_MODEL="gpt-4o"
```

---

## 更新日志

### 2026-06-09

#### 简化版 SOUL.md 主身份迁移

- **迁移 Hermes SOUL.md 核心机制**：将 `SOUL.md` 作为 R-Agent 的项目级主身份/persona 文件，system prompt 构建时优先加载，缺失或为空时回退到 `DEFAULT_AGENT_IDENTITY`。
- **默认模板与初始化**：`core/prompt_builder.py` 新增 `ensure_default_soul_md()`，首次构建 prompt 时自动创建项目根目录 `SOUL.md`，且不会覆盖用户已有内容。
- **安全与长度控制**：加载 `SOUL.md` 时增加基础 prompt injection / secret-exfiltration 扫描，并对超长内容做 head/tail 截断，避免无界注入 system prompt。
- **CLI 接入**：`main.py` 改为通过 `build_system_prompt()` 构建基础 system prompt，再叠加自我进化提示和 frozen memory snapshot，保留当前 memory 语义。
- **模板更新**：更新根目录 `SOUL.md` 为简洁中文默认行为/persona 说明，便于用户直接编辑定制。
- **验证结果**：已通过 `python3 -m py_compile core/prompt_builder.py main.py`，并手动确认 `SOUL.md` 成功作为 identity slot 加载。

#### 文件/代码工具风险审批非阻塞修复

- **修复工作区外文件操作授权不可用问题**：`read_file` / `write_file` / `search_files` 不再通过隐藏的 `console.input()` 等待终端输入，避免用户在 API/CLI 工具调用过程中无法授权或白名单的问题。
- **统一结构化二次确认**：工作区外读取、写入、搜索首次调用会返回 `permission_required=true`、风险原因、绝对路径与 `next_call_example`；用户在对话中明确同意后，Agent 可再次调用并传入 `allow_outside_workspace=true`。
- **危险 Python 执行审批修复**：`run_python` 检测到 `os.remove` / `shutil.rmtree` 等删除代码时，同样改为非阻塞 `permission_required`，明确同意后通过 `allow_dangerous_code=true` 二次执行。
- **保留命令审批机制**：`run_command` 仍使用 `permission_required + approval_token + allow_high_privilege=true` 的一次性 token 审批，未改动其高风险命令拦截模型。
- **验证结果**：已通过 `python3 -m py_compile tools/file_tools.py tools/sys_tools.py`，并手动验证工作区外搜索/读取/写入与危险 Python 首次调用均返回结构化审批请求，不再尝试读取终端输入。

### 2026-06-08

#### Agent Memory 系统阶段性升级

- **P0 文件 memory 安全加固**：重构 `core/memory.py`，为 `USER.md` / `MEMORY.md` 增加 atomic write、进程/线程锁、duplicate check、unique replace/remove、char limit 与基础 prompt injection / secret 扫描。
- **Frozen Snapshot 语义**：新增 `load_snapshot()`、`read_memory_snapshot()`、`read_memory_live()`；`main.py` 在启动 system prompt 时加载一次 memory snapshot，memory tool 写入只影响落盘与未来会话。
- **Memory 工具稳定性**：更新 `tools/memory_tool.py`，统一返回 `success/error` JSON，补充“当前 frozen system prompt 不会被修改”的可见性提示，并兼容异常类重命名导致的热加载问题。
- **P1-minimal 检索能力**：新增 `tools/memory_read_tool.py`，注册 `memory_search` 与 `memory_get`，支持在 `USER.md` / `MEMORY.md` 上进行纯文本行级搜索和分页读取，为后续 SQLite FTS/vector index 保持接口稳定。
- **CLI memory 读取修正**：`/mem USER`、`/mem MEMORY` 改为通过 `MemoryManager.read_target()` 读取，避免绕过锁与文件初始化逻辑。
- **测试与验证**：新增 memory P0/P1 测试文件；当前环境未安装 pytest，已用手动脚本验证 P0/P1 核心行为，并通过 `python3 -m py_compile core/memory.py tools/memory_tool.py tools/memory_read_tool.py main.py`。
- **维护进度文档**：新增并维护 `outputs/agent_memory_maintenance_progress.md`，记录当前 Agent Memory 迭代进展、已完成事项、验证结果和下一步建议；保留 `outputs/current_memory_system_improvement_plan.md` 作为设计路线图。
- **工程清理**：调整 `.gitignore`，避免提交本地临时仓库、memory lock 等运行时文件，并允许纳入正式 `tests/` 目录。
- **Memory 目录规范化**：将默认活跃 memory 目录从 `R-Agent/memories/` 迁移为仓库根目录 `memories/`，迁移真实 USER/MEMORY 内容，删除旧嵌套 tracked 文件，并补充迁移备份文档。


#### delete_file 安全审批 A 方案修正

- **移除审批 token 字段**：将 `tools/file_tools.py` 中的 `delete_file_tool` 从 token/B 方案修正为 A 方案，仅保留 `path` 与 `confirm` 参数，避免 `approval_token` 等字段触发接口安全审核。
- **非阻塞危险删除审批**：删除沙盒外文件/目录时不再调用 `console.input()` 阻塞等待输入，而是首次返回 `permission_required=true`；只有用户明确同意后，Agent 才可再次调用并传入 `confirm=true` 执行。
- **覆盖工作区外路径**：工作区外删除同样改为结构化审批返回，不再触发隐藏输入问题，并在返回消息中提示更高风险与绝对路径核对。
- **路径边界加固**：将 `is_in_sandbox()` / `is_in_workspace()` 从字符串 `startswith` 判断改为 `os.path.commonpath()`，避免 `sandbox_evil` 等前缀路径被误判为沙盒内。
- **验证结果**：已通过 `python3 -m py_compile tools/file_tools.py`，并手动验证沙盒内直接删除、沙盒外首次不删除、`confirm=true` 后删除、工作区外首次返回审批、无 `approval_token` 残留。

#### 下一步建议

- 补齐 P0 增强：drift detection、entry id / metadata block。
- 进入 P2：实现 session summary / compaction flush，避免把临时任务状态误写入长期 memory。

