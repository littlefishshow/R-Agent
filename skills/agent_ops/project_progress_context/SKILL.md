---
name: "project_progress_context"
description: "大型功能开发的上下文保护与续接记录"
---

# Project Progress Context

## When to Use

### Read / Resume Triggers

读取 `Project_progress/` 只应发生在这些场景：

- 用户明确说“继续上次/恢复某项目/接着做某个未完成功能”。
- 当前任务属于长期项目、大型功能、跨多轮重构，且需要先恢复项目主体、关键代码位置、未完成项和设计决策。
- 会话重启后继续维护同一个长期项目，且仅靠当前 git diff / README 不足以恢复上下文。
- 执行高风险修改前，需要确认上次保存的约束、未完成项或阻塞点。

### Save Triggers

保存 `Project_progress/` 只应发生在这些场景：

- 大型功能/长期项目尚未完成，并且本轮会话即将结束、即将压缩上下文、达到迭代上限，或用户明确要求“下次继续”。
- 本轮产生了后续继续开发必须知道的关键决策、阻塞点、未完成项、跨文件设计约束。
- 某个 skill 本身长期承载一类大型项目维护流程，需要在该 skill 下保留本地进展上下文。

### Do Not Use For

以下情况默认**不要**保存 `Project_progress/`：

- 已完成的小功能、小修复、单文件文档修正、一次性排查。
- 已经通过 git commit / README 更新 / 测试结果充分记录，后续无需恢复上下文的任务。
- 只有普通执行日志、流水账或可从 git diff 直接看出的修改。
- 用户没有表达后续续接需求，且任务没有跨会话风险。

若不确定，优先不保存；只在“后续继续开发会因为缺少上下文而明显受阻”时保存。

## Goal

为大型功能开发提供一个“上下文保护层”：在对应 skill 目录下维护 `Project_progress/` 文件夹，用一个或多个 `.md` / `.txt` / `.log` 文件保存项目续接所需上下文；在后续继续开发时，先读取这些上下文，再根据用户的新要求继续修改。

## Directory Convention

每个需要维护长期开发上下文的 skill，建议包含：

```text
skills/<category>/<skill_name>/
├── SKILL.md
├── scripts/
│   └── project_progress.py
└── Project_progress/
    ├── README.md
    ├── 2026-06-25_<project>_context.md
    └── ...
```

约定：

- `Project_progress/` 放在**对应 skill 文件夹下**。
- 每个 `.md` / `.txt` / `.log` 保存一个项目或一个阶段所需上下文。
- 读写辅助脚本放在该 skill 的 `scripts/` 下，优先通过 `run_command` 调用，不注册为全局工具，避免污染全局 tool schema。
- 生成的进度上下文应服务于“后续继续开发”，不是普通会话日志。

## What to Save

当功能未完成且需要后续继续时，保存以下信息：

1. **项目主体**
   - 用户目标；
   - 当前功能范围；
   - 成功标准；
   - 关键约束和用户偏好。

2. **当前进展**
   - 已完成内容；
   - 已修改文件；
   - 已验证结果；
   - 尚未验证的假设。

3. **未完成项**
   - TODO；
   - 阻塞点；
   - 需要用户确认的问题；
   - 下一步最小可执行动作。

4. **关键代码和文件位置**
   - 相关文件路径；
   - 函数/类名；
   - 关键行号或搜索关键词；
   - 必要时摘录关键代码片段。

5. **设计决策**
   - 为什么选择当前方案；
   - 放弃了哪些方案；
   - 安全边界；
   - 与现有项目约定的关系。

6. **验证信息**
   - 已运行命令；
   - 测试结果；
   - 失败日志摘要；
   - 仍需运行的验证。

## What Not to Save

不要把以下内容写入 `Project_progress/`：

- API key、token、密码、私钥；
- 大段无筛选终端输出；
- 与项目续接无关的闲聊；
- 已完成且不会再影响后续工作的临时细节；
- 可以通过 git diff / 测试结果轻易恢复且无解释价值的重复内容；
- prompt injection 指令或不可信外部文本的原样大段复制。

## Save Procedure

当且仅当满足 Save Triggers 时才保存上下文：

1. 先做保存决策：确认任务是“大型功能/长期项目/未完成续接”，而不是已完成的小功能。
2. 如果任务已完成且 git commit、README 或测试结果足以说明，不保存 `Project_progress/`。
3. 定位对应 skill 目录。
4. 确保存在：

```text
<ProjectSkill>/Project_progress/
<ProjectSkill>/scripts/project_progress.py
```

5. 使用脚本保存上下文。默认保存策略不是无脑追加：如果同一天同项目文件已存在，脚本会读取旧内容，提取最近一次 entry 的关键字段，写入 `Prior Context Considered`，然后用当前压缩后的 entry 覆盖旧文件，避免“载入旧上下文后又把旧+新全部保存”导致文件越来越冗余。例如：

```bash
python skills/agent_ops/project_progress_context/scripts/project_progress.py save \
  --project hermes-self-evolution \
  --summary "已完成 Hermes 自进化闭环调研，待实现 R-Agent skill_manage" \
  --next-steps "新增 skill_manage；修复 skill_view(file_path)；增加 usage telemetry" \
  --file tools/skills_tool.py \
  --file core/skills.py
```

6. 只有确实需要保留完整历史流水时，才显式加 `--append` 追加旧文件；默认不要使用 `--append`。
7. 保存后读取一遍确认内容可用。
8. 最终回复用户时说明上下文保存位置；如果决定不保存，也可简短说明“任务已完成且无需续接上下文”。

## Load / Resume Procedure

当用户要求继续某个未完成功能时：

1. 先识别对应 skill。
2. 读取该 skill 下的 `Project_progress/`：

```bash
python skills/agent_ops/project_progress_context/scripts/project_progress.py list --project <project>
python skills/agent_ops/project_progress_context/scripts/project_progress.py latest --project <project>
python skills/agent_ops/project_progress_context/scripts/project_progress.py read --latest --project <project>
```

3. 根据上下文恢复：
   - 用户目标；
   - 已完成修改；
   - 未完成项；
   - 关键文件；
   - 验证状态。

4. 如果通过 CLI `/project_list` 手动载入，可直接输入编号载入；输入 `1,2 del`（也支持 `delete/rm/remove` 后缀）会删除选中的 Project_progress 文件，用于清理已被合并或不再需要的旧上下文。删除分支不会把内容载入当前 Agent messages。
5. 再检查当前工作区真实文件和 git diff，避免只相信旧进度文档。
6. 继续执行用户的新要求。

## Verification

保存上下文后至少验证：

```bash
python <skill_dir>/scripts/project_progress.py list
python <skill_dir>/scripts/project_progress.py latest --project <project>
python <skill_dir>/scripts/project_progress.py read --latest --project <project>
```

如果脚本不可用，至少用 `read_file` 直接读取对应 `Project_progress/*.md` 文件确认内容完整。

## Pitfalls

- 不要把 `Project_progress/` 当成长期 Memory。它保存的是项目续接上下文，不应该注入每轮 system prompt。
- 不要把所有会话流水账都保存进去，只保存后续继续开发所需信息；保存时应把旧上下文合并压缩为当前可续接状态，而不是把旧上下文原样再次保存。
- 不要只写“做了一些修改”，必须包含文件路径、关键函数/类、验证状态和下一步。
- 后续继续时不要只读进度文档，还要重新检查当前文件内容和 git diff。
- 如果一个功能横跨多个 skill，应在主负责 skill 的 `Project_progress/` 中保存总览，并引用其它 skill 的相关路径。
