import os
from dotenv import load_dotenv

# Ensure absolute path based on this file's location
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 自动加载根目录下的 .env 文件
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

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
            default_headers={"X-TT-LOGID": uuid.uuid4().hex}
        )
    else:
        from openai import OpenAI
        base_url = get_openai_base_url()
        kwargs = {"api_key": key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)