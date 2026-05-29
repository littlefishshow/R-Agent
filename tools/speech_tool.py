import os
import re
import shutil
import subprocess
import datetime
import tempfile
from tools.registry import registry


def _slug(text: str, max_len: int = 24) -> str:
    # 保留少量中文/英文/数字，避免文件名太长或含特殊字符
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text.strip(), flags=re.UNICODE).strip("_")
    return (s[:max_len] or "speech")


def _default_path(text: str, output_dir: str = "outputs/tts", ext: str = ".aiff") -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f"{ts}_{_slug(text)}{ext}")


def _synthesize_with_say(text: str, path: str, voice: str, rate: int):
    say_bin = shutil.which("say")
    if not say_bin:
        return False, "say not found"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as f:
        f.write(text)
        text_file = f.name
    try:
        proc = subprocess.run([say_bin, "-v", voice, "-r", str(rate), "-f", text_file, "-o", path], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            proc = subprocess.run([say_bin, "-r", str(rate), "-f", text_file, "-o", path], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return False, proc.stderr or proc.stdout or "say 合成失败"
        return True, ""
    finally:
        try:
            os.unlink(text_file)
        except OSError:
            pass


def _synthesize_with_espeak(text: str, path: str, rate: int):
    espeak_bin = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak_bin:
        return False, "espeak not found"
    proc = subprocess.run([espeak_bin, "-s", str(rate), "-w", path, text], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return False, proc.stderr or proc.stdout or "espeak 合成失败"
    return True, ""


def speak_text(
    text: str,
    voice: str = "Tingting",
    rate: int = 180,
    play: bool = None,
    save: bool = False,
    output_path: str = "",
    output_dir: str = "outputs/tts",
    voice_enabled: bool = False,
):
    """智能语音输出：由 voice_enabled 显式控制是否播放；需要时才保存到统一目录。"""
    if not text or not text.strip():
        raise ValueError("text 不能为空")

    # voice_enabled 是最高优先级开关：False 时绝不播放。
    # 兼容旧参数 play：只有 voice_enabled=True 且 play 未显式为 False 时才播放。
    should_play = bool(voice_enabled) if play is None else bool(voice_enabled and play)

    say_bin = shutil.which("say")
    espeak_bin = shutil.which("espeak-ng") or shutil.which("espeak")
    result = {
        "played": False,
        "saved": False,
        "path": None,
        "engine": None,
        "voice_enabled": bool(voice_enabled),
        "note": "" if should_play else "voice_enabled=false，已按静音设置不播放；",
    }

    if say_bin:
        result["engine"] = "say"
        if should_play:
            # 非阻塞播放：启动系统 TTS 后立即返回，让 Agent 可以继续执行后续任务。
            # 注意：这里只确认进程已成功启动，不等待整段语音播放完成。
            try:
                proc = subprocess.Popen([say_bin, "-v", voice, "-r", str(rate), text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                try:
                    proc = subprocess.Popen([say_bin, "-r", str(rate), text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e:
                    proc = None
                    result["note"] += f"直接朗读启动失败：{e}；"
            if proc is not None:
                result["played"] = True
                result["play_async"] = True
                result["pid"] = proc.pid

        if save:
            path = output_path or _default_path(text, output_dir, ".aiff")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            ok, err = _synthesize_with_say(text, path, voice, rate)
            if ok:
                result.update({"saved": True, "path": path, "size_bytes": os.path.getsize(path)})
            else:
                result["note"] += f"保存音频失败：{err}；"
        return result

    if espeak_bin:
        result["engine"] = os.path.basename(espeak_bin)
        if should_play:
            # 非阻塞播放：启动后立即返回，不等待播放完成。
            try:
                proc = subprocess.Popen([espeak_bin, "-s", str(rate), text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                result["played"] = True
                result["play_async"] = True
                result["pid"] = proc.pid
            except Exception as e:
                result["note"] += f"直接朗读启动失败：{e}；"
        if save:
            path = output_path or _default_path(text, output_dir, ".wav")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            ok, err = _synthesize_with_espeak(text, path, rate)
            if ok:
                result.update({"saved": True, "path": path, "size_bytes": os.path.getsize(path)})
            else:
                result["note"] += f"保存音频失败：{err}；"
        return result

    raise RuntimeError("当前环境未找到可用 TTS 引擎：say/espeak-ng/espeak")


def text_to_speech(
    text: str,
    output_path: str = "",
    voice: str = "Tingting",
    rate: int = 180,
    output_dir: str = "outputs/tts",
):
    """兼容旧接口：只合成并保存，不播放；默认统一归档到 outputs/tts。"""
    return speak_text(
        text=text,
        voice=voice,
        rate=rate,
        play=False,
        save=True,
        output_path=output_path,
        output_dir=output_dir,
        voice_enabled=False,
    )


registry.register(
    name="speak_text",
    description="更智能地语音朗读文本：由 voice_enabled 控制是否播放；需要保存时统一归档到 outputs/tts。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要朗读的文本"},
            "voice_enabled": {"type": "boolean", "description": "显式语音播放开关；false 时绝不播放", "default": False},
            "voice": {"type": "string", "description": "语音名称；中文可用 Tingting", "default": "Tingting"},
            "rate": {"type": "integer", "description": "语速", "default": 180},
            "play": {"type": "boolean", "description": "兼容旧参数：是否尝试播放；仍受 voice_enabled 约束", "default": None},
            "save": {"type": "boolean", "description": "是否同时保存音频文件", "default": False},
            "output_path": {"type": "string", "description": "指定保存路径；留空则自动归档", "default": ""},
            "output_dir": {"type": "string", "description": "自动归档目录", "default": "outputs/tts"}
        },
        "required": ["text"]
    },
    handler=speak_text
)

registry.register(
    name="text_to_speech",
    description="将文本合成为语音音频文件；不播放，默认统一归档到 outputs/tts。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要合成的文本"},
            "output_path": {"type": "string", "description": "输出音频路径；留空则自动归档到 outputs/tts", "default": ""},
            "output_dir": {"type": "string", "description": "自动归档目录", "default": "outputs/tts"},
            "voice": {"type": "string", "description": "语音名称；macOS/say 环境中文可用 Tingting", "default": "Tingting"},
            "rate": {"type": "integer", "description": "语速", "default": 180}
        },
        "required": ["text"]
    },
    handler=text_to_speech
)
