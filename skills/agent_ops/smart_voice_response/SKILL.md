---
name: "smart_voice_response"
description: "智能语音回复：voice_enabled 控制播放"
---

# Smart Voice Response

## When to Use
- 用户要求“语音告诉我”“我想听”“读出来”等语音交互。
- 用户需要可控的语音开关，有时希望保持安静。
- 需要避免音频文件散落在当前目录。

## Voice Control Contract
- 所有涉及语音播放的 skill/tool 都应支持或遵守显式字段：`voice_enabled`。
- `voice_enabled=true`：允许直接播放语音。
- `voice_enabled=false`：禁止播放语音；只返回文字，或在用户明确要求时仅保存音频文件。
- 如果没有显式字段，默认遵循用户偏好：**安静优先**，不主动播放。
- 用户自然语言也可控制：
  - 开启： “开启语音 / 语音模式 / 读出来 / 我想听”。
  - 关闭： “关闭语音 / 静音 / 安静 / 悄悄的”。

## Procedure
1. 默认使用 `speak_text`，而不是只生成文件。
2. 调用语音工具前先判断 `voice_enabled`：
   - 若 `true`，可设置 `play=true`。
   - 若 `false`，必须设置 `play=false`；如需保存，统一设置 `save=true` 并归档。
3. 默认参数：`voice="Tingting"`, `rate=180`。
4. 默认不保存文件；只有用户明确要求保存、下载、留档，才设置 `save=true`。
5. 保存时统一放到 `outputs/tts/`，不要散落到项目根目录。
6. 汇报时简洁说明：成功播放/静音未播放/失败；如保存了，再给路径。

## Fallback
- 如果播放失败但可以保存，则保存到 `outputs/tts/` 并告知路径。
- 如果本地 TTS 引擎不可用，说明失败原因。

## Output Style
- 用户要求只要结果时，只说“成功，已播放。”、“成功，已按静音设置未播放。”或“失败：原因”。
- 不反复询问用户要不要保存，除非结果依赖用户选择。