import sys
import os
import re

# 确保 R-Agent 目录在模块搜索路径中
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.agent import RAgent
from core.config import get_api_key, set_api_key, get_model, set_model, get_display_mode, set_display_mode, CONFIG_FILE, get_client_type, create_llm_client
import tools  # 这将触发 tools/__init__.py 动态加载所有工具
from tools.registry import registry
from core.memory import memory_manager
from core.skills import skill_manager

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

def display_welcome_banner():
    banner_text = Text()
    banner_text.append("✨ 欢迎使用 R-Agent CLI ✨\n", style="bold magenta")
    banner_text.append("\n", style="default")
    banner_text.append("💡 提示: 默认模型为 ", style="info")
    banner_text.append(f"{get_model()}", style="bold cyan")
    banner_text.append(f" ({get_client_type().upper()})。当前显示模式: ", style="info")
    banner_text.append(f"{get_display_mode()}", style="bold cyan")
    banner_text.append("。\n", style="info")
    # banner_text.append("⌨️  命令: 输入 ", style="info")
    # banner_text.append("/", style="bold yellow")
    # banner_text.append(" 可查看一级菜单；在命令后输入", style="info")
    # banner_text.append("空格", style="bold yellow")
    # banner_text.append("（如 ", style="info")
    # banner_text.append("/skill ", style="bold green")
    # banner_text.append("）可触发", style="info")
    # banner_text.append("二级选项菜单", style="bold yellow")
    # banner_text.append("。\n", style="info")
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
        skills = os.listdir(skill_manager.skills_dir)
        for s in skills:
            if os.path.isdir(os.path.join(skill_manager.skills_dir, s)):
                skills_dict[s] = None
    except Exception:
        pass
        
    # 获取动态的 tools 列表
    tools_dict = {"list": None}
    for tool_name in registry._tools.keys():
        tools_dict[tool_name] = None
        
    # 构造 NestedCompleter 的字典
    completer_dict = {
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

def handle_slash_command(command_str):
    """处理以 / 开头的命令"""
    parts = command_str.strip().split()
    if not parts:
        return True
        
    cmd = parts[0]
    arg = parts[1] if len(parts) > 1 else None
    
    if cmd == "/skill":
        if not arg or arg == "list":
            text = skill_manager.list_skills()
            if not arg:
                text += "\n\n> 💡 **提示**: 输入 `/skill <空格>` 可以选择并查看特定的 skill 详情。"
            console.print(Panel(Markdown(text), title="📚 可用 Skills"))
        else:
            result = skill_manager.view_skill(arg)
            console.print(Panel(Markdown(result), title=f"📚 Skill: {arg}"))
        return True
        
    if cmd == "/tool":
        if not arg or arg == "list":
            tools = registry.get_all_schemas()
            
            # 对工具进行分类展示
            categories = {
                "📂 文件操作": ["read_file", "write_file", "search_files", "delete_file"],
                "🌐 网络操作": ["web_search", "web_extract"],
                "🧠 记忆管理": ["memory"],
                "📚 技能管理": ["skills_list", "skill_view", "skill_create", "skill_delete"],
                "💻 系统执行": ["run_command", "run_python"]
            }
            
            categorized_text = []
            for category_name, tool_names in categories.items():
                category_tools = [t for t in tools if t['function']['name'] in tool_names]
                if category_tools:
                    categorized_text.append(f"### {category_name}")
                    for t in category_tools:
                        categorized_text.append(f"- **{t['function']['name']}**: {t['function']['description']}")
            
            # 处理未预定义的其他工具
            all_known = [name for names in categories.values() for name in names]
            other_tools = [t for t in tools if t['function']['name'] not in all_known]
            if other_tools:
                categorized_text.append("### ⚙️ 其他扩展工具")
                for t in other_tools:
                    categorized_text.append(f"- **{t['function']['name']}**: {t['function']['description']}")
                    
            text = "\n".join(categorized_text)
            
            if not arg:
                text += "\n\n> 💡 **提示**: 输入 `/tool <空格>` 可以选择并查看特定工具的 JSON Schema。"
            console.print(Panel(Markdown(text), title="🛠️ 可用 Tools"))
        else:
            if arg in registry._tools:
                schema = registry._tools[arg]["schema"]
                import json
                console.print(Panel(json.dumps(schema, indent=2, ensure_ascii=False), title=f"🛠️ Tool: {arg}"))
            else:
                console.print(f"[bold red]Tool '{arg}' 不存在[/bold red]")
        return True
        
    if cmd == "/mem":
        if not arg or arg == "list":
            text = "- **USER**: 保存用户的个人偏好与身份信息\n- **MEMORY**: 保存项目或环境的客观事实"
            if not arg:
                text += "\n\n> 💡 **提示**: 输入 `/mem <空格>` 然后选择 `USER` 或 `MEMORY` 查看具体记忆内容。"
            console.print(Panel(Markdown(text), title="🧠 记忆区"))
        elif arg.upper() == "USER":
            with open(memory_manager.user_file, "r") as f:
                console.print(Panel(f.read(), title="🧠 USER Memory"))
        elif arg.upper() == "MEMORY":
            with open(memory_manager.memory_file, "r") as f:
                console.print(Panel(f.read(), title="🧠 MEMORY Memory"))
        else:
            console.print("[bold red]只支持 /mem USER 或 /mem MEMORY[/bold red]")
        return True
        
    if cmd == "/model":
        if not arg:
            console.print(f"当前模型: [bold cyan]{get_model()}[/bold cyan]")
            console.print("使用 '/model <新模型名>' 来切换")
        else:
            set_model(arg)
            console.print(f"✅ 模型已切换为: [bold cyan]{arg}[/bold cyan]")
        return True
        
    if cmd == "/mode":
        if not arg:
            console.print(f"当前显示模式: [bold cyan]{get_display_mode()}[/bold cyan]")
            console.print("使用 '/mode detailed' 或 '/mode concise' 来切换")
        else:
            new_mode = arg.lower()
            if new_mode in ["detailed", "concise"]:
                set_display_mode(new_mode)
                console.print(f"✅ 显示模式已切换为: [bold cyan]{new_mode}[/bold cyan]")
            else:
                console.print("[bold red]无效的模式，请使用 'detailed' 或 'concise'[/bold red]")
        return True
        
    if cmd == "/apikey":
        console.print(f"当前 API Key 保存路径: [bold green]{os.path.abspath(CONFIG_FILE)}[/bold green]")
        new_key = console.input("请输入新的 API Key (直接回车保持不变): ")
        if new_key.strip():
            set_api_key(new_key.strip())
            console.print("✅ API Key 已更新")
        return True
        
    console.print(f"[bold red]未知的命令: {cmd}[/bold red]")
    return True

def ensure_api_key():
    """检查 API Key，如果没有则提示用户输入"""
    api_key = get_api_key()
    if not api_key:
        console.print("[bold yellow]⚠️ 未检测到 API Key，请先配置[/bold yellow]")
        client_type = get_client_type()
        api_key = console.input(f"请输入 {client_type.upper()} API Key: ")
        set_api_key(api_key)
        console.print(f"✅ API Key 已保存至: [bold green]{os.path.abspath(CONFIG_FILE)}[/bold green]\n")

def main():
    display_welcome_banner()
    ensure_api_key()
    
    agent = RAgent()
    system_prompt = (
        "你是一个强大的 AI Agent，负责全局调度。\n"
        "【重要】面对特别复杂、包含多个步骤或依赖关系的任务时，请务必启用“父子协同”模式：\n"
        "1. 首先，使用 `todo_manage` 工具(action='init')创建包含拓扑依赖的 Todo List 计划。\n"
        "2. 然后，使用 `todo_manage` (action='view') 查看当前 ready_to_execute (可执行) 的任务。\n"
        "3. 【父进程的职责】：你绝对不要亲自执行具体的文件读写或代码编写！你只关注全局任务。请将 ready 的任务通过 `delegate_task` 工具委托给子智能体执行。\n"
        "4. 【跟进与调整】：子任务返回结果后，父进程必须根据执行结果使用 `todo_manage` (action='update') 标记其状态为 completed 或 failed，并记录 result 摘要。若子任务失败，父进程负责分析原因，并决定是重试该任务还是修改 Todo List。\n"
        "5. 不断循环执行“查看 ready 任务 -> 委托执行 -> 更新状态”，直到所有任务完成。\n"
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
                handle_slash_command(user_input)
                continue
                
            console.print()
            
            # 每次对话前，由于模型可能被切换，需要更新 agent 实例的模型
            agent.model = get_model()
            # 同样需要更新 agent 的 client (如果 API Key 或配置被更新了)
            agent.client = create_llm_client()
            
            # 状态回调函数
            def on_think(iteration):
                status.update(f"[bold cyan]🤖 Agent 正在思考 (第 {iteration+1} 轮)...[/bold cyan]")
                
            def on_tool_start(func_name, func_args):
                if get_display_mode() == "concise":
                    status.update(f"[bold cyan]🤖 Agent 正在执行: {func_name}...[/bold cyan]")
                    return
                console.log(f"[bold yellow]🛠️  调用工具:[/bold yellow] [bold magenta]{func_name}[/bold magenta]\n[dim]参数: {func_args}[/dim]")
                
            def on_tool_end(func_name, result):
                if get_display_mode() == "concise":
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
            
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold yellow]👋 再见！[/bold yellow]")
            break
        except Exception as e:
            console.print(f"\n[bold red]发生错误: {e}[/bold red]")

if __name__ == "__main__":
    main()
