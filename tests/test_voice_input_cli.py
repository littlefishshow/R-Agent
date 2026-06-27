import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main


class _DummyConsole:
    def __init__(self):
        self.messages = []

    def print(self, *args, **kwargs):  # noqa: ARG002
        self.messages.append(" ".join(str(arg) for arg in args))


def test_is_voice_input_command_matches_bbb_only():
    assert main._is_voice_input_command("/bbb")
    assert main._is_voice_input_command("  /BBB  ")
    assert not main._is_voice_input_command("/bbb hello")
    assert not main._is_voice_input_command("/help")


def test_transcription_text_from_response_supports_object_and_dict():
    obj = type("Resp", (), {"text": "  你好  "})()
    assert main._transcription_text_from_response(obj) == "你好"
    assert main._transcription_text_from_response({"text": " hello "}) == "hello"
    assert main._transcription_text_from_response({}) == ""


def test_capture_voice_input_cancel_does_not_transcribe(monkeypatch, tmp_path):
    console = _DummyConsole()
    monkeypatch.setattr(main.config, "get_api_key", lambda: "key")
    monkeypatch.setattr(main, "TOOL_LOG_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "_record_audio_until_keypress", lambda console, path: False)

    def fail_transcribe(path):  # pragma: no cover - should not be called
        raise AssertionError("cancelled voice input should not transcribe")

    monkeypatch.setattr(main, "_transcribe_audio_file", fail_transcribe)

    assert main.capture_voice_input(console) is None


def test_capture_voice_input_transcribes_and_removes_temp_audio(monkeypatch, tmp_path):
    console = _DummyConsole()
    captured = {}
    monkeypatch.setattr(main.config, "get_api_key", lambda: "key")
    monkeypatch.setattr(main, "TOOL_LOG_CACHE_DIR", str(tmp_path))

    def fake_record(console, path):  # noqa: ARG001
        captured["path"] = path
        Path(path).write_bytes(b"fake wav")
        return True

    def fake_transcribe(path):
        assert path == captured["path"]
        assert os.path.exists(path)
        return "语音识别结果"

    monkeypatch.setattr(main, "_record_audio_until_keypress", fake_record)
    monkeypatch.setattr(main, "_validate_audio_file", lambda path: (True, ""))
    monkeypatch.setattr(main, "_transcribe_audio_file", fake_transcribe)

    assert main.capture_voice_input(console) == "语音识别结果"
    assert not os.path.exists(captured["path"])
    assert any("👤 You>" in message and "语音识别结果" in message for message in console.messages)

def test_record_audio_command_uses_ffmpeg_on_macos(monkeypatch):
    commands = []

    def fake_which(name):
        return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

    class FakeProc:
        returncode = 0
        stderr = None

        def __init__(self):
            self._poll_count = 0

        def poll(self):
            self._poll_count += 1
            return 0

        def terminate(self):  # pragma: no cover - should not be needed
            raise AssertionError("ffmpeg already exited")

    monkeypatch.setattr(main.shutil, "which", fake_which)
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **kwargs: commands.append(cmd) or FakeProc())
    monkeypatch.setattr(main.os.path, "exists", lambda path: True)

    main._record_audio_with_command("/tmp/test.wav", main.threading.Event())

    assert commands
    assert commands[0][:6] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "avfoundation"]
    assert ":0" in commands[0]

def test_validate_audio_file_rejects_too_short_file(tmp_path):
    audio = tmp_path / "empty.wav"
    audio.write_bytes(b"")

    ok, reason = main._validate_audio_file(str(audio))

    assert ok is False
    assert "过短" in reason or "为空" in reason


def test_format_voice_transcription_error_adds_hint_for_json_error():
    message = main._format_voice_transcription_error(Exception("Error code: 400 - unexpected end of JSON input"))

    assert "VOICE_INPUT_BASE_URL" in message
    assert "VOICE_INPUT_FFMPEG_DEVICE" in message



def test_voice_input_backend_aliases_whispercpp(monkeypatch):
    monkeypatch.setenv("VOICE_INPUT_STT_BACKEND", "whisper.cpp")

    assert main._get_voice_input_stt_backend() == "whispercpp"
    assert main._voice_input_uses_local_whispercpp() is True


def test_build_whispercpp_command(monkeypatch, tmp_path):
    bin_path = tmp_path / "whisper-cli"
    model_path = tmp_path / "ggml-base.bin"
    audio_path = tmp_path / "audio.wav"
    bin_path.write_text("bin")
    model_path.write_text("model")
    audio_path.write_bytes(b"wav")
    monkeypatch.setenv("VOICE_INPUT_WHISPERCPP_BIN", str(bin_path))
    monkeypatch.setenv("VOICE_INPUT_WHISPERCPP_MODEL", str(model_path))
    monkeypatch.setenv("VOICE_INPUT_LANGUAGE", "zh")
    monkeypatch.setenv("VOICE_INPUT_WHISPERCPP_THREADS", "4")
    monkeypatch.setenv("VOICE_INPUT_WHISPERCPP_EXTRA_ARGS", "--no-timestamps")

    cmd = main._build_whispercpp_command(str(audio_path), str(tmp_path / "out"))

    assert cmd[:7] == [str(bin_path), "-m", str(model_path), "-f", str(audio_path), "-otxt", "-of"]
    assert "-l" in cmd and "zh" in cmd
    assert "-t" in cmd and "4" in cmd
    assert "--no-timestamps" in cmd


def test_parse_whispercpp_text_output_removes_timestamps():
    text = """whisper_init_from_file: loading model
[00:00:00.000 --> 00:00:01.000] 你好
[00:00:01.000 --> 00:00:02.000] 世界
"""

    assert main._parse_whispercpp_text_output(text) == "你好 世界"


def test_transcribe_audio_file_with_whispercpp_reads_txt_and_cleans(monkeypatch, tmp_path):
    bin_path = tmp_path / "whisper-cli"
    model_path = tmp_path / "ggml-base.bin"
    audio_path = tmp_path / "audio.wav"
    bin_path.write_text("bin")
    model_path.write_text("model")
    audio_path.write_bytes(b"wav")
    monkeypatch.setattr(main, "TOOL_LOG_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_INPUT_WHISPERCPP_BIN", str(bin_path))
    monkeypatch.setenv("VOICE_INPUT_WHISPERCPP_MODEL", str(model_path))

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        prefix = cmd[cmd.index("-of") + 1]
        Path(prefix + ".txt").write_text("[00:00:00.000 --> 00:00:01.000] 本地识别", encoding="utf-8")
        return FakeCompleted()

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    assert main._transcribe_audio_file_with_whispercpp(str(audio_path)) == "本地识别"
    assert not list(tmp_path.glob("voice-input-transcript-*.txt"))


def test_capture_voice_input_whispercpp_does_not_require_openai_key(monkeypatch, tmp_path):
    console = _DummyConsole()
    captured = {}
    monkeypatch.setenv("VOICE_INPUT_STT_BACKEND", "whispercpp")
    monkeypatch.setattr(main.config, "get_api_key", lambda: "")
    monkeypatch.setattr(main, "TOOL_LOG_CACHE_DIR", str(tmp_path))

    def fake_record(console, path):  # noqa: ARG001
        captured["path"] = path
        Path(path).write_bytes(b"fake wav")
        return True

    monkeypatch.setattr(main, "_record_audio_until_keypress", fake_record)
    monkeypatch.setattr(main, "_validate_audio_file", lambda path: (True, ""))
    monkeypatch.setattr(main, "_transcribe_audio_file", lambda path: "本地 whisper 结果")

    assert main.capture_voice_input(console) == "本地 whisper 结果"
    assert not os.path.exists(captured["path"])

def test_project_progress_helpers_find_and_extract_project(tmp_path, monkeypatch):
    progress = tmp_path / "skills" / "agent_ops" / "demo_skill" / "Project_progress"
    progress.mkdir(parents=True)
    file_path = progress / "2026-06-27_demo_context.md"
    file_path.write_text("# Demo\n\n### Project\n\nDemo Project\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    files = main._find_project_progress_files()

    assert [f.resolve() for f in files] == [file_path.resolve()]
    assert main._extract_project_name_from_progress(file_path) == "Demo Project"
    assert "skill=demo_skill" in main._project_progress_choice_label(file_path)


def test_load_project_progress_context_selects_file(monkeypatch, tmp_path):
    progress = tmp_path / "skills" / "agent_ops" / "demo_skill" / "Project_progress"
    progress.mkdir(parents=True)
    file_path = progress / "2026-06-27_demo_context.md"
    file_path.write_text("# Demo\n\n### Project\n\nDemo Project\n\n### Summary\n\nImportant context", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FakeSession:
        def prompt(self, *args, **kwargs):  # noqa: ARG002
            return "1"

    class FakeAgent:
        def __init__(self):
            self.messages = []

        def get_token_usage_total(self):
            return "unavailable"

    console = _DummyConsole()
    agent = FakeAgent()

    assert main.load_project_progress_context(console, FakeSession(), agent, "system") is True
    assert agent.messages[0] == {"role": "system", "content": "system"}
    assert "Demo Project" in agent.messages[1]["content"]
    assert any("已载入" in message for message in console.messages)

