
- 工具/技能设计约定：涉及语音播放或语音回复的新 tool/skill 应包含或遵守 `voice_enabled` 显式开关字段；`voice_enabled=false` 时不得播放语音，必要时仅返回文字或按需保存文件。默认遵循用户偏好：安静优先。
- 技能查询约定：优先使用层次化 skill 查询。复杂任务先用 `skill_categories` 查看类目，再用 `skills_by_category` 查询一个或多个相关类目，最后用 `skill_view` 读取具体 skill。避免默认全量 `skills_list` 造成 token 浪费；发现分类不合理时用 `skill_relocate` 动态维护。
- read_paper 相关论文定位与 PDF 图表截图的核心实现归属于 `skills/productivity/read_paper/scripts/`；默认通过 `run_command` 调用 skill-local scripts，不注册为全局 LLM tools，以减少工具选择干扰。
- 用户偏好：生成的暂存文件都放在 sandbox 中。
- 用户读论文工作流偏好：当用户要求读论文并给出链接时，将论文下载到 `outputs/papers/指定类目/` 下，然后调用 `read_paper` skill 读取；论文保存命名使用 `日期_命名简称.pdf`，便于后续归档。
- 项目约定：sandbox/ 属于本地运行/验证产物，关闭对话后可清理，且不应被 git 跟踪；tests/ 保留为可跟踪的自动测试目录。
- autoresearch 设计偏好：避免每轮完整上下文无限累积导致溢出；长期 loop 应采用演化算法式选择/压缩，仅保留优秀或帕累托代表性修改的摘要与 artifact，失败/无用轮次可归档或丢弃；多目标优化需支持 Pareto 前沿；代码修改版本管理优先使用 git。
- 用户希望在维护 R-Agent autoresearch mode 时，同时维护 autoresearch.md：用中文、按小学生都能懂的方式描述当前已修改进程。
