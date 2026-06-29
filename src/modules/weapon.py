"""
武器剧情爬虫模块
从观测枢爬取所有武器的详细信息，包括基础属性、技能描述和背景故事。
增量策略：使用 crawled.json 记录已爬取的武器 ID，实现增量更新。
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
from src.core.storage import is_crawled, mark_crawled  # 导入增量管理函数

# ========== 日志配置 ==========
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "weapon.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name):
    """清洗非法字符，生成安全的文件名"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_ext_info(ext_str: str):
    """
    解析武器列表中的 ext 字段，提取星级、类型、属性加成、获取途径。
    返回 dict。
    """
    result = {
        "star": "",
        "category": "",
        "attributes": [],
        "source": []
    }
    try:
        ext_data = json.loads(ext_str)
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("武器星级/"):
                        result["star"] = item.split("/")[-1]
                    elif item.startswith("武器类型/"):
                        result["category"] = item.split("/")[-1]
                    elif item.startswith("属性加成/"):
                        attr = item.split("/")[-1]
                        if attr not in result["attributes"]:
                            result["attributes"].append(attr)
                    elif item.startswith("获取途径/"):
                        source = item.split("/")[-1]
                        if source not in result["source"]:
                            result["source"].append(source)
                break
    except Exception as e:
        logger.warning(f"解析武器 ext 失败: {e}")
    return result


# ========== 1. 获取武器列表 ==========
def get_weapon_list():
    """
    获取所有武器的 ID、名称和 ext 信息。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("weapon")   # 从配置文件读取频道 ID (5)
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
            logger.warning("未找到外层武器列表")
            return []

        weapon_category = None
        for cat in outer_list:
            if cat.get("name") == "武器":
                weapon_category = cat
                break
        if not weapon_category:
            logger.warning("未找到「武器」分类")
            return []

        items = weapon_category.get("list", [])
        weapon_list = []
        for item in items:
            wid = str(item.get("content_id"))
            wname = item.get("title")
            ext_str = item.get("ext", "{}")
            if wid and wname:
                ext_info = parse_ext_info(ext_str)
                weapon_list.append({
                    "id": wid,
                    "name": wname,
                    "star": ext_info["star"],
                    "category": ext_info["category"],
                    "attributes": ext_info["attributes"],
                    "source": ext_info["source"]
                })
        logger.info(f"成功获取 {len(weapon_list)} 个武器 (频道ID: {params['channel_id']})")
        for i, w in enumerate(weapon_list[:5], 1):
            logger.info(f"  {i}. {w['name']} (ID: {w['id']}) [{w['star']} {w['category']}]")
        return weapon_list
    except Exception as e:
        logger.exception("获取武器列表失败")
        return []


# ========== 2. 解析武器详情页 ==========
def parse_weapon_detail(page_data):
    """
    从武器详情页的 JSON 中提取所需信息。
    返回 dict，包含 name, type, star, skill_name, skill_desc, story, source 等。
    """
    modules = page_data.get("modules", [])
    result = {
        "name": "",
        "category": "",
        "star": 0,
        "skill_name": "",
        "skill_desc": "",
        "story": "",
        "source": ""
    }

    for module in modules:
        comps = module.get("components", [])
        if not comps:
            continue
        comp_id = comps[0].get("component_id", "")
        raw_data = comps[0].get("data", "{}")

        if comp_id == "equipment_base_info":
            try:
                info = json.loads(raw_data)
                result["name"] = info.get("name", "")
                result["category"] = info.get("category", "")
                result["star"] = info.get("star", 0)
            except Exception as e:
                logger.warning(f"解析 equipment_base_info 失败: {e}")

        elif comp_id == "good_desc":
            try:
                info = json.loads(raw_data)
                rich_text = info.get("rich_text", "")
                if rich_text:
                    text = clean_html_to_text(rich_text)
                    lines = text.split('\n')
                    skill_name = ""
                    for line in lines:
                        if line.strip():
                            skill_name = line.strip()
                            break
                    result["skill_name"] = skill_name
                    result["skill_desc"] = text
                attr_list = info.get("attr", [])
                for attr in attr_list:
                    if attr.get("key") == "获取途径：" or attr.get("key") == "获取途径":
                        values = attr.get("value", [])
                        if values:
                            result["source"] = clean_html_to_text(values[0])
            except Exception as e:
                logger.warning(f"解析 good_desc 失败: {e}")

        elif comp_id == "collapse_panel" and module.get("name") == "相关故事":
            try:
                info = json.loads(raw_data)
                rich_text = info.get("rich_text", "")
                if rich_text:
                    result["story"] = clean_html_to_text(rich_text)
            except Exception as e:
                logger.warning(f"解析 相关故事 失败: {e}")

    return result


# ========== 3. 获取单个武器详情并保存 ==========
def process_weapon(weapon_id, weapon_name, output_dir="data/cleaned/weapon"):
    """
    处理单个武器，提取详情并保存为文件。
    如果文件已存在，则跳过（此功能由调用方通过 is_crawled 控制）。
    """
    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": weapon_id,
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
            logger.warning(f"武器 {weapon_name} 无页面数据")
            return False

        detail = parse_weapon_detail(page)
        if not detail.get("story") and not detail.get("skill_desc"):
            logger.info(f"武器 {weapon_name} 没有故事或技能描述，跳过")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        safe_name = safe_filename(weapon_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 构建内容
        content_lines = []
        content_lines.append(f"武器名称：{detail['name'] or weapon_name}")
        if detail["category"]:
            content_lines.append(f"武器类型：{detail['category']}")
        if detail["star"]:
            content_lines.append(f"星级：{detail['star']}")
        if detail["skill_name"]:
            content_lines.append(f"技能名称：{detail['skill_name']}")
        if detail["skill_desc"]:
            content_lines.append("技能描述：")
            content_lines.append(detail["skill_desc"])
        if detail["source"]:
            content_lines.append(f"获取途径：{detail['source']}")
        if detail["story"]:
            content_lines.append("")
            content_lines.append("相关故事：")
            content_lines.append(detail["story"])

        content = "\n".join(content_lines)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存武器: {weapon_name}")
        return True

    except Exception as e:
        logger.exception(f"处理武器 {weapon_name} 失败")
        return False


# ========== 4. 主运行逻辑 ==========
def run():
    weapons = get_weapon_list()
    if not weapons:
        logger.warning("未获取到武器列表，退出")
        return

    # 统计信息
    stats = {
        "total": len(weapons),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    # 测试限制（可调整）
    test_limit = 0  # 设置为 0 或 None 表示全量

    for idx, weapon in enumerate(weapons[:test_limit] if test_limit else weapons, start=1):
        wid = weapon["id"]
        wname = weapon["name"]
        logger.info(f"[{idx}/{len(weapons)}] 正在处理: {wname} (ID: {wid})")

        # 检查是否已爬取
        if is_crawled("weapon", wid):
            logger.info(f"  跳过 (ID {wid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_weapon(wid, wname)
        if success:
            mark_crawled("weapon", wid, wname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": wid, "name": wname})

        # 控制请求间隔
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "weapon_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("武器模块测试完成！")


if __name__ == "__main__":
    run()