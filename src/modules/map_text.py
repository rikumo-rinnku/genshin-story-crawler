"""
地图文本爬虫模块
从观测枢爬取所有地图交互文本的详细信息，包括文本内容、地区、分类、活动限定、相关任务等。
增量策略：使用 crawled.json 记录已爬取的文本 ID，实现增量更新。
"""
import json
import re
import time
import random
import logging
import os
from bs4 import BeautifulSoup

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
        logging.FileHandler(os.path.join(LOG_DIR, "map_text.log"), encoding='utf-8', mode='a'),
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
    解析 ext 中 filter.text 的 JSON 数组，提取地区、分类、活动限定。
    返回 dict，包含 region, category, is_event_only。
    """
    result = {
        "region": "",        # 蒙德、璃月、稻妻、须弥、枫丹、纳塔、挪德卡莱、活动区域
        "category": "",      # 对话项、闪光点
        "is_event_only": ""  # 是、否
    }
    try:
        tags = json.loads(filter_text)
        for tag in tags:
            if tag.startswith("地区/"):
                result["region"] = tag.split("/")[-1]
            elif tag.startswith("分类/"):
                result["category"] = tag.split("/")[-1]
            elif tag.startswith("活动限定/"):
                result["is_event_only"] = tag.split("/")[-1]
    except Exception as e:
        logger.warning(f"解析 filter 标签失败: {e}")
    return result


# ========== 1. 获取地图文本列表 ==========
def get_map_text_list():
    """
    获取所有地图文本条目的 ID、名称、简介和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("map_text")   # 从配置文件读取频道 ID (251)
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
            logger.warning("未找到外层地图文本列表")
            return []

        # 查找 name 为 "地图文本" 的分类
        target_category = None
        for cat in outer_list:
            if cat.get("name") == "地图文本":
                target_category = cat
                break
        if not target_category:
            logger.warning("未找到「地图文本」分类")
            return []

        items = target_category.get("list", [])
        map_text_list = []
        for item in items:
            tid = str(item.get("content_id"))
            tname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if tid and tname:
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
                    logger.warning(f"解析地图文本 ext 失败: {e}")

                map_text_list.append({
                    "id": tid,
                    "name": tname,
                    "summary": summary,
                    "region": tags.get("region", ""),
                    "category": tags.get("category", ""),
                    "is_event_only": tags.get("is_event_only", "")
                })
        logger.info(f"成功获取 {len(map_text_list)} 个地图文本条目 (频道ID: {params['channel_id']})")
        for i, item in enumerate(map_text_list[:5], 1):
            logger.info(f"  {i}. {item['name']} (ID: {item['id']}) [{item['region']}]")
        return map_text_list
    except Exception as e:
        logger.exception("获取地图文本列表失败")
        return []


# ========== 2. 解析地图文本详情页 ==========
def parse_map_text_detail(page_data):
    """
    从地图文本详情页的 JSON 中提取交互文本和相关任务。
    返回 dict，包含 content (str), related_tasks (str)。
    """
    modules = page_data.get("modules", [])
    result = {
        "content": "",        # 交互文本内容（所有 dialogue 拼接）
        "related_tasks": ""   # 相关任务列表
    }

    for module in modules:
        module_name = module.get("name", "")
        comps = module.get("components", [])

        # 交互文本 (interactive_dialogue)
        if module_name == "交互文本":
            for comp in comps:
                if comp.get("component_id") == "interactive_dialogue":
                    try:
                        data = json.loads(comp.get("data", "{}"))
                        dialogue_list = data.get("list", [])
                        all_dialogues = []

                        for item in dialogue_list:
                            contents = item.get("contents", {})
                            for node_id, node_data in contents.items():
                                dialogue = node_data.get("dialogue", "")
                                if dialogue and dialogue.strip():
                                    all_dialogues.append(dialogue)

                        if all_dialogues:
                            # 用两个换行分隔不同节点的对话
                            combined = "\n\n".join(all_dialogues)
                            result["content"] = clean_html_to_text(combined)
                    except Exception as e:
                        logger.warning(f"解析 interactive_dialogue 失败: {e}")
                    break

        # 相关任务 (collapse_panel)
        elif module_name == "相关任务":
            for comp in comps:
                if comp.get("component_id") == "collapse_panel":
                    try:
                        data = json.loads(comp.get("data", "{}"))
                        rich_text = data.get("rich_text", "")
                        if rich_text and rich_text.strip():
                            clean_text = clean_html_to_text(rich_text)
                            if clean_text.strip():
                                result["related_tasks"] = clean_text
                    except Exception as e:
                        logger.warning(f"解析相关任务失败: {e}")
                    break

    return result


# ========== 3. 生成文件内容 ==========
def generate_map_text_content(item_info, detail):
    """
    根据地图文本信息和详情数据生成纯文本内容。
    参数：
        item_info (dict): 包含 id, name, summary, region, category, is_event_only
        detail (dict): 包含 content, related_tasks
    返回：
        str: 格式化后的纯文本内容
    """
    lines = []

    # 1. 基本信息
    lines.append(f"文本名称：{item_info['name']}")
    if item_info.get("region"):
        lines.append(f"地区：{item_info['region']}")
    if item_info.get("category"):
        lines.append(f"分类：{item_info['category']}")
    if item_info.get("is_event_only"):
        lines.append(f"活动限定：{item_info['is_event_only']}")
    if item_info.get("summary"):
        lines.append(f"简介：{item_info['summary']}")
    lines.append("")

    # 2. 交互文本内容（核心）
    if detail.get("content"):
        lines.append("【内容】")
        lines.append(detail["content"])
        lines.append("")

    # 3. 相关任务（若有）
    if detail.get("related_tasks"):
        lines.append("【相关任务】")
        lines.append(detail["related_tasks"])
        lines.append("")

    # 去除末尾多余换行
    return "\n".join(lines).strip()


# ========== 4. 处理单个地图文本条目 ==========
def process_map_text(item_info, output_dir="data/cleaned/map_text"):
    """
    处理单个地图文本条目：请求详情 API，解析内容，生成文件。
    参数：
        item_info (dict): 包含 id, name, summary, region, category, is_event_only
        output_dir (str): 输出目录路径
    返回：
        bool: 成功返回 True，失败返回 False
    """
    item_id = item_info["id"]
    item_name = item_info["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": item_id,
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
            logger.warning(f"地图文本 {item_name} 无页面数据")
            return False

        # 解析详情
        detail = parse_map_text_detail(page)

        # 如果没有任何有效内容（交互文本或相关任务都为空），视为失败
        if not detail.get("content") and not detail.get("related_tasks"):
            logger.info(f"地图文本 {item_name} 无任何可提取内容")
            return False

        # 生成内容
        content = generate_map_text_content(item_info, detail)
        if not content.strip():
            logger.info(f"地图文本 {item_name} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(item_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存地图文本: {item_name}")
        return True

    except Exception as e:
        logger.exception(f"处理地图文本 {item_name} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取地图文本列表。
    2. 遍历每个条目，检查是否已爬取（基于 crawled.json）。
    3. 若未爬取，则调用 process_map_text 处理。
    4. 成功则标记已爬取，失败则记录失败信息。
    5. 生成爬取报告。
    """
    # 获取地图文本列表
    items = get_map_text_list()
    if not items:
        logger.warning("未获取到地图文本列表，退出")
        return

    # 统计信息
    stats = {
        "total": len(items),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    # 测试限制（可调整）
    test_limit = 0  # 设置为 0 或 None 表示全量

    for idx, item in enumerate(items[:test_limit] if test_limit else items, start=1):
        tid = item["id"]
        tname = item["name"]
        logger.info(f"[{idx}/{len(items)}] 正在处理: {tname} (ID: {tid})")

        # 增量检查
        if is_crawled("map_text", tid):
            logger.info(f"  跳过 (ID {tid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_map_text(item)
        if success:
            mark_crawled("map_text", tid, tname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": tid, "name": tname})

        # 控制请求频率
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "map_text_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("地图文本模块爬取完成！")


if __name__ == "__main__":
    run()