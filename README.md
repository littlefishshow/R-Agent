# R-Agent

> 一个面向个人研究与工程工作的本地 AI Agent 工作台：能帮你**调研论文、精读论文、阅读论文仓库/代码仓库，并运行正在迭代中的 AutoResearch 自动实验框架**。

R-Agent 不是只会聊天的问答壳子。它更像一个“可控的研究助理”：能调用工具、读写文件、检索网页、维护长期记忆、复用技能包，把复杂任务拆成父子 Agent 协作执行，并在需要时启动 autoresearch 循环去尝试改代码、跑验证、总结经验。

它也支持用户按自己的工作流 **DIY tools 和 skills**：你可以直接在终端告诉 Agent“我想加入一个什么样的功能 / 希望以后怎么处理某类任务”，Agent 会在可控范围内交互式地帮你设计、编写、注册和验证新工具，或把稳定流程沉淀成 `skills/**/SKILL.md`。同时，R-Agent 通过 `memories/USER.md` 与 `memories/MEMORY.md` 保存长期偏好和稳定项目事实，会逐渐记住你的使用习惯、目录约定和常用工作流。

<p align="center">
  <b>Research Scout</b> · <b>Paper Reader</b> · <b>Repo Reader</b> · <b>AutoResearch</b> · <b>Tool-using Local Agent</b>
</p>

> **想直接用可视化界面？** 装好依赖并配置 `.env` 后，在项目根目录运行 `bash scripts/start_cockpit.sh`，浏览器打开 **http://127.0.0.1:5173** 即可使用可视化 Cockpit（学习型对话 / 论文与 Markdown 阅读 / 上下文分支）。停止用 `bash scripts/stop_cockpit.sh`。详见 [1.4 启动可视化 Cockpit](#14-启动可视化-cockpit可选)。

---

## 目录

- [1. 快速开始](#1-快速开始)
- [2. 这个项目想解决什么问题](#2-这个项目想解决什么问题)
- [3. 核心能力总览](#3-核心能力总览)
- [4. 论文调研：paper_research_scout](#4-论文调研paper_research_scout)
- [5. 论文阅读：read_paper](#5-论文阅读read_paper)
- [6. 仓库阅读与论文代码定位](#6-仓库阅读与论文代码定位)
- [7. AutoResearch：让 Agent 自己做小型实验闭环](#7-autoresearch让-agent-自己做小型实验闭环)
- [8. atr_playground 测试项目概览](#8-atr_playground-测试项目概览)
- [9. 为什么 R-Agent 比普通聊天更适合长任务](#9-为什么-r-agent-比普通聊天更适合长任务)
- [10. 常用命令与入口](#10-常用命令与入口)
- [11. 项目结构](#11-项目结构)
- [12. 测试与维护](#12-测试与维护)
- [13. 更新日志](#13-更新日志)

---

## 1. 快速开始

### 1.1 准备环境

建议使用 Python 3.10+，在项目根目录执行：

```bash
git clone https://github.com/littlefishshow/R-Agent.git
cd R-Agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> `requirements.txt` 当前包含 OpenAI SDK、Rich、python-dotenv、pytest、语音输入相关依赖，以及可视化 Cockpit 所需的 FastAPI / Uvicorn / websockets / PyMuPDF / Pillow。

### 1.2 配置 `.env`

复制示例配置：

```bash
cp .env.example .env
```

最小配置如下：

```env
# openai 或 azure
LLM_CLIENT_TYPE="openai"

# OpenAI / 兼容 OpenAI 的 API Key
OPENAI_API_KEY="YOUR_API_KEY_HERE"

# OpenAI 模式填模型名；Azure 模式填接入点名称
LLM_MODEL="gpt-4o"

# 可选：第三方兼容 OpenAI 的 Base URL
# OPENAI_BASE_URL="https://api.example.com/v1"
```

Azure 模式可额外配置：

```env
LLM_CLIENT_TYPE="azure"
AZURE_OPENAI_ENDPOINT="https://xxx.openai.azure.com/"
AZURE_OPENAI_API_VERSION="2024-02-01"
LLM_MODEL="你的 Azure deployment / endpoint 名称"
```

### 1.3 启动命令行 Agent

```bash
python main.py
```

启动后你可以直接输入自然语言任务，例如：

```text
帮我找最近一个月关于 test-time scaling 的高质量论文，按价值排序
```

```text
阅读 outputs/papers/agent_RL/xxx.pdf，生成研究型中文笔记
```

```text
阅读这个论文仓库，告诉我论文核心方法在代码里怎么实现
```


### 1.4 启动可视化 Cockpit（可选）

R-Agent 也提供浏览器版可视化 Cockpit，用于在本地管理学习型对话、论文文件、Markdown 笔记和上下文分支。

前置条件：

- 已执行 `pip install -r requirements.txt`（含 FastAPI / Uvicorn / websockets / PyMuPDF / Pillow）；
- 已安装 Node.js 18+ 与 npm（前端基于 Vite，首次启动脚本会自动执行 `npm install`）；
- 已按 [1.2](#12-配置-env) 配置好 `.env`。

启动：

```bash
bash scripts/start_cockpit.sh
```

默认入口：

- 前端页面：`http://127.0.0.1:5173`（在浏览器打开这个即可）
- 后端 API：`http://127.0.0.1:8765`

停止（关闭前后端并释放端口）：

```bash
bash scripts/stop_cockpit.sh
```

可选端口：

```bash
R_AGENT_COCKPIT_PORT=8765 R_AGENT_COCKPIT_FRONTEND_PORT=5173 bash scripts/start_cockpit.sh
```

当前 Cockpit 支持：

- 左侧树状问题链：普通对话、文件对话和选中文本产生的子对话都以树状上下文保存；
- 中间聊天/文件双模式：点击左侧进入纯聊天模式，点击右侧文件进入文件浏览模式；
- VSCode 风格文件系统：右侧直接映射项目 `outputs/`，默认包含 `outputs/papers`，可上传、复制、粘贴、下载和删除文件；
- PDF 阅读：使用后端 PyMuPDF 渲染页面图片并叠加文本选择层，保留公式、图片和版面，同时支持选中文本提问、高亮和子窗口唤回；
- Markdown 笔记：支持 Markdown 预览/编辑、表格、图片链接、数学公式和选中文本提问；
- 子窗口分支：用户消息和选中文本都可以 fork / setback / 打开分支窗口，分支上下文写入独立 `context.json`；
- Agent Tools 开关：每个会话可控制下一次请求是否携带 tools schema；关闭后模型不会获得工具上下文，也不能实际执行工具调用；
- 可调三栏布局：左右栏宽度可拖拽调整，子窗口全屏会跟随中间栏尺寸。

---

## 2. 这个项目想解决什么问题

很多 AI 工具能回答问题，但做研究和工程时，真正麻烦的往往不是“一问一答”，而是下面这些连续任务：

1. **找论文**：不是随便列 10 篇，而是要按方向、时间、引用、社区热度、代码可用性筛选。
2. **读论文**：不是摘要复述，而是理解动机、方法、实验、局限、可复现性和下一步研究价值。
3. **读仓库**：不是列目录，而是定位“论文里的核心机制到底在哪些类/函数/配置里实现”。
4. **做实验**：读完之后要能尝试改代码、跑评测、记录指标、保留好结果、总结失败经验。
5. **长任务不断线**：上下文会变长，工具输出会很大，子任务会很多，普通聊天很容易爆上下文或遗忘目标。

R-Agent 的目标就是把这些过程放进一个本地可控的 Agent 工作台里：

```text
用户目标
  ↓
构造上下文 / 读取 memory / 选择 skill
  ↓
LLM 决策
  ↓
调用真实工具：文件、Shell、Python、Web、Skill、Todo、Delegate、AutoResearch
  ↓
工具结果回填与压缩
  ↓
继续推理、验证、总结
```

通俗地说：

> 你告诉它“我要研究这个方向 / 读这篇论文 / 优化这个小项目”，它会尽量像一个谨慎的研究助理一样，先查资料，再读原文，再看代码，再做实验，并且把中间产物保存下来。

---

## 3. 核心能力总览

| 能力 | 说明 | 典型用途 |
|---|---|---|
| Tool 系统 | 通过 `tools/registry.py` 动态注册工具，支持文件、Shell、Python、Web、Memory、Skill、Todo、Delegate、语音等，也支持按用户需求新增自定义工具 | 让模型不只“说”，还能执行真实操作 |
| Skill 系统 | `skills/**/SKILL.md` 保存稳定工作流，用户也可以让 Agent 把反复使用的流程沉淀为自定义 skill | 论文调研、论文阅读、仓库阅读、项目进度恢复、创意生成、GitHub 工作流 |
| Memory 系统 | `memories/USER.md` 与 `memories/MEMORY.md` 区分用户偏好和项目稳定事实 | 记住长期偏好、项目约定、环境事实 |
| 上下文控制 | 自动估算上下文、压缩历史、大工具输出外置到 artifact | 避免长任务把模型上下文撑爆 |
| Todo / Delegate | 父 Agent 维护树状任务与依赖，子 Agent 执行独立叶子任务 | 并行调研、复杂工程维护、降低父上下文压力 |
| Paper Research | `paper_research_scout` 负责发现、筛选、排序论文 | 找最新/高引/热门/有代码的论文 |
| Paper Reading | `read_paper` 负责 PDF 精读、图表截图、中文研究笔记 | 读懂方法、实验、局限与后续研究价值 |
| Repo Reading | `paper_repo_code_research` 等 skill 负责源码定位 | 把论文方法映射到代码实现 |
| AutoResearch | `autoresearch/` 提供 plan → attempt → conclude 小型研究闭环 | 自动改进小项目、跑 eval、记录指标和经验 |
| Cockpit GUI | 浏览器可视化界面，管理树状对话、文件系统、PDF/Markdown 阅读、选中文本分支和工具上下文开关 | 非终端学习、论文阅读与上下文审计 |

---

## 4. 论文调研：`paper_research_scout`

`paper_research_scout` 是 R-Agent 中负责“找论文”的工作流。它适合在你提出一个研究方向时，帮你从多个来源筛选候选论文，而不是简单返回搜索结果。

### 4.1 它会关注哪些信号

- 论文标题、摘要、作者、机构、venue / workshop
- arXiv、OpenReview、Hugging Face Papers 等公开来源
- OpenAlex / Semantic Scholar / Crossref / DataCite 等结构化指标
- GitHub 代码仓库 stars、forks、更新时间、license、issues 等弱信号
- Hugging Face upvotes、comments、关联模型/数据集/Spaces 等社区信号
- 论文是否已经读过，避免重复推荐

### 4.2 适合怎么问

```text
调研最近 3 个月关于 reward model for reasoning 的论文，优先找有代码和实验可信的
```

```text
帮我找 agentic RL 方向值得读的 10 篇论文，按“研究价值/可复现性/与我项目相关性”排序
```

```text
找 OpenReview 上 test-time compute / verifier / search 相关的新论文，并说明哪些最值得读
```

### 4.3 输出风格

它通常会给出：

1. 候选论文列表；
2. 每篇为什么值得看；
3. 可能的风险或不确定性；
4. 代码/项目链接；
5. 引用、热度、社区信号来源；
6. 推荐下一步：精读、略读、跳过或交给 autoresearch。

---

## 5. 论文阅读：`read_paper`

`read_paper` 是 R-Agent 中最重要的研究型阅读 skill。它的目标不是“把论文翻译一遍”，而是产出能服务后续研究判断的中文笔记。

### 5.1 阅读目标

`read_paper` 会尽量回答这些问题：

- 这篇论文到底想解决什么问题？
- 作者为什么认为这个问题重要？
- 方法的核心机制是什么？
- 公式、算法、图表分别支撑了哪一段论证？
- 实验是否可信？有没有数据泄漏、评测污染、统计不足？
- 哪些结论值得相信，哪些只是局部有效？
- 如果我要复现或改造，最小行动是什么？
- 读完后下一篇该读什么？

### 5.2 文件组织约定

用户给论文链接或 PDF 时，通常保存到：

```text
outputs/papers/<类别>/<日期_简称>.pdf
```

阅读笔记通常输出到：

```text
outputs/papers_output/<类别>/<日期_简称>_阅读笔记.md
```

图表截图保存到：

```text
outputs/papers_output/<类别>/assets/<pdf_stem>/
```

中间抽取、索引、调试文件放到：

```text
sandbox/read_paper/<paper_stem>/
```

### 5.3 图表与公式

`read_paper` 内置了 skill-local 脚本来辅助：

- 定位 PDF；
- 抽取文本；
- 按 Figure/Table caption 截图；
- 将关键图表插入阅读笔记；
- 对公式、算法和附录细节做复核。

这使笔记不只是文字摘要，而是能把关键图表放在对应论证附近，方便以后回看。

---

## 6. 仓库阅读与论文代码定位

很多论文真正难懂的部分在代码里：论文只写了方法名字，但实现细节藏在配置、训练 loop、loss、数据处理和评测脚本中。

R-Agent 通过 `paper_repo_code_research` 等 skill 支持“论文 → 仓库 → 核心实现”的定位式阅读。

### 6.1 它关注什么

- 论文核心模块对应哪些文件、类、函数；
- 方法在训练/推理/评测哪个阶段调用；
- 输入输出是什么；
- 哪些配置项控制关键机制；
- baseline 与论文方法的差异在哪里；
- 如果要复现，最小运行链路是什么；
- 如果要改造，应该从哪里下手。

### 6.2 典型提问

```text
根据这篇论文笔记，阅读它的 GitHub 仓库，定位核心算法实现
```

```text
这个 repo 里 verifier/reward model/search 分别在哪里实现？给我论文方法到代码的对应表
```

```text
不要做工程百科，只找影响复现和改造的关键代码路径
```

---

## 7. AutoResearch：让 Agent 自己做小型实验闭环

`autoresearch/` 是 R-Agent 中正在开发的自动研究运行时。它面向的是“小型、可验证、指标明确”的工程/算法实验，不是无限制地让模型乱改大项目。最终目标是可以用更少的 token 和上下文管理，自动化的进行 research 项目规划、验证和改进。

### 7.1 基本思路

AutoResearch 的核心循环是：

```text
Plan（规划） → Attempt（尝试修改/运行） → Conclude（评估/总结） → 下一轮 Plan
```

更通俗地说：

1. **Plan**：读 `program.md`、项目状态和历史结果，决定下一步尝试什么；
2. **Attempt**：在允许范围内改代码、运行 train/eval、读取 `metrics.json`；
3. **Conclude**：判断指标有没有变好，保留有价值结果，记录失败经验，压缩上下文；
4. **Repeat**：继续下一轮，直到满足完成标准、预算耗尽或用户停止。

### 7.2 当前入口

CLI：

```text
/autoresearch run <项目目录>
/autoresearch show [项目目录]
/autoresearch debug [on|off|show] [项目目录]
/autoresearch kill
```

工具入口：

- `auto_research_run` / `auto_research_status`：legacy workflow；
- `auto_research_run_v2` / `auto_research_v2_status`：当前 V3 三步循环；
- `auto_research_stop`：写入或清除停止标记。

### 7.3 关键文件与产物

目标项目内通常会出现：

```text
.autoresearch/
  monitor.json          # 当前运行状态、phase、step、预算、心跳
  state.json            # 实验、best、pareto、上下文桶等结构化状态
  debug/                # debug event 与 inflight 状态
  round_traces/         # 每轮 prompt / 上下文 / 原始响应调试信息
  step_traces/
  delegate_contexts/

.auto/
  todo.json             # V3 父进程任务状态
  plan.md               # 当前计划

project.md              # 项目态
program.md              # 任务目标、约束、评估指标、完成标准
```

### 7.4 安全边界

AutoResearch 目前强调“受控小闭环”：

- 不自动 `git reset --hard`；
- 不在非 git 项目里擅自初始化仓库；
- 默认把中间版本保存为 artifact/patch/manifest，而不是频繁 commit；
- 使用预算、超时、debug、monitor 和 stop 文件控制长运行；
- 只在项目边界内执行允许的实验命令；
- 通过 metrics 和 Completion Criteria 判断是否真正 solved。

---

## 8. `atr_playground` 测试项目概览

当前 R-Agent 的 AutoResearch 能力正在通过仓库内置的 `autoresearch/benchmarks/atr_playground` toy/benchmark 项目持续验证。这些项目都有相似协议：

```text
prepare.py → train/train.sh → eval.sh → metrics.json
```

目标是在不修改固定评测文件的前提下，修改 `solution.py`、`train/` 或 `submission/`，让 `metrics.json` 中的官方指标变好。

| 项目 | 大致任务 | 主要指标 / 目标 |
|---|---|---|
| `byte_codec_detector` | 修复 mojibake、HTML entity、escape sequence，把乱码小文本解码为干净 Unicode | `decoded_exact_accuracy`，目标全对 |
| `coin_change_dp` | 最小硬币兑换；baseline 是递归 memoized solver，期望改成 bottom-up DP | `score`，正确性优先、速度其次 |
| `csv_cleaner` | 清洗脏 CSV，规范姓名、年龄、邮箱、州名 | `score`，综合 row exact 与 cell F1/accuracy |
| `json_repair_micro` | 修复小型损坏 JSON，如单引号、未加引号 key、尾逗号、Python bool/None | `repair_exact_accuracy`，目标全对 |
| `knapsack_solver` | 0/1 背包；baseline 是 value/weight 贪心，期望 DP 最优解 | `score`，精确最优为主 |
| `log_anomaly_f1` | 判断合成服务日志是否异常，覆盖延迟、5xx、retry storm、资源耗尽等 | `positive_f1`，目标无误报/漏报 |
| `mini_ir_ranker` | 微型信息检索排序，改进 raw token overlap baseline | `mean_reciprocal_rank`，目标相关文档排第一 |
| `route_heuristic_optimizer` | 小型欧氏路径/TSP-like heuristic，改进最近邻 baseline | `route_quality_score`，接近已知最优路径 |
| `string_matcher` | 多 pattern 子串匹配，统计 overlapping occurrences | `score`，精确计数优先、速度其次 |
| `text_normalizer_editrules` | 噪声产品/类别短字符串规范化，学习/手写编辑规则 | `exact_match_accuracy`，目标规范化全对 |

这些测试覆盖了文本清洗、JSON 修复、编码修复、日志分类、信息检索、动态规划、组合优化、字符串算法等场景。它们规模不大，但很适合检验 AutoResearch 是否真的能做到：

- 读懂目标和约束；
- 不改评测；
- 提出可执行假设；
- 修改代码；
- 运行评测；
- 读取指标；
- 保留改进并总结失败。

---

## 9. 为什么 R-Agent 比普通聊天更适合长任务

### 9.1 上下文控制

长任务中最容易出问题的是上下文爆炸：大文件、长日志、工具输出、子任务历史全部塞回模型，就会变慢、变贵、甚至超过上下文窗口。

R-Agent 做了几层治理：

- 自动估算 `messages + tools` 的上下文占用；
- 接近阈值时压缩历史；
- 保留最近完整 message，不从中间截断；
- 大工具输出落盘为 artifact；
- 需要时用 `artifact_inspect` / `artifact_search` / `artifact_slice` 二次检索；

### 9.2 父子进程 / 父子 Agent 管理

复杂任务不适合一个 Agent 从头记到尾。R-Agent 支持：

- 父 Agent 维护动态 todo list；
- 子 Agent 只领取可执行叶子任务；
- 子 Agent 需要拆分时只提交 split proposal；
- 父 Agent 决定依赖、并发数和是否批准拆分；
- 子 Agent 完整上下文保存为 sandbox artifact，不默认回灌给父进程。

这能显著减少父上下文压力，也让复杂任务更可控。

### 9.3 比较完整的工具面

当前工具覆盖：

- 文件读取、写入、搜索、删除；
- Shell / Python 执行；
- Web Search / Web Extract；
- Memory 读写与检索；
- Skill 查询、读取、维护、生命周期治理；
- Todo 看板与 delegate 子 Agent；
- 上下文归档与 artifact 检索；
- AutoResearch 后台运行、状态查看、停止；
- TTS / 语音输入相关能力；
- GUI/Cockpit 事件与资源可视化。

### 9.4 可维护、可审计

R-Agent 不是黑盒：

- 工具都在 `tools/` 下注册；
- skill 都是 Markdown 工作流；
- memory 是可读文本文件；
- AutoResearch 产物保存在目标项目目录；
- 大输出、trace、debug 都有 artifact；
- 高风险命令、工作区外访问、危险 Python 代码都有审批边界。


### 9.5 整体 Skill 系统

R-Agent 当前内置的 skill 已经比较多，README 不逐个展开每个 `SKILL.md` 的完整内容，而是按用途给出整体地图。Skill 可以理解为“可复用工作流说明书”：当某类任务经常重复出现时，就把步骤、注意事项、输出格式和验证方法写成 skill，让 Agent 下次不用从零规划。

当前 skill 大致分为几类：

- **Agent 运维类（`skills/agent_ops/`）**：用于维护 Agent 自身能力，例如上下文控制、动态 todo 委派、项目进度保存、代码库巡检、工具面审计、autoresearch 工作流等。
- **论文与研究生产力类（`skills/productivity/`）**：包括 `paper_research_scout`、`read_paper`、`paper_repo_code_research`、`paper_note_targeted_correction`、`research_explainer_md` 等，覆盖论文发现、精读、笔记修正、论文代码定位和研究材料解释。
- **文档/办公/知识库类（`skills/productivity/`）**：包括 OCR 与文档处理、PDF、Notion、Airtable、Google Workspace、PowerPoint、地图、会议流水线等工作流。
- **GitHub 工程协作类（`skills/github/`）**：支持仓库检查、代码审查、issue、PR workflow、仓库管理和认证相关流程。
- **创意与可视化类（`skills/creative/`）**：覆盖架构图、ASCII art/video、漫画、信息图、网页设计、Manim、p5.js、像素画、音乐创作、TouchDesigner 等创意任务。

常用查看方式：

```text
/skill
```

也可以直接对 Agent 说：

```text
以后我做某类任务时，希望你按这个流程处理，请帮我沉淀成 skill
```

或者：

```text
我想新增一个工具，用来读取/转换/检查某类文件，请你帮我设计并注册
```

这也是 R-Agent 与普通一次性聊天工具的重要区别：它不仅能完成当前任务，还能把稳定经验逐渐变成可复用的工具、skill 和长期记忆。

---

## 10. 常用命令与入口

### 10.1 CLI 本地命令

进入 `python main.py` 后，常用命令包括：

```text
/help                      查看帮助
/model                     查看或切换模型相关配置
/mem                       查看 memory
/skill                     查看 skill
/tool                      查看工具
/project_list              载入历史项目进度上下文
/bbb                       语音输入，按 Enter 停止，Esc 取消
/autoresearch run <dir>    启动 AutoResearch
/autoresearch show [dir]   查看 AutoResearch 进度
/autoresearch kill         停止 AutoResearch
exit / quit                退出
```

### 10.2 论文相关常用说法

```text
调研 xxx 方向的论文，优先最近半年、有代码、实验可信的
```

```text
下载并阅读这篇论文：<url>，保存到 outputs/papers/<类别>/ 下
```

```text
基于这篇论文笔记，阅读它的开源仓库，定位核心方法代码
```

### 10.3 Gateway 服务模式（可选）

项目还包含 `gateway/`，可把 R-Agent 封装为 HTTP 服务，并对接微信、飞书、QQ 官方机器人等外部入口。相关文件：

```text
gateway/
Dockerfile.gateway
docker-compose.gateway.yml
.env.gateway.example
```

---

## 11. 项目结构

```text
R-Agent/
├── main.py                         # CLI 入口
├── core/                           # Agent loop、配置、memory、prompt、上下文控制
├── tools/                          # 全局工具注册与实现
├── skills/                         # 可复用工作流：论文调研、论文阅读、仓库阅读等
├── autoresearch/                   # AutoResearch runtime package
│   └── benchmarks/atr_playground/   # 内置 AutoResearch 示例 benchmark
├── app_gui/                        # Cockpit 后端 runtime / event / snapshot
├── app_gui_frontend/               # Cockpit 前端
├── gateway/                        # HTTP/Gateway/外部平台接入
├── memories/                       # USER.md / MEMORY.md 长期记忆
├── outputs/                        # 论文、笔记、研究输出等本地产物（通常不进 Git）
├── sandbox/                        # 临时运行文件、todo、tool artifact（不进 Git）
├── tests/                          # 自动化测试
├── requirements.txt
├── .env.example
├── README.md
└── CHANGELOG.md                    # 独立更新日志
```

---

## 12. 测试与维护

运行测试：

```bash
python -m pytest
```

如果当前 Python 环境缺少 pytest：

```bash
pip install -r requirements.txt
```

维护约定：

- `README.md`：项目入口、能力介绍、使用说明；
- `CHANGELOG.md`：按日期记录维护更新；
- `skills/**/SKILL.md`：稳定可复用流程；
- `memories/`：长期偏好和稳定事实，不保存临时任务日志；
- `sandbox/`：临时运行产物，可清理，不应被 Git 跟踪；
- `outputs/`：论文、笔记、研究输出等本地产物，默认不进入版本库。

---

## 13. 更新日志

更新日志已从 README 中拆分到独立文件：[`CHANGELOG.md`](CHANGELOG.md)。

最近一次文档维护：

- 将 `atr_playground` 移入 `autoresearch/benchmarks/atr_playground`，作为内置 AutoResearch 示例 benchmark；
- 将 README 改为项目入口文档，重点介绍环境配置、论文调研、论文阅读、仓库阅读、AutoResearch 与 atr_playground 测试项目；
- 补充 tools / skills DIY、长期记忆与整体 Skill 系统说明；
- 记录上下文主动压缩规则与 delegate 子 Agent timeout 默认值/提示文案优化；
- 历史更新记录迁移到 `CHANGELOG.md`，避免 README 过长。
