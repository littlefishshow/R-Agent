---
name: "tool_surface_audit"
description: "审计并精简 Agent 工具暴露面的流程"
---

# Tool Surface Audit

## When to Use
- 当需要梳理 R-Agent 当前 tools、判断哪些应保留为主 Agent 工具、哪些应迁移到 skill scripts/后台流程/内部模块时使用。
- 当工具上下文臃肿、模型工具选择不稳定、准备精简 registry 暴露面时使用。

## Procedure
1. 用 `search_files(pattern="registry.register(", target="content", path="tools")` 枚举注册到 LLM 的工具。
2. 用脚本扫描 `tools/*.py` 中的 `registry.register(name=...)`，得到模块到工具名映射。
3. 分页读取各工具注册段，确认 schema 描述、参数、handler 语义；不要凭记忆判断。
4. 单独检查没有注册工具但被工具内部 import 的辅助模块，例如 `progress_render.py`。
5. 按四类输出审计：
   - Core surface：必须保留给主 Agent 的高频、通用、不可替代工具。
   - Conditional surface：可保留但默认隐藏/按 profile 暴露的工具。
   - Skill-local scripts：更适合放入某个 skill 的 scripts 或内部库，不应常驻主工具上下文。
   - Background/internal：应由系统钩子、后台子进程、CLI/API 入口触发，而非主 Agent 直接选择。
6. 对每个工具给出一句作用、一句处置建议和理由。
7. 最后给出目标最小工具集和迁移顺序，优先处理兼容旧接口、curator/maintenance、speech、self-evolution 等低频工具。

## Notes
- `progress_render.py` 当前不是注册工具，而是终端渲染辅助模块；不要把它误算为 LLM-visible tool。
- skill 查询宜保留层次化查询最小面：`skill_categories` / `skills_by_category` / `skill_view`；skill 写入/curator 管理宜下沉为 skill scripts 或维护命令。
- `self_evolution_review` 适合改为后台生命周期 hook 或独立 CLI，不适合常驻主工具列表。