---
name: "parent_child_todo_delegation"
description: "父子协同模式执行依赖任务"
---

# 父子协同模式执行依赖任务

## When to Use
- 用户明确要求启用父子协同模式。
- 任务包含多个步骤，且步骤之间存在严格依赖关系或拓扑顺序。
- 父进程需要负责全局调度，而具体文件读写、代码编写或执行交给子智能体。

## Procedure
1. 使用 `todo_manage(action='init')` 初始化 Todo List，每个任务包含 `id`、`description`、`dependencies`。
2. 使用 `todo_manage(action='view')` 查看当前 `ready_to_execute`。
3. 父进程不要亲自执行具体文件读写或代码编写；将 ready 任务通过 `delegate_task` 分发给子智能体。
4. 子智能体返回结果后，使用 `todo_manage(action='update')` 将任务标记为 `completed` 或 `failed`，并写入结果摘要。
5. 再次 `view`，继续分发新的 ready 任务，直到 `ready_to_execute` 为空且所有任务完成。
6. 最终回复用户时，概述每个任务的完成情况和关键产物。

## Notes
- 对有严格依赖的任务，不要并行分发后续任务；必须等待前置任务完成并更新状态后再查看 ready 任务。
- 如果子任务失败，父进程应分析原因，必要时重试或追加修复任务。