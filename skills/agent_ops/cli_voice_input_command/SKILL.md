---
name: "cli_voice_input_command"
description: "为 R-Agent CLI 增加 /bbb 语音输入命令"
---

# CLI Voice Input Command

## When to Use
- 用户要求在 R-Agent 终端聊天框中通过斜杠命令触发语音输入。
- 需要把麦克风录音转写成文本，并作为普通用户输入继续进入 `RAgent.run_conversation()`。
- 需要明确 Enter 停止识别、Esc 取消返回聊天框等终端交互提示。

## Procedure
1. 先定位 `main.py` 中的 `PromptSession.prompt()` 主循环、`get_completions()` 与 `handle_slash_command()`。
2. 对“语音输入并继续正常聊天”的命令，不要只放在 `handle_slash_command()` 内吞掉；应在主循环斜杠命令拦截前特殊处理：
   - 检测 `/bbb`。
   - 执行录音与转写。
   - 若转写成功，把识别文本赋给 `user_input` 并继续走普通对话链路。
   - 若 Esc 取消、空音频、空转写或错误，则返回聊天框。
3. 录音实现建议：
   - 优先使用可选 Python 后端 `sounddevice` 写 WAV。
   - 未安装时可降级系统命令 `sox`/`rec`/`arecord`/`ffmpeg`。
   - macOS 可通过 `ffmpeg -f avfoundation -i :0 ...` 录音；设备号可暴露为 `VOICE_INPUT_FFMPEG_DEVICE`，让用户按本机麦克风设备调整。
   - 临时音频写入进程临时目录或项目 sandbox/outputs 临时目录，用完清理，不应提交到 git。
4. 终端按键监听：
   - 使用 `termios` + `tty.setcbreak()` + `select.select()` 监听单字符。
   - Enter (`\r`/`\n`) 停止录音并转写。
   - Esc (`\x1b`) 停止录音但不转写。
   - finally 中恢复 tty 属性。
5. 转写实现：
   - 默认在线后端可使用 OpenAI/Azure 兼容客户端，调用 `client.audio.transcriptions.create(file=..., model=...)`。
   - 默认模型可设为 `whisper-1`，通过 `VOICE_INPUT_STT_MODEL` 配置；语言可通过 `VOICE_INPUT_LANGUAGE` 配置。
   - 若用户希望免费/离线转写，可增加本地 `whisper.cpp` 后端：用 `VOICE_INPUT_STT_BACKEND="whispercpp"` 显式启用，录音后调用本机 `whisper-cli -m <model> -f <wav> -otxt -of <prefix>`，不要求 OpenAI/Azure API Key。
   - whisper.cpp 后端应配置 `VOICE_INPUT_WHISPERCPP_BIN` 与 `VOICE_INPUT_WHISPERCPP_MODEL`；可选 `VOICE_INPUT_WHISPERCPP_THREADS`、`VOICE_INPUT_STT_TIMEOUT`、`VOICE_INPUT_WHISPERCPP_EXTRA_ARGS`。
6. 所有需要用户操作或等待的位置必须有明确提示：开始说话、Enter 停止、Esc 取消、正在转写、未录到音频、未识别文本、未配置 API Key。
7. 同步更新：
   - `get_completions()` 加 `/bbb`。
   - `/help` 文案加 `/bbb`。
   - 欢迎 banner 提及 `/bbb`。
   - `.env.example` 记录 STT 配置项。
   - `requirements.txt` 如需加入 `sounddevice`，说明系统命令可替代。
   - README 更新日志按日期记录。
8. 测试建议：
   - 单元测试命令匹配函数。
   - 测试转写 response object/dict 兼容。
   - monkeypatch 录音函数模拟 Esc 取消，确保不调用转写。
   - monkeypatch 录音写临时 WAV 并模拟转写，确保回显文本且临时文件被清理。
   - 用 `PYTHONPATH=. pytest ...` 运行，避免部分环境下测试无法导入项目根模块。

## Configuration / Troubleshooting
- `VOICE_INPUT_BASE_URL` 应填写在项目根目录 `.env` 文件或当前 shell 环境变量中；`.env.example` 只是模板，不会自动生效。
- 语音转写应优先使用独立配置：`VOICE_INPUT_CLIENT_TYPE`、`VOICE_INPUT_BASE_URL`、`VOICE_INPUT_API_KEY`、`VOICE_INPUT_STT_MODEL`。不要默认假设普通聊天模型的 base URL/客户端类型也支持 Audio Transcriptions。
- 在主 LLM 使用 Azure/私有网关/只支持 chat completions 的代理时，若未设置 `VOICE_INPUT_*`，`/bbb` 会复用主配置，常见报错是 `Error code: 400`、`unexpected end of JSON input`、空/非 JSON 响应。
- OpenAI-compatible 配置示例：`VOICE_INPUT_CLIENT_TYPE="openai"`，`VOICE_INPUT_BASE_URL` 填服务商文档要求的 `/v1` 根地址，不要填到 `/chat/completions` 或 `/audio/transcriptions` 的完整端点；`VOICE_INPUT_STT_MODEL` 填服务商支持的 STT 模型名。
- Azure 配置示例：使用 `VOICE_INPUT_CLIENT_TYPE="azure"`，并单独设置 `VOICE_INPUT_AZURE_ENDPOINT`、`VOICE_INPUT_AZURE_API_VERSION`、`VOICE_INPUT_API_KEY`、`VOICE_INPUT_STT_MODEL`（通常是转写 deployment 名）。
- 若使用 ffmpeg 后端，另需检查 `VOICE_INPUT_FFMPEG_DEVICE` 麦克风设备号；macOS 可先列出 avfoundation 设备后再配置。

## Safety Notes
- 不要在无提示情况下监听用户麦克风；必须由显式 `/bbb` 触发并显示提示。
- Esc 取消时不得转写录音。
- 录音临时文件不应长期保存，除非用户明确要求归档。
- 该功能是语音输入，不是语音播放；若以后扩展播放仍需遵守 `voice_enabled` 显式开关约定。
