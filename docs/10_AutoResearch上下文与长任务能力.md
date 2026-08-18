# AutoResearch 上下文与长任务能力：当前实现、兼容性与实测结论

> 本文基于当前仓库源码与 2026-08-16 的一次真实 benchmark 验证。  
> 目标不是宣传 AutoResearch，而是回答四个实际问题：
>
> 1. 它为什么会生效？
> 2. 它和普通 `RAgent` Agent Loop 到底是什么关系？
> 3. R-Agent 已大幅修改后，它还能不能运行？
> 4. 它现在是否真的能做长任务迭代优化？

---

## 0. 先给结论

### 0.1 一句话结论

当前 AutoResearch **不是“把普通 RAgent 连续运行很多轮”**，而是一套独立的、文件驱动的研究状态机：

```text
Plan（规划实验）
  -> Attempt（修改 + 运行指标）
  -> Conclude（选 best、记经验、决定继续/重规划/暂停）
  -> 下一轮
```

它之所以能持续优化，关键不是“模型记性特别好”，而是把重要状态写到项目文件中。即使每次 LLM 调用都是新请求，下一步仍能从文件恢复：

- 目标和不可违反规则；
- 当前计划与任务 DAG；
- 已经做过哪些实验；
- 当前最好指标和最好代码快照；
- 剩余失败样例；
- 哪些方向失败过；
- 下一步应继续、重规划还是暂停。

### 0.2 当前能力判断

| 问题 | 当前判断 |
|---|---|
| 默认 AutoResearch 能否执行 | **能。** 定向测试通过，真实 benchmark 也完成了改代码和指标提升闭环。 |
| 是否依赖当前普通 `RAgent.run_conversation()` | **默认不依赖。** 默认走 AutoResearch 自己的阶段型 LLM 调用和安全执行器。 |
| R-Agent 大改会不会直接破坏 AutoResearch | **大多数主循环改造不会直接破坏默认路径。** 但共享的 LLM 配置、工具模块、Git、安全策略、项目文件格式仍可能产生影响。 |
| 是否已经使用新版 ThreadState / durable context / DeerMem | **默认没有。** AutoResearch 有自己独立的文件型上下文和实验记忆。 |
| 是否真的能迭代优化 | **能，已实测。** 指标从 `0.0833` 提升到 `0.9167`。 |
| 是否等于“任意长任务都能自动做到最终完成” | **不等于。** 当前更准确的定位是“可恢复、可度量、可反复实验的优化循环”。 |
| 可选完整 RAgent step loop 是否已可放心启用 | **不建议直接启用。** 当前存在项目根目录、Sandbox、状态收尾契约三类兼容风险。 |

---

## 1. 用“小学生实验本”理解它

普通 Agent 像一个聪明同学：

1. 老师给一个问题；
2. 他在脑子里想；
3. 查资料、改文件、跑命令；
4. 回答老师。

AutoResearch 更像一个“自动做科学实验的小组”：

- **Plan 同学**：决定下一次实验做什么；
- **Attempt 同学**：只做当前实验并跑测试；
- **Conclude 同学**：看分数、选最好答案、记录经验；
- **文件柜**：保存目标、计划、实验结果和失败教训；
- **校长**：检查预算、停滞、完成标准和停止信号。

最重要的区别是：

> 普通 Agent 主要依靠一段持续增长的聊天历史；AutoResearch 主要依靠项目中的结构化文件接力。

所以 AutoResearch 可以在下一次 LLM 调用、下一阶段，甚至进程重启后继续读“实验本”，不要求一个模型永远记住前面所有对话。

---

## 2. 当前真实调用链

公开入口是 `auto_research_run_v2`：

```text
autoresearch/tool.py
  auto_research_run_v2_tool(...)
    -> AutoResearchSettings(...)
    -> run_phase_loop(...)
      -> run_three_step_loop(...)
        -> ThreeStepController.run(...)
```

核心控制器在 `autoresearch/controller.py`：

```text
ThreeStepController.run(max_steps)
  for each public step:
    step()
      -> 根据 project.md 的 PHASE 选择：
         plan / attempt / conclude / pause
```

每一步不是自由聊天，而是固定职责：

```text
plan
  输入：目标、项目摘要、实验历史、todo、best
  输出：结构化任务 DAG

attempt
  输入：一个 ready task、允许修改路径、失败证据
  输出：代码修改、smoke check、正式 eval、实验记录

conclude
  输入：实验指标、best/Pareto、任务状态、预算
  输出：选 best、复现检查、经验账本、下一 phase
```

### 2.1 为什么一次设置 `max_steps=6`，可能实际产生 7 个报告？

`ThreeStepController.run()` 有一个刻意的收尾规则：

```text
如果最后一个公开 step 结束在 next_phase=conclude，
就额外执行一次 conclude。
```

原因是：不能刚跑完指标就直接退出，否则 best、Pareto、版本治理和 lessons 还没有最终落盘。

所以：

```text
max_steps = 公共推进预算
最终 reports 数 = max_steps 或 max_steps + 1 个治理收尾
```

这不是多跑了一轮实验，而是补做一次便宜的治理阶段。

---

## 3. 上下文到底存在哪里？

AutoResearch 的上下文不是一条聊天记录，而是多层“外部大脑”。

## 3.1 L0：`program.md` 的 Constitution

它保存用户定义的硬规则，例如：

- 目标是什么；
- 哪些文件禁止修改；
- 主指标是什么；
- 指标越大越好还是越小越好；
- 什么数值才算完成。

源码会把它标记为：

```md
<!-- CONSTITUTION -->
用户规则，不允许循环自行修改
<!-- /CONSTITUTION -->
```

这相当于实验室章程。

## 3.2 L1：`program.md` 的 Belief

同一个文件里还有可演化的信念段：

```md
<!-- BELIEF -->
目前认为最有希望的方向、暂时结论
<!-- /BELIEF -->
```

循环可以更新 Belief，但不能偷偷改 Constitution。

这解决了一个常见问题：  
Agent 可以改变“我认为该怎么做”，但不能改变“用户要求我做到什么”。

## 3.3 L2：`project.md`

这是给人和控制器共同阅读的项目状态：

- 项目梗概；
- 当前计划；
- 改动记录；
- 短期结论；
- 当前 phase 和 phase reason。

例如：

```md
<!-- PHASE: attempt -->
<!-- PHASE_REASON: current DAG still has open work -->
```

控制器下一次启动时，不用猜自己做到哪里了，直接读 phase 标记。

## 3.4 L3：`.auto/*.md`

这里保存实现细节和局部笔记，例如：

- `plan.md`
- `execute_validation.md`
- 分析摘要

它有文件数和总字符上限，旧内容会被 GC，避免无限增长。

## 3.5 结构化状态：`.autoresearch/*.json`

这是机器真正依赖的状态：

| 文件 | 作用 |
|---|---|
| `todo_state.json` | DAG 任务、依赖、状态、重试次数、上次结果 |
| `state.json` | experiments、Pareto、best、useful failures |
| `gate_signals.json` | 是否进步、是否停滞、是否要 replan |
| `experiment_memory.json` | 当前指标、best、近期尝试、剩余失败、建议 |
| `budget.json` | token、USD、调用次数、分阶段花费 |
| `monitor.json` | 当前 phase、step、心跳、运行状态 |
| `regression_cases.json` | 已知回归样例 |

## 3.6 归档层：artifacts、trace、source snapshot

原始日志、完整 diff、执行输出、源码快照不会全塞回 prompt，而是落到 artifact：

```text
大输出 -> artifact 文件
父上下文 -> 摘要 + artifact path
```

这条规则是 AutoResearch 能长期运行的重要原因之一。否则每一轮都把所有 shell 输出和源码重新塞进模型，上下文会很快爆炸。

---

## 4. 每次模型真正看到多少上下文？

可选完整 step context 由 `build_step_context()` 构造，默认预算约为：

```text
max_chars = 12000
```

主要组成：

```text
C_step =
  program 摘要
  + project 摘要
  + todo 摘要
  + gate 摘要
  + state 的 best/Pareto/近期实验
  + experiment memory
  + regression cases
  + 当前 task
  + tool policy
```

超过预算后，不是直接把整个 JSON 从中间截断，而是依次压缩重字段，尽量保留 JSON 结构：

```text
experiment_memory
-> regression_cases
-> todo_state
-> project
-> program
-> state
```

AutoResearch 旧的阶段型 LLM 路径也有自己的父上下文预算：

```text
C_parent =
  program_md
  + modular_context buckets
  + state_summary
  + recent_observations
  + best/versioning digest

len(C_parent) <= context_char_budget
```

默认常见预算：

- 总父上下文：`24000` 字符；
- `program.md`：`12000` 字符；
- 状态摘要：`6000` 字符；
- 最近 observation：最多 `8` 个；
- 每个 bucket：最多 `3` 条，每条约 `900` 字符。

### 4.1 它和普通 RAgent 的上下文压缩不是一套东西

普通 RAgent 当前使用：

```text
ThreadState.messages
+ summary_text
+ delegation_ledger
+ skill_context
+ MemoryProvider context
```

并在请求时临时注入 durable context，聊天过长时可用 LLM 做 rolling summary。

AutoResearch 默认使用：

```text
program.md
+ project.md
+ todo/state/gate/experiment_memory
+ artifacts
```

二者目标相似，都是避免把全部历史塞进 prompt；但数据结构、更新时机和所有权完全不同。

---

## 5. AutoResearch 为什么会生效？

它不是靠一个神奇技巧，而是五个闭环同时成立。

## 5.1 目标闭环

`program.md` 持续提供：

```text
目标 + 约束 + 主指标 + 完成阈值
```

框架不会擅自定义“什么叫完成”。`parse_completion_criteria()` 只读取项目自己写出的标准，例如：

```text
repair_exact_accuracy >= 1.0
```

只有：

```text
is_metric_solved(best_metric, completion_criteria) == True
```

才应该进入 solved pause。

## 5.2 行动闭环

Plan 不是只写一段漂亮计划，而是转成 DAG：

```text
analysis
  -> implementation
    -> validation / experiment
```

Attempt 每次默认只消化有限的 ready task，避免一轮 LLM 同时改太多内容。

## 5.3 证据闭环

一次修改不等于一次成功实验。

Attempt 会：

1. 写入或 patch；
2. 做便宜 smoke check；
3. 当 run task ready 时执行正式 `eval.sh`；
4. 读取 `metrics.json`；
5. 把结果记录为 experiment。

因此它优化的是：

```text
代码 -> 可执行评测 -> 数值证据
```

而不是：

```text
模型觉得代码看起来更好
```

## 5.4 选择闭环

Conclude 会从 experiments 中计算：

```text
Pareto front + best experiment
```

然后：

- 保留 best 的源码快照；
- 对 best 重新跑 eval 做 reproducibility check；
- 对失败或未入选方向写 lesson；
- 根据 versioning policy 决定保存、提交、分支或回滚。

因此下一轮可以从“目前最好版本”继续，而不是盲目从“最新但可能更差的版本”继续。

## 5.5 控制闭环

下一步不是模型自由决定，而是状态机根据硬信号决定：

```text
if solved:
    pause
elif budget exhausted:
    pause
elif blocking failure or major error:
    plan
elif plateau >= patience:
    plan a new direction
elif too many replans without progress:
    pause for user
elif DAG has open tasks:
    attempt
else:
    plan next direction
```

这就是它比普通聊天更不容易“说着说着忘了目标”的原因。

---

## 6. 和普通 Agent Loop 的真实对比

| 维度 | 普通 `RAgent` | AutoResearch |
|---|---|---|
| 外层控制器 | 模型驱动的 think/tool/result 循环 | `plan -> attempt -> conclude` 状态机 |
| 主要状态 | `ThreadState` 和消息历史 | 项目文件与结构化实验状态 |
| 任务目标 | 通用用户任务 | 有明确指标、评测入口的优化任务 |
| 记忆单位 | 消息、摘要、委派账本、长期事实 | hypothesis、metric、best、failure、artifact |
| 上下文增长控制 | rolling summary + durable context | 固定预算摘要 + artifact path + 有界实验记忆 |
| 修改判断 | 模型判断任务是否完成 | 评测指标 + Completion Criteria |
| 失败处理 | 模型读工具错误后继续 | failure ledger + retry + replan + plateau gate |
| 版本管理 | 通常依赖用户或 Git 工具 | experiment snapshot + policy + best/Pareto |
| 长任务恢复 | 依赖会话状态与持久化设施 | 直接读取项目内状态文件 |
| 灵活性 | 高，适合开放式任务 | 较低，适合可评测的反复优化 |

### 6.1 AutoResearch 相对普通 Agent Loop 的优势

#### 优势 A：不会把“最新改动”误当成“最好改动”

普通 Agent 往往沿着当前工作区继续改。  
AutoResearch 明确区分：

```text
latest != best
```

并记录 best snapshot。

#### 优势 B：失败也能成为下一轮的输入

它保存：

- 失败样例；
- 失败 experiment；
- regression；
- lessons；
- artifact path。

失败不是聊天里一句被压缩掉的话，而是可查询状态。

#### 优势 C：适合后台运行和断点恢复

外层状态不只在进程内，所以后台子进程、心跳监控、STOP 文件和重启恢复都更自然。

#### 优势 D：完成条件更客观

普通 Agent 很容易在“看起来差不多”时停止。  
AutoResearch 可以要求：

```text
metric >= threshold
```

没有达到就不应宣称 solved。

#### 优势 E：上下文围绕“决策事实”而不是“全部历史”

对下一轮真正重要的是：

```text
试过什么 -> 得分多少 -> 哪里失败 -> 哪个最好 -> 下一步建议
```

不是之前每一句模型思考。

### 6.2 普通 Agent Loop 仍然更强的地方

- 处理模糊需求；
- 跨多个系统协调；
- 与用户实时澄清；
- 灵活使用大量工具和 Skills；
- 处理没有统一评分函数的任务；
- 在同一轮中完成复杂、多阶段的开放式推理。

所以 AutoResearch 不是普通 Agent 的升级替代品，而是一种专门的“实验优化工作流”。

---

## 7. R-Agent 大幅修改后，它还能执行吗？

答案需要分成两条路径。

## 7.1 默认公开路径：当前可以执行

公开工具参数中：

```text
use_llm_step_agents = True
```

表示 Plan/Execute 可以调用 AutoResearch 自己的 `AutoResearchStepAgent` 和直接 chat completion。

但 `AutoResearchSettings` 中另一个参数：

```text
autoresearch_step_agent_loop = False
```

才表示“每一步改用完整 `RAgent.run_conversation()`”。

当前 `auto_research_run_v2_tool()` **没有公开传入这个参数**，所以默认仍是：

```text
ThreeStepController
  + AutoResearch phase handlers
  + AutoResearchStepAgent / direct chat
  + ProjectConfinedCommandRunner
```

不是：

```text
ThreeStepController
  + full RAgent per step
```

因此你对普通 R-Agent 做的这些大改：

- MiddlewareChain；
- ThreadState；
- durable context；
- MemoryProvider / DeerMem；
- Agent 对话压缩；
- 普通 Agent 的事件流；

**不会自动进入默认 AutoResearch，也通常不会直接打断它。**

这既是好事，也是限制：

- 好处：主 Agent 大改后 AutoResearch 仍相对稳定；
- 限制：AutoResearch 没有自动获得新版 R-Agent 的全部能力。

## 7.2 共享依赖仍可能影响它

虽然外循环独立，但以下模块仍共享：

- `core.config.create_llm_client()`；
- 模型名和 API 配置；
- Git 仓库和工作区状态；
- 部分 tool / delegate 契约；
- Python 环境；
- `program.md` / `metrics.json` / `eval.sh` 协议。

所以“默认不依赖 RAgent Loop”不等于“完全无关”。

## 7.3 可选完整 RAgent step loop：当前有三类风险

源码已经预留：

```python
settings.autoresearch_step_agent_loop = True
```

开启后，每个 step 会新建：

```python
RAgent(
    max_iterations=...,
    session_id=f"autoresearch-{project_id}-{step_name}",
)
```

然后传入：

- step JSON context；
- system prompt；
- allowed tools；
- excluded tools；
- tool guard；
- `PLAN_DONE` / `ATTEMPT_DONE` / `CONCLUDE_DONE` 标签。

接口级单元测试目前通过，但真实使用仍有以下问题。

### 风险 1：项目根目录没有显式绑定到 RAgent 工具

AutoResearch 知道真正项目根目录：

```text
settings.root()
```

但 `RAgent` 构造器没有 `project_root` / `cwd` 参数。  
完整 step agent 的 `run_command` 使用进程 `os.getcwd()`；文件工具在 session sandbox 开启时，会把相对路径解析到：

```text
sandbox/sessions/<session_id>/workspace/
```

而当前环境：

```text
SESSION_SANDBOX_ENABLED=1
```

这会产生危险错位：

```text
模型上下文说：目标在 benchmark/project
相对 read_file/write_file 实际解析：session sandbox workspace
run_command 实际 cwd：R-Agent 仓库根
```

也就是说，完整 step agent 可能“理解了正确项目，却在错误目录操作”。

### 风险 2：Attempt 的 DONE 标签不能证明 AutoResearch 状态已更新

当前 `_apply_step_agent_result()` 只对 `plan` 解析任务 JSON。  
对 `attempt` 和 `conclude`，只要模型最终文本含有 DONE tag，控制器就可能认为 step 完成。

但完整 RAgent 使用的是普通 `todo_manage`，而 AutoResearch 外循环读取的是：

```text
.autoresearch/todo_state.json
```

两套 todo 不天然同步。

因此可能出现：

```text
RAgent 回答 ATTEMPT_DONE
但 AutoResearch todo 仍是 pending
也没有正式 experiment record
控制器却进入 conclude
```

### 风险 3：Conclude DONE 可能绕过确定性治理

如果完整 RAgent conclude 返回 `CONCLUDE_DONE`，当前代码会提前返回，不再执行默认：

- `make_evaluate_handler()`；
- `finalize_experiments()`；
- `make_compress_handler()`。

这可能绕过：

- best / Pareto 计算；
- versioning；
- rollback；
- lessons；
- reproducibility；
- completion criteria 判断。

### 7.4 当前推荐

现阶段推荐保持：

```text
autoresearch_step_agent_loop = False
```

先使用已验证的默认阶段型路径。

如果未来要真正复用新版 RAgent，至少要先补齐：

1. `RAgent(project_root=...)` 或等价的 scoped workspace；
2. file tools 与 `run_command` 使用同一个项目根；
3. AutoResearch todo 与普通 `todo_manage` 的桥接；
4. step 输出使用结构化 result contract，而不是只找 DONE tag；
5. Conclude 的确定性治理必须始终执行，不能被模型文本短路；
6. 为完整模式补真实 benchmark，而不仅是 mock 单测。

---

## 8. 这次真实 benchmark 测到了什么？

测试项目：

```text
autoresearch/benchmarks/atr_playground/json_repair_micro
```

目标：修复常见损坏 JSON。  
主指标：

```text
repair_exact_accuracy
```

### 8.1 测试参数

```text
background = False
max_steps = 6
max_experiments = 3
use_llm_step_agents = True
plan_max_personas = 1
llm_request_timeout = 120s
versioning_policy = artifact_only
```

使用模型：

```text
gpt-5.5-2026-04-24
```

### 8.2 真实执行顺序

```text
Step 1: Plan
  - divergent persona
  - leader 汇总
  - 生成 DAG

Step 2: Attempt
  - 检查当前 solution
  - 跑 baseline eval

Step 3: Conclude
  - 记录首个 experiment
  - 建立 best

Step 4: Attempt
  - LLM 根据失败样例生成修复代码
  - 运行验证

Step 5: Conclude
  - 比较新旧 experiment

Step 6: Attempt
  - 运行 official eval checkpoint

额外治理 Step 7: Conclude
  - finalize experiment
  - 更新 best / Pareto / memory
```

### 8.3 指标结果

```text
测试前：
1 / 12 correct
repair_exact_accuracy = 0.0833333333

测试后：
11 / 12 correct
repair_exact_accuracy = 0.9166666667
```

提升：

```text
绝对提升 = 0.9167 - 0.0833 = 0.8334
正确样例数 = 1 -> 11
```

资源：

```text
LLM calls = 3
total tokens = 18,855
estimated USD = 0.039301
LLM duration total = 127.343s
```

### 8.4 这个结果证明了什么？

它证明了默认 AutoResearch 当前可以完成：

```text
读取目标
-> 建计划
-> 跑 baseline
-> 读取失败样例
-> 修改代码
-> 正式复评
-> 记录 experiment
-> 选出更优结果
```

这不是 mock，也不是只跑了 deterministic no-op。

### 8.5 它没有证明什么？

最终状态是：

```text
monitor.status = completed
final_phase = attempt
best metric = 0.9167
completion target = 1.0（未达到）
```

所以：

```text
completed = 这次给定的 step 预算正常执行完
solved = 项目完成标准已达到
```

二者不相等。

本次 benchmark 说明“它会真实改进”，但没有说明“6 步内一定完全解决”。

### 8.6 测试后的仓库状态

测试产生的：

- benchmark 源码修改；
- `metrics.json`；
- `results.tsv`；
- `project.md`；
- `.auto/`；
- `.autoresearch/`；
- outputs 和缓存；

均已按测试前 Git 基线还原或清理。  
benchmark 路径最终 `git status --short` 为空，用户原有其他工作区改动未被还原。

### 8.7 Git preflight 暴露出的实际限制

这次 benchmark 位于 R-Agent 父仓库内部，而父仓库测试前已有用户自己的未提交改动，因此 preflight 明确给出：

```text
target is inside another git repo
target git worktree is dirty before autoresearch starts
```

这不会阻止 `artifact_only` 模式做实验和保存 diff / source snapshot，但会削弱自动版本治理。尤其是 `commit_pareto`、`commit_all_trials`、`branch_per_trial` 需要干净基线；源码在 dirty base 下会降级成：

```text
version_action = artifact_only_dirty_base
rollback_status = skipped_dirty_base
```

因此，真正长时间无人值守运行时，推荐把目标放在：

```text
独立 Git 仓库 + 干净工作树 + 明确忽略 .autoresearch/.auto/outputs
```

否则可以评测和改进，但不能把“自动回滚到每轮基线”当作已得到保证。

---

## 9. 它真的能做“长任务迭代优化”吗？

需要把“长任务”拆成三种。

## 9.1 A 类：很多轮、每轮较短、指标明确

例如：

- 修算法正确率；
- 调启发式策略；
- 优化解析规则；
- 搜索超参数；
- 修复有限失败集合；
- 每轮能在几秒到几分钟内跑完 eval。

判断：**当前可以。**

原因：

- 状态文件可恢复；
- 每轮上下文有界；
- 有 best / Pareto；
- 有 plateau / replan；
- 有 budget / STOP；
- 有实验和失败记忆；
- 支持搜索 driver 在一次 LLM 决策后内部跑多次便宜评测。

## 9.2 B 类：单次训练本身很长

例如：

- 一次训练 2 小时；
- 需要 GPU 队列；
- 训练过程要定期读取曲线并早停；
- 任务跨机器或调度系统；
- 需要 checkpoint resume。

判断：**只有雏形，不能称为成熟支持。**

当前虽有：

- `long_job` mode；
- `monitor_commands`；
- `poll_interval_seconds`；
- timeout；
- 后台子进程；

但真正成熟的长训练还需要：

- 持久 job id；
- 进程重启后的 job reattach；
- checkpoint 语义；
- GPU/集群调度适配；
- 超时后区分“仍在训练”和“已挂死”；
- 中间指标曲线和 early stopping；
- 资源配额治理。

## 9.3 C 类：没有可靠指标的开放研究任务

例如：

- “研究一个更好的架构”；
- “写一篇高质量论文”；
- “让产品体验更好”；
- “重构整个大型系统并保证长期维护性”。

判断：**当前不适合完全自动闭环。**

没有可靠 evaluator 时，AutoResearch 的核心选择函数失去依据：

```text
哪个 experiment 更好？
是否回归？
什么时候 solved？
```

可以使用代理指标或 LLM-as-judge，但可靠性会显著下降。

### 9.4 因此最准确的结论

当前 AutoResearch 已经具备：

> **长跨度、多轮、可恢复、以指标为中心的自动改进能力。**

但还不具备：

> **对任意开放式长任务都能无人值守地稳定达到最终目标。**

---

## 10. 当前上下文管理的优点和短板

## 10.1 优点

### 优点 1：控制状态和模型对话分离

模型可以忘记，但 `project.md`、todo、state 和 best 不会跟着忘。

### 优点 2：原始证据外置

父上下文只保留 digest 和 artifact path，适合多轮运行。

### 优点 3：实验记忆是任务专用的

它不是泛化的“用户喜欢什么”，而是：

```text
当前指标
best 指标
近期尝试
剩余失败
回归
下一轮建议
```

### 优点 4：失败经验可跨 Git rollback 保留

`lessons.jsonl` 设计为 Git 回滚后仍存在。  
代码可以退回，教训不能跟着丢失。

## 10.2 短板

### 短板 1：Conclude 压缩仍是字符裁剪

当前 `make_compress_handler()` 只是：

```text
belief 超过 4000 字符 -> 截到 4000 字符
```

不是新版 RAgent 那种：

```text
previous summary + discarded history -> LLM semantic summary
```

因此跑很多轮后可能丢掉早期但重要的因果结论。

### 短板 2：两套上下文系统重复建设

当前有：

```text
普通 RAgent ThreadState / durable context / MemoryProvider
AutoResearch program/project/state/experiment memory
```

它们没有统一的数据契约，维护成本较高。

### 短板 3：默认 LLM phase 不是完整 Agent Loop

它可以做结构化 chat 和安全 action，但无法天然享受完整 RAgent 的：

- 多轮工具交互；
- Middleware；
- 动态工具发现；
- archive_subtask 统一压缩；
- 新版 delegation ledger；
- run event 语义。

### 短板 4：`completed` 容易被误读

同步路径当前会返回：

```json
{"completed": true}
```

它只表示函数正常返回，不一定表示 completion criteria 达标。

更稳妥的输出应该同时包含：

```text
run_status
solved
stop_reason
final_phase
completion_criteria
best_metric
```

---

## 11. 推荐的改造优先级

## P0：先修“完成语义”

统一输出：

```json
{
  "run_status": "completed",
  "solved": false,
  "stop_reason": "max_steps_reached",
  "final_phase": "attempt",
  "best_metric": 0.9167,
  "completion_target": 1.0
}
```

避免把“本次运行结束”误认为“研究目标完成”。

## P0：完整 RAgent 模式绑定项目根

让 `RAgent` 和全部工具显式收到同一个：

```text
project_root
```

并保证：

```text
read_file relative path
write_file relative path
search_files relative path
run_command cwd
delegate child cwd
```

全部解析到该根目录。

## P0：Conclude 始终执行确定性治理

即使 LLM conclude 成功，也必须再执行 framework-owned finalizer：

```text
finalize_experiments
-> choose best/Pareto
-> version governance
-> reproducibility
-> experiment memory
-> completion check
```

模型可以写总结，但不能代替账本结算。

## P1：给 step agent 定义结构化输出契约

不要只依赖：

```text
"ATTEMPT_DONE" in result
```

建议返回：

```json
{
  "done": true,
  "task_updates": [...],
  "changed_files": [...],
  "validation": {...},
  "artifacts": [...],
  "needs_framework_eval": true
}
```

## P1：桥接 AutoResearch todo 和 RAgent todo

外层 AutoResearch 是唯一调度源。  
普通 `todo_manage` 可以作为 step 内视图，但最终必须回写 `.autoresearch/todo_state.json`。

## P1：升级语义压缩

将 Conclude 压缩从字符裁剪升级为：

```text
旧 belief
+ 新 experiments
+ lessons
+ remaining failures
-> LLM 生成新的 bounded belief
```

但 Constitution、best metric、失败 case id 必须作为不可丢字段。

## P2：统一观测

把 AutoResearch phase / experiment 事件映射到当前 R-Agent append-only event stream：

```text
phase_started
task_started
code_changed
eval_finished
experiment_recorded
best_changed
plateau_detected
run_stopped
```

这样 GUI、CLI、离线回放能使用同一套证据。

## P2：建立两层 benchmark

建议同时保留：

### 层 1：微型确定性 benchmark

验证：

- 状态机；
- 指标读取；
- best / rollback；
- completion；
- 恢复。

### 层 2：真实长任务 benchmark

验证：

- 20+ experiments；
- 中途进程重启；
- plateau 后换方向；
- 失败实验不污染 best；
- 上下文预算长期稳定；
- token / USD 上限；
- 长 job resume；
- 完整 RAgent step loop。

---

## 12. 阅读源码的推荐顺序

如果想真正掌握，不建议从所有文件平铺阅读。按下面顺序最快：

1. `autoresearch/tool.py`  
   看公开入口、后台运行、参数和状态查询。

2. `autoresearch/phases.py`  
   看公共三阶段入口。

3. `autoresearch/controller.py`  
   看 phase 跳转、attempt/run 合并、plateau 和停止条件。

4. `autoresearch/state/memory.py`  
   看 L0–L3 文件型上下文。

5. `autoresearch/runtime_policy.py`  
   看每个 step 能看到什么、能用什么工具。

6. `autoresearch/planner.py`  
   看 persona debate 和 DAG 生成。

7. `autoresearch/execution.py` 与 `autoresearch/run_handler.py`  
   看如何改代码、验证和记录指标。

8. `autoresearch/legacy/loop.py` 的 `finalize_experiments()`  
   看 best / Pareto / versioning / reproducibility / lessons。

9. `autoresearch/state/completion.py`  
   看“完成”如何从 `program.md` 变成硬判断。

10. `core/agent.py`、`core/state.py`、`core/context_control.py`  
    再比较普通 RAgent 的 ThreadState 和上下文压缩。

---

## 13. 最终速记

记住下面六句话就够了：

1. **AutoResearch 的外循环不是普通 RAgent，而是文件驱动的三阶段状态机。**
2. **它靠实验文件和结构化状态接力，不靠一条无限增长的聊天历史。**
3. **默认路径当前能运行，而且真实 benchmark 从 1/12 提升到了 11/12。**
4. **`completed` 只表示这次运行结束；只有 completion criteria 达标才叫 `solved`。**
5. **它适合有明确 evaluator 的多轮优化，不等于能自动解决任意开放式长任务。**
6. **完整 RAgent step loop 目前只是预留路径，必须先修项目根、todo 桥接和 Conclude 治理，才能安全启用。**
