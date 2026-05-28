import subprocess
import json
import os
import sys
from rich.console import Console
from tools.registry import registry

console = Console()

WORKSPACE_DIR = os.getcwd()

def is_in_workspace(path: str) -> bool:
    abs_path = os.path.abspath(path)
    abs_workspace = os.path.abspath(WORKSPACE_DIR)
    return abs_path.startswith(abs_workspace)

def is_dangerous_command(command: str) -> bool:
    """简单检测是否是危险命令"""
    dangerous_keywords = ['rm ', 'mv ', 'sudo ', 'mkfs', 'dd ']
    cmd_lower = command.lower()
    for kw in dangerous_keywords:
        if kw in cmd_lower:
            return True
    return False

def run_command_tool(command: str, timeout: int = 30) -> str:
    """Execute a shell command and return its output."""
    try:
        cwd = os.getcwd()
        if not is_in_workspace(cwd):
            console.print(f"\n[bold red]⚠️ 警告: Agent 尝试在工作区外 ({cwd}) 执行命令: {command}[/bold red]")
            user_input = console.input("是否允许？(y/N): ")
            if user_input.strip().lower() != 'y':
                return json.dumps({"error": "User denied command execution outside workspace"}, ensure_ascii=False)
        elif is_dangerous_command(command):
            console.print(f"\n[bold red]⚠️ 警告: Agent 尝试执行敏感命令: {command}[/bold red]")
            user_input = console.input("是否允许执行？(y/N): ")
            if user_input.strip().lower() != 'y':
                return json.dumps({"error": "User denied dangerous command execution"}, ensure_ascii=False)

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        output = {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],  # Truncate to avoid huge outputs
            "stderr": result.stderr[:4000]
        }
        return json.dumps(output, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Command timed out after {timeout} seconds"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def run_python_tool(code: str, timeout: int = 30) -> str:
    """Execute python code and return its output."""
    try:
        # 简单检查 Python 代码是否包含危险操作
        if 'os.remove' in code or 'shutil.rmtree' in code:
            console.print(f"\n[bold red]⚠️ 警告: Agent 尝试执行包含文件删除的 Python 代码。[/bold red]")
            user_input = console.input("是否允许执行？(y/N): ")
            if user_input.strip().lower() != 'y':
                return json.dumps({"error": "User denied dangerous python code execution"}, ensure_ascii=False)

        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(code)
            temp_path = f.name
            
        result = subprocess.run(
            [sys.executable, temp_path],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        os.unlink(temp_path)
        
        output = {
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],
            "stderr": result.stderr[:4000]
        }
        return json.dumps(output, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)
        return json.dumps({"error": f"Python script timed out after {timeout} seconds"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def sys_reload_tool() -> str:
    """重新加载所有工具和技能列表"""
    try:
        from tools.registry import registry
        from core.skills import skill_manager
        
        # 强制热更 tools
        registry.reload_all()
        tools_count = len(registry._tools)
        
        # 获取最新 skills 列表
        skills_info = skill_manager.list_skills()
        
        return json.dumps({
            "success": True, 
            "message": f"系统重新加载完成！共加载 {tools_count} 个工具。",
            "skills": skills_info
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

registry.register(
    name="sys_reload",
    description="当手动修改了技能文件或工具代码后，使用此工具重新加载并刷新系统的工具和技能列表。",
    parameters={
        "type": "object",
        "properties": {}
    },
    handler=sys_reload_tool
)

registry.register(
    name="run_command",
    description="在终端中执行系统 Shell 命令并返回结果。注意：如果是破坏性命令，需谨慎使用。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时时间(秒)，默认 30", "default": 30}
        },
        "required": ["command"]
    },
    handler=run_command_tool
)

registry.register(
    name="run_python",
    description="执行 Python 代码片段并返回输出结果。",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 完整代码"},
            "timeout": {"type": "integer", "description": "超时时间(秒)，默认 30", "default": 30}
        },
        "required": ["code"]
    },
    handler=run_python_tool
)
