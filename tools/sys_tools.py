import subprocess
import json
import os
import sys
import re
import shlex
import time
import secrets
from rich.console import Console
from tools.registry import registry

console = Console()

WORKSPACE_DIR = os.getcwd()
APPROVAL_TTL_SECONDS = 10 * 60
_PENDING_COMMAND_APPROVALS = {}

# 重定向目标白名单：这些是 POSIX 标准的“丢弃/终端”特殊设备，
# 写入它们不会产生真实文件，也不构成数据外泄风险。
_REDIRECT_SAFE_TARGETS = {
    "/dev/null",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/tty",
    "/dev/zero",
}


def is_in_workspace(path: str) -> bool:
    abs_path = os.path.abspath(os.path.expanduser(path))
    abs_workspace = os.path.abspath(WORKSPACE_DIR)
    try:
        return os.path.commonpath([abs_path, abs_workspace]) == abs_workspace
    except ValueError:
        return False


def _safe_shlex_split(command: str):
    try:
        return shlex.split(command, posix=True)
    except Exception:
        return command.split()


def _strip_heredoc_bodies(command: str) -> str:
    """Remove here-document bodies before regex risk scanning.

    This avoids false positives when a multi-line shell command merely contains
    example strings such as dangerous commands inside a heredoc/python snippet.
    The heredoc introducer line is kept; only the literal body is removed.
    """
    lines = command.splitlines(keepends=True)
    output = []
    pending_delimiters = []
    heredoc_pattern = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_-]*)\1")

    for line in lines:
        if pending_delimiters:
            current = pending_delimiters[0]
            compare = line.strip()
            if compare == current:
                pending_delimiters.pop(0)
                output.append(line)
            # Otherwise drop heredoc body line from risk regex surface.
            continue

        output.append(line)
        for match in heredoc_pattern.finditer(line):
            pending_delimiters.append(match.group(2))

    return "".join(output)


def _effective_command_words(tokens):
    """Return best-effort executable words, including commands after separators/wrappers."""
    words = []
    separators = {";", "&&", "||", "|"}
    wrappers = {"sudo", "doas", "command", "builtin", "env", "nohup", "time"}
    expect_command = True
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in separators:
            expect_command = True
            i += 1
            continue
        if not expect_command:
            i += 1
            continue

        # Skip VAR=value environment assignments before a command.
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", token):
            i += 1
            continue

        base = os.path.basename(token).lower()
        if base in wrappers:
            words.append(base)
            i += 1
            if base == "env":
                while i < len(tokens) and (
                    re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*", tokens[i])
                    or tokens[i] in {"-i", "-0"}
                    or tokens[i].startswith("-u")
                ):
                    # Skip option value for `env -u NAME` when split.
                    if tokens[i] == "-u" and i + 1 < len(tokens):
                        i += 2
                    else:
                        i += 1
            expect_command = True
            continue

        words.append(base)
        expect_command = False
        i += 1

    return words


def _command_contains_shell_pipe_to_interpreter(command_lower: str) -> bool:
    """Detect curl/wget remote script piped directly into an interpreter."""
    return bool(re.search(
        r"\b(curl|wget)\b[^\n;&|]*\|\s*(sudo\s+)?(sh|bash|zsh|fish|python|python3|perl|ruby|node)\b",
        command_lower,
    ))


def _extract_path_like_tokens(tokens):
    """Best-effort extraction of path-looking CLI arguments for permission checks."""
    skip_next_for_options = {
        "-o", "--output", "-O", "--directory-prefix", "-C", "--cwd", "--prefix",
        "--config", "--file", "-f", "--target", "-t"
    }
    paths = []
    skip_next = False
    redirect_token_pattern = re.compile(r"^(?:&>>|&>|>>|2>>|2>|1>>|1>|0<|<|>)(.*)$")

    for token in tokens:
        if skip_next:
            if token not in _REDIRECT_SAFE_TARGETS:
                paths.append(token)
            skip_next = False
            continue

        # 处理 shlex 后仍粘在一起的重定向 token，例如 2>/dev/null、>/tmp/x。
        redirect_match = redirect_token_pattern.match(token)
        if redirect_match:
            target = redirect_match.group(1).strip()
            if not target:
                continue
            if target.startswith("&") and target[1:].isdigit():
                continue
            if target in _REDIRECT_SAFE_TARGETS:
                continue
            paths.append(target)
            continue

        if token in skip_next_for_options:
            skip_next = True
            continue
        if token.startswith("-"):
            # 支持 --output=/tmp/x 这类参数
            if "=" in token:
                maybe_path = token.split("=", 1)[1]
                if maybe_path in _REDIRECT_SAFE_TARGETS:
                    continue
                if maybe_path.startswith(("/", "~/", "../", "./")):
                    paths.append(maybe_path)
            continue
        if token in _REDIRECT_SAFE_TARGETS:
            continue
        if token.startswith(("/", "~/", "../", "./")) or "/" in token:
            paths.append(token)
    return paths


def _outside_workspace_paths(tokens):
    outside = []
    for path in _extract_path_like_tokens(tokens):
        expanded = os.path.expanduser(path)
        abs_path = expanded if os.path.isabs(expanded) else os.path.abspath(expanded)
        if not is_in_workspace(abs_path):
            outside.append(path)
    return outside


def _has_dangerous_root_target(command_lower: str) -> bool:
    """Commands so destructive that they are blocked instead of merely requiring approval."""
    block_patterns = [
        r"\brm\s+[^\n;&|]*(-[a-z]*r[a-z]*f|-force|-rf|-fr)[^\n;&|]*(\s+/\s*($|[;&|])|\s+/\*|\s+--no-preserve-root\b)",
        r"\brm\s+(?=[^\n;&|]*(?:\s+/\s*($|[;&|])|\s+/\*))(?=[^\n;&|]*(?:-[a-z]*r[a-z]*|--recursive)\b)(?=[^\n;&|]*(?:-[a-z]*f[a-z]*|--force)\b)",
        r"\bchmod\s+[^\n;&|]*-R[^\n;&|]*(\s+/\s*($|[;&|])|\s+/\*)",
        r"\bchown\s+[^\n;&|]*-R[^\n;&|]*(\s+/\s*($|[;&|])|\s+/\*)",
        r"\bmkfs(\.|\s)",
        r"\bdd\b[^\n;&|]*\bof=/dev/(sd|hd|vd|nvme|disk)",
        r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # fork bomb
    ]
    return any(re.search(pattern, command_lower) for pattern in block_patterns)


def _strip_quoted_segments(text: str) -> str:
    """把单/双引号包裹的字面量替换为等长空白，使后续的正则扫描不会
    把字符串里的 `>` 误识别为真实 shell 重定向。

    说明：
      - 仅做朴素状态机扫描，不处理引号嵌套与转义的所有边角情况；
      - 目的不是完整解析 shell，而是"降低对引号内字符的敏感度"；
      - 用空格替换而不是直接删除，是为了保留偏移量，便于将来需要列号定位时复用。
    """
    out = []
    i = 0
    n = len(text)
    quote = None  # 当前所在的引号类型
    while i < n:
        ch = text[i]
        if quote is None:
            if ch in ("'", '"'):
                quote = ch
                out.append(" ")
            else:
                out.append(ch)
        else:
            if ch == quote:
                quote = None
                out.append(" ")
            else:
                # 字面量：以空白替换，保留换行以免影响行级正则
                out.append("\n" if ch == "\n" else " ")
        i += 1
    return "".join(out)


def _outside_workspace_redirects(command: str):
    """从命令中提取真实 shell 重定向目标，并返回越出工作区的清单。

    误报防护：
      - 输入先经 _strip_heredoc_bodies 去除 heredoc 文档体；
      - 再经 _strip_quoted_segments 去除引号内字面量；
      - 跳过描述符复制（>&N、<&N、2>&1 等）；
      - 跳过 _REDIRECT_SAFE_TARGETS 白名单（/dev/null 等）。
    """
    surface = _strip_quoted_segments(_strip_heredoc_bodies(command))

    # 注意 alt 顺序：把更长的运算符放前面，避免 `>` 抢先匹配 `>>`。
    # 同时排除 `>&` / `<&` 这类描述符复制（后续还会再过滤一次以兜底）。
    pattern = re.compile(r"(?:&>>|&>|>>|2>>|2>|>)(?!\s*&\s*\d)\s*([^\s;&|()<>]+)")

    outside = []
    for target in pattern.findall(surface):
        target = target.strip("'\"")
        if not target:
            continue
        # 描述符复制兜底：`>&1`、`2>&1` 这类被分隔后剩 `&1` 的情况
        if target.startswith("&") and target[1:].isdigit():
            continue
        # 安全 sink 白名单
        if target in _REDIRECT_SAFE_TARGETS:
            continue

        expanded = os.path.expanduser(target)
        abs_target = expanded if os.path.isabs(expanded) else os.path.abspath(expanded)
        if not is_in_workspace(abs_target):
            outside.append(target)
    return outside


def assess_command_risk(command: str, cwd: str = None) -> dict:
    """Return a structured, best-effort risk assessment for a shell command."""
    cwd = cwd or os.getcwd()
    shell_surface = _strip_heredoc_bodies(command)
    command_lower = shell_surface.lower()
    tokens = _safe_shlex_split(shell_surface)
    command_words = _effective_command_words(tokens)
    first = command_words[0] if command_words else ""
    reasons = []
    risk_level = "low"
    blocked = False

    if not command.strip():
        return {
            "risk_level": "low",
            "requires_approval": False,
            "blocked": False,
            "reasons": [],
            "outside_workspace_paths": [],
        }

    if not is_in_workspace(cwd):
        reasons.append(f"当前工作目录不在工作区内: {cwd}")

    if _has_dangerous_root_target(command_lower):
        blocked = True
        reasons.append("命令疑似会对根目录、系统磁盘或整机产生灾难性破坏")

    if _command_contains_shell_pipe_to_interpreter(command_lower):
        reasons.append("命令会把远程下载内容直接管道到解释器执行")

    outside_paths = _outside_workspace_paths(tokens)
    if outside_paths:
        reasons.append("命令涉及工作区外路径: " + ", ".join(outside_paths[:5]))

    high_risk_first_tokens = {
        "sudo": "尝试提权执行命令",
        "su": "尝试切换用户/提权",
        "doas": "尝试提权执行命令",
        "pkexec": "尝试提权执行命令",
        "rm": "删除文件或目录",
        "rmdir": "删除目录",
        "unlink": "删除文件链接",
        "shred": "不可逆擦除文件",
        "truncate": "截断文件内容",
        "dd": "底层块设备/文件复制，可能覆盖磁盘或文件",
        "diskutil": "磁盘管理操作",
        "fdisk": "磁盘分区操作",
        "parted": "磁盘分区操作",
        "wipefs": "擦除文件系统签名",
        "mount": "挂载文件系统",
        "umount": "卸载文件系统",
        "chmod": "修改权限",
        "chown": "修改所有者",
        "chgrp": "修改所属组",
        "kill": "终止进程",
        "killall": "批量终止进程",
        "pkill": "按条件终止进程",
        "shutdown": "关机/重启系统",
        "reboot": "重启系统",
        "halt": "停止系统",
        "launchctl": "修改系统/用户服务",
        "systemctl": "修改系统服务",
        "service": "修改系统服务",
        "crontab": "修改定时任务",
    }
    for command_word in command_words:
        reason = high_risk_first_tokens.get(command_word)
        if reason and reason not in reasons:
            reasons.append(reason)

    # 包管理/运行环境修改：通常不是读操作，可能影响全局系统或项目环境。
    package_managers = {"apt", "apt-get", "yum", "dnf", "pacman", "zypper", "brew", "port"}
    if any(w in package_managers for w in command_words) and any(t in tokens for t in ["install", "remove", "uninstall", "upgrade", "update", "autoremove", "purge"]):
        reasons.append("包管理器会安装、升级或移除软件")
    if any(w in {"pip", "pip3", "python", "python3"} for w in command_words) and "install" in tokens:
        reasons.append("Python 包安装会修改运行环境")
    if any(w in {"npm", "pnpm", "yarn"} for w in command_words) and any(t in tokens for t in ["install", "add", "remove", "uninstall", "update"]):
        reasons.append("Node 包管理命令会修改依赖或全局环境")
    if "gem" in command_words and any(t in tokens for t in ["install", "uninstall", "update"]):
        reasons.append("Ruby gem 命令会修改运行环境")

    # 常见版本控制/容器破坏性操作。
    if re.search(r"\bgit\s+clean\b", command_lower):
        reasons.append("git clean 会删除未跟踪文件")
    if re.search(r"\bgit\s+reset\s+--hard\b", command_lower):
        reasons.append("git reset --hard 会丢弃工作区修改")
    if re.search(r"\bfind\b[^\n;&|]*\s-delete\b", command_lower):
        reasons.append("find -delete 会批量删除文件")
    if re.search(r"\bdocker\b[^\n;&|]*(\ssystem\s+prune|\svolume\s+rm|\srm\b|\srmi\b)", command_lower):
        reasons.append("Docker 命令会删除容器、镜像、卷或缓存")
    if re.search(r"\bdocker\s+compose\b[^\n;&|]*\bdown\b[^\n;&|]*\s-v\b", command_lower):
        reasons.append("docker compose down -v 会删除数据卷")

    # 通过 shell 重定向写入工作区外路径。
    # 委托给 _outside_workspace_redirects：内部已处理 heredoc / 引号 /
    # 描述符复制 / /dev/null 等安全 sink 白名单。
    outside_redirects = _outside_workspace_redirects(command)
    if outside_redirects:
        suffix = ""
        if len(outside_redirects) > 5:
            suffix = f" ... (+{len(outside_redirects) - 5} more)"
        reasons.append(
            "命令会通过重定向写入工作区外路径: "
            + ", ".join(outside_redirects[:5]) + suffix
        )

    if blocked:
        risk_level = "critical"
    elif reasons:
        risk_level = "high"

    return {
        "risk_level": risk_level,
        "requires_approval": bool(reasons),
        "blocked": blocked,
        "reasons": reasons,
        "outside_workspace_paths": outside_paths,
    }


def _permission_required_response(command: str, cwd: str, assessment: dict) -> str:
    # 使用随机 nonce，而不是由 command/cwd/reasons 确定性生成，避免调用方伪造审批 token。
    token = secrets.token_urlsafe(24)
    _PENDING_COMMAND_APPROVALS[token] = {
        "command": command,
        "cwd": os.path.abspath(cwd),
        "reasons": list(assessment["reasons"]),
        "created_at": time.time(),
        "expires_at": time.time() + APPROVAL_TTL_SECONDS,
    }
    return json.dumps({
        "permission_required": True,
        "risk_level": assessment["risk_level"],
        "command": command,
        "cwd": cwd,
        "reasons": assessment["reasons"],
        "approval_token": token,
        "expires_in_seconds": APPROVAL_TTL_SECONDS,
        "message": (
            "该命令被识别为高权限/高风险操作，已被暂停且尚未执行。"
            "请向用户说明命令、风险原因和影响范围；只有在用户明确同意后，"
            "才可再次调用 run_command，并传入 allow_high_privilege=true 与该 approval_token。"
        ),
    }, ensure_ascii=False)


def _is_valid_approval(command: str, cwd: str, assessment: dict, approval_token: str) -> bool:
    if not approval_token:
        return False

    record = _PENDING_COMMAND_APPROVALS.get(approval_token)
    if not record:
        return False

    if time.time() > record.get("expires_at", 0):
        _PENDING_COMMAND_APPROVALS.pop(approval_token, None)
        return False

    return (
        record.get("command") == command
        and record.get("cwd") == os.path.abspath(cwd)
        and record.get("reasons") == list(assessment["reasons"])
    )


def run_command_tool(
    command: str,
    timeout: int = 30,
    allow_high_privilege: bool = False,
    approval_token: str = "",
) -> str:
    """Execute a shell command with a human-approval gate for high-risk commands."""
    try:
        cwd = os.getcwd()
        assessment = assess_command_risk(command, cwd)

        if assessment["blocked"]:
            return json.dumps({
                "error": "Blocked critical command",
                "blocked": True,
                "risk_level": assessment["risk_level"],
                "command": command,
                "reasons": assessment["reasons"],
                "message": "该命令疑似会造成灾难性破坏，工具已拒绝执行。请改用更窄范围、更可审计的命令。",
            }, ensure_ascii=False)

        if assessment["requires_approval"]:
            if not allow_high_privilege or not _is_valid_approval(command, cwd, assessment, approval_token):
                return _permission_required_response(command, cwd, assessment)

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # 审批 token 一次性使用。
        if approval_token:
            _PENDING_COMMAND_APPROVALS.pop(approval_token, None)

        output = {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout[:4000],  # Truncate to avoid huge outputs
            "stderr": result.stderr[:4000],
            "risk_assessment": assessment,
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
    description=(
        "在终端中执行系统 Shell 命令并返回结果。高权限/高风险命令默认不会执行，"
        "会返回 permission_required、风险原因和 approval_token；只有在用户明确授权后，"
        "才可带 allow_high_privilege=true 与 approval_token 再次调用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {"type": "integer", "description": "超时时间(秒)，默认 30", "default": 30},
            "allow_high_privilege": {
                "type": "boolean",
                "description": "用户已明确授权执行该高权限/高风险命令时设为 true；默认 false",
                "default": False,
            },
            "approval_token": {
                "type": "string",
                "description": "第一次被拦截时返回的 approval_token；授权后再次执行需原样传入",
                "default": "",
            },
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
