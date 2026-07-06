from __future__ import annotations
import sys
import os
import json
import atexit
import shutil
import tempfile
import uuid
import threading
import select
import termios
import tty
import subprocess
import wave
import time
import shlex
import re
from pathlib import Path


# 确保 R-Agent 目录在模块搜索路径中
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.agent import AgentInterrupted, RAgent
from core import config
from tools.registry import registry
from core.skills import skill_manager
from core.memory import memory_manager
from core.prompt_builder import build_system_prompt
from core.sandbox_cleanup import maybe_cleanup_sandbox

# 导入 rich 相关库
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.theme import Theme

# 导入 prompt_toolkit
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.formatted_text import HTML

# 自定义主题色
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "magenta",
    "danger": "bold red"
})

console = Console(theme=custom_theme)

# 当前 Rich status 动画对象。工具在精简模式下打印看板前可临时暂停它，
# 避免 spinner 与工具输出黏在同一终端行。
ACTIVE_STATUS = None

# 工具输出超过该字符数后会写入缓存日志文件，CLI 中只显示截断 + 展开链接
TOOL_OUTPUT_TRUNCATE_LIMIT = 2000

# 本次 Agent 进程的日志缓存目录，进程退出时自动清理
TOOL_LOG_CACHE_DIR = os.path.join(
    tempfile.gettempdir(), f"r-agent-logs-{uuid.uuid4().hex[:8]}"
)


def _cleanup_tool_log_cache():
    """关闭 Agent 时清理本次会话产生的工具输出日志缓存。"""
    shutil.rmtree(TOOL_LOG_CACHE_DIR, ignore_errors=True)


os.makedirs(TOOL_LOG_CACHE_DIR, exist_ok=True)
atexit.register(_cleanup_tool_log_cache)


def _dump_tool_output(func_name: str, content: str) -> str:
    """将超长工具输出写入缓存日志文件，返回文件绝对路径。"""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in func_name) or "tool"
    filename = f"{safe_name}-{uuid.uuid4().hex[:8]}.log"
    log_path = os.path.join(TOOL_LOG_CACHE_DIR, filename)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(content)
    return log_path


INTERRUPT_STATUS_HINT = "[dim](按 Esc 中断)[/dim]"


def _with_interrupt_status_hint(message: str) -> str:
    """为可 Esc 中断的 Rich status 文案追加提示，并避免重复追加。

    该 helper 只在 interruptible status 流程中调用，避免影响普通/非中断场景的状态文案。
    """
    if "按 Esc 中断" in message:
        return message
    if not message:
        return INTERRUPT_STATUS_HINT
    return f"{message} {INTERRUPT_STATUS_HINT}"


def _format_token_usage_label(agent: RAgent) -> str:
    """生成 token 使用量文案，区分父会话、子 Agent 与总量。"""
    get_last = getattr(agent, "get_last_token_usage_total", None)
    get_children = getattr(agent, "get_delegated_token_usage_total", None)
    get_total = getattr(agent, "get_total_token_usage_including_children", None)
    last = get_last() if callable(get_last) else "unavailable"
    parent = agent.get_token_usage_total()
    children = get_children() if callable(get_children) else "unavailable"
    total = get_total() if callable(get_total) else parent
    return f"last/parent/children/total tokens: {last}/{parent}/{children}/{total}"


def _format_token_usage_rprompt(agent: RAgent) -> HTML:
    """生成输入框右侧 token 使用量提示。"""
    return HTML(f'<ansibrightblack>{_format_token_usage_label(agent)}</ansibrightblack>')


def _token_usage_panel_subtitle(agent: RAgent) -> str:
    """生成回复面板右下角 token 使用量提示。"""
    return f"[dim]{_format_token_usage_label(agent)}[/dim]"


def _is_voice_input_command(text: str) -> bool:
    """判断是否触发语音输入本地命令。"""
    return text.strip().lower() == "/bbb"


def _get_voice_input_stt_model() -> str:
    """语音输入转写模型；默认使用 OpenAI 兼容的 whisper-1。"""
    return os.environ.get("VOICE_INPUT_STT_MODEL", "whisper-1")


def _get_voice_input_language() -> str:
    """语音输入语言提示；空字符串表示不显式传语言。"""
    return os.environ.get("VOICE_INPUT_LANGUAGE", "zh")


def _get_voice_input_stt_backend() -> str:
    """语音转写后端。未显式配置时保持原在线 OpenAI/Azure 逻辑。"""
    raw = os.environ.get("VOICE_INPUT_STT_BACKEND", "online").strip().lower()
    aliases = {
        "": "online",
        "default": "online",
        "openai-compatible": "openai",
        "openai_compatible": "openai",
        "whisper.cpp": "whispercpp",
        "whisper-cpp": "whispercpp",
        "local": "whispercpp",
    }
    return aliases.get(raw, raw)


def _voice_input_uses_local_whispercpp() -> bool:
    return _get_voice_input_stt_backend() == "whispercpp"


def _get_voice_input_api_key() -> str:
    """语音在线转写 API key；本地 whisper.cpp 后端不需要。"""
    return os.environ.get("VOICE_INPUT_API_KEY") or config.get_api_key()


def _get_voice_input_stt_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("VOICE_INPUT_STT_TIMEOUT", "120")))
    except ValueError:
        return 120.0


def _transcription_text_from_response(response) -> str:
    """兼容 OpenAI SDK 对象或 dict 形式的转写结果。"""
    if response is None:
        return ""
    text = getattr(response, "text", None)
    if text is None and isinstance(response, dict):
        text = response.get("text")
    return str(text or "").strip()


def _record_audio_with_sounddevice(path: str, stop_event: threading.Event, sample_rate: int = 16000):
    """使用可选 sounddevice 后端录制 WAV；未安装时由调用方降级。"""
    import sounddevice as sd  # type: ignore[import-not-found]

    frames = []
    frame_lock = threading.Lock()

    def callback(indata, frame_count, time_info, status):  # noqa: ARG001
        with frame_lock:
            frames.append(bytes(indata))

    with sd.RawInputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        callback=callback,
    ):
        while not stop_event.wait(0.05):
            pass

    with frame_lock:
        payload = b"".join(frames)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(payload)


def _record_audio_with_command(path: str, stop_event: threading.Event, sample_rate: int = 16000):
    """使用系统录音命令录制 WAV。

    支持常见命令：sox/rec 或 Linux arecord。
    """
    recorder = None
    if shutil.which("sox"):
        recorder = ["sox", "-d", "-r", str(sample_rate), "-c", "1", "-b", "16", path]
    elif shutil.which("rec"):
        recorder = ["rec", "-r", str(sample_rate), "-c", "1", "-b", "16", path]
    elif shutil.which("arecord"):
        recorder = ["arecord", "-q", "-f", "S16_LE", "-r", str(sample_rate), "-c", "1", path]
    elif shutil.which("ffmpeg"):
        if sys.platform == "darwin":
            # macOS avfoundation 音频设备默认常见为 :0；可用 VOICE_INPUT_FFMPEG_DEVICE 覆盖。
            ffmpeg_input = os.environ.get("VOICE_INPUT_FFMPEG_DEVICE", ":0")
            recorder = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "avfoundation",
                "-i",
                ffmpeg_input,
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                "-y",
                path,
            ]
        elif sys.platform.startswith("linux"):
            ffmpeg_input = os.environ.get("VOICE_INPUT_FFMPEG_DEVICE", "default")
            recorder = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "alsa",
                "-i",
                ffmpeg_input,
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                "-y",
                path,
            ]

    if recorder is None:
        raise RuntimeError(
            "当前环境没有可用录音后端。请安装 Python 包 sounddevice，或安装 sox/rec/arecord/ffmpeg。"
        )

    is_ffmpeg = bool(recorder and recorder[0] == "ffmpeg")
    proc = subprocess.Popen(
        recorder,
        stdin=subprocess.PIPE if is_ffmpeg else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        # 录音期间不读取 PIPE 容易被 ffmpeg/sox stderr 填满后阻塞；
        # 这里保持静默，启动失败会由后续音频文件校验给出友好提示。
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        while proc.poll() is None and not stop_event.wait(0.05):
            pass
    finally:
        if proc.poll() is None:
            if is_ffmpeg and proc.stdin is not None:
                try:
                    proc.stdin.write("q\n")
                    proc.stdin.flush()
                except Exception:
                    pass
            else:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError("录音进程无法终止，请检查系统录音后端。") from exc
    if proc.returncode not in (0, -15, 143, None) and not os.path.exists(path):
        raise RuntimeError(f"系统录音命令执行失败，退出码: {proc.returncode}")


def _record_audio_until_keypress(console, path: str) -> bool:
    """开始录音，Enter 停止并返回 True；Esc 取消并返回 False。"""
    stop_event = threading.Event()
    result = {"error": None}

    def worker():
        try:
            try:
                _record_audio_with_sounddevice(path, stop_event)
            except ImportError:
                _record_audio_with_command(path, stop_event)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc
            stop_event.set()

    console.print(
        "[bold cyan]🎙️  语音输入已开始，请开始说话。[/bold cyan] "
        "[yellow]按 Enter 停止并识别；按 Esc 取消并返回聊天框。[/yellow]"
    )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    stdin_fd = None
    old_tty_attrs = None
    cancelled = False

    try:
        if sys.stdin.isatty():
            stdin_fd = sys.stdin.fileno()
            old_tty_attrs = termios.tcgetattr(stdin_fd)
            tty.setcbreak(stdin_fd)
    except Exception:
        stdin_fd = None
        old_tty_attrs = None

    try:
        while not stop_event.is_set():
            if stdin_fd is None:
                raise RuntimeError("当前标准输入不是交互式 TTY，无法使用 /bbb 语音输入。")
            readable, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not readable:
                continue
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                console.print("[bold cyan]已收到 Enter，停止监听并开始识别。[/bold cyan]")
                break
            if ch == "\x1b":
                cancelled = True
                console.print("[yellow]已收到 Esc，取消本次语音输入并返回聊天框。[/yellow]")
                break
    finally:
        stop_event.set()
        if old_tty_attrs is not None and stdin_fd is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty_attrs)
            except Exception:
                pass
        thread.join(timeout=5)

    if thread.is_alive():
        raise RuntimeError("录音后端停止超时，请检查麦克风权限或系统录音命令。")
    if result["error"] is not None:
        raise result["error"]
    return not cancelled



def _validate_audio_file(path: str) -> tuple[bool, str]:
    """确认录音文件可被转写接口读取，避免把空/损坏音频发给模型服务。"""
    if not os.path.exists(path):
        return False, "录音文件不存在"
    size = os.path.getsize(path)
    if size <= 44:
        return False, "录音文件为空或过短"
    try:
        with wave.open(path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        if frames <= 0:
            return False, "录音文件没有有效音频帧"
        duration = frames / float(rate or 1)
        if duration < 0.15:
            return False, f"录音太短（约 {duration:.2f}s）"
    except wave.Error as exc:
        return False, f"录音文件不是有效 WAV：{exc}"
    return True, ""


def _create_voice_input_client():
    """创建语音在线转写客户端。

    默认复用主 LLM 配置；若主模型服务不支持 Audio Transcriptions，可单独配置：
    VOICE_INPUT_API_KEY / VOICE_INPUT_BASE_URL。
    """
    api_key = _get_voice_input_api_key()
    base_url = os.environ.get("VOICE_INPUT_BASE_URL") or config.get_openai_base_url()
    backend = _get_voice_input_stt_backend()
    default_client_type = "azure" if backend == "azure" else "openai" if backend == "openai" else config.get_client_type()
    client_type = os.environ.get("VOICE_INPUT_CLIENT_TYPE", default_client_type).lower()
    if client_type == "azure":
        from openai import AzureOpenAI
        import uuid as _uuid

        return AzureOpenAI(
            api_key=api_key,
            api_version=os.environ.get("VOICE_INPUT_AZURE_API_VERSION") or config.get_azure_api_version(),
            azure_endpoint=os.environ.get("VOICE_INPUT_AZURE_ENDPOINT") or config.get_azure_endpoint(),
            default_headers={"X-TT-LOGID": _uuid.uuid4().hex},
        )

    from openai import OpenAI

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _format_voice_transcription_error(exc: Exception) -> str:
    raw = str(exc)
    hint = ""
    if "whisper.cpp" in raw or "VOICE_INPUT_WHISPERCPP" in raw or "whispercpp" in raw:
        hint = (
            "\n提示：当前使用本地 whisper.cpp 转写。请确认已编译/安装 whisper.cpp，"
            "并正确配置 VOICE_INPUT_WHISPERCPP_BIN 与 VOICE_INPUT_WHISPERCPP_MODEL；"
            "若转写超时，可调大 VOICE_INPUT_STT_TIMEOUT 或换用更小模型。"
        )
    elif "unexpected end of JSON input" in raw or "Error code: 400" in raw:
        hint = (
            "\n提示：当前模型服务可能不支持 OpenAI Audio Transcriptions 接口，"
            "或录音文件未被服务端正确解析。请配置支持语音转写的 "
            "VOICE_INPUT_BASE_URL / VOICE_INPUT_API_KEY / VOICE_INPUT_STT_MODEL；"
            "如果使用 ffmpeg 后端，也请检查 VOICE_INPUT_FFMPEG_DEVICE 麦克风设备号。"
        )
    return f"语音转写失败：{raw}{hint}"


def _find_project_progress_files() -> list[Path]:
    """查找所有 skill-local Project_progress 文件，排除 README。"""
    return sorted(
        [
            p
            for p in Path("skills").glob("**/Project_progress/*")
            if p.is_file() and p.name.lower() != "readme.md"
        ],
        key=lambda p: (p.stat().st_mtime, str(p)),
        reverse=True,
    )


def _extract_project_name_from_progress(path: Path) -> str:
    """从进度文件中提取 Project 字段，失败则回退到文件名。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return path.stem
    marker = "### Project"
    idx = text.find(marker)
    if idx < 0:
        return path.stem
    tail = text[idx + len(marker):].splitlines()
    for line in tail:
        value = line.strip()
        if value:
            return value
    return path.stem


def _project_progress_choice_label(path: Path) -> str:
    project = _extract_project_name_from_progress(path)
    try:
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
    except Exception:
        mtime = "unknown-time"
    skill_name = path.parent.parent.name if path.parent.name == "Project_progress" else "unknown-skill"
    return f"{project} | skill={skill_name} | {mtime} | {path.name}"


def _parse_project_progress_selection(raw: str, total: int) -> tuple[list[int], bool, list[str]]:
    """解析 /project_list 输入。支持 `1,2` 载入与 `1,2 del` 删除。"""
    raw = (raw or "").strip()
    delete_selected = False
    match = re.search(r"(?:^|\s)(del|delete|rm|remove)\s*$", raw, flags=re.IGNORECASE)
    if match:
        delete_selected = True
        raw = raw[: match.start()].strip()

    selected: list[int] = []
    bad: list[str] = []
    seen: set[int] = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            bad.append(part)
            continue
        idx = int(part)
        if 1 <= idx <= total:
            if idx not in seen:
                selected.append(idx)
                seen.add(idx)
        else:
            bad.append(part)
    return selected, delete_selected, bad


def load_project_progress_context(console, session: PromptSession, agent: RAgent, system_prompt: str) -> bool:
    """交互式列出项目进度，并载入或删除选中文件。"""
    files = _find_project_progress_files()
    if not files:
        console.print("[bold yellow]当前没有找到 Project_progress 项目进度文件。[/bold yellow]")
        return True

    lines = ["**可载入的项目进度:**\n"]
    for i, path in enumerate(files, 1):
        lines.append(f"{i}. `{_project_progress_choice_label(path)}`")
    lines.append("\n请输入要载入的项目编号；可用逗号选择多个；直接回车取消。若要删除选中文件，输入如 `1,2 del`。")
    console.print(Panel(Markdown("\n".join(lines)), title="📌 Project Progress", border_style="cyan", expand=False))

    raw = session.prompt(
        HTML('<ansicyan><b>📌 载入项目编号&gt;</b></ansicyan> '),
        rprompt=_format_token_usage_rprompt(agent),
    ).strip()
    if not raw:
        console.print("[yellow]已取消载入项目进度，返回聊天框。[/yellow]")
        return True

    indexes, delete_selected, bad = _parse_project_progress_selection(raw, len(files))
    if bad:
        action = "删除" if delete_selected else "载入"
        console.print(f"[bold red]无效编号：{', '.join(bad)}。已取消{action}。[/bold red]")
        return True
    if not indexes:
        console.print("[yellow]没有选择任何项目，返回聊天框。[/yellow]")
        return True

    selected = [files[idx - 1] for idx in indexes]

    if delete_selected:
        deleted = []
        failed = []
        for path in selected:
            try:
                path.unlink()
                deleted.append(path)
            except Exception as exc:  # pragma: no cover - defensive terminal feedback
                failed.append((path, exc))
        if deleted:
            console.print(f"[bold green]🗑️ 已删除 {len(deleted)} 个项目进度文件。[/bold green]")
            for path in deleted:
                console.print(f"[dim]- {path}[/dim]")
        if failed:
            console.print(f"[bold red]有 {len(failed)} 个文件删除失败：[/bold red]")
            for path, exc in failed:
                console.print(f"[dim]- {path}: {exc}[/dim]")
        return True

    if not any(m.get("role") == "system" for m in agent.messages):
        agent.messages.append({"role": "system", "content": system_prompt})

    loaded_sections = []
    for path in selected:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        loaded_sections.append(f"# Source: {path}\n\n{text}")
    content = (
        "【已由 /project_list 手动载入的项目进度上下文】\n"
        "以下内容来自 skill-local Project_progress 文件。它用于恢复长期项目上下文；"
        "后续回答和行动应结合当前工作区真实文件/git diff 再判断，不能只依赖旧进度文档。\n\n"
        + "\n\n---\n\n".join(loaded_sections)
    )
    agent.messages.append({"role": "system", "content": content})
    console.print(f"[bold green]✅ 已载入 {len(selected)} 个项目进度到当前上下文。[/bold green]")
    for path in selected:
        console.print(f"[dim]- {path}[/dim]")
    return True


def _resolve_whispercpp_binary() -> str:
    configured = os.environ.get("VOICE_INPUT_WHISPERCPP_BIN", "").strip()
    if configured:
        if os.path.isabs(configured) or os.sep in configured:
            if os.path.exists(configured):
                return configured
            raise RuntimeError(f"whisper.cpp 可执行文件不存在：{configured}。请检查 VOICE_INPUT_WHISPERCPP_BIN。")
        found = shutil.which(configured)
        if found:
            return found
        raise RuntimeError(f"找不到 whisper.cpp 可执行文件：{configured}。请检查 VOICE_INPUT_WHISPERCPP_BIN。")

    for candidate in ("whisper-cli", "whisper-cpp"):
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("找不到 whisper.cpp 可执行文件。请设置 VOICE_INPUT_WHISPERCPP_BIN，例如 /path/to/whisper-cli。")


def _get_whispercpp_model_path() -> str:
    model = os.environ.get("VOICE_INPUT_WHISPERCPP_MODEL", "").strip()
    if not model:
        raise RuntimeError("未配置 whisper.cpp 模型路径。请设置 VOICE_INPUT_WHISPERCPP_MODEL，例如 /path/to/ggml-base.bin。")
    if not os.path.exists(model):
        raise RuntimeError(f"whisper.cpp 模型文件不存在：{model}。请检查 VOICE_INPUT_WHISPERCPP_MODEL。")
    return model


def _build_whispercpp_command(audio_path: str, output_prefix: str) -> list[str]:
    """构造 whisper.cpp CLI 转写命令。"""
    cmd = [
        _resolve_whispercpp_binary(),
        "-m",
        _get_whispercpp_model_path(),
        "-f",
        audio_path,
        "-otxt",
        "-of",
        output_prefix,
    ]
    language = _get_voice_input_language()
    if language:
        cmd.extend(["-l", language])
    threads = os.environ.get("VOICE_INPUT_WHISPERCPP_THREADS", "").strip()
    if threads:
        try:
            thread_count = int(threads)
            if thread_count > 0:
                cmd.extend(["-t", str(thread_count)])
        except ValueError:
            raise RuntimeError("VOICE_INPUT_WHISPERCPP_THREADS 必须是正整数。")
    extra_args = os.environ.get("VOICE_INPUT_WHISPERCPP_EXTRA_ARGS", "").strip()
    if extra_args:
        cmd.extend(shlex.split(extra_args))
    return cmd


def _parse_whispercpp_text_output(text: str) -> str:
    """从 whisper.cpp 文本输出中提取纯转写文本。"""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("whisper_", "main:", "system_info:")):
            continue
        line = re.sub(r"^\[[0-9:.]+\s*-->\s*[0-9:.]+\]\s*", "", line).strip()
        if line:
            lines.append(line)
    return " ".join(lines).strip()


def _transcribe_audio_file_with_whispercpp(path: str) -> str:
    """调用本地 whisper.cpp CLI，把录音文件转成文本。"""
    output_prefix = os.path.join(TOOL_LOG_CACHE_DIR, f"voice-input-transcript-{uuid.uuid4().hex[:8]}")
    txt_path = output_prefix + ".txt"
    cmd = _build_whispercpp_command(path, output_prefix)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_get_voice_input_stt_timeout(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"whisper.cpp 转写超时（>{_get_voice_input_stt_timeout():.0f}s）。请调大 VOICE_INPUT_STT_TIMEOUT 或换用更小模型。") from exc

    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        if len(stderr) > 1200:
            stderr = stderr[:1200] + "..."
        raise RuntimeError(f"whisper.cpp 转写失败（exit {proc.returncode}）：{stderr}")

    try:
        if os.path.exists(txt_path):
            return _parse_whispercpp_text_output(Path(txt_path).read_text(encoding="utf-8", errors="replace"))
        return _parse_whispercpp_text_output(proc.stdout or "")
    finally:
        if os.environ.get("VOICE_INPUT_KEEP_TRANSCRIPT", "false").strip().lower() not in ("1", "true", "yes", "on"):
            for suffix in (".txt", ".srt", ".vtt", ".json", ".csv", ".lrc", ".wts"):
                try:
                    candidate = output_prefix + suffix
                    if os.path.exists(candidate):
                        os.remove(candidate)
                except Exception:
                    pass


def _transcribe_audio_file_with_openai_compatible(path: str) -> str:
    """调用 OpenAI/Azure 兼容转写接口，把录音文件转成文本。"""
    client = _create_voice_input_client()
    kwargs = {"model": _get_voice_input_stt_model()}
    language = _get_voice_input_language()
    if language:
        kwargs["language"] = language
    with open(path, "rb") as audio_file:
        response = client.audio.transcriptions.create(file=audio_file, **kwargs)
    return _transcription_text_from_response(response)


def _transcribe_audio_file(path: str) -> str:
    """根据配置选择在线 STT 或本地 whisper.cpp，把录音文件转成文本。"""
    if _voice_input_uses_local_whispercpp():
        return _transcribe_audio_file_with_whispercpp(path)
    return _transcribe_audio_file_with_openai_compatible(path)


def capture_voice_input(console) -> str | None:
    """执行 /bbb 语音输入流程。

    返回识别文本；取消或空识别返回 None。
    """
    if not _voice_input_uses_local_whispercpp() and not _get_voice_input_api_key():
        console.print("[bold red]无法使用 /bbb：未配置 OPENAI_API_KEY 或 VOICE_INPUT_API_KEY。[/bold red]")
        return None

    audio_path = os.path.join(TOOL_LOG_CACHE_DIR, f"voice-input-{uuid.uuid4().hex[:8]}.wav")
    try:
        should_transcribe = _record_audio_until_keypress(console, audio_path)
        if not should_transcribe:
            return None
        ok, reason = _validate_audio_file(audio_path)
        if not ok:
            console.print(f"[bold yellow]{reason}，已返回聊天框。[/bold yellow]")
            return None
        console.print("[bold cyan]📝 正在将语音转为文字，请稍候...[/bold cyan]")
        try:
            text = _transcribe_audio_file(audio_path)
        except Exception as exc:
            console.print(f"[bold red]{_format_voice_transcription_error(exc)}[/bold red]")
            return None
        if not text:
            console.print("[bold yellow]没有识别到文字，已返回聊天框。[/bold yellow]")
            return None
        console.print(f"[bold green]👤 You>[/bold green] {text}")
        return text
    finally:
        try:
            if os.path.exists(audio_path):
                getattr(os, "remove")(audio_path)
        except Exception:
            pass


def _run_with_esc_interrupt(run_callable, status_message: str, status_ref=None):
    """后台执行 Agent，前台在状态动画期间监听 Esc 并请求中断。"""
    global ACTIVE_STATUS
    cancel_event = threading.Event()
    finished = threading.Event()
    result = {"response": None, "error": None}

    def worker():
        try:
            result["response"] = run_callable(cancel_event)
        except BaseException as exc:  # noqa: BLE001 - 需要把后台异常传回主线程
            result["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    interrupted = False
    stdin_fd = None
    old_tty_attrs = None

    def request_interrupt():
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            cancel_event.set()
            console.print("\n[bold yellow]esc 中断[/bold yellow]")

    try:
        if sys.stdin.isatty():
            stdin_fd = sys.stdin.fileno()
            old_tty_attrs = termios.tcgetattr(stdin_fd)
            tty.setcbreak(stdin_fd)
    except Exception:
        stdin_fd = None
        old_tty_attrs = None

    try:
        with console.status(
            _with_interrupt_status_hint(status_message),
            spinner="dots",
        ) as status:
            ACTIVE_STATUS = status
            if status_ref is not None:
                status_ref["status"] = status
            thread.start()
            try:
                while not finished.wait(0.1):
                    if stdin_fd is None:
                        continue
                    readable, _, _ = select.select([sys.stdin], [], [], 0)
                    if readable and sys.stdin.read(1) == "\x1b":
                        request_interrupt()
            except KeyboardInterrupt:
                request_interrupt()
                while not finished.wait(0.1):
                    pass
    finally:
        if old_tty_attrs is not None and stdin_fd is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty_attrs)
            except Exception:
                pass
        ACTIVE_STATUS = None
        if status_ref is not None:
            status_ref["status"] = None

    thread.join(timeout=0)

    if result["error"] is not None:
        raise result["error"]
    return result["response"]


def update_env_var(key: str, value: str):
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    lines = []
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f'{key}="{value}"\n'
            found = True
            break
            
    if not found:
        lines.append(f'{key}="{value}"\n')
        
    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    os.environ[key] = value

def _terminal_safe_banner_line(parts):
    """Build one styled banner line without emoji width edge cases.

    Rich 的 Panel 会按 Unicode cell width 计算填充；但部分终端/字体对 emoji
    variation selector（例如 ⌨️、✨）的实际宽度和 Rich 计算不一致，长行右边框
    就可能看起来错一列。欢迎 banner 使用纯文本标签作为左侧提示符，避免
    把不稳定宽度字符放进用于确定面板宽度的内容行。
    """
    line = Text()
    for value, style in parts:
        line.append(value, style=style)
    return line


def _build_welcome_banner_text(model_name: str, key_status: str, client_type: str) -> Text:
    lines = [
        _terminal_safe_banner_line([("欢迎使用 R-Agent CLI", "bold magenta")]),
        Text(""),
        _terminal_safe_banner_line([
            ("模型: ", "info"),
            (model_name, "bold cyan"),
            (f" (API Key {key_status}, Client: {client_type.upper()})", "info"),
        ]),
        _terminal_safe_banner_line([
            ("命令: 输入 ", "info"),
            ("/", "bold yellow"),
            (" 触发自动补全菜单（如 ", "info"),
            ("/skill", "bold green"),
            ("、", "info"),
            ("/tool", "bold green"),
            (" 等）。也可输入 ", "info"),
            ("/bbb", "bold green"),
            (" 使用语音输入。", "info"),
        ]),
        _terminal_safe_banner_line([
            ("退出: 输入 ", "info"),
            ("'exit'", "bold green"),
            (" 或 ", "info"),
            ("'quit'", "bold green"),
            (" 退出。", "info"),
        ]),
    ]

    banner_text = Text()
    for index, line in enumerate(lines):
        banner_text.append_text(line)
        if index != len(lines) - 1:
            banner_text.append("\n")
    return banner_text


def display_welcome_banner():
    model_name = config.get_model()
    api_key = config.get_api_key()
    key_status = "已配置" if api_key else "未配置"
    client_type = config.get_client_type()

    banner_text = _build_welcome_banner_text(model_name, key_status, client_type)

    panel = Panel(
        banner_text,
        title="[bold blue]R-Agent[/bold blue]",
        border_style="blue",
        expand=False,
    )
    console.print(panel)
    console.print()

def get_completions():
    """动态获取分级菜单的补全命令"""
    # 获取动态的 skills 列表
    skills_dict = {"list": None}
    try:
        # 使用 os.walk 深度遍历查找所有 SKILL.md
        for root, dirs, files in os.walk(skill_manager.skills_dir):
            if "SKILL.md" in files:
                skill_name = os.path.basename(root)
                skills_dict[skill_name] = None
    except Exception:
        pass
        
    # 获取动态的 tools 列表
    tools_dict = {"list": None}
    schemas = registry.get_all_schemas()
    for schema in schemas:
        tools_dict[schema.get("function", {}).get("name")] = None
        
    # 构造 NestedCompleter 的字典
    completer_dict = {
        "/help": None,
        "/bbb": None,
        "/project_list": None,
        "/skill": skills_dict,
        "/tool": tools_dict,
        "/mem": {
            "list": None,
            "USER": None,
            "MEMORY": None
        },
        "/model": None,
        "/mode": {
            "detailed": None,
            "concise": None
        },
        "/apikey": None
    }
    
    return NestedCompleter.from_nested_dict(completer_dict)

def handle_slash_command(command_str: str, console) -> bool:
    """处理本地斜杠命令，返回 True 表示已拦截处理，False 表示继续传递给 Agent"""
    parts = command_str.strip().split()
    if not parts:
        return True
        
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None
    
    if cmd == "/help":
        help_text = (
            "**本地可用命令:**\n"
            "- `/help`: 显示此帮助信息\n"
            "- `/bbb`: 开始语音输入；按 Enter 停止并识别，按 Esc 取消\n"
            "- `/project_list`: 列出项目进度，并手动选择载入当前上下文\n"
            "- `/skill [list|name]`: 列出或查看技能\n"
            "- `/tool [list|name]`: 列出或查看基础工具\n"
            "- `/mem [list|USER|MEMORY]`: 查看环境记忆与用户偏好\n"
            "- `/model [新模型名]`: 切换当前大语言模型\n"
            "- `/mode [detailed|concise]`: 切换输出显示模式\n"
            "- `/apikey [新密钥]`: 修改当前 API Key\n"
        )
        console.print(Panel(Markdown(help_text), title="[bold cyan]帮助[/bold cyan]", border_style="cyan", expand=False))
        return True

    if cmd == "/skill":
        if not arg or arg == "list":
            skills_text = skill_manager.list_skills()
            if not arg:
                skills_text += "\n\n> 💡 **提示**: 输入 `/skill <空格>` 可以选择并查看特定的 skill 详情。"
            console.print(Panel(Markdown(skills_text), title="📚 可用 Skills", border_style="cyan"))
        else:
            # 在新版中，需要查找技能的具体路径
            import glob
            search_pattern = os.path.join(skill_manager.skills_dir, "**", arg, "SKILL.md")
            matches = glob.glob(search_pattern, recursive=True)
            if matches:
                with open(matches[0], "r", encoding="utf-8") as f:
                    console.print(Panel(Markdown(f.read()), title=f"📚 Skill: {arg}"))
            else:
                console.print(f"[bold red]Skill '{arg}' 不存在[/bold red]")
        return True
        
    if cmd == "/tool":
        if not arg or arg == "list":
            schemas = registry.get_all_schemas()
            if not schemas:
                tools_text = "当前没有任何已注册的工具。"
            else:
                tools_text = "**可用的基础工具:**\n\n"
                for schema in schemas:
                    func = schema.get("function", {})
                    name = func.get("name", "Unknown")
                    desc = func.get("description", "无描述")
                    tools_text += f"- **`{name}`**: {desc}\n"
            
            if not arg:
                tools_text += "\n\n> 💡 **提示**: 输入 `/tool <空格>` 可以选择并查看特定工具的 JSON Schema。"
            console.print(Panel(Markdown(tools_text), title="🛠️ 可用 Tools", border_style="cyan"))
        else:
            schemas = registry.get_all_schemas()
            found = False
            for schema in schemas:
                if schema.get("function", {}).get("name") == arg:
                    console.print(Panel(json.dumps(schema, indent=2, ensure_ascii=False), title=f"🛠️ Tool: {arg}"))
                    found = True
                    break
            if not found:
                console.print(f"[bold red]Tool '{arg}' 不存在[/bold red]")
        return True
        
    if cmd == "/mem":
        if not arg or arg == "list":
            text = "- **USER**: 保存用户的个人偏好与身份信息\n- **MEMORY**: 保存项目或环境的客观事实"
            if not arg:
                text += "\n\n> 💡 **提示**: 输入 `/mem <空格>` 然后选择 `USER` 或 `MEMORY` 查看具体记忆内容。"
            console.print(Panel(Markdown(text), title="🧠 记忆区"))
        elif arg.upper() == "USER":
            console.print(Panel(memory_manager.read_target("user"), title="🧠 USER Memory"))
        elif arg.upper() == "MEMORY":
            console.print(Panel(memory_manager.read_target("memory"), title="🧠 MEMORY Memory"))
        else:
            console.print("[bold red]只支持 /mem USER 或 /mem MEMORY[/bold red]")
        return True
        
    if cmd == "/model":
        if not arg:
            console.print(f"当前模型: [bold cyan]{config.get_model()}[/bold cyan]")
            console.print("使用 '/model <新模型名>' 来切换")
        else:
            update_env_var("LLM_MODEL", arg)
            console.print(f"✅ 模型已切换为: [bold cyan]{arg}[/bold cyan] (已更新 .env)")
        return True
        
    if cmd == "/mode":
        if not arg:
            console.print(f"当前显示模式: [bold cyan]{config.get_display_mode()}[/bold cyan]")
            console.print("使用 '/mode detailed' 或 '/mode concise' 来切换")
        else:
            new_mode = arg.lower()
            if new_mode in ["detailed", "concise"]:
                update_env_var("DISPLAY_MODE", new_mode)
                console.print(f"✅ 显示模式已切换为: [bold cyan]{new_mode}[/bold cyan] (已更新 .env)")
            else:
                console.print("[bold red]无效的模式，请使用 'detailed' 或 'concise'[/bold red]")
        return True
        
    if cmd == "/apikey":
        if not arg:
            console.print("使用 '/apikey <新密钥>' 来更新 API Key")
        else:
            update_env_var("OPENAI_API_KEY", arg)
            console.print("✅ API Key 已更新 (已保存至 .env)")
        return True
        
    console.print(f"[bold red]未知的命令: {cmd}[/bold red]")
    return True

def _shutdown_agent(agent: RAgent, console, timeout: float = 1.0) -> None:
    """退出 CLI 前收敛 Agent 后台任务，避免 exit 时与后台复盘竞争资源。"""
    try:
        alive = agent.shutdown_background_tasks(timeout=timeout)
        if alive:
            console.print(f"[dim yellow]仍有 {alive} 个后台任务未及时结束，已请求停止并继续退出。[/dim yellow]")
    except Exception:
        pass


def main():
    maybe_cleanup_sandbox()
    display_welcome_banner()
    
    cli_session_id = f"cli-{uuid.uuid4().hex[:12]}"
    os.environ["R_AGENT_SESSION_ID"] = cli_session_id
    console.print(f"[dim]Todo session: {cli_session_id}[/dim]")
    agent = RAgent(session_id=cli_session_id)
    system_prompt = (
        build_system_prompt()
        + "\n\n【重要提示：自我进化能力】\n"
        + "1. 更新技能(Skills)：你可以使用 `skill_manage` 工具维护技能包；默认优先 patch 现有技能。只有当用户明确要求或发现高度可复用且现有技能无法承载的稳定工作流时，才创建新技能，避免每轮任务都新增 skill。\n"
        + "2. 更新工具(Tools)：你可以使用 `write_file` 工具直接在 `tools/` 目录下编写新的 Python 工具模块并调用 `registry.register`。在下一轮对话时，系统会自动热重载并为你注册新工具。\n"
        + "请始终使用中文回复用户。"
        + memory_manager.load_snapshot()
    )
    
    # 初始化 prompt_toolkit session
    session = PromptSession()
    
    while True:
        try:
            # 动态生成补全列表
            completer = get_completions()
            
            # 获取用户输入
            user_input = session.prompt(
                HTML('<ansigreen><b>👤 You&gt;</b></ansigreen> '),
                completer=completer,
                complete_while_typing=True,
                rprompt=_format_token_usage_rprompt(agent),
            )
            
            if user_input.strip().lower() in ["exit", "quit"]:
                _shutdown_agent(agent, console)
                console.print("\n[bold yellow]👋 再见！[/bold yellow]")
                break
            if not user_input.strip():
                continue

            # /bbb 是本地语音输入命令：识别成功后把文本当作正常用户输入继续进入 Agent。
            if _is_voice_input_command(user_input):
                voice_text = capture_voice_input(console)
                if not voice_text:
                    continue
                user_input = voice_text

            elif user_input.strip().lower() == "/project_list":
                load_project_progress_context(console, session, agent, system_prompt)
                continue

            # 拦截处理其它斜杠命令
            elif user_input.strip().startswith("/"):
                handle_slash_command(user_input, console)
                continue
                
            console.print()
            
            # 每次对话前，由于模型或API Key可能被切换，需要更新 agent 实例的配置
            agent.model = config.get_model()
            agent.client = config.create_llm_client()
            
            # 状态回调函数
            status_ref = {"status": None}

            def update_status(message: str):
                current_status = status_ref.get("status")
                if current_status is not None:
                    current_status.update(_with_interrupt_status_hint(message))

            def on_think(iteration, **kwargs):
                if "retry_attempt" in kwargs:
                    update_status(
                        f"[bold yellow]🤖 模型请求瞬时失败，正在重试 "
                        f"({kwargs['retry_attempt']}/{kwargs['retry_max']})，"
                        f"约 {kwargs['retry_delay']:.1f}s 后继续...[/bold yellow]"
                    )
                else:
                    update_status(f"[bold cyan]🤖 Agent 正在思考 (第 {iteration+1} 轮)...[/bold cyan]")
                
            def on_tool_start(func_name, func_args):
                update_status(f"[bold cyan]🤖 Agent 正在执行: {func_name}...[/bold cyan]")
                if config.get_display_mode() == "concise":
                    return
                console.log(f"[bold yellow]🛠️  调用工具:[/bold yellow] [bold magenta]{func_name}[/bold magenta]\n[dim]参数: {func_args}[/dim]")
                
            def on_tool_end(func_name, result):
                if config.get_display_mode() == "concise":
                    return
                # 输出过长时，前 N 字符正常展示，省略部分写入日志缓存并提供可点击的展开链接
                str_res = str(result)
                if len(str_res) > TOOL_OUTPUT_TRUNCATE_LIMIT:
                    log_path = _dump_tool_output(func_name, str_res)
                    head = str_res[:TOOL_OUTPUT_TRUNCATE_LIMIT]
                    omitted = len(str_res) - TOOL_OUTPUT_TRUNCATE_LIMIT
                    console.log(
                        f"[bold green]✅ 工具返回:[/bold green] [dim]{head}...[/dim]"
                        f" [yellow](已省略 {omitted} 字符，"
                        f"[link=file://{log_path}]展开[/link]"
                        f"，关闭 Agent 后自动清理)[/yellow]"
                    )
                    return
                console.log(f"[bold green]✅ 工具返回:[/bold green] [dim]{str_res}[/dim]")
                
            # 后台运行 Agent，前台监听 Esc 中断
            try:
                response = _run_with_esc_interrupt(
                    lambda cancel_event: agent.run_conversation(
                        user_input,
                        system_message=system_prompt,
                        on_think=on_think,
                        on_tool_start=on_tool_start,
                        on_tool_end=on_tool_end,
                        cancel_event=cancel_event,
                    ),
                    "[bold cyan]🤖 Agent 正在思考...[/bold cyan]",
                    status_ref=status_ref,
                )
            except AgentInterrupted:
                console.print("[yellow]已中断，本轮 assistant/tool 中间上下文已回退。[/yellow]")
                console.print()
                continue

            # 打印回复
            console.print(Panel(
                Markdown(response),
                title="[bold blue]🤖 R-Agent[/bold blue]",
                subtitle=_token_usage_panel_subtitle(agent),
                subtitle_align="right",
                border_style="blue",
                expand=False,
            ))
            console.print()

            # 若 Agent 因迭代上限被强制收尾，主动询问用户是否扩展预算续跑
            while agent.is_truncated():
                console.print(
                    "[bold yellow]⚠️  Agent 已达迭代上限并完成强制收尾。"
                    "上下文完整保留，可输入额外轮数继续推进，"
                    "或回车跳过（保留当前结果）。[/bold yellow]"
                )
                extra_raw = session.prompt(
                    HTML('<ansiyellow><b>➕ 扩展轮数&gt;</b></ansiyellow> '),
                    rprompt=_format_token_usage_rprompt(agent),
                ).strip()
                if not extra_raw:
                    break
                try:
                    extra = int(extra_raw)
                except ValueError:
                    console.print("[bold red]请输入正整数，或回车跳过[/bold red]")
                    continue
                if extra <= 0:
                    break

                try:
                    response = _run_with_esc_interrupt(
                        lambda cancel_event: agent.continue_after_truncation(
                            extra,
                            on_think=on_think,
                            on_tool_start=on_tool_start,
                            on_tool_end=on_tool_end,
                            cancel_event=cancel_event,
                        ),
                        f"[bold cyan]🤖 Agent 续跑中（+{extra} 轮）...[/bold cyan]",
                        status_ref=status_ref,
                    )
                except AgentInterrupted:
                    console.print("[yellow]已中断，本次续跑中间上下文已回退。[/yellow]")
                    console.print()
                    break
                console.print(Panel(
                    Markdown(response),
                    title="[bold blue]🤖 R-Agent (续跑)[/bold blue]",
                    subtitle=_token_usage_panel_subtitle(agent),
                    subtitle_align="right",
                    border_style="blue",
                    expand=False,
                ))
                console.print()
            
        except (KeyboardInterrupt, EOFError):
            _shutdown_agent(agent, console)
            console.print("\n[bold yellow]👋 再见！[/bold yellow]")
            break
        except Exception as e:
            console.print(f"\n[bold red]❌ 发生错误: {e}[/bold red]")

if __name__ == "__main__":
    main()
