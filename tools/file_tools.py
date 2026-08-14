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
    try:
        return os.path.commonpath([abs_path, abs_sandbox]) == abs_sandbox
    except ValueError:
        return False


def _session_workspace(session_id: str = ""):
    """Return the active per-session workspace when the opt-in sandbox is enabled."""
    try:
        from core import config
        from core.sandbox_workspace import SandboxWorkspace

        if not config.get_session_sandbox_enabled():
            return None
        sid = str(session_id or os.environ.get("R_AGENT_SESSION_ID", "")).strip()
        if not sid or sid == "default":
            return None
        workspace = SandboxWorkspace(sid, root=config.get_session_sandbox_root())
        workspace.ensure()
        return workspace
    except Exception:
        return None


def _resolve_tool_path(path: str, session_id: str = "") -> tuple[str, object]:
    """Resolve a tool path against the session workspace when enabled.

    Relative paths become session-workspace-relative. Supported /mnt virtual
    paths use SandboxWorkspace's mapper. Absolute host paths remain absolute and
    are handled by the existing outside-workspace approval policy.
    """
    raw = str(path or "").strip()
    workspace = _session_workspace(session_id)
    if workspace is None:
        return os.path.abspath(os.path.expanduser(raw)), None

    normalized = raw.replace("\\", "/")
    if normalized.startswith("/mnt/"):
        return str(workspace.resolve_virtual(normalized)), workspace
    expanded = os.path.expanduser(raw)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded), workspace
    candidate = (workspace.workspace / expanded).resolve()
    root = workspace.workspace.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path escapes session workspace")
    return str(candidate), workspace


def is_in_workspace(path: str, session_id: str = "") -> bool:
    try:
        abs_path, workspace = _resolve_tool_path(path, session_id)
    except ValueError:
        return False
    abs_workspace = str(workspace.workspace.resolve()) if workspace is not None else os.path.abspath(WORKSPACE_DIR)
    try:
        return os.path.commonpath([abs_path, abs_workspace]) == abs_workspace
    except ValueError:
        return False


def _is_in_active_sandbox(path: str, session_id: str = "") -> bool:
    try:
        abs_path, workspace = _resolve_tool_path(path, session_id)
    except ValueError:
        return False
    if workspace is None:
        return is_in_sandbox(abs_path)
    root = str(workspace.root.resolve())
    try:
        return os.path.commonpath([abs_path, root]) == root
    except ValueError:
        return False

def _outside_workspace_permission_required(
    path: str,
    action: str,
    tool_name: str,
    allow_param: str = "allow_outside_workspace",
    session_id: str = "",
) -> str:
    """Return a non-blocking approval response for outside-workspace file operations.

    Tool calls are executed through an API/agent loop, so blocking on
    ``console.input()`` is not visible or usable for the human user.  Instead,
    the first call returns a structured ``permission_required`` payload.  The
    assistant must explain the risk to the user; only after explicit chat
    approval may it retry with ``allow_outside_workspace=true``.
    """
    abs_path, workspace = _resolve_tool_path(path, session_id)
    abs_workspace = str(workspace.workspace.resolve()) if workspace is not None else os.path.abspath(WORKSPACE_DIR)
    risk_level = "high" if action in {"写入/修改", "删除"} else "medium"

    return json.dumps({
        "permission_required": True,
        "risk_level": risk_level,
        "action": tool_name,
        "operation": action,
        "path": path,
        "absolute_path": abs_path,
        "workspace_dir": abs_workspace,
        "reason": f"目标路径不在当前工作区内，执行 [{action}] 操作需要用户显式授权",
        "message": (
            "该文件操作尚未执行。请向用户说明操作、路径和风险；"
            f"只有在用户明确同意后，才可再次调用 {tool_name}，"
            f"并传入 {allow_param}=true。工具不会再通过终端 input() 等待授权。"
        ),
        "next_call_example": {
            "path": path,
            allow_param: True,
        },
    }, ensure_ascii=False)


def check_outside_workspace_auth(
    path: str,
    action: str,
    allow_outside_workspace: bool = False,
    tool_name: str = "file_operation",
    session_id: str = "",
) -> object:
    """Check outside-workspace access without blocking for terminal input.

    Returns None when the operation may continue; otherwise returns a JSON
    string containing ``permission_required=true``.
    """
    if (
        not is_in_workspace(path, session_id)
        and not _is_in_active_sandbox(path, session_id)
        and not allow_outside_workspace
    ):
        return _outside_workspace_permission_required(path, action, tool_name, session_id=session_id)
    return None

def read_file_tool(path: str, offset: int = 1, limit: int = 500, allow_outside_workspace: bool = False, session_id: str = "") -> str:
    """Read a file with pagination and line numbers."""
    try:
        resolved_path, _workspace = _resolve_tool_path(path, session_id)
        permission_response = check_outside_workspace_auth(path, "读取", allow_outside_workspace, "read_file", session_id)
        if permission_response:
            return permission_response
        if not os.path.exists(resolved_path):
            return json.dumps({"error": f"File not found: {path}", "resolved_path": resolved_path}, ensure_ascii=False)
        abs_path = os.path.abspath(resolved_path)
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
                    
        with open(resolved_path, 'r', encoding='utf-8') as f:
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
            "resolved_path": resolved_path,
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

def write_file_tool(path: str, content: str, allow_outside_workspace: bool = False, session_id: str = "") -> str:
    """Write content to a file, completely replacing existing content."""
    try:
        resolved_path, _workspace = _resolve_tool_path(path, session_id)
        permission_response = check_outside_workspace_auth(path, "写入/修改", allow_outside_workspace, "write_file", session_id)
        if permission_response:
            return permission_response
        old_content = ""
        if os.path.exists(resolved_path):
            with open(resolved_path, 'r', encoding='utf-8') as f:
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

        os.makedirs(os.path.dirname(os.path.abspath(resolved_path)), exist_ok=True)
        with open(resolved_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        # 使写操作路径对应的去重缓存失效
        abs_path = os.path.abspath(resolved_path)
        with _read_tracker_lock:
            keys_to_remove = [k for k in _read_tracker["dedup"] if k[0] == abs_path]
            for k in keys_to_remove:
                _read_tracker["dedup"].pop(k, None)
                _read_tracker["dedup_hits"].pop(k, None)
                
        return json.dumps({"success": True, "path": path, "resolved_path": resolved_path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def search_files_tool(pattern: str, target: str = "content", path: str = ".", allow_outside_workspace: bool = False, session_id: str = "") -> str:
    """Search for content or files."""
    try:
        resolved_path, _workspace = _resolve_tool_path(path, session_id)
        permission_response = check_outside_workspace_auth(path, "搜索", allow_outside_workspace, "search_files", session_id)
        if permission_response:
            return permission_response
        import re
        results = []
        if target == "files":
            for root, _, files in os.walk(resolved_path):
                for file in files:
                    if re.search(pattern, file):
                        results.append(os.path.join(root, file))
        else:
            for root, _, files in os.walk(resolved_path):
                for file in files:
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            for i, line in enumerate(f):
                                if re.search(pattern, line):
                                    results.append(f"{filepath}:{i+1}:{line.strip()}")
                    except Exception:
                        pass
        return json.dumps({"results": results[:100], "truncated": len(results) > 100, "resolved_path": resolved_path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

def delete_file_tool(path: str, confirm: bool = False, session_id: str = "") -> str:
    """Delete a file or directory with sandbox protection.

    A 方案：删除沙盒外文件/目录时，第一次调用只返回
    permission_required，不阻塞式 input() 询问，也不返回/要求审批 token；
    只有用户明确同意后，Agent 才应再次调用并传入 confirm=true。
    """
    try:
        resolved_path, workspace = _resolve_tool_path(path, session_id)
        if not os.path.exists(resolved_path):
            return json.dumps({"error": f"File not found: {path}", "resolved_path": resolved_path}, ensure_ascii=False)

        abs_path = os.path.abspath(resolved_path)
        abs_sandbox = str(workspace.root.resolve()) if workspace is not None else os.path.abspath(SANDBOX_DIR)
        abs_workspace = str(workspace.workspace.resolve()) if workspace is not None else os.path.abspath(WORKSPACE_DIR)
        target_type = "directory" if os.path.isdir(resolved_path) else "file"

        in_workspace = is_in_workspace(path, session_id)

        # 如果不在沙盒内，必须由上层对话确认；不阻塞等待 input。
        if not _is_in_active_sandbox(path, session_id) and not confirm:
            return json.dumps({
                "permission_required": True,
                "risk_level": "high",
                "action": "delete_file",
                "path": path,
                "absolute_path": abs_path,
                "target_type": target_type,
                "reason": "删除沙盒外的工作区文件/目录" if in_workspace else "删除工作区外文件/目录",
                "workspace_dir": abs_workspace,
                "sandbox_dir": abs_sandbox,
                "message": (
                    "该删除操作尚未执行。请向用户说明将删除的路径、目标类型和风险；"
                    "工作区外路径风险更高，必须额外提醒用户核对绝对路径；"
                    "只有在用户明确同意后，才可再次调用 delete_file，并传入 confirm=true。"
                    "直接回车或未明确同意均视为拒绝。"
                ),
                "next_call_example": {
                    "path": path,
                    "confirm": True,
                },
            }, ensure_ascii=False)

        if os.path.isdir(resolved_path):
            module = __import__("shutil")
            getattr(module, "rmtree")(resolved_path)
        else:
            getattr(os, "remove")(resolved_path)

        # 使删除操作路径对应的去重缓存失效
        with _read_tracker_lock:
            keys_to_remove = [k for k in _read_tracker["dedup"] if k[0] == abs_path or k[0].startswith(abs_path + os.sep)]
            for k in keys_to_remove:
                _read_tracker["dedup"].pop(k, None)
                _read_tracker["dedup_hits"].pop(k, None)

        console.print(f"[bold green]✅ 已删除: {path}[/bold green]")
        return json.dumps({"success": True, "message": f"Deleted {path}", "resolved_path": resolved_path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

registry.register(
    name="read_file",
    description="读取文件的内容，支持分页。返回内容带有行号。读取工作区外路径时会先返回 permission_required；用户明确同意后需再次调用，并传入 allow_outside_workspace=true。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要读取的文件路径"},
            "offset": {"type": "integer", "description": "起始行号 (默认 1)", "default": 1},
            "limit": {"type": "integer", "description": "最大读取行数 (默认 500)", "default": 500},
            "allow_outside_workspace": {
                "type": "boolean",
                "description": "用户已明确同意读取工作区外路径时设为 true；默认 false",
                "default": False
            },
            "session_id": {
                "type": "string",
                "description": "当前 session ID；Agent 会自动注入。启用 session sandbox 时，相对路径从该 session 的 workspace 解析。"
            }
        },
        "required": ["path"]
    },
    handler=read_file_tool
)

registry.register(
    name="write_file",
    description="将内容写入文件，覆盖已有文件，会自动创建父目录。写入工作区外路径时会先返回 permission_required；用户明确同意后需再次调用，并传入 allow_outside_workspace=true。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件路径"},
            "content": {"type": "string", "description": "文件完整内容"},
            "allow_outside_workspace": {
                "type": "boolean",
                "description": "用户已明确同意写入工作区外路径时设为 true；默认 false",
                "default": False
            },
            "session_id": {
                "type": "string",
                "description": "当前 session ID；Agent 会自动注入。启用 session sandbox 时，相对路径写入该 session 的 workspace。"
            }
        },
        "required": ["path", "content"]
    },
    handler=write_file_tool
)

registry.register(
    name="search_files",
    description="搜索文件内容或按名称查找文件。搜索工作区外路径时会先返回 permission_required；用户明确同意后需再次调用，并传入 allow_outside_workspace=true。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式模式"},
            "target": {"type": "string", "enum": ["content", "files"], "description": "'content' 搜索文件内容, 'files' 搜索文件名"},
            "path": {"type": "string", "description": "搜索的根目录 (默认 .)", "default": "."},
            "allow_outside_workspace": {
                "type": "boolean",
                "description": "用户已明确同意搜索工作区外路径时设为 true；默认 false",
                "default": False
            },
            "session_id": {
                "type": "string",
                "description": "当前 session ID；Agent 会自动注入。启用 session sandbox 时，相对搜索根位于该 session 的 workspace。"
            }
        },
        "required": ["pattern"]
    },
    handler=search_files_tool
)

registry.register(
    name="delete_file",
    description=(
        "删除指定的文件或目录。删除沙盒外文件/目录时不会立即执行，"
        "会先返回 permission_required；用户明确同意后需再次调用，"
        "并传入 confirm=true。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要删除的文件或目录路径"},
            "confirm": {
                "type": "boolean",
                "description": "用户已明确同意执行沙盒外删除时设为 true；默认 false",
                "default": False
            },
            "session_id": {
                "type": "string",
                "description": "当前 session ID；Agent 会自动注入。启用 session sandbox 时，相对路径从该 session 的 workspace 解析。"
            }
        },
        "required": ["path"]
    },
    handler=delete_file_tool
)
