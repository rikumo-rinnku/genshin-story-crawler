import json
import re
from pathlib import Path

from src.core.runtime import CONFIG_DIR, DATA_DIR

CRAWLED_FILE = CONFIG_DIR / "crawled.json"

def sanitize_filename(name: str) -> str:
    """替换 Windows 文件名中的非法字符为下划线"""
    # Windows 不允许的字符: \ / : * ? " < > |
    return re.sub(r'[\\/*?:"<>|]', '_', name)

def load_crawled():
    """加载 crawled.json，返回 { module: { id: name, ... } }"""
    if CRAWLED_FILE.exists():
        with open(CRAWLED_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)
            # 兼容旧格式：如果值是列表，转换为字典（无名称则用空字符串）
            for module, value in data.items():
                if isinstance(value, list):
                    new_dict = {item: "" for item in value}
                    data[module] = new_dict
            return data
    return {}

def save_crawled(data):
    CRAWLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CRAWLED_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def is_crawled(module, entry_id):
    data = load_crawled()
    return entry_id in data.get(module, {})

def mark_crawled(module, entry_id, name=""):
    data = load_crawled()
    if module not in data:
        data[module] = {}
    # 如果传入名称，则更新；否则保留原有名称（如果有）
    if name:
        data[module][entry_id] = name
    elif entry_id not in data[module]:
        data[module][entry_id] = ""   # 无名称时存空
    save_crawled(data)

def get_crawled_name(module, entry_id):
    """根据 ID 获取存储的角色名，若不存在返回 None"""
    data = load_crawled()
    return data.get(module, {}).get(entry_id)

def save_text(content, module, entry_id, name=None):
    """以统一格式保存文本，并返回文件路径。"""
    safe_entry = sanitize_filename(entry_id)
    dir_path = DATA_DIR / module
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{safe_entry}.txt"
    if name:
        full_content = f"# {name}\n\n{content}"
    else:
        full_content = content
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(full_content)
    return file_path
