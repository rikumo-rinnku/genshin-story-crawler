import json
import os

CONFIG_PATH = os.path.join("config", "channels.json")  # 或 "data/channels.json"

def load_channel_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_channel_id(module_name: str) -> int:
    config = load_channel_config()
    return config[module_name]["channel_id"]