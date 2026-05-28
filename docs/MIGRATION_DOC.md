# R-Agent 迁移与重构说明 (MIGRATION_DOC)

本文档说明了从 `hermes-agent` 项目迁移至 `R-Agent` 的基础且有用的功能模块，以及相关的结构重构情况。迁移旨在增强 `R-Agent` 的基础能力，使其更贴近生产环境的智能体表现，同时保持架构的轻量化。

## 1. 迁移与新增的工具 (Tools)

### 1.1 文件操作工具 (`tools/file_tools.py`)
从 `hermes-agent` 的 `file_tools.py` 中汲取了核心设计，为 `R-Agent` 新增了三个基础文件操作工具：
- **`read_file`**: 用于读取文件内容，支持 `offset`（起始行号）和 `limit`（读取行数）参数，便于大文件分页读取。返回内容附带行号，方便大模型精准定位代码。
- **`write_file`**: 用于将内容完整写入文件，如果文件不存在则自动创建，包含所在目录的级联创建。
- **`search_files`**: 轻量级搜索工具，支持两种目标模式：`target="content"` 用于正则表达式搜索文件内容，`target="files"` 用于搜索匹配的文件名。

### 1.2 网络访问工具 (`tools/web_tools.py`)
从 `hermes-agent` 的 `web_tools.py` 中迁移了简易版本，为 Agent 赋予基础的互联网信息获取能力：
- **`web_search`**: 提供基础 Web 搜索功能（当前预留 API 接口结构，可接入 Exa/Firecrawl 等搜索引擎）。
- **`web_extract`**: 从给定的 URL 列表中提取网页的纯文本内容，过滤掉 HTML 标签，返回清洗后的文本，供模型理解网页内容。

## 2. 记忆系统重构 (`tools/memory_tool.py` & `core/memory.py`)

`hermes-agent` 的记忆系统采用单一工具多操作 (action) 的设计，我们对 `R-Agent` 进行了同样的重构：
- **移除零散工具**: 将原本分散的 `core_memory_append` 等工具整合为一个标准的 **`memory`** 工具。
- **操作支持**: 提供了 `action="add"`, `"replace"`, `"remove"` 三种标准操作。
- **底层支持**: 在 `core/memory.py` 的 `MemoryManager` 中补充了 `remove_memory` 的底层实现，支持精确删除指定的记忆条目。
- **存储目标**: 继续支持对 `user`（用户偏好）和 `memory`（项目环境事实）的分类存储，确保上下文信息稳定更新。

## 3. 技能系统重构 (`tools/skills_tool.py`)

为了向 `hermes-agent` 靠拢并满足“渐进式加载”(Progressive Disclosure) 设计，我们将技能工具重新整理为三个独立的工具：
- **`skills_list`**: 仅列出可用技能的名称和简短描述，节省 Token。
- **`skill_view`**: 专门用于读取指定技能的完整说明 (`SKILL.md`)，可选支持查看技能目录内的关联文件 (`file_path`)。
- **`skill_create`**: 提供固化标准工作流的入口，模型可自主创建新的 `SKILL.md`，扩展自身的可用能力集。

## 4. 主程序入口集成 (`main.py`)

在 `main.py` 中补充了新工具模块的注册引用，确保在 `RAgent` 的循环中可以正确加载这些新功能：
```python
from tools import file_tools
from tools import web_tools
```

这些迁移显著充实了 `R-Agent` 的 Tool、Skill 和 Memory 三大支柱，使其从相对空白的状态转变为具备基本自主文件交互、网页阅读和记忆管理的雏形。

## 5. 并行操作与子智能体委托 (`tools/delegate_tool.py`)

在 `hermes-agent` 中，智能体可以通过 `delegate_task` 工具来生成子智能体（Subagents）并行完成任务。其底层并行架构包含以下特性：
- **并发调度**：父智能体负责调度，通过线程池 (`ThreadPoolExecutor`) 生成隔离的子进程（或子线程）来执行子任务。
- **任务隔离**：每个子智能体拥有独立的对话历史、终端会话与提示词，避免中间步骤污染父智能体的上下文窗口。
- **同步等待**：父进程会阻塞，等待所有子智能体完成任务并汇总返回最终摘要。

为了在 `R-Agent` 中支持这种并行操作模式，我们新增了 `delegate_tool.py`，实现了轻量级的任务委托机制：
- **`delegate_task`**: 允许智能体将复杂问题拆解为多个子任务（接收 JSON 格式的任务列表），并利用多线程池并发为每个子任务生成独立的 `RAgent` 实例。各个子智能体独立思考并执行完毕后，将结果统一返回给主智能体，极大地提升了处理批量任务的效率。

## 6. Prompt 编写指南与标准 (Prompt Guidelines & Skill Authoring Standards)

This document contains useful prompt guidelines and skill authoring standards migrated from `hermes-agent/AGENTS.md`. These guidelines are meant to ensure consistency and quality across agent prompts and skills in the `R-Agent` project.

### 6.1 Skill Authoring Standards (HARDLINE)

Every new or modernized skill — bundled, optional, or contributed — must meet these standards.

1. **`description` ≤ 60 characters, one sentence, ends with a period.**
   Long descriptions bloat skill listings and dilute the model's attention when many skills are loaded. State the capability, not the implementation. No marketing words ("powerful", "comprehensive", "seamless", "advanced"). Don't repeat the skill name. 

2. **Tools referenced in SKILL.md prose must be native tools or MCP servers the skill explicitly expects.** 
   When the skill needs a capability, point at the proper tool by name in backticks (`` `terminal` ``, `` `web_extract` ``, `` `read_file` ``, `` `patch` ``, `` `search_files` ``, etc.). Do NOT name shell utilities the agent already has wrapped — `grep` → `search_files`, `cat`/`head`/`tail` → `read_file`, `sed`/`awk` → `patch`, `find`/`ls` → `search_files target='files'`. If the skill depends on an MCP server, name the MCP server and document the expected setup in `## Prerequisites`. 

3. **`platforms:` gating audited against actual script imports.**
   Skills that use POSIX-only primitives (`fcntl`, `termios`, `os.setsid`, `os.kill(pid, 0)` for liveness, `/proc`, `/tmp` hardcoded, `signal.SIGKILL`, bash heredocs, `osascript`, `apt`, `systemctl`) must declare their supported platforms. Default posture: try to fix it cross-platform first — `tempfile.gettempdir`, `pathlib.Path`, `psutil.pid_exists`, Python-level filtering instead of `grep`. Gate to a narrower set only when the dependency is genuinely platform-bound.

4. **`author` credits the human contributor first.** 
   For external contributions, the contributor's real name + GitHub handle goes first.

5. **SKILL.md body uses the modern section order.** 
   `# <Skill> Skill` title, 2-3 sentence intro stating what it does and doesn't do, `## When to Use`, `## Prerequisites`, `## How to Run`, `## Quick Reference`, `## Procedure`, `## Pitfalls`, `## Verification`. Target ~200 lines for a complex skill, ~100 lines for a simple one. Cut redundant intro fluff, marketing prose, and re-explanations of env vars already in `## Prerequisites`.

6. **Scripts go in `scripts/`, references in `references/`, templates in `templates/`.** 
   Don't expect the model to inline-write parsers, XML walkers, or non-trivial logic every call — ship a helper script. Reference it from SKILL.md by path relative to the skill directory.

7. **Tests live at `tests/skills/test_<skill>_skill.py`** 
   Use only stdlib + pytest + `unittest.mock`. No live network calls.

8. **`.env.example` additions are isolated to a clearly delimited block.** 
   Don't touch the surrounding file — contributor-supplied `.env.example` versions are usually stale and edits outside the skill's own block must be dropped during salvage.

### 6.2 Important Policies

#### 6.2.1 Prompt Caching Must Not Break
R-Agent ensures caching remains valid throughout a conversation. **Do NOT implement changes that would:**
- Alter past context mid-conversation
- Change toolsets mid-conversation
- Reload memories or rebuild system prompts mid-conversation

Cache-breaking forces dramatically higher costs. 

#### 6.2.2 Writing Tests
A test is a **change-detector** if it fails whenever data that is **expected to change** gets updated — model catalogs, config version numbers, enumeration counts, hardcoded lists. These tests add no behavioral coverage.
Write behavioral tests or invariant tests instead of snapshot/change-detector tests.

### 6.3 高效任务分解与 Sub-agent 协作模式 (Task Decomposition & Sub-agents)

为防止大模型陷入上下文过载或由于无意义的迭代（例如连续重复读取同一个未修改的文件）导致迭代次数耗尽，R-Agent 提供并强制使用以下设计模式：

#### 6.3.1 任务规划与拆解 (Plan & Todo)
- 在执行复杂重构或代码编写前，Agent 必须通过 `manage_todo` 工具（内置看板机制）建立 `Bite-sized` 的子任务列表。
- 每个子任务的粒度应该控制在单次执行或少量代码修改，状态可通过 `pending`, `in_progress`, `completed` 进行维护。
- 这不仅将模糊需求结构化，同时也有效防止在一次迭代中产生大量的上下文。

#### 6.3.2 子智能体协作 (`delegate_task`)
- **并行与解耦**：对于互不依赖的分析任务（如分析不同文件、检索资料），应当使用 `delegate_task` 派发给多个独立拥有干净上下文的子智能体 (Sub-agent) 并行执行。
- **机械采集分离**：要求执行大量检索时（Search Agent），将机械化的脚本采集与数据总结隔离，交由专注的 Search Sub-agent 完成，避免主进程上下文被污染。

#### 6.3.3 防御性文件读取 (Deduplication & Loop Detection)
- **去重 (Deduplication)**：`read_file` 会在内部跟踪读取记录与文件最后修改时间 (mtime)。若 Agent 重复读取一个未发生修改的文件区域，工具将拦截请求并返回 "unchanged" 存根信息，要求 Agent 查阅已有上下文。
- **死循环阻断 (Loop Detection)**：如果在同一任务中连续多次重复发起同样的搜索 (`search_files`) 或读取 (`read_file`)，系统在第3次会给出强警告，并在第4次触发 `BLOCKED` 错误，强制打断 Agent 的死循环幻觉。写操作与删除操作会自动使去重缓存失效，以确保文件更新后可正确读取。

## 7. 技能池扩充 (`skills/`)

将 `hermes-agent/skills` 下部分具有代表性且高价值的技能包直接迁移到 `R-Agent/skills/` 目录下，用于即插即用地增强 `R-Agent` 在特定领域的能力：
- **`github/`**: 包含 `github-code-review`, `github-pr-workflow`, `github-issues` 等技能，赋予 Agent 自主处理代码审查和 PR 工作流的经验指导。
- **`productivity/`**: 包含 `google-workspace` 和 `airtable` 等工作流提升技能。
- **`creative/`**: 包含 `architecture-diagram` 等创意图表技能，提升模型在图形化和复杂结构表达上的能力。
