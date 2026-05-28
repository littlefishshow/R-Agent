import os
import json

# Ensure absolute path based on this file's location
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")

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
    return config.get("api_key")
    
def set_api_key(api_key):
    config = load_config()
    config["api_key"] = api_key
    save_config(config)

def get_model():
    config = load_config()
    return config.get("model", "gpt-5.5-2026-04-24")

def set_model(model):
    config = load_config()
    config["model"] = model
    save_config(config)

def get_display_mode():
    config = load_config()
    return config.get("display_mode", "detailed")

def set_display_mode(mode):
    config = load_config()
    config["display_mode"] = mode
    save_config(config)
