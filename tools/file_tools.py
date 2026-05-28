import os
import json
import glob
import difflib
import threading
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from tools.registry import registry

console = Console()

# 定义工作区和沙盒
WORKSPACE_DIR = os.getcwd()
SANDBOX_DIR = os.path.join(WORKSPACE_DIR, "sandbox")
os.makedirs(SANDBOX_DIR, exist_ok=True)

# 文件读取追踪，用于防死循环和去重
_read_tracker_lock = threading.Lock()
_read_tracker = {
    "last_key": None,
    "consecutive": 0,
    "dedup": {},          # (path, offset, limit) -> mtime
    "dedup_hits": {}      # (path, offset, limit) -> hit_count
}

def is_in_sandbox(path: str) -> bool:
    abs_path = os.path.abspath(path)
    abs_sandbox = os.path.abspath(SANDBOX_DIR)
    return abs_path.startswith(abs_sandbox)

def is_in_workspace(path: str) -> bool:
    abs_path = os.path.abspath(path)
    abs_workspace = os.path.abspath(WORKSPACE_DIR)
    return abs_path.startswith(abs_workspace)

def check_outside_workspace_auth(path: str, action: str) -> bool:
    """如果文件在工作区外，要求用户授权"""
    if not is_in_workspace(path):
        console.print(f"\n[bold red]⚠️ 警告: Agent 尝试在工作区外执行 [{action}] 操作: {path}[/bold red]")
        user_input = console.input("是否允许？(y/N): ")
        if user_input.strip().lower() != 'y':
            console.print("[yellow]已拒绝操作。[/yellow]")
            return False
    return True

def read_file_tool(path: str, offset: int = 1, limit: int = 500) -> str:
    """Read a file with pagination and line numbers."""
    if not check_outside_workspace_auth(path, "读取"):
        return json.dumps({"error": "User denied file read outside workspace"}, ensure_ascii=False)
        
    if not os.path.exists(path):
        return json.dumps({"error": f"File not found: {path}"}, ensure_ascii=False)
    try:
        abs_path = os.path.abspath(path)
        read_key = (abs_path, offset, limit)
        
        # 1. 去重检查 (Deduplication Check)
        with _read_tracker_lock:
            cached_mtime = _read_tracker["dedup"].get(read_key)
            if cached_mtime is not None:
                current_mtime = os.path.getmtime(abs_path)
                if current_mtime == cached_mtime:
                    hits = _read_tracker["dedup_hits"].get(read_key, 0) + 1
                    _read_tracker["dedup_hits"][read_key] = hits
                    if hits >= 2:
                        return json.dumps({
                            "error": f"BLOCKED: 您已经连续 {hits+1} 次读取该文件相同区域且文件未改变。请停止重复读取，并利用已有信息继续完成任务。"
                        }, ensure_ascii=False)
                    return json.dumps({
                        "status": "unchanged",
                        "message": "文件自上次读取后未发生改变。之前的 read_file 结果仍然是最新的，请直接参考。",
                        "path": path,
                        "dedup": True
                    }, ensure_ascii=False)
                    
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        start = max(0, offset - 1)
        end = min(len(lines), start + limit)
        content = ""
        for i in range(start, end):
            content += f"{i+1}|{lines[i]}"
            
        # 2. 更新读取追踪 (Loop Detection)
        with _read_tracker_lock:
            current_mtime = os.path.getmtime(abs_path)
            _read_tracker["dedup"][read_key] = current_mtime
            _read_tracker["dedup_hits"].pop(read_key, None)
            
            if _read_tracker["last_key"] == read_key:
                _read_tracker["consecutive"] += 1
            else:
                _read_tracker["last_key"] = read_key
                _read_tracker["consecutive"] = 1
            count = _read_tracker["consecutive"]

        result_dict = {
            "path": path,
            "content": content,
            "total_lines": len(lines),
            "offset": offset,
            "limit": limit
        }
        
        if count >= 4:
            return json.dumps({
                "error": f"BLOCKED: 您已经连续读取同一文件区域 {count} 次。请停止无意义的循环读取操作。"
            }, ensure_ascii=False)
        elif count >= 3:
            result_dict["_warning"] = f"警告: 您已经连续读取同一文件区域 {count} 次。若陷入死循环，请重新规划。"
            
        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def write_file_tool(path: str, content: str) -> str:
    """Write content to a file, completely replacing existing content."""
    if not check_outside_workspace_auth(path, "写入/修改"):
        return json.dumps({"error": "User denied file write outside workspace"}, ensure_ascii=False)
        
    try:
        old_content = ""
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                old_content = f.read()
                
        # 比较并输出 diff
        if old_content != content:
            diff = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile='Old',
                tofile='New'
            ))
            diff_text = "".join(diff)
            if diff_text:
                syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
                console.print(Panel(syntax, title=f"📝 修改文件: {path}", border_style="yellow"))
        else:
            console.print(f"[dim]ℹ️ 文件 {path} 内容未改变[/dim]")

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        # 使写操作路径对应的去重缓存失效
        abs_path = os.path.abspath(path)
        with _read_tracker_lock:
            keys_to_remove = [k for k in _read_tracker["dedup"] if k[0] == abs_path]
            for k in keys_to_remove:
                _read_tracker["dedup"].pop(k, None)
                _read_tracker["dedup_hits"].pop(k, None)
                
        return json.dumps({"success": True, "path": path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def search_files_tool(pattern: str, target: str = "content", path: str = ".") -> str:
    """Search for content or files."""
    if not check_outside_workspace_auth(path, "搜索"):
        return json.dumps({"error": "User denied file search outside workspace"}, ensure_ascii=False)
        
    try:
        import re
        results = []
        if target == "files":
            for root, _, files in os.walk(path):
                for file in files:
                    if re.search(pattern, file):
                        results.append(os.path.join(root, file))
        else:
            for root, _, files in os.walk(path):
                for file in files:
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            for i, line in enumerate(f):
                                if re.search(pattern, line):
                                    results.append(f"{filepath}:{i+1}:{line.strip()}")
                    except Exception:
                        pass
        return json.dumps({"results": results[:100], "truncated": len(results) > 100}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def delete_file_tool(path: str) -> str:
    """Delete a file or directory with sandbox protection."""
    if not check_outside_workspace_auth(path, "删除"):
        return json.dumps({"error": "User denied file deletion outside workspace"}, ensure_ascii=False)
        
    try:
        if not os.path.exists(path):
            return json.dumps({"error": f"File not found: {path}"}, ensure_ascii=False)
            
        # 如果不在沙盒内，且在工作区内，依然需要针对删除操作进行授权
        if not is_in_sandbox(path):
            console.print(f"\n[bold red]⚠️ 警告: Agent 尝试删除沙盒外的工作区文件/目录: {path}[/bold red]")
            user_input = console.input("是否允许删除？(y/N): ")
            if user_input.strip().lower() != 'y':
                console.print("[yellow]已拒绝删除操作。[/yellow]")
                return json.dumps({"error": "User denied file deletion"}, ensure_ascii=False)
                
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
        else:
            os.remove(path)
            
        # 使删除操作路径对应的去重缓存失效
        abs_path = os.path.abspath(path)
        with _read_tracker_lock:
            keys_to_remove = [k for k in _read_tracker["dedup"] if k[0] == abs_path or k[0].startswith(abs_path + os.sep)]
            for k in keys_to_remove:
                _read_tracker["dedup"].pop(k, None)
                _read_tracker["dedup_hits"].pop(k, None)

        console.print(f"[bold green]✅ 已删除: {path}[/bold green]")
        return json.dumps({"success": True, "message": f"Deleted {path}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

registry.register(
    name="read_file",
    description="读取文件的内容，支持分页。返回内容带有行号。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要读取的文件路径"},
            "offset": {"type": "integer", "description": "起始行号 (默认 1)", "default": 1},
            "limit": {"type": "integer", "description": "最大读取行数 (默认 500)", "default": 500}
        },
        "required": ["path"]
    },
    handler=read_file_tool
)

registry.register(
    name="write_file",
    description="将内容写入文件，覆盖已有文件，会自动创建父目录。可在沙盒内外自由写入，修改会有 diff 提示。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件路径"},
            "content": {"type": "string", "description": "文件完整内容"}
        },
        "required": ["path", "content"]
    },
    handler=write_file_tool
)

registry.register(
    name="search_files",
    description="搜索文件内容或按名称查找文件。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式模式"},
            "target": {"type": "string", "enum": ["content", "files"], "description": "'content' 搜索文件内容, 'files' 搜索文件名"},
            "path": {"type": "string", "description": "搜索的根目录 (默认 .)", "default": "."}
        },
        "required": ["pattern"]
    },
    handler=search_files_tool
)

registry.register(
    name="delete_file",
    description="删除指定的文件或目录。注意：删除沙盒外的文件需要用户授权。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的文件或目录路径"}
        },
        "required": ["path"]
    },
    handler=delete_file_tool
)
