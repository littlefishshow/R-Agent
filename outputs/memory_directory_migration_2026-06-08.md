# Memory Directory Migration Backup

Date: 2026-06-08

## Goal

Normalize active memory directory from `R-Agent/memories/` to `memories/`.

## Before: `memories/MEMORY.md`

```md
<br />


```

## Before: `memories/USER.md`

```md
<br />


```

## Source: `R-Agent/memories/MEMORY.md`

```md

- 工具/技能设计约定：涉及语音播放或语音回复的新 tool/skill 应包含或遵守 `voice_enabled` 显式开关字段；`voice_enabled=false` 时不得播放语音，必要时仅返回文字或按需保存文件。默认遵循用户偏好：安静优先。
- 技能查询约定：优先使用层次化 skill 查询。复杂任务先用 `skill_categories` 查看类目，再用 `skills_by_category` 查询一个或多个相关类目，最后用 `skill_view` 读取具体 skill。避免默认全量 `skills_list` 造成 token 浪费；发现分类不合理时用 `skill_relocate` 动态维护。

```

## Source: `R-Agent/memories/USER.md`

```md
- 用户偏好复杂任务管理方式：父进程统筹动态 todo list，子进程只领取可执行子任务；子进程先判断任务是否需要拆分，若需要则提出拆分意见交给父进程判断，若不需要才执行；todo list 可为树状结构并包含拓扑依赖，父进程决定子进程数量并按依赖调度，父进程不需要掌握所有子任务上下文。

* 用户正在维护一个 Agent 项目；当用户要求进行升级换代/准备 git push 时，需要将本次升级过的内容整理保存到 README.md 中，方便用户实时查看更新了哪些内容。用户要求：每次维护 Agent 项目进行升级/重构/准备 git push 时，需要在 README.md 中按日期记录更新日志，包含具体更新内容。
- 用户正在进行 Agent memory 项目的更新迭代；需要在 outputs 中维护一份当前维护进度文档，基于已提供/调研文档的待完成进度记录当前具体进展，便于每次重启后快速了解进展。

```
