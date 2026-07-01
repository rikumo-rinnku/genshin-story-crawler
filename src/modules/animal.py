"""
动物爬虫模块
从观测枢爬取所有动物的详细信息，包括动物类型、是否可捕获、背景故事等。
增量策略：使用 crawled.json 记录已爬取的动物 ID，实现增量更新。
"""
import json
import re
import time
import random
import logging
import os

from src.core.client import get
from src.core.parser import clean_html_to_text
from src.core.config_loader import get_channel_id
from src.core.storage import is_crawled, mark_crawled

# ========== 日志配置 ==========
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "animal.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name: str) -> str:
    """清洗非法字符，生成安全的文件名"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_filter_tags(filter_text: str) -> dict:
    """
    解析 ext 中 filter.text 的 JSON 数组，提取动物类型、是否可捕获。
    返回 dict。
    """
    result = {
        "animal_type": "",   # 禽鸟、走兽、游鱼、其他
        "can_capture": ""    # 是、否
    }
    try:
        tags = json.loads(filter_text)
        for tag in tags:
            if tag.startswith("动物类型/"):
                result["animal_type"] = tag.split("/")[-1]
            elif tag.startswith("是否可捕获/"):
                result["can_capture"] = tag.split("/")[-1]
    except Exception as e:
        logger.warning(f"解析 filter 标签失败: {e}")
    return result


# ========== 1. 获取动物列表 ==========
def get_animal_list():
    """
    获取所有动物的 ID、名称、简介和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("animal")   # 从配置文件读取频道 ID (49)
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = get(url, headers=headers, params=params)
        data = resp.json()
        if data.get("retcode") != 0:
            logger.error(f"API 错误: {data}")
            return []

        outer_list = data.get("data", {}).get("list", [])
        if not outer_list:
            logger.warning("未找到外层动物列表")
            return []

        # 查找 name 为 "动物" 的分类
        target_category = None
        for cat in outer_list:
            if cat.get("name") == "动物":
                target_category = cat
                break
        if not target_category:
            logger.warning("未找到「动物」分类")
            return []

        items = target_category.get("list", [])
        animal_list = []
        for item in items:
            aid = str(item.get("content_id"))
            aname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if aid and aname:
                # 解析 ext 中的 filter 标签
                tags = {}
                try:
                    ext_data = json.loads(ext_str)
                    for key, value in ext_data.items():
                        if key.startswith("c_"):
                            filter_text = value.get("filter", {}).get("text", "[]")
                            tags = parse_filter_tags(filter_text)
                            break
                except Exception as e:
                    logger.warning(f"解析动物 ext 失败: {e}")

                animal_list.append({
                    "id": aid,
                    "name": aname,
                    "summary": summary,
                    "animal_type": tags.get("animal_type", ""),
                    "can_capture": tags.get("can_capture", "")
                })
        logger.info(f"成功获取 {len(animal_list)} 个动物 (频道ID: {params['channel_id']})")
        for i, animal in enumerate(animal_list[:5], 1):
            logger.info(f"  {i}. {animal['name']} (ID: {animal['id']}) [{animal['animal_type']}]")
        return animal_list
    except Exception as e:
        logger.exception("获取动物列表失败")
        return []


# ========== 2. 解析动物详情页 ==========
def parse_animal_detail(page_data):
    """
    从动物详情页的 JSON 中提取背景故事。
    返回 dict，包含 background (str)。
    """
    modules = page_data.get("modules", [])
    result = {
        "background": ""   # 背景故事
    }

    for module in modules:
        module_name = module.get("name", "")
        comps = module.get("components", [])
        
        # 背景故事 (good_desc) - module name 为 "背景故事"
        if module_name == "背景故事":
            for comp in comps:
                if comp.get("component_id") == "good_desc":
                    try:
                        data = json.loads(comp.get("data", "{}"))
                        rich_text = data.get("rich_text", "")
                        if rich_text:
                            clean_text = clean_html_to_text(rich_text)
                            if clean_text.strip():
                                result["background"] = clean_text
                    except Exception as e:
                        logger.warning(f"解析背景故事失败: {e}")
                    break
        
        # 兼容：如果没有 "背景故事" 模块名，直接找 good_desc
        else:
            for comp in comps:
                if comp.get("component_id") == "good_desc":
                    # 已经在上面的分支处理过了，避免重复
                    pass

    return result


# ========== 3. 生成文件内容 ==========
def generate_animal_content(animal_info, detail):
    """
    根据动物信息和详情数据生成纯文本内容。
    参数：
        animal_info (dict): 包含 id, name, summary, animal_type, can_capture
        detail (dict): 包含 background
    返回：
        str: 格式化后的纯文本内容
    """
    lines = []

    # 1. 基本信息
    lines.append(f"动物名称：{animal_info['name']}")
    if animal_info.get("animal_type"):
        lines.append(f"动物类型：{animal_info['animal_type']}")
    if animal_info.get("can_capture"):
        can_capture_text = "是" if animal_info["can_capture"] == "是" else "否"
        lines.append(f"是否可捕获：{can_capture_text}")
    if animal_info.get("summary"):
        lines.append(f"简介：{animal_info['summary']}")
    lines.append("")

    # 2. 背景故事
    if detail.get("background"):
        lines.append("【背景故事】")
        lines.append(detail["background"])
        lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个动物 ==========
def process_animal(animal_info, output_dir="data/cleaned/animal"):
    """
    处理单个动物：请求详情 API，解析内容，生成文件。
    参数：
        animal_info (dict): 包含 id, name, summary, animal_type, can_capture
        output_dir (str): 输出目录路径
    返回：
        bool: 成功返回 True，失败返回 False
    """
    animal_id = animal_info["id"]
    animal_name = animal_info["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": animal_id,
        "lang": "zh-cn"
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = get(url, headers=headers, params=params)
        data = resp.json()
        if data.get("retcode") != 0:
            logger.error(f"API 错误: {data.get('message')}")
            return False

        page = data.get("data", {}).get("page", {})
        if not page:
            logger.warning(f"动物 {animal_name} 无页面数据")
            return False

        # 解析详情
        detail = parse_animal_detail(page)

        # 如果没有背景故事，视为失败
        if not detail.get("background"):
            logger.info(f"动物 {animal_name} 无背景故事，跳过")
            return False

        # 生成内容
        content = generate_animal_content(animal_info, detail)
        if not content.strip():
            logger.info(f"动物 {animal_name} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(animal_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存动物: {animal_name}")
        return True

    except Exception as e:
        logger.exception(f"处理动物 {animal_name} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取动物列表。
    2. 遍历每个动物，检查是否已爬取（基于 crawled.json）。
    3. 若未爬取，则调用 process_animal 处理。
    4. 成功则标记已爬取，失败则记录失败信息。
    5. 生成爬取报告。
    """
    # 获取动物列表
    animals = get_animal_list()
    if not animals:
        logger.warning("未获取到动物列表，退出")
        return

    # 统计信息
    stats = {
        "total": len(animals),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    # 测试限制（可调整）
    test_limit = 0  # 设置为 0 或 None 表示全量

    for idx, animal in enumerate(animals[:test_limit] if test_limit else animals, start=1):
        aid = animal["id"]
        aname = animal["name"]
        logger.info(f"[{idx}/{len(animals)}] 正在处理: {aname} (ID: {aid})")

        # 增量检查
        if is_crawled("animal", aid):
            logger.info(f"  跳过 (ID {aid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_animal(animal)
        if success:
            mark_crawled("animal", aid, aname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": aid, "name": aname})

        # 控制请求频率
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "animal_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("动物模块爬取完成！")


if __name__ == "__main__":
    run()