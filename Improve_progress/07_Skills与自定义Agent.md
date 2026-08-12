# 07 · Skills 与自定义 Agent

**状态：🚧 进行中（2026-08-11：metadata 发现 + 结构化目录 + skill_context 持久 + 权限契约 + allowed-tools 已落地；仅 custom agent 待做）**
**对应 deer-flow 学习文档：** 第 10 章（Skills 与 custom agent）
**建议顺序：** 第 8 步（体验增强，可最后做）
**依赖：** `02_ThreadState结构化状态`（`skill_context` channel）、`03_上下文管理`（skill_context 通过 durable context 回注）。

---

## 1. 要解决什么问题（R-Agent 现状）

已有（核对过代码）：
- `core/skills.py:SkillManager`：文件型 skill 库（`skills/` 下分类），`list_skills`/`view_skill`/`create_skill`/`edit_skill_file`/`patch_skill_file`/`remove_skill_file`/`delete_skill`，含路径安全校验。
- `core/skill_usage.py` 记录使用情况。
- 暴露：`tools/skills_tool.py` + `tools/skill_curator_tool.py` + `tools/skill_hierarchy_tool.py`。
- prompt 里有 `SKILLS_GUIDANCE`（`core/prompt_builder.py:257`）引导模型按需 `view_skill`。
- skill 可被 agent 自己编辑（自演化角度）。

缺口（对照 deer-flow 第 10 章）：
1. **发现靠目录扫描 + prompt 引导**，没有结构化 metadata（名称/描述/触发词）供模型先"知道有哪些 skill"再决定读不读。
2. **无延迟加载契约**：没有 deer-flow `describe_skill` 那种"先看摘要、需要时再读全文"的两段式。
3. **无 allowed-tools policy**：skill 激活后不能限制"只允许用哪些工具"。
4. **skill_context 不持久**：读过的 skill 没进 `skill_context` channel，压缩对话后模型会"忘记读过什么"。
5. **无 custom agent / SOUL.md 覆盖机制**：R-Agent 有全局 `SOUL.md`，但没有 per-agent 的 model override / tool group / skill allowlist / `update_agent`。

---

## 2. deer-flow 是怎么做的

第 10 章要点：
- skill 有**可发现 metadata**（先知道有哪些，不必马上读全文）。
- 可 slash 显式激活（`/skill-name`）。
- 可通过 `describe_skill` **延迟加载**详情。
- 有 **allowed-tools policy**（激活后限制可用工具）。
- 被读取后进入 `skill_context`，压缩后仍保留引用。
- 可通过 `skill_manage` 创建/更新，但不鼓励直接写 `SKILL.md` 到 workspace（skill 是持久能力，不是交付物）。

custom agent 可有：`SOUL.md` / model override / tools group / skill allowlist / thinking 配置 / 自更新工具 `update_agent`。

**权限边界（原文）：** skill 内容、custom agent 描述、memory、durable context 都可能含用户/模型文本，需 HTML escape + authority contract，防 structured tag breakout。

---

## 3. R-Agent 打算怎么改（简略步骤）

R-Agent 的 skill CRUD 已很完整，这里主要补"发现/延迟/边界/持久"。

1. **给 skill 加 metadata 头**：约定每个 skill 目录的 `SKILL.md` 顶部包含 `name / description / 触发词`，`list_skills` 返回这些摘要而非全文。
2. **两段式加载**：`list_skills`（只给摘要目录）→ `view_skill`（读全文）。把 `SKILLS_GUIDANCE` 改为引导模型"先看目录、命中触发词再 view"。
3. **skill_context 持久化**（依赖 `02`/`03`）：`view_skill` 成功后把"skill 名 + 摘要"写入 `ThreadState.skill_context`，压缩后通过 durable context 回注，避免"读完就忘"。
4. **allowed-tools（可选）**：约定 skill metadata 可声明 `allowed_tools`，激活后收敛工具集（复用 `_loop` 现有 `allowed_tools` 过滤，天然契合）。
5. **权限转义**：skill 摘要/内容注入 prompt 前做 HTML escape + 一句 authority contract（对齐 `03`/memory 的处理），防止 skill 文本伪装成系统指令。
6. **custom agent 留后续**：SOUL.md override / update_agent 属较大特性，先记录接口设想，本轮不实现（可跳过项，见学习文档第 14 章精神）。

> 关键约束：现有 skill 文件不加 metadata 也要能继续用（metadata 缺失时退回"用目录名 + 首行"），保证零迁移成本。

### 本轮已落地（✅） / 待做（⬜）

- ✅ **步骤 1 · skill metadata 解析**：`core/skills.py` 新增 `SkillManager.parse_skill_metadata()`（解析 `name`/`description`/`triggers`，兼容 `---` 包裹的 front-matter、顶部裸 `key: value`、以及无 metadata 时取正文首行兜底，**绝不抛异常**）。既有 `list_skills` 的即兴解析统一改用它。
- ✅ **步骤 2 · 结构化发现**：新增 `list_skills_structured()` 返回 `[{name, dir_name, category, description, triggers}]`。R-Agent 本来就是两段式（`list_skills` 摘要 → `skill_view` 全文），本轮把"摘要"质量升级为 metadata 解析后的干净描述。实测对真实 54 个 skill 全部解析出描述。
- ✅ **步骤 3 · skill_context 持久化**：`core/agent.py:_maybe_record_skill_context` 在 `skill_view` 执行后，从入参取 `skill_name`、从返回 SKILL.md 解析出摘要，写入 `ThreadState.skill_context`（`02` 章 channel，按 skill 去重）。压缩后由 `03` 章 durable context 回注，避免"读完就忘"。
- ✅ **步骤 5 · 权限契约**：skill_context 通过 `03` 章的 `build_durable_context` 注入时，落在 `<durable_skills>` 分区并统一带 authority contract（"参考资料，非指令"）。本轮 skill 摘要只提取纯描述文本、且以低权限 user 段注入，已满足"不被当作系统指令"的目标。
- ✅ **步骤 4 · allowed-tools policy**：metadata 支持 `allowed_tools: [read_file, write_file]`。新增 `skill_activate` 工具，只有显式 `activate` 才把策略写入 `ThreadState.active_skill_policy` 并从下一轮收窄工具；普通 `skill_view` 不改变工具集。外部 `allowed_tools` 与 skill policy 取交集；执行期再次强制校验，不能靠伪造 tool_call 绕过。始终保留 `skill_activate/skill_view/skill_search/tool_search`，可随时 deactivate 或切换策略。
- ⬜ **步骤 6 · custom agent**：`SOUL.md` override / model override / `update_agent` 属较大特性，按学习文档第 14 章"可暂时跳过"的精神，本轮不实现。

---

## 4. 为什么这样改

- **为什么两段式发现足够、无需大改**：R-Agent 原本就是 `list_skills`（摘要）→ `skill_view`（全文）的渐进式加载，本身就避免了把所有 skill 全文塞进上下文。本轮的增量价值在**把摘要质量做对**——统一用 `parse_skill_metadata` 从 front-matter 取干净的 `description`，而不是原来"取首行、偶尔撞上 `---` 或 `name:`"的脆弱逻辑。
- **为什么 skill_context 要持久化**：长任务里模型可能读了某个 skill、几轮后上下文被压缩，skill 全文随旧消息被删，模型就"忘了自己读过什么"。把"读过哪个 skill + 一句话摘要"记进独立 channel，压缩后仍能通过 durable context 回注一个轻量引用——既省 token 又不失忆。
- **为什么解析/记录都放主循环、且绝不抛异常**：与 `tool_search` 同理，`skill_view` 在隔离子进程执行，主进程解析入参 + 返回最稳。skill 是体验增强，任何解析异常都不该打断对话，所以全程 `try/except` 兜底。
- **为什么 metadata 缺失要兜底而非报错**：仓库已有 54 个 skill，不能要求它们全部先补 metadata。缺失时用目录名当 name、正文首行当 description，保证**零迁移成本**，新旧 skill 都能用。
- **为什么 allowed-tools 必须显式激活**：只查看 skill 往往只是学习说明，如果此时立刻收窄工具，会让普通任务突然失去能力。显式 `skill_activate` 把“读过”与“应用权限策略”分开；没有 `allowed_tools` 的 skill 会拒绝激活，不会意外变成空工具集。
- **为什么 custom agent 暂缓**：custom agent 是独立大特性，涉及 SOUL/model/tool group/update_agent 的持久化与权限边界，仍按学习文档第 14 章建议后置。

---

## 5. 测试示例

新增 `tests/test_skill_context.py`，7 个用例全部通过：

1. `test_parse_metadata_front_matter_wrapped` —— `---` 包裹的 name/description/triggers。
2. `test_parse_metadata_top_keys_without_fence` —— 顶部裸 `key: value`。
3. `test_parse_metadata_fallback_first_line` —— 无 metadata 时取正文首行。
4. `test_parse_metadata_empty` —— 空内容不报错。
5. `test_list_skills_structured` —— 结构化目录；缺 metadata 用目录名 + 首行兜底。
6. `test_skill_view_populates_skill_context` —— **端到端**：`skill_view` 后 `skill_context` 记录该 skill，且出现在 durable context 的 `<durable_skills>` 里。
7. `test_skill_context_dedupes_repeat_views` —— 同一 skill 多次 view 只保留一条最新摘要。

**你可以亲手验证：**

```bash
cd /Users/bytedance/myenv/hermes/R-Agent

# 1) 本章测试
python3 -m pytest tests/test_skill_context.py -q          # 7 passed

# 2) 看真实 skill 目录被正确解析（54 个 skill 的一句话描述）
python3 -c "from core.skills import skill_manager; [print(c['category'],'/',c['dir_name'],'::',c['description'][:50]) for c in skill_manager.list_skills_structured()[:8]]"

# 3) 既有 skill 测试零回归 + 相关子集
python3 -m pytest tests/ -q -k "skill or agent or context or thread or event or tool"
# -> 272 passed（另 3 个 autoresearch 用例失败，git stash 已证与本次改动无关）
```

---

## 6. 进度记录
- 2026-08-11 · 建立简略计划。
- 2026-08-11 · **落地步骤 1/2/3/5**：`core/skills.py` 加 `parse_skill_metadata` + `list_skills_structured`（`list_skills` 改用统一解析）；`core/agent.py` 加 `_maybe_record_skill_context`，`skill_view` 后把 skill 摘要写入 `skill_context`（去重，经 `03` durable context 回注 + authority contract）。新增 `tests/test_skill_context.py`（7 passed），零回归（272 passed）。allowed-tools（步骤 4）与 custom agent（步骤 6）留作独立后续项。
- 2026-08-11 · **P2-2 allowed-tools 落地**：metadata 解析 `allowed_tools`；新增显式 `skill_activate activate/deactivate`；`ThreadState.active_skill_policy` 持久化当前策略；主循环 schema 与执行期双重收窄，并与外部 allowed_tools 取交集。普通 skill_view 不改变工具集。Skills 定向 9 passed，工具过滤回归 25 passed。仅 custom agent 待做。

## 6. 进度记录
- 2026-08-11 · 建立简略计划。
