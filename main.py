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


# 确保 R-Agent 目录在模块搜索路径中
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.agent import AgentInterrupted, RAgent
from core import config
from tools.registry import registry
from core.skills import skill_manager
from core.memory import memory_manager
from core.prompt_builder import build_system_prompt

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


def _run_with_esc_interrupt(run_callable, status_message: str, status_ref=None):
    """后台执行 Agent，前台在状态动画期间监听 Esc 并请求中断。"""
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

def display_welcome_banner():
    model_name = config.get_model()
    api_key = config.get_api_key()
    key_status = "已配置" if api_key else "未配置"
    client_type = config.get_client_type()

    banner_text = Text()
    banner_text.append("✨ 欢迎使用 R-Agent CLI ✨\n", style="bold magenta")
    banner_text.append("\n", style="default")
    banner_text.append("💡 当前模型: ", style="info")
    banner_text.append(f"{model_name}", style="bold cyan")
    banner_text.append(f" (API Key {key_status}, Client: {client_type.upper()})\n", style="info")
    
    banner_text.append("⌨️  命令: 输入 ", style="info")
    banner_text.append("/", style="bold yellow")
    banner_text.append(" 触发自动补全菜单（如 ", style="info")
    banner_text.append("/skill", style="bold green")
    banner_text.append("、", style="info")
    banner_text.append("/tool", style="bold green")
    banner_text.append(" 等）。\n", style="info")

    banner_text.append("🚪 退出: 输入 ", style="info")
    banner_text.append("'exit'", style="bold green")
    banner_text.append(" 或 ", style="info")
    banner_text.append("'quit'", style="bold green")
    banner_text.append(" 退出。\n", style="info")
    
    panel = Panel(
        banner_text,
        title="[bold blue]R-Agent[/bold blue]",
        border_style="blue",
        expand=False
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

def main():
    display_welcome_banner()
    
    agent = RAgent()
    system_prompt = (
        build_system_prompt()
        + "\n\n【重要提示：自我进化能力】\n"
        + "1. 更新技能(Skills)：你可以使用 `skill_create` 工具随时创建新的技能，或者为现有技能添加新分类。如果你发现现有技能不足以完成任务，请自主提炼总结并创建为新技能。\n"
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
                complete_while_typing=True
            )
            
            if user_input.lower() in ["exit", "quit"]:
                console.print("\n[bold yellow]👋 再见！[/bold yellow]")
                break
            if not user_input.strip():
                continue
                
            # 拦截处理斜杠命令
            if user_input.strip().startswith("/"):
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
                border_style="blue",
                expand=False
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
                    HTML('<ansiyellow><b>➕ 扩展轮数&gt;</b></ansiyellow> ')
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
                    border_style="blue",
                    expand=False,
                ))
                console.print()
            
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold yellow]👋 再见！[/bold yellow]")
            break
        except Exception as e:
            console.print(f"\n[bold red]❌ 发生错误: {e}[/bold red]")

if __name__ == "__main__":
    main()
