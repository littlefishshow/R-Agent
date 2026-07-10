# R-Agent 小型 autoresearch mode 说明

这份文档用很简单的话记录：R-Agent 现在新增的“小型 autoresearch mode”到底改了什么、会怎么跑、第一版不会做什么。

## 一句话说明

autoresearch mode 就像一个“小研究员流水线”：

1. 先想清楚要看什么；
2. 再做很安全的小实验；
3. 最后把结果写下来，告诉你这轮要保留还是要丢掉。

第一版重点不是自动大改代码，而是先把“计划 → 执行 → 总结”这条路跑通。

## 用户怎么进入

在 R-Agent CLI 聊天框输入：

```text
/autoresearch
```

然后输入项目路径。

也可以直接输入：

```text
/autoresearch /path/to/project
```

开始后：

- 主界面还是原来的终端聊天界面；
- 运行期间输入框会锁住，用户只能看进程；
- 如果不想继续，按 `Esc` 可以中断；
- 中断后会回到普通聊天框。

## 三个 worker 是什么

第一版有三个固定角色，按顺序工作。

### 1. Plan

Plan 像“先列计划的小朋友”。

它只负责想：

- 这个项目在哪里；
- 这一轮先看哪些最基本的信息；
- 应该跑哪些安全命令。

它不会改代码。

### 2. Execute

Execute 像“按计划做事的小朋友”。

第一版它只跑短时间、只读的安全命令，例如：

- `pwd`
- `git status --short`
- `find . -maxdepth 2 -type f`

它会把命令输出写到日志里。

### 3. Conclude

Conclude 像“写作业总结的小朋友”。

它会看 Execute 的日志，然后写出：

- 这一轮有没有跑完；
- 有没有命令失败；
- 决定是 `keep` 还是 `crash`；
- 这轮学到了什么。

## 文件会保存在哪里

在用户选择的项目目录下面，会创建：

```text
.autoresearch/
```

里面主要有：

```text
.autoresearch/
  state.json                 当前运行状态
  plan.json                  Plan 写的计划
  execute_result.json        Execute 的执行结果
  conclude_result.json       Conclude 的总结
  memory.md                  长期观察记录
  lessons.md                 每轮经验总结
  results.tsv                多轮结果表格
  traces/                    Debug trace 总目录
    trace.jsonl              所有小进程按时间排队的事件
    plan.jsonl               只看 Plan 的事件
    execute.jsonl            只看 Execute 的事件
    conclude.jsonl           只看 Conclude 的事件
    flow.md                  人能直接读的流程记录
    contexts/                三个小进程的上下文快照
      plan_latest.json
      execute_latest.json
      conclude_latest.json
  runs/
    exp_时间戳/
      plan.json
      execute_result.json
      conclude_result.json
      trace.jsonl
      flow.md
      plan_after_plan_context.json
      execute_after_execute_context.json
      conclude_after_conclude_context.json
      01_pwd.log
      02_git.log
      ...
```

可以把 `.autoresearch/` 理解成这个项目自己的“研究笔记本”。

## Debug trace 是什么

Debug trace 就像给三个小朋友每人发了一个“记录本”。

现在每一轮运行时，系统会自动记录：

- Plan 什么时候开始、想出了什么计划、当时看到的目标和安全命令；
- Execute 什么时候开始、每条命令什么时候跑、退出码是多少、输出日志在哪里；
- Conclude 什么时候开始、看到哪些执行结果、最后为什么决定 `keep` 或 `crash`；
- Main 总控什么时候初始化了本轮目录。

这些记录分两种：

1. `.jsonl`：给之后 debug 或程序读取用，一行就是一个事件；
2. `.md`：给人直接看，用时间顺序写清楚流程。

如果之后某一轮 autoresearch 出问题，可以先看：

```text
.autoresearch/traces/flow.md
.autoresearch/traces/trace.jsonl
.autoresearch/traces/contexts/
```

这样就不用只靠终端上闪过去的文字，也能知道三个小进程当时做了什么、看到了什么。

## 第一版不会做什么

为了安全，第一版故意不做这些事：

- 不自动大改代码；
- 不自动跑很长训练；
- 不自动下载大文件；
- 不自动 `git reset --hard`；
- 不无限循环；
- 不做复杂 GUI。

第一版只是先把最小闭环跑通。

## 本次代码改动

本次实现主要改了这些地方：

1. 新增 `core/autoresearch.py`
   - 放 autoresearch 的核心逻辑；
   - 包含 Plan / Execute / Conclude 三个 worker；
   - 创建 `.autoresearch/` 状态目录；
   - 写入 JSON、Markdown、TSV 和每轮日志；
   - 新增 `AutoresearchTracer`，把 Main / Plan / Execute / Conclude 的事件、上下文快照和流程说明分类归档到 `.autoresearch/traces/` 与每轮 `runs/exp_xxx/`。

2. 修改 `main.py`
   - 新增 `/autoresearch` 本地命令；
   - 支持输入项目路径；
   - 运行时复用已有 Esc 中断机制；
   - 运行结束后在终端显示本轮结果。

3. 新增 `tests/test_autoresearch_mode.py`
   - 检查状态文件是否生成；
   - 检查中断后状态是否标记为 interrupted；
   - 检查终端展示结果是否可读；
   - 检查 debug trace、worker 分类事件、上下文快照和流程归档是否生成。

4. 更新 `README.md`
   - 在更新日志中记录本次 autoresearch mode 最小闭环升级。

## 后续可以怎么升级

下一步可以逐步加入：

- 让 Plan 能根据项目类型生成更聪明的实验计划；
- 让 Execute 在安全白名单内做小范围代码修改；
- 让 Conclude 根据指标决定保留或回滚；
- 增加更多评估指标；
- 把 `.autoresearch/` 的结果展示到 Cockpit 可视化界面里。
