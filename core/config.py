import os

try:
    from dotenv import load_dotenv as _python_dotenv_load_dotenv
except ImportError:  # pragma: no cover - exercised when python-dotenv is absent
    _python_dotenv_load_dotenv = None


def _strip_inline_comment(value: str) -> str:
    """Strip dotenv-style inline comments while preserving # inside quotes."""
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _unescape_double_quoted(value: str) -> str:
    """Handle the most common dotenv double-quoted escape sequences."""
    replacements = {
        "\\n": "\n",
        "\\r": "\r",
        "\\t": "\t",
        '\\"': '"',
        "\\\\": "\\",
        "\\$": "$",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _parse_env_line(line: str):
    """Parse a lightweight dotenv line into (key, value), or None if ignored."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    if "=" not in stripped:
        return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key or not (key[0].isalpha() or key[0] == "_"):
        return None
    if any(not (char.isalnum() or char == "_") for char in key):
        return None

    value = _strip_inline_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = _unescape_double_quoted(value)
    return key, value


def _fallback_load_dotenv(dotenv_path: str) -> bool:
    """
    Minimal .env loader used when python-dotenv is not installed.

    Supports common KEY=VALUE lines, optional leading export, single/double
    quotes, and comments. Existing environment variables are never overwritten,
    matching python-dotenv's default override=False behavior.
    """
    loaded_any = False
    try:
        with open(dotenv_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                parsed = _parse_env_line(raw_line.lstrip("\ufeff"))
                if not parsed:
                    continue
                key, value = parsed
                if key not in os.environ:
                    os.environ[key] = value
                    loaded_any = True
    except OSError:
        return False
    return loaded_any


def _load_dotenv(dotenv_path: str) -> bool:
    if _python_dotenv_load_dotenv is not None:
        return bool(_python_dotenv_load_dotenv(dotenv_path, override=False))
    return _fallback_load_dotenv(dotenv_path)


# Ensure absolute path based on this file's location
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 自动加载根目录下的 .env 文件；python-dotenv 缺失时使用轻量 fallback。
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    _load_dotenv(env_path)

def get_api_key():
    # 从环境变量读取。防范空字符串 ""
    val = os.environ.get("OPENAI_API_KEY")
    return val if val else ""

def get_model():
    # Azure 模式下，通常使用特定的部署名，如 gpt-4o 或其他模型名。
    # 支持多种常见的环境变量名，防范空字符串 "" 覆盖默认值
    val = os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL")
    if not val:
        return "gpt-4o"
    return val

def get_client_type():
    """获取客户端类型：'openai' 或 'azure'"""
    return os.environ.get("LLM_CLIENT_TYPE", "openai")

def get_azure_endpoint():
    return os.environ.get("AZURE_OPENAI_ENDPOINT", "https://aidp.bytedance.net/api/modelhub/online/v2/crawl")

def get_azure_api_version():
    return os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")

def get_openai_base_url():
    return os.environ.get("OPENAI_BASE_URL", "")

def get_display_mode():
    return os.environ.get("DISPLAY_MODE", "detailed")

def get_max_iterations():
    """单次对话允许的最大思考轮数。"""
    try:
        return int(os.environ.get("MAX_ITERATIONS", "30"))
    except ValueError:
        return 50

def get_soft_warn_ratio():
    """软提醒阈值占 max_iterations 的比例（达到此比例后注入提醒）。"""
    try:
        ratio = float(os.environ.get("SOFT_WARN_RATIO", "0.7"))
    except ValueError:
        ratio = 0.7
    if ratio <= 0 or ratio >= 1:
        ratio = 0.7
    return ratio

def get_llm_max_retries():
    """瞬时错误（超时/限流/5xx）的最大重试次数（不含首次请求）。"""
    try:
        n = int(os.environ.get("LLM_MAX_RETRIES", "3"))
    except ValueError:
        n = 3
    return max(0, n)

def get_llm_retry_base_delay():
    """重试初始等待秒数，按 2^attempt 指数退避。"""
    try:
        d = float(os.environ.get("LLM_RETRY_BASE_DELAY", "1.0"))
    except ValueError:
        d = 1.0
    return max(0.0, d)



def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def get_llm_request_timeout():
    """LLM 单次请求超时时间（秒）；防止子 Agent 卡在 provider 请求上。"""
    return _env_float("LLM_REQUEST_TIMEOUT", 120.0, minimum=1.0)


def get_tool_execution_timeout():
    """隔离工具单次执行超时时间（秒）；<=0 时禁用超时。"""
    value = _env_float("TOOL_EXECUTION_TIMEOUT", 300.0, minimum=0.0)
    return None if value <= 0 else value


def get_delegate_task_wall_timeout():
    """单个 delegate 子任务默认墙钟超时时间（秒）；<=0 时禁用。"""
    value = _env_float("DELEGATE_TASK_WALL_TIMEOUT", 900.0, minimum=0.0)
    return None if value <= 0 else value

def get_self_evolution_review_interval():
    """每多少轮用户对话触发一次后台自演进复盘；<=0 表示关闭。"""
    try:
        n = int(os.environ.get("SELF_EVOLUTION_REVIEW_INTERVAL", "3"))
    except ValueError:
        n = 3
    return max(0, n)

def create_llm_client(api_key=None):
    """
    根据环境变量统一创建 LLM 客户端。
    支持 openai 和 azure 两种客户端。
    """
    key = api_key or get_api_key()
    client_type = get_client_type().lower()
    
    if client_type == "azure":
        from openai import AzureOpenAI
        import uuid
        return AzureOpenAI(
            api_key=key,
            api_version=get_azure_api_version(),
            azure_endpoint=get_azure_endpoint(),
            default_headers={"X-TT-LOGID": uuid.uuid4().hex},
            timeout=get_llm_request_timeout(),
        )
    else:
        from openai import OpenAI
        base_url = get_openai_base_url()
        kwargs = {"api_key": key, "timeout": get_llm_request_timeout()}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)