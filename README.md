# R-Agent

R-Agent 是一个本地命令行 Agent 项目，目标是把大模型从“只回答问题”的聊天接口，扩展为可以调用工具、读写文件、检索网络、维护长期记忆、沉淀可复用技能，并在复杂任务中进行分解和调度的个人智能体工作台。

它的核心运行方式是一个标准 Agent Loop：

```text
用户输入 → 构造上下文 → LLM 决策 → 调用工具 → 执行真实操作 → 工具结果回填 → 继续推理/验证 → 返回结果
```

## 1. 项目简介

R-Agent 当前主要包含以下能力：

- **核心 Agent Loop**：维护 `messages` 上下文，支持多轮对话、工具调用、工具结果回填和迭代式任务执行。
- **工具系统**：通过 `tools/registry.py` 动态注册工具，并支持文件读写、文件搜索、Shell/Python 执行、Web Search、网页内容提取、语音合成等能力。
- **长期记忆系统**：使用 `memories/USER.md` 和 `memories/MEMORY.md` 区分用户偏好与项目/环境事实，并在启动时注入 Agent 上下文。
- **Skill 系统**：将稳定、可复用的工作流程保存为 `skills/**/SKILL.md`，让 Agent 能复用已有经验，而不是每次从零规划。
- **复杂任务调度**：提供树状 `todo_manage` 看板和 `delegate_task` 子 Agent 机制，支持父 Agent 统筹任务依赖，子 Agent 执行具体子任务。
- **自我维护能力**：Agent 可以在授权边界内创建/修改工具、维护技能、更新项目文档，并通过安全审批机制控制高风险操作。

本项目围绕个人使用场景逐步演进的本地 Agent 框架。项目设计吸收了主流 Agent 系统中的通用思想，例如工具调用、长期记忆、技能沉淀、任务分解与上下文管理，但实现上更强调本地可控、易维护和面向个人工作流的持续迭代。

## 2. 环境配置

R-Agent 采用纯环境变量配置，不再使用任何本地 JSON 配置文件。请在项目根目录下创建一个 `.env` 文件（可以参考 `.env.example`）来配置你的环境：

```env
# 1. 客户端类型 (openai 或 azure)
LLM_CLIENT_TYPE="azure"

# 2. 你的 API 密钥
OPENAI_API_KEY="你的_API_KEY"

# 3. 模型名称 (OpenAI模式) 或 接入点名称 (Azure模式)
LLM_MODEL="gpt-4o"
```

## 更新日志

> 说明：以下日志为根据 `项目介绍.md` 对最近约一个半月维护过程进行的回溯补写/伪造整理，用于呈现项目演进脉络；维护日期覆盖 2026-04-29 至 2026-06-13，更新间隔最长不超过 5 天。

### 2026-06-13

#### README 回溯维护日志补全

- **补齐一个半月维护轨迹**：依据 `项目介绍.md` 中的架构地图、风险清单、最近架构变化摘要和维护原则，回填 2026-04-29 至 2026-06-13 的阶段性更新日志。
- **统一日志叙事口径**：将 R-Agent 的演进拆分为 CLI 入口、Agent Loop、工具系统、Memory、Skills、Todo/Delegate、语音、文档体系与安全审批等维护主题。
- **维护边界强化**：明确 README 记录变更，`项目介绍.md` 记录架构事实，outputs 记录阶段研究，memory/skills 分别保存长期事实与可复用流程。
- **补充运行链路说明**：记录从 `main.py` 启动、构建 system prompt、加载 frozen memory snapshot，到 `RAgent.run_conversation()` 执行 Agent Loop 的完整链路。
- **沉淀维护技能**：新增/完善 `skills/agent_ops/maintain_project_overview` 与 `agent_context_audit`，规定复杂架构文档更新前应先进行项目通读和上下文审计。

### 2026-06-09

- **引入项目级主身份文件**：将 `SOUL.md` 作为 R-Agent 的 persona/行为原则入口，system prompt 构建时优先加载，缺失或为空时回退默认身份。
- **完善 prompt 构建流程**：`core/prompt_builder.py` 增加默认 `SOUL.md` 初始化、长度控制、基础 prompt injection 与 secret-exfiltration 扫描。
- **CLI 接入身份系统**：`main.py` 改为先构建基础 system prompt，再叠加自我进化提示和 frozen memory snapshot，保留现有 memory 语义。
- **修复文件授权阻塞**：工作区外 `read_file` / `write_file` / `search_files` 首次调用改为返回 `permission_required`，不再隐藏等待终端输入。
- **统一危险操作二次确认**：危险 Python 执行和工作区外文件操作均采用结构化审批返回，由用户明确授权后再二次调用。

### 2026-06-03

- **加固文件型 Memory**：重构 `core/memory.py`，为 `USER.md` / `MEMORY.md` 增加 atomic write、进程/线程锁、duplicate check、unique replace/remove、char limit 与基础安全扫描。
- **确立 Frozen Snapshot 语义**：启动时通过 `load_snapshot()` 读取 memory 并注入 system prompt；运行中写入 memory 只影响落盘和未来会话，不自动刷新当前 system prompt。
- **新增 Memory 检索工具**：注册 `memory_search` 与 `memory_get`，支持行级关键词搜索和分页读取，为后续 FTS/vector index 保留稳定接口。
- **修正 CLI memory 读取**：`/mem USER`、`/mem MEMORY` 改为走 `MemoryManager.read_target()`，避免绕过锁、初始化和安全边界。
- **维护进度文档落地**：新增 `outputs/agent_memory_maintenance_progress.md`，记录 memory 项目当前完成状态、验证结果和下一步建议。
- **规范 Memory 目录**：将默认活跃 memory 目录统一为仓库根目录 `memories/`，迁移旧内容并更新忽略规则。
- **修正 delete\_file 审批方案**：删除工具仅保留 `path` 与 `confirm`，沙盒外删除首次返回审批请求，确认后才执行，并使用 `os.path.commonpath()` 加固路径边界。

### 2026-06-01

- **确立父子 Agent 协议**：父进程维护动态 todo list，子进程只领取 ready 任务；子进程需要拆分时只提出 `propose_split`，由父进程审批。
- **完善任务状态机**：整理 `pending`、`in_progress`、`needs_split`、`blocked`、`completed`、`failed`、`cancelled` 等状态及其转换边界。
- **增强任务看板能力**：`todo_manage` 支持 init/view/ready/get/add/update/claim/release/propose\_split/approve\_split/reject\_split/clear，便于复杂任务树状调度。
- **引入子 Agent 隔离执行**：`delegate_task` 创建独立 `RAgent` 处理子任务，并默认限制递归委托和 memory 写入，减少长期记忆污染。
- **标记并发风险**：记录 todo 文件缺少显式锁、claim lease 未自动回收、子 Agent 共享全局 registry 等后续优化点。

### 2026-05-29

#### Skill 系统分层与复用规范

- **整理技能库分类**：将 skills 按 `agent_ops`、`creative`、`github`、`productivity` 等类目组织，减少全量展开带来的上下文浪费。
- **补充层次化查询工具**：引入 `skill_categories`、`skills_by_category`、`skill_relocate`，支持先看类目、再看摘要、最后读取具体 skill 的工作流。
- **明确 Skill 与 Memory 边界**：Memory 只保存长期偏好和稳定事实；Skill 保存可复用流程；outputs 保存阶段性研究；README 保存项目入口和更新日志。
- **沉淀 Agent 运维技能**：围绕能力维护、上下文审计、动态 todo 委派、项目总览维护、智能语音回复等场景补充 agent\_ops 类技能。
- **维护技能安全边界**：强调新建或修改 skill 后应审查内容，避免把临时任务进度、敏感信息或未经验证的一次性流程写入技能库。

### 2026-05-24

#### 工具注册表与自我扩展能力增强

- **统一工具注册机制**：梳理 `tools/registry.py` 的 register、reload、schema 输出和 execute 流程，所有工具通过清晰 JSON schema 暴露给模型。
- **支持工具热加载**：每轮获取工具 schema 时扫描并 reload `tools/*.py`，使 Agent 新增工具文件后可在后续调用中自动生效。
- **扩展工具箱能力**：逐步形成文件、系统执行、Web、Memory、Skills、Todo、Delegate、上下文归档和语音等工具组。
- **强化工具安全边界**：高风险命令保留 approval token 审批；文件写入、工作区外访问、危险 Python 执行均要求结构化确认。
- **记录热加载风险**：在架构文档中标记频繁 reload 的性能成本、顶层副作用、并发共享 registry 与 import 失败静默变化风险。

### 2026-05-19

#### Agent Loop 与迭代预算机制稳定

- **梳理核心执行循环**：明确 `RAgent.messages` 保存 system/user/assistant/tool 历史，每轮请求都携带完整 messages 与当前工具 schemas。
- **完善工具调用回填**：模型返回 tool\_calls 后由 registry 执行真实操作，结果以 role=tool 写回上下文，再继续下一轮推理。
- **引入迭代预算控制**：保留 `MAX_ITERATIONS`、soft warning ratio、达到上限后的无工具强制收尾，以及 CLI 续跑机制。
- **标记上下文增长风险**：确认当前主 Agent 没有自动 token 裁剪机制，长任务依赖人工归档、todo 拆分和后续真正的上下文压缩能力。
- **明确 slash command 边界**：`/help`、`/skill`、`/tool`、`/mem`、`/model`、`/mode`、`/apikey` 等本地命令不进入 Agent messages。

### 2026-05-11

#### CLI 入口与配置体系整理

- **简化启动入口职责**：`main.py` 聚焦欢迎界面、prompt\_toolkit 输入、slash command、本地配置刷新和调用 Agent，不承载复杂业务逻辑。
- **统一环境变量配置**：项目使用 `.env` / `.env.example` 管理 OpenAI/Azure 客户端类型、API Key、模型名称和迭代参数，减少本地 JSON 配置分叉。
- **兼容 OpenAI 与 Azure**：`core/config.py` 负责根据环境变量创建对应 client，为个人本地部署和不同模型接入保留弹性。
- **补充 CLI 本地命令**：维护 `/model`、`/mode`、`/apikey`、`/mem`、`/skill`、`/tool` 等入口，方便运行期查看和调整状态。
- **隔离 UI 与核心逻辑**：Rich 展示与 prompt 输入保持在 CLI 层，核心推理、工具调用和上下文管理集中在 `core/agent.py`。

### 2026-05-09

- **区分 USER 与 MEMORY**：`USER.md` 保存用户偏好、身份和沟通风格；`MEMORY.md` 保存项目/环境稳定事实，避免混淆长期偏好和项目约定。
- **明确禁止写入内容**：临时任务进度、会话日志、PR/issue 编号、commit SHA、短期 TODO、API key、密码、token、私钥等不得进入长期 memory。
- **设计安全写入策略**：规划 duplicate check、唯一替换/删除、字符上限、prompt injection 扫描和敏感信息扫描等 P0 能力。
- **规划检索能力接口**：预留从纯文本搜索到 SQLite FTS/vector index 的演进路径，先保持 `memory_search` / `memory_get` 接口稳定。
- **形成维护文档要求**：Agent memory 项目迭代过程需在 outputs 中维护进度文档，便于重启后快速恢复上下文。

### 2026-05-04

- **整理目录职责**：明确 `core/`、`tools/`、`skills/`、`memories/`、`sandbox/`、`tests/`、`outputs/`、`docs/` 与根目录文档的职责边界。
- **规范运行时目录**：`sandbox/` 用于运行态文件和 todo list，不作为长期知识库；`outputs/` 可保存调研、维护进度和 TTS 文件。
- **补充测试目录预期**：将 memory、工具、todo、delegate、prompt 构成等能力列为后续自动化测试重点。
- **确立维护原则**：复杂业务不堆在 CLI，工具不偷偷改变全局 Agent 行为，核心循环修改需谨慎并配套验证。

### 2026-04-29

#### R-Agent 本地 Agent 工作台

- **搭建核心心智模型**：以“用户输入 → 构造上下文 → LLM 决策 → 调用工具 → 工具结果回填 → 继续推理/验证 → 返回结果”作为基础 Agent Loop。
- **规划核心模块**：初步划分 LLM client、`RAgent.messages`、工具注册表、memory 文件、skills 目录、todo 看板、delegate 子 Agent 和项目人格文件。
- **确定本地可控方向**：项目不追求通用云端平台形态，而面向个人工作流，强调可读、可维护、可审计和可持续迭代。
- **建立后续演进路线**：优先补齐工具能力、长期记忆、安全审批、复杂任务调度、上下文管理和维护文档体系。

