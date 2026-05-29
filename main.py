import sys
import os
import json

# 确保 R-Agent 目录在模块搜索路径中
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.agent import RAgent
from core import config
from tools import builtin  # 导入此模块以触发工具注册
from tools.registry import registry
from core.skills import skill_manager
from core.memory import memory_manager

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
            with open(memory_manager.user_file, "r", encoding="utf-8") as f:
                console.print(Panel(f.read(), title="🧠 USER Memory"))
        elif arg.upper() == "MEMORY":
            with open(memory_manager.memory_file, "r", encoding="utf-8") as f:
                console.print(Panel(f.read(), title="🧠 MEMORY Memory"))
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
        "你是一个强大的 AI Agent，你可以使用提供的工具来完成任务。\n"
        "【重要提示：自我进化能力】\n"
        "1. 更新技能(Skills)：你可以使用 `skill_create` 工具随时创建新的技能，或者为现有技能添加新分类。如果你发现现有技能不足以完成任务，请自主提炼总结并创建为新技能。\n"
        "2. 更新工具(Tools)：你可以使用 `write_file` 工具直接在 `tools/` 目录下编写新的 Python 工具模块并调用 `registry.register`。在下一轮对话时，系统会自动热重载并为你注册新工具。\n"
        "请始终使用中文回复用户。"
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
            def on_think(iteration):
                status.update(f"[bold cyan]🤖 Agent 正在思考 (第 {iteration+1} 轮)...[/bold cyan]")
                
            def on_tool_start(func_name, func_args):
                if config.get_display_mode() == "concise":
                    status.update(f"[bold cyan]🤖 Agent 正在执行: {func_name}...[/bold cyan]")
                    return
                console.log(f"[bold yellow]🛠️  调用工具:[/bold yellow] [bold magenta]{func_name}[/bold magenta]\n[dim]参数: {func_args}[/dim]")
                
            def on_tool_end(func_name, result):
                if config.get_display_mode() == "concise":
                    return
                # 如果结果太长，截断显示
                str_res = str(result)
                if len(str_res) > 200:
                    str_res = str_res[:200] + " ... (已截断)"
                console.log(f"[bold green]✅ 工具返回:[/bold green] [dim]{str_res}[/dim]")
                
            # 显示状态动画
            with console.status("[bold cyan]🤖 Agent 正在思考...[/bold cyan]", spinner="dots") as status:
                response = agent.run_conversation(
                    user_input, 
                    system_message=system_prompt,
                    on_think=on_think,
                    on_tool_start=on_tool_start,
                    on_tool_end=on_tool_end
                )
            
            # 打印回复
            console.print(Panel(
                Markdown(response), 
                title="[bold blue]🤖 R-Agent[/bold blue]", 
                border_style="blue",
                expand=False
            ))
            console.print()
            
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold yellow]👋 再见！[/bold yellow]")
            break
        except Exception as e:
            console.print(f"\n[bold red]❌ 发生错误: {e}[/bold red]")

if __name__ == "__main__":
    main()
