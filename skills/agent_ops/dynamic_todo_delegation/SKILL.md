---
name: "dynamic_todo_delegation"
description: "父进程统筹动态树状任务与子进程领取/拆分协议"
---

# Dynamic Todo Delegation

## When to Use

- 用户要求父进程统筹复杂任务、子进程只处理具体子任务。
- 任务之间存在依赖关系，需要按拓扑顺序调度。
- 初始任务可能比较笼统，需要允许子进程提出拆分建议，但由父进程审批。
- 需要控制子进程数量和每个子进程的最大思考轮数。
- 父进程不希望掌握每个子任务的完整上下文，只维护全局任务树、依赖和状态。

## Core Principles

1. 父进程是唯一的全局调度者。
2. 子进程只领取 ready leaf task，不直接调度其他子进程。
3. 子进程如果发现任务过于笼统，应提交拆分建议，不要强行完成。
4. 拆分建议必须由父进程 approve/reject。
5. Todo list 是动态树状结构，任务可有 parent_id 和 dependencies。
6. 父进程根据 ready_to_execute 和依赖关系决定并发子进程数量。
7. 每个被委托任务应设置合理 max_iterations，避免子进程失控。

## Task Schema

建议任务字段：

```json
{
  "id": "t1",
  "description": "任务描述",
  "parent_id": null,
  "dependencies": [],
  "context_summary": "子任务需要知道的最小上下文",
  "acceptance_criteria": ["验收标准 1"],
  "deliverable": "期望交付物",
  "status": "pending"
}
```

状态包括：

- `pending`
- `in_progress`
- `needs_split`
- `blocked`
- `completed`
- `failed`
- `cancelled`

## Parent Process Procedure

### 1. 初始化动态 todo list

使用 `todo_manage init`，把复杂任务拆成初始节点。初始节点可以较粗，但应尽量包含：

- description
- dependencies
- context_summary
- acceptance_criteria
- deliverable

示例：

```json
{
  "tasks": [
    {
      "id": "t1",
      "description": "调研 A 项目 memory 实现",
      "dependencies": [],
      "context_summary": "用户想比较多个 agent memory 实现。",
      "acceptance_criteria": ["找到官方仓库", "总结存储/检索/注入/压缩流程"],
      "deliverable": "中文调研摘要"
    },
    {
      "id": "t2",
      "description": "综合所有调研结果输出文档",
      "dependencies": ["t1"],
      "deliverable": "Markdown 文档"
    }
  ]
}
```

### 2. 查看 ready 任务

调用：

```json
{"action":"ready","payload":"{}"}
```

ready 任务必须满足：

- status 是 pending；
- dependencies 全部 completed；
- 没有子任务，即 leaf task。

### 3. 决定并发子进程数量

父进程根据以下因素决定 `max_workers`：

- ready 任务数量；
- 任务是否资源密集；
- 是否可能互相写同一文件；
- 用户对速度/准确性的要求。

通常：

```text
max_workers = min(3, ready_count)
```

但若任务重、需要大量工具调用，可降为 1-2；若任务独立且轻量，可提高到 5-10。

### 4. 委托子进程

调用 `delegate_task`，为每个任务传入：

- task_id/id
- goal 或 description
- worker_id
- max_iterations

示例：

```json
{
  "tasks": "[{\"task_id\":\"t1\",\"worker_id\":\"worker-t1\",\"goal\":\"完成 t1。背景：...\",\"max_iterations\":20}]",
  "max_workers": 1,
  "default_max_iterations": 20
}
```

### 5. 审批拆分建议

如果子进程把任务置为 `needs_split`，父进程应：

1. `todo_manage get` 查看该任务和 proposal；
2. 判断拆分是否合理；
3. 合理则 `approve_split`；
4. 不合理则 `reject_split`，并可改写任务描述或直接添加更好的子任务。

批准拆分后：

- 新子任务挂到 parent_id 下；
- 父任务默认置为 blocked；
- 后续调度其 ready leaf children。

### 6. 汇总父任务

当一个父任务的所有子任务 completed 后，父进程应根据子任务结果决定：

- 将父任务 update 为 completed，并写 result；或
- 添加/批准补充子任务；或
- 标记 failed/blocked。

## Child Process Protocol

子进程收到 task_id 后应执行：

1. `todo_manage get` 查看任务；
2. `todo_manage claim` 领取任务；
3. 判断任务是否具体可完成；
4. 如果不可完成，调用 `todo_manage propose_split`，然后停止；
5. 如果可完成，执行任务；
6. 完成后 `todo_manage update` 为 completed，写 result；失败则 failed。

子进程不得：

- 调用 `delegate_task` 再派生子进程；
- 调用 `approve_split` 或 `reject_split`；
- 擅自修改不属于自己的任务；
- 假设知道父进程完整上下文。

## Split Proposal Requirements

子进程提交拆分建议时，每个 proposal task 应尽量包含：

```json
{
  "description": "具体、可执行的子任务",
  "dependencies": [],
  "context_summary": "完成该子任务所需的最小背景",
  "acceptance_criteria": ["明确验收标准"],
  "deliverable": "期望产出"
}
```

拆分粒度判断：

- 单个子任务应能在给定 max_iterations 内完成；
- 子任务之间依赖应显式写入 dependencies；
- 并行任务不要共享不可协调的写入目标；
- 不要把“研究所有东西”这种任务交给单个子进程。

## Recommended Loop

父进程循环：

```text
view/ready todo list
if 有 needs_split:
    get proposal
    approve/reject
elif 有 ready leaf tasks:
    选择 N 个 ready tasks
    delegate_task(tasks, max_workers=N)
    收集结果
    更新/检查父任务状态
elif 有 blocked 父任务且子任务已完成:
    汇总父任务
elif 全部 completed/failed/cancelled:
    输出最终结果
else:
    诊断阻塞原因
```

## Notes

- `delegate_task` 现在支持 `max_workers` 和 `default_max_iterations`。
- 每个 task 也可单独设置 `max_iterations`。
- `todo_manage` 已支持树状任务、ready leaf 查询、claim/release、propose_split、approve_split、reject_split。
