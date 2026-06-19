
- 工具/技能设计约定：涉及语音播放或语音回复的新 tool/skill 应包含或遵守 `voice_enabled` 显式开关字段；`voice_enabled=false` 时不得播放语音，必要时仅返回文字或按需保存文件。默认遵循用户偏好：安静优先。
- 技能查询约定：优先使用层次化 skill 查询。复杂任务先用 `skill_categories` 查看类目，再用 `skills_by_category` 查询一个或多个相关类目，最后用 `skill_view` 读取具体 skill。避免默认全量 `skills_list` 造成 token 浪费；发现分类不合理时用 `skill_relocate` 动态维护。
- read_paper 相关论文定位与 PDF 图表截图的核心实现归属于 `skills/productivity/read_paper/scripts/`；默认通过 `run_command` 调用 skill-local scripts，不注册为全局 LLM tools，以减少工具选择干扰。
