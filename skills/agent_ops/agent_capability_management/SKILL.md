---
name: "agent_capability_management"
description: "自主改进、层次化查询与维护 skills/tools"
---

# Agent Capability Management

## When to Use
- 用户希望 Agent 不反复询问执行细节，而是自主寻找答案、补齐能力并汇报结果。
- 遇到现有 skills/tools 不足、重复、过时、分类不合理或 token 成本过高的问题。
- 完成了可复用复杂流程，需要沉淀为 skill。
- skill 数量较多，需要先按类目缩小范围，再读取具体 skill。

## Principles
- 先判断任务域，再选择工具或 skill；不要默认全量展开所有 skill。
- 优先使用层次化查询，降低 token 成本。
- 类目应稳定、语义清晰、数量适中。
- 新 skill 必须放入合理类目；分类不合理时动态迁移。
- 工具和技能要避免重复：同一能力优先保留一个主入口，必要时保留兼容别名。
- 用户要求“只告诉成功失败”时，优先简短汇报。

## Hierarchical Skill Lookup Procedure
1. 对复杂任务，先判断大致任务域。
2. 如果不确定有哪些类目，调用 `skill_categories`：只返回类目和数量。
3. 根据任务域选择一个或多个类目，调用 `skills_by_category`：
   - `categories=[...]`
   - 需要更多判断依据时设置 `include_when_to_use=true`。
   - 类目过大时设置较小 `limit_per_category`，必要时再细查。
4. 从类目内候选中选择最相关的 skill，再调用 `skill_view` 读取完整 `SKILL.md`。
5. 如果发现 skill 放错类目，调用 `skill_relocate` 移动到更合适类目。

## Capability Improvement Procedure
1. 明确用户目标与成功标准；若不影响安全和结果，不向用户追问实现细节。
2. 检查现有 tools/skills 是否足够，优先复用已有能力。
3. 若缺少可复用流程，使用 `skill_create` 创建 skill。
4. 若缺少可执行能力，可在 `tools/` 下编写 Python 工具并注册。
5. 修改工具或技能后，调用 `sys_reload` 并验证。
6. 完成后汇报：成功/失败、改了什么、保留或未合并的原因。

## Consolidation Procedure
1. 盘点同类 tools/skills：名称、描述、输入输出、是否被现有流程依赖。
2. 能安全合并时：
   - 保留功能更完整、语义更清晰的主入口。
   - 把旧入口改成兼容包装，或在确认无依赖后删除。
3. 不能安全合并时：说明原因，例如职责边界不同、风险高、外部依赖不同。
4. 删除或移动后必须重新加载并做最小验证。

## Category Guidelines
- `agent_ops`: Agent 自我管理、工具/技能维护、语音策略、上下文压缩、执行纪律。
- `creative`: 漫画、插画、设计、视频、音乐、ASCII、可视化等创作型任务。
- `github`: GitHub、git、PR、issue、代码审查、仓库协作。
- `productivity`: 办公自动化、文档、日历、表格、Notion、PDF、地图等。
- 可新增类目，但应避免过细；如果某类目只有 1 个临时 skill，优先考虑是否并入现有类目。

## Safety Notes
- 不创建破坏性、越权、隐私侵犯或绕过安全限制的工具。
- 修改沙盒外文件、执行高风险命令前需要用户授权。
- 不删除明显仍有独立价值或被用户依赖的能力；必要时保留兼容接口。

## Restricted Background Agent Pattern
- 当为 Agent 增加后台复盘、自演进、curator、审计类子 Agent 时，优先采用“受限子 Agent + 工具白名单 + 运行时 guard”的模式，而不是只从 prompt 层要求模型不要调用危险工具。
- 白名单应同时作用于 tool schema 暴露面和工具执行路径；即使模型尝试调用非白名单工具，也必须在执行前返回拒绝结果。
- 后台子 Agent 默认关闭自身后台调度，避免递归启动后台 Agent。
- dry-run 模式下应拒绝 memory/skill 等长期资产写入，只允许只读查询和明确安全的 telemetry 查询；需要写入时必须有显式 apply 开关和可审计日志。
- 为这类能力补测试时，至少覆盖：非白名单工具拒绝、dry-run 写入拒绝、日志落盘、子 Agent 不递归。

## Output Style
- 对用户无需暴露完整检索过程，除非用户询问。
- 汇报应说明：合并了哪些、保留了哪些、验证结果。