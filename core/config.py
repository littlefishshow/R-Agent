import os
import json
from dotenv import load_dotenv

# Ensure absolute path based on this file's location
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

# 自动加载根目录下的 .env 文件
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

def load_config():
    """加载配置"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config):
    """保存配置"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
        
def get_api_key():
    config = load_config()
    # 优先从环境变量读取，其次读取配置文件。防范空字符串 ""
    val = os.environ.get("OPENAI_API_KEY") or config.get("api_key")
    return val if val else ""
    
def set_api_key(api_key):
    config = load_config()
    config["api_key"] = api_key
    save_config(config)

def get_model():
    config = load_config()
    # Azure 模式下，通常使用特定的部署名，如 gpt-4o 或其他模型名。
    # 支持多种常见的环境变量名，防范空字符串 "" 覆盖默认值
    val = os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL") or config.get("model")
    if not val:
        return "gpt-4o"
    return val

def set_model(model):
    config = load_config()
    config["model"] = model
    save_config(config)

def get_client_type():
    """获取客户端类型：'openai' 或 'azure'"""
    config = load_config()
    return os.environ.get("LLM_CLIENT_TYPE") or config.get("client_type", "openai")

def get_azure_endpoint():
    config = load_config()
    return os.environ.get("AZURE_OPENAI_ENDPOINT") or config.get("azure_endpoint", "https://aidp.bytedance.net/api/modelhub/online/v2/crawl")

def get_azure_api_version():
    config = load_config()
    return os.environ.get("AZURE_OPENAI_API_VERSION") or config.get("azure_api_version", "2024-02-01")

def get_openai_base_url():
    config = load_config()
    return os.environ.get("OPENAI_BASE_URL") or config.get("base_url", "")

def get_display_mode():
    config = load_config()
    return config.get("display_mode", "detailed")

def set_display_mode(mode):
    config = load_config()
    config["display_mode"] = mode
    save_config(config)

def create_llm_client(api_key=None):
    """
    根据配置或环境变量统一创建 LLM 客户端。
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
