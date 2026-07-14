# AutoResearch 框架详解（小学生也能看懂版）

这份文档用最直白的话，把 R-Agent 里的 `autoresearch/` 包讲清楚：
它是什么、怎么一步步跑、每一步能用什么工具、能看到什么、会把结果写到哪、
以及现在还差什么才算“真正的自动科研”。

---

## 0. 一句话比喻

> AutoResearch 就像一个**很自律的小研究员**：
> 他先看清楚“老师留的题目”，然后**开会想办法 → 动手做实验 → 复盘总结**，
> 一轮一轮循环，直到题目达标或者预算花完，中间所有事都记在几个本子上，
> 你随时能翻本子看他做到哪了。

它不是“喊一句就自动发论文”的魔法，而是一个**边界清晰、状态落盘、可中断、可复盘**的循环。

---

## 1. 三层结构：谁负责喊、谁负责调度、谁负责干活

```mermaid
flowchart TD
    U[你 / 用户] -->|/autoresearch run 项目目录| CLI

    subgraph L1[第一层 · 操作台 CLI]
        CLI["main.py 里的 /autoresearch<br/>run · show · debug · kill"]
    end

    subgraph L2[第二层 · 工具桥 Tool]
        SHIM["tools/autoresearch_tool.py (薄壳)"]
        TOOL["autoresearch/tool.py<br/>auto_research_run_v2 / v2_status / stop"]
        SHIM --> TOOL
    end

    subgraph L3[第三层 · 循环大脑]
        CTRL["autoresearch/controller.py<br/>ThreeStepController"]
        LOOP["autoresearch/legacy/loop.py<br/>安全执行 / 指标 / 版本 / 实验记录"]
        CTRL --> LOOP
    end

    CLI --> TOOL
    TOOL -->|后台子进程| CTRL
```

- **第一层 CLI**：你打字的地方。`/autoresearch run <目录>` 在后台起一个不阻塞主聊天的进程；
  `/autoresearch show` 只读进度条；`debug` 开关调试；`kill` 杀掉在跑的进程。
- **第二层 Tool**：`tools/autoresearch_tool.py` 只是个“门牌”，真正逻辑在 `autoresearch/tool.py`，
  它注册了 4 个工具：`auto_research_run`（老版）、`auto_research_run_v2`（现在主力）、
  `auto_research_v2_status`（看心跳）、`auto_research_stop`（放停止哨兵）。
- **第三层 循环大脑**：`ThreeStepController` 是真正的“研究员”，它跑 **plan → attempt → conclude**；
  脏活累活（在项目目录里安全跑命令、解析指标、记录实验、版本管理）复用 `legacy/loop.py`。

---

## 2. 五个“本子”：记忆是分层的（L0–L4）

小研究员不会把所有东西塞进脑子，而是分成 5 个本子，越靠上越“神圣不可改”，越靠下越“可以随便涂”。

| 层级 | 载体 | 谁能改 | 装什么 | 比喻 |
|------|------|--------|--------|------|
| **L0 宪法** | `program.md` 里 `<!-- CONSTITUTION -->` 段 | 只有用户 | 题目、规则、**完成标准**、禁止改的文件 | 老师写的考卷，学生不能涂改 |
| **L1 信念** | `program.md` 里 `<!-- BELIEF -->` 段 | Planner 可改 | 当前“我觉得该怎么解”的直觉 | 学生写在考卷旁的解题思路 |
| **L2 项目态** | `project.md` | 只有父进程 | 梗概/当前计划/改动记录/**短期结论**/PHASE 标记 | 班级黑板，全班都能看，只有班长写 |
| **L3 细节** | `.auto/*.md` | 子任务 | survey、plan、execute_report、run_report、run_failure… | 每个小组自己的草稿纸（会定期清理） |
| **L4 原始档案** | `.autoresearch/artifacts/` | 框架 | 辩论全文、命令原始输出、write 记录、快照 | 档案室，父进程只留“摘要+路径”，不塞回脑子 |

> 关键设计（记住这条）：**父进程只看“摘要/digest”，子任务的完整上下文落到 artifact 文件，绝不把子进程的长篇大论回灌到 project.md**。这样脑子（上下文）永远不爆。

`program.md` 如果没写 `<!-- BELIEF -->` 标记，就被当成**纯只读宪法**，框架永远不会去改它（见 `state/memory.py:73` `update_belief`）。

---

## 3. 主循环：三步走 + 谁决定下一步去哪

```mermaid
flowchart TD
    START([启动]) --> INIT

    INIT["init 阶段（只跑一次）<br/>扫一遍项目 → 写 .auto/survey.md"] --> PLAN

    PLAN["🧠 plan 计划<br/>多性格开会 → 产出任务 DAG"] --> ATTEMPT

    ATTEMPT["🔨 attempt 尝试<br/>做一个 ready 任务 + 立刻跑验证"]

    ATTEMPT -->|这次直接达标| SOLVED_EVAL["顺手刷新 project.md/gate<br/>写清人类可读结论"]
    SOLVED_EVAL --> PAUSE
    ATTEMPT -->|还没达标| CONCLUDE

    CONCLUDE["📌 conclude 复盘<br/>算 best/Pareto + 记 lessons + 压缩"]

    CONCLUDE -->|已解决| PAUSE([⏸ pause 等你来看])
    CONCLUDE -->|预算花光| PAUSE
    CONCLUDE -->|有失败任务卡住| PLAN
    CONCLUDE -->|连续多轮没进步| PLAN
    CONCLUDE -->|重复换方向仍没进步| PAUSE
    CONCLUDE -->|还有活没干完| ATTEMPT
    CONCLUDE -->|活干完了但没达标| PLAN

    PAUSE --> STOPFILE{".autoresearch/STOP<br/>存在? 或按了 Esc?"}
    STOPFILE -->|是| END([干净退出])
    STOPFILE -->|否| END2([等待下次])
```

- 每走一步（step）都会写一次 `monitor.json` 心跳，所以你 `/autoresearch show` 永远能看到“现在第几步、在哪个阶段、花了多少钱”。
- 遇到 `pause`、发现 `STOP` 哨兵、或预算耗尽，循环就停。
- **收尾保险**：如果预算刚好停在 attempt 之后（下一步本该是 conclude），框架会**多补跑一次 conclude**，保证 best/lessons 一定被结算（见 `controller.py:560` 附近 `run()`）。

决定“下一步去哪”的逻辑全在 `_next_after_conclude`（`controller.py:145`），**这是纯代码判断，不靠 LLM 自觉**。

---

## 4. 每一步详解：能干啥 / 能用啥工具 / 能用啥技能 / 能看到啥

### 4.1 三步的“工具白名单”（框架强制，越权直接拒绝）

工具白名单定义在 `runtime_policy.py`，`tool_guard` 会拦截不在名单里的调用。

| 能力 | 🧠 plan | 🔨 attempt | 📌 conclude |
|------|:---:|:---:|:---:|
| `read_file` 读文件 | ✅ | ✅ | ✅ |
| `search_files` 搜文件 | ✅ | ✅ | ✅ |
| `write_file` 写文件 | ❌ | ✅ | ✅ |
| `run_command` 跑命令 | ❌ | ✅ | ❌ |
| `artifact_inspect/search/slice` 翻档案 | ✅ | ✅ | ✅ |
| `skill_search / skill_view` 查技能 | ✅ | ✅ | ❌ |
| `todo_manage` 管任务看板 | ✅ | ✅ | ✅ |
| `delegate_task` 派子任务 | ✅ | ✅ | ❌ |

一句话记忆：
- **plan 只动嘴不动手**（能读能查能规划，不能改代码、不能跑命令）；
- **attempt 才是唯一能改代码、能跑命令的人**；
- **conclude 只做账房先生**（读证据、写结论、管看板，不改代码不跑命令）。

### 4.2 能用哪些 Skill

- plan / attempt 允许的技能白名单目前只有 **`codebase_scout`**（快速摸清代码库），见 `runtime_policy.py:155,175`。
- conclude 不开放技能。
- 想用别的技能？`skill_view` 会被 `tool_guard` 拦下来，报“不在白名单”。这是刻意的——研究循环要收敛，不能让子步骤乱翻技能。

### 4.3 每一步“能看到的上下文”

上下文由 `build_step_context`（`runtime_policy.py:213`）打包，**有 12000 字符预算，超了就按固定顺序砍最重的字段**（先砍 state.json、experiment_memory，再砍 project.md/program.md）。

| 🧠 plan 看到 | 🔨 attempt 看到 | 📌 conclude 看到 |
|------|------|------|
| 宪法 + 信念（program.md） | 那一个 ready 任务 + 它的 last_result | state 里的实验记录 |
| 项目梗概（project.md） | program 的约束（哪些能改/不能改） | best / Pareto |
| 代码库扫描摘要 / survey | 可改路径 / 受保护路径 | 任务看板 digest |
| 任务看板 digest | 验证命令表 | 最近的 execute/run 产物 |
| 最近的 best 实验 | 最近的行为检查产物 | lessons + 预算/门控信号 |
| **额外**：关键项目源码（README/train/eval/pyproject，最多 18000 字符，见 `planner.py:356`） | | |

> plan 是**最贵**的一步，所以它被特批看更多真实源码（像“父进程拿到充足上下文再规划”）；
> attempt/conclude 只看和自己那点活相关的东西，省钱省脑子。

### 4.4 plan（计划）到底怎么“开会”

```mermaid
sequenceDiagram
    participant P as Planner
    participant D as 发散派 DIVERGENT
    participant R as 务实派 PRAGMATIC
    participant L as 组长 LEADER
    P->>D: 给你稳定上下文，出点子（只准回 JSON）
    P->>R: 给你稳定上下文，挑刺+砍不靠谱的
    D-->>P: {opinion, ideas, risks}
    R-->>P: {opinion, feasible, reject}
    P->>L: 把两方意见给你，你必须拍板一个方案
    L-->>P: {belief, plan, detailed_plan, tasks(DAG), rationale}
```

- 默认 2 个性格（发散 + 务实）并行发言，**预算紧张时自动降到 1 个**（`planner.py:113`）。
- 组长 LEADER 只有一次机会，必须输出**结构化 JSON**：
  - `belief` → 写回 L1（program.md 信念）
  - `plan` → 写进 L2（project.md 当前计划）
  - `detailed_plan` + `tasks` → 变成任务看板 `todo_state.json` + `.auto/plan.md`
  - 全部辩论记录 → 丢进 L4 档案（**不进 project.md**）
- 贴心兜底：
  - 如果还没有任何“带指标的实验”，会**自动插入一个 baseline 任务**先跑通再说（`planner.py:310`）；
  - 如果已经有实验了还在 replan，会**把计划压缩成“一个实现 + 一个验证”**，避免越想越多把步数耗光（`planner.py:482` `_compact_repair_plan`）；
  - 没有可用 LLM 时，也会给一个**确定性的兜底 DAG**，绝不卡死。

### 4.5 attempt（尝试）到底怎么“动手”

attempt 一步里其实做了两件事：**Execute（改/读）+ Run（验证）**。

```mermaid
flowchart TD
    A[取下一个 ready 任务] --> T{任务类型?}
    T -->|analysis 分析| AN[读 context 文件<br/>写 .auto/analysis_*.md]
    T -->|implementation 实现| IM[让模型返回<br/>files:path+content]
    IM --> SAFE{路径在 train/** 内?<br/>不是 eval/oracle?}
    SAFE -->|否| REJ[拒绝这次写入]
    SAFE -->|是| WRITE[写文件]
    WRITE --> PYC[py_compile 静态检查]
    PYC --> BEH[行为冒烟: bash train/train.sh]
    T -->|validation/experiment 验证| RUN

    AN --> READYRUN{有 ready 验证任务?}
    BEH --> READYRUN
    READYRUN -->|有| RUN[跑 train + eval<br/>读 metrics.json]
    RUN --> FIX{失败?}
    FIX -->|是, 且还有额度| AUTOFIX[bounded autofix 重试]
    FIX -->|是, 没额度| RUNFAIL[写 run_failure.md<br/>把报错交给下一轮修]
    FIX -->|否| SOLVE{达标? 对照完成标准}
    SOLVE -->|是| DONE[标记 solved]
    SOLVE -->|否| NEXT[继续 conclude]
```

attempt 的几条硬规矩：
- **只有 `verification==True` 才算做完**（`execution.py` 里贯穿的“硬约束”）。光说“我改好了”不算数，得能编译、能跑。
- **只准写 `train/**`，绝不碰 `eval.py` / `eval.sh` / `blackbox_oracle.py`**（`execution.py:515` `_is_train_side_write_path`）。
- 大任务会**切成小 subgoal**，一次只做一小块，每个任务最多试 `execute_max_task_attempts`（默认 3）次。
- 验证失败时，**真实的 stderr/traceback 会被写进 `.auto/run_failure.md`**，下一轮 Execute 第一件事就是修它，而不是继续优化指标。
- 失败的验证任务会被 `repair_failed_run_tasks`（`todo.py:287`）**自动变成一个“修复任务”**插进 DAG。
- 子任务的完整上下文落到 `.autoresearch/delegate_contexts/*.json`（父进程只看摘要）。

### 4.6 conclude（复盘）到底在“算账”

- `evaluate` handler（`phase_handlers.py`）：
  1. `finalize_experiments`：算出 best / Pareto 前沿 / 按版本策略处理 commit/rollback；
  2. 更新 `gate_signals.json`（门控信号）；
  3. 往 project.md 的“短期结论”写**人话**：best 是谁、指标多少、**完成标准达没达标、下一步是 pause 还是继续**；
  4. 把经验写进 `lessons.jsonl`。
- `compress` handler：信念段太长时裁剪，别让上下文膨胀。
- 然后 `_next_after_conclude` 拍板下一步（见第 3 节的分支）。

---

## 5. 状态文件地图：想查什么去哪翻

都在项目目录下的 `.autoresearch/`（和根目录的 `project.md` / `.auto/`）。

| 文件 | 装什么 | 你什么时候看它 |
|------|--------|----------------|
| `monitor.json` | 心跳：状态/阶段/第几步/花了多少钱/pid | 想知道“**现在跑到哪了**”→ `/autoresearch show` |
| `project.md` | 人类可读的项目态 + **短期结论** | 想知道“**结果咋样、解没解决**”，第一眼看这里 |
| `state.json` | 所有实验 / best / Pareto / 指标历史 | 想看“**每次实验的数字**” |
| `todo_state.json` | 任务 DAG（谁依赖谁、做到哪） | 想看“**计划和进度**” |
| `gate_signals.json` | best_id / pareto_changed / plateau 计数 / 是否要重规划 | 想知道“**为什么它决定换方向/暂停**” |
| `budget.json` | token / USD 账本 | 想知道“**花了多少钱**” |
| `lessons.jsonl` | 经验账本（**gitignore，回滚也不丢**） | 想看“**踩过哪些坑**” |
| `experiment_memory.json/md` | 实验记忆摘要 | 规划下一步的参考 |
| `regression_cases.json` | 回归用例 / 收尾契约 | 收尾阶段防止改坏 |
| `debug/inflight.json` | **当前卡在哪个 LLM/shell/phase** | 感觉“卡住了”时看这个 |
| `debug/debug.jsonl` | debug 事件流 | 详细排查 |
| `step_traces/step_XXX_*.json` | 每一步的完整快照 | 事后复盘单步 |
| `delegate_contexts/*.json` | 子任务拿到的完整上下文 | 想看“子任务当时看到了啥” |
| `.auto/run_failure.md` | 最近一次失败的报错 | 排查为什么没跑通 |
| `STOP` | 停止哨兵（放一个文件就优雅停） | 想让它停下来 |
| `bin/python` | 指向 python3 的小垫片 | 防止老环境把 `python` 认成 python2 |

---

## 6. 框架“机械负责”的四件事（不交给 LLM 瞎判断）

这是这个框架最重要的设计原则：**机械的判断交给代码，别让大模型“凭感觉”决定**。

```mermaid
flowchart LR
    subgraph 框架代码负责
        B[💰 预算<br/>budget.json 触顶→暂停/降级]
        M[❤️ 心跳<br/>monitor.json 每步一写]
        T[⏱️ 超时<br/>call_with_deadline 给每次调用硬 deadline]
        S[✅ 完成判定<br/>读 metrics.json 对照 Completion Criteria]
    end
    subgraph LLM 负责
        I[💡 出主意 / 写代码 / 想方案]
    end
```

- **完成判定**：程序读 `program.md` 里的 `## Completion Criteria`（比如 `z <= 0.001`），
  再读 `metrics.json` 的真实指标，用 `is_metric_solved` **算**出来解没解决（`state/completion.py`）。
  不是让大模型说“我觉得完成了”。
- **超时**：每次 LLM/命令调用都套 `call_with_deadline`，卡住会被打断并记录到 inflight。
- **预算**：`budget.json` 累计 token/USD，触顶就暂停或降级 persona，保证“可以无限跑”不会变成“无限烧钱”。
- **心跳**：`monitor.json` 每步一写，即使它在后台独立进程里跑，你也能随时观测。

---

## 7. 安全边界（它绝对不会做的事）

- 不改 `eval.py` / `eval.sh` / `blackbox_oracle.py`，只在 `train/**` 里改；
- 不做静默的破坏性 git 操作，默认版本策略 `artifact_only`（只存 patch/manifest，不自动 commit）；
- 不把子进程长篇上下文塞回父进程状态；
- 不靠“LLM 自觉”决定停不停，用 `STOP` 哨兵 / 预算 / 完成标准这些**硬闸门**；
- 越权工具调用被 `tool_guard` 当场拒绝。

---

## 8. 跟着一个真实例子走一遍（black_box）

题目：最小化黑盒函数 `z`，完成标准 `z <= 0.001`，只能改 `train/`。

1. **plan**：开会 → 决定“修好 train 入口 + 写一个带缓存的确定性 2D 搜索器 + 跑验证”，生成任务 DAG。
2. **attempt #1**：跑 baseline，发现 `train/optimizer.py` 缺失报错 → 自动生成“修复任务”。
3. **attempt #2**：修好入口，baseline 跑通，`z=10522`（有指标了）。
4. **attempt #3**：写出真正的搜索器（广域网格探索 + 局部精修），跑验证 → `z=0.0`。
5. **完成判定**：`0.0 <= 0.001` ✅ → 直接刷新 `project.md` 短期结论（best=exp-0003、metric z=0.0、completion met、next=pause）→ **pause**。
6. 你 `/autoresearch show` 看到 `status: paused`，翻 `project.md` 一眼就知道“解决了”。

---

## 9. 现在还差什么？（距离“真正的 AutoResearch”的 TODO）

当前框架已经能把**单目标、指标明确、可快速评测**的任务端到端跑通（black_box 已验证）。
但离“通用自动科研”还有这些可以做：

### A. 让每一步真正变成“完整 Agent”
- 现在 attempt 主要走**确定性的 direct_write**（让模型直接返回文件内容），
  `_run_step_agent_loop`（`controller.py:337` 附近）虽然预留了“把每一步换成完整 RAgent 循环”的接口，但默认没启用。
- **TODO**：把 plan/attempt/conclude 都升级成真正带工具调用的子 Agent 循环，让它能自己多轮 read→改→跑→再改。

### B. Planner 直接产出结构化任务，去掉“prose 解析”兜底
- `planner.py` 里 `_plan_to_todo_state`/`_classify_plan_item` 明确写着是“临时兼容层”，
  靠正则把自然语言计划猜成任务类型，容易误判。
- **TODO**：强约束 LEADER 只输出 typed DAG JSON，彻底退役 prose 解析。

### C. 真正的“语义压缩”
- 现在 conclude 的 `compress` 只是“信念段太长就裁掉尾巴”，是确定性截断。
- **TODO**：用便宜模型做真正的语义压缩（保留关键结论、丢冗余），让它能跑成百上千轮不爆上下文。

### D. 并行 / 多分支探索
- 现在是单线程一条 DAG 往前走。
- **TODO**：支持并行开多个实验分支、用 Pareto 前沿做真正的多目标搜索与择优。

### E. 版本管理落地
- `commit_pareto` / `branch_per_trial` 等策略代码在，但默认只用 `artifact_only`，未充分实战。
- **TODO**：在真实 git 项目上验证“每个有效 trial 自动 commit / 开分支”，让好结果可回溯、坏结果可回滚。

### F. 子进程真委派
- attempt 目前大多在**父进程内联**完成子任务，`delegate_contexts` 已经把上下文落盘了，但还没大规模用 `delegate_task` 起真正的子进程去干。
- **TODO**：把叶子任务真正委派给隔离子 Agent 执行，父进程只调度 + 收 digest。

### G. 更广的任务类型
- 目前深度验证的是黑盒优化这类“有明确数值指标 + 秒级评测”的任务。
- **TODO**：验证多文件工程改造、长训练任务（`long_job` 模式已有雏形）、非数值指标（如通过率/人评）等更真实的科研场景，并补齐鲁棒性与断点续跑。

### H. 可视化
- 现在看进度靠 `monitor.json` 文本。
- **TODO**：把 monitor / step_traces / DAG 接到 R-Agent Cockpit，做成可视化面板。

---

## 10. 一页速记

- **入口**：`/autoresearch run <目录>` 后台起；`show` 看进度；`kill` 杀进程。
- **大脑**：`ThreeStepController` 跑 **plan → attempt → conclude** 循环。
- **记忆**：L0 宪法 / L1 信念 / L2 项目态 / L3 细节 / L4 档案，父进程只看摘要。
- **分工**：plan 动嘴、attempt 动手（唯一能改代码跑命令）、conclude 算账。
- **闸门**：完成标准、预算、超时、STOP —— 全是**代码机械判断**，不靠 LLM 自觉。
- **安全**：只改 `train/**`，不碰 eval/oracle，默认不自动 commit。
- **状态**：想知道“解没解决”看 `project.md`；想知道“跑到哪”看 `monitor.json`。
