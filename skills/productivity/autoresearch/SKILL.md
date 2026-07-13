---
name: autoresearch
description: 构建和运行受控 autoresearch 实验框架：参考 karpathy/autoresearch，将用户给定论文/仓库/提示凝练为 eval、train、program.md、baseline、实验循环、日志解析和报告流程。
---

# autoresearch

## When to use

当用户希望把一个论文、代码仓库、算法想法或实验目标转化为可迭代的自动研究框架时使用本 skill。典型请求包括：

- “帮我基于这篇论文/这个仓库做 autoresearch”；
- “构建 eval.py/eval.sh、train/train.sh、program.md，让 Agent 可以反复实验”；
- “跑 baseline，提出单一实验假设，比较指标后保留或回滚”；
- “把 karpathy/autoresearch 的模式迁移到我的任务上”。

本 skill 参考 `reference/` 中的 `karpathy/autoresearch` 源码快照，但目标不是复制其 LLM 预训练任务，而是抽象出一个通用、受控、可审计的自动实验框架。

## Reference files

首次使用本 skill 时先阅读：

1. `reference/SOURCE.md`：来源和许可注意事项；
2. `reference/README.md`：autoresearch 原始理念；
3. `reference/program.md`：原始实验循环协议；
4. `reference/prepare.py`：固定数据准备、评估函数、训练常量的设计范式；
5. `reference/train.py`：单文件训练和日志输出范式。

注意：`reference/` 只是本 skill 的参考材料。正式项目应根据用户给定论文/仓库/提示生成项目自己的 `prepare.py`、`eval.py`、`train/` 和 `program.md`。

## Core principles

1. **先定义评估，再做训练**：先构建可重复的 `eval.py`/`eval.sh`，再写训练或实验代码。
2. **固定评估协议**：实验过程中不要随意修改 evaluation harness；除非用户明确要求重定义指标。
3. **单一实验假设**：每轮只改一个核心变量，方便归因。
4. **可回滚**：所有实验在专用 git 分支上进行；差结果回滚，好结果保留。
5. **日志驱动**：训练和评估必须产生日志；结果从日志或 metrics 文件解析，不凭印象判断。
6. **资源有边界**：不要继承原始 `program.md` 的无限循环；必须设置轮数、时间、预算或用户确认边界。
7. **用户需求优先**：指标、数据集、训练任务、成功标准都来自用户提示、论文或目标仓库，而不是固定使用 val_bpb。

## Codebase reading before building the autoresearch project

本 skill 的用途是帮助 R-Agent **构建一个 autoresearch 项目脚手架**，例如生成/整理 `eval.py`、`eval.sh`、`train/`、`program.md`、baseline 和实验记录流程。它不是 autoresearch loop 自己的 Planner。

当输入是已有代码仓库，尤其是大型仓库或复杂项目时，R-Agent 在构建 autoresearch 项目前应先完整理解仓库。可以使用 `skills/agent_ops/codebase_scout/SKILL.md` 的流程建立项目地图：

- 小仓库：父进程可以直接读取 README、配置、入口、测试和关键模块。
- 大仓库：先扫目录，再把目录或问题拆给只读子 Agent，父进程只收结构化摘要和文件证据。
- 输出必须包含 train/eval/smoke/full/long_job 命令候选、可修改范围、保护范围、核心入口、模块地图和风险。

构建 autoresearch 项目时，应把 codebase scout 的结构化结果转化为项目脚手架内容：

- 写入 `program.md`：研究目标、允许修改范围、禁止修改范围、运行协议、停止条件；
- 写入 `eval.py` / `eval.sh`：固定评估入口和机器可解析指标；
- 写入 `train/train.sh`：训练或实验入口；
- 写入 `README.md` 或 `reports/autoresearch_report.md`：仓库地图、验证命令和当前 baseline；
- 必要时将完整阅读报告保存到 `.auto/survey.md` 或 `reports/codebase_scout.md`，在 `program.md` 中只保留摘要和路径。

## Target project structure

为用户项目构建如下框架：

```text
<project>/
  README.md                    # 如已有则不覆盖，必要时追加 autoresearch 说明
  prepare.py                   # 数据、缓存、下载、预处理、固定常量；可选但推荐
  eval.py                      # 评估入口：读取模型/输出，计算指标，打印机器可解析 summary
  eval.sh                      # 评估 shell wrapper
  program.md                   # 给 Agent 的实验协议和任务列表，核心文件
  results.tsv                  # 实验记录，不提交或按用户要求处理
  run.log                      # 当前训练/实验日志，通常不提交
  eval.log                     # 当前评估日志，通常不提交
  train/
    train.sh                   # 训练/实验入口
    train.py                   # 或用户任务需要的训练代码
    ...                        # 其他训练代码，尽量保持边界清楚
  reports/
    autoresearch_report.md     # 实验报告
```

如果用户给的是已有仓库，应尽量适配其现有结构；若会覆盖文件，必须先读取现有文件并确认是否覆盖/改名。

## Workflow

### 0. Gather context

先收集并确认：

- 用户的研究目标：要优化什么？要验证什么假设？
- 输入材料：论文链接、代码仓库、数据集、已有训练脚本、baseline 指标；
- 目标指标：accuracy、F1、loss、BLEU、pass@k、latency、memory、val_bpb 等；
- 数据来源：用户提供、论文指定、公开数据集、仓库自带测试集；
- 资源约束：GPU/CPU、显存、最长运行时间、最多实验轮数；
- 允许修改范围：哪些文件可改，哪些是固定 evaluation harness。

如果材料缺失但可通过工具检索，应实际检索；如果不能确定数据集或指标，向用户澄清。

### 1. Build `eval.sh` and `eval.py`

根据用户提示、论文、目标仓库和 `reference/prepare.py` 的思想构建评估文件。

`eval.py` 必须满足：

- 有清晰 CLI 参数，例如 `--data`, `--pred`, `--model`, `--split`, `--output-json`；
- 能自动检查测试集/验证集是否存在；
- 如需要测试集：
  - 优先使用用户提供的数据；
  - 其次根据论文或官方仓库说明下载/构建；
  - 下载前说明来源、大小和缓存目录；
- 固定指标计算逻辑；
- 打印机器可解析 summary，至少包含主指标；
- 将完整指标写入 JSON，例如 `metrics.json`；
- 失败时返回非零 exit code，并输出可诊断错误。

推荐输出格式：

```text
---
primary_metric: 0.912300
primary_metric_name: accuracy
higher_is_better: true
runtime_seconds: 12.4
```

`eval.sh` 必须：

- 使用 `set -euo pipefail`；
- 进入项目根目录；
- 调用 `uv run python eval.py ...` 或项目指定环境；
- 将日志写入 `eval.log`；
- 不吞掉错误。

可参考 `templates/eval.py` 和 `templates/eval.sh`。

### 2. Build `train/` and `train.sh`

根据用户给定代码仓库或论文实现训练/实验代码。

要求：

- 训练入口统一为 `train/train.sh`；
- 训练主逻辑放在 `train/` 下，避免污染根目录；
- 如果已有训练脚本，`train/train.sh` 可以只是 wrapper；
- 训练完成后应产出模型、预测文件或中间结果，供 `eval.py` 使用；
- 训练日志写入 `run.log`；
- 输出关键资源信息：时间、显存/内存、步数、loss 或任务指标；
- 单轮训练时间应有预算，必要时用 shell `timeout`。

可参考 `templates/train.sh`。

### 3. Write `program.md`

`program.md` 是核心文件：把当前 research 的目标、边界、指标、任务、实验循环凝练成给 Agent 的执行协议。

必须包含：

- 项目目标和当前 baseline；
- 允许修改的文件；
- 禁止修改的文件，尤其是 `eval.py`/`eval.sh`/固定测试集；
- 数据准备步骤；
- 运行 `uv sync` 和 `prepare.py` 的说明；
- 初始化实验分支和 `results.tsv`；
- baseline 运行步骤；
- 每轮提出一个实验假设；
- 修改训练代码；
- 运行 `train/train.sh` 和 `eval.sh`；
- 解析 `run.log` / `eval.log` / `metrics.json`；
- 根据主指标决定 keep/discard/crash；
- 回滚规则；
- 报告格式；
- 最大轮数、最长时间或停止条件。

可参考 `templates/program.md`。

### 4. Guide user through `uv sync` and `prepare.py`

如果项目使用 uv：

```bash
uv sync
uv run python prepare.py
```

如果没有 `prepare.py`，根据任务创建它或在 `program.md` 中说明无需数据准备。

执行前检查：

- Python 版本；
- `uv --version`；
- GPU/CPU 资源；
- 数据缓存目录；
- 网络访问需求；
- 是否会下载大文件。

不要在用户未授权的情况下启动长时间训练、大规模下载或高成本 API 调用。

### 5. Initialize branch and `results.tsv`

建议流程：

```bash
git status --short
git checkout -b autoresearch/<tag>
printf 'timestamp\tcommit\tprimary_metric\tmetric_name\thigher_is_better\tmemory_gb\tstatus\thypothesis\tchange_summary\tnotes\n' > results.tsv
```

如仓库没有 git，先询问用户是否初始化 git；不要擅自 `git init` 大型目录。

### 6. Run baseline

baseline 必须在任何实验修改前运行：

```bash
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
```

然后解析：

- `metrics.json`；
- `eval.log` 中的 summary；
- `run.log` 中的训练耗时/显存/异常。

将 baseline 写入 `results.tsv`，状态为 `keep`。

### 7. Propose and apply one experimental hypothesis

每轮实验前写出：

- hypothesis：为什么这个改动可能更好；
- target files：会改哪些文件；
- expected effect：预期影响主指标、耗时、内存的方向；
- risk：可能失败原因。

然后只做这个假设需要的最小修改。不要同时改多个不相关因素。

### 8. Run experiment and parse logs

运行：

```bash
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
```

解析主指标。如果没有指标：

```bash
tail -n 80 run.log
tail -n 80 eval.log
```

判断 crash、OOM、数据错误、导入错误、指标无效等。

### 9. Keep, discard, rollback

根据主指标方向判断：

- `higher_is_better=true`：更高更好；
- `higher_is_better=false`：更低更好。

规则：

- 明显改善：commit，记录 `keep`；
- 退化：记录 `discard`，回滚到实验前 commit；
- 崩溃：记录 `crash`，回滚或修复后重跑；
- 指标近似持平但代码明显更简单：可标记 `keep_simplify`，但报告中说明理由；
- 结果波动较大：建议重复 baseline/实验，报告不确定性。

### 10. Output experiment report

报告写入 `reports/autoresearch_report.md`，内容包括：

- 研究目标；
- 数据和评估协议；
- 环境信息；
- baseline；
- 实验表格；
- 最佳结果；
- 失败/回滚案例；
- 当前结论；
- 下一步建议。

可参考 `templates/report.md`。

## Safety and boundaries

- 不要无授权执行长时间训练、大规模下载、付费 API 调用或系统级安装。
- 不要自动修改 evaluation harness，除非用户明确要求重新定义任务。
- 不要把论文结论当成已验证事实；实验结果必须来自实际日志/指标。
- 不要继承原始 autoresearch 的无限循环；必须有明确预算或停止条件。
- 如果涉及语音输出，必须遵守 `voice_enabled` 显式开关，默认安静。

## Minimal command checklist

```bash
# inspect
pwd
ls
python --version
uv --version || true
git status --short || true

# setup
uv sync
uv run python prepare.py

# branch/results
git checkout -b autoresearch/<tag>
printf 'timestamp\tcommit\tprimary_metric\tmetric_name\thigher_is_better\tmemory_gb\tstatus\thypothesis\tchange_summary\tnotes\n' > results.tsv

# baseline / experiment
bash train/train.sh > run.log 2>&1
bash eval.sh > eval.log 2>&1
cat metrics.json

# diagnose
tail -n 80 run.log
tail -n 80 eval.log
```
