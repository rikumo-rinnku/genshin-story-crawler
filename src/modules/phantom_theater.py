"""
幻想真境剧诗（月谕圣牌）爬虫模块
从观测枢爬取所有月谕圣牌的描述文本（诗歌/释义）。
增量策略：使用 crawled.json 记录已爬取的圣牌 ID，实现增量更新。
"""

import json
import re
import time
import random
import logging
import os
from typing import Dict, List, Any, Optional

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
        logging.FileHandler(os.path.join(LOG_DIR, "phantom_theater.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name: str) -> str:
    """
    清洗非法字符，生成安全的文件名。
    1. 去除首尾空白字符（空格、制表符、换行符等）
    2. 将 Windows 文件名中不允许的字符替换为下划线
    3. 将剩余的空白字符（空格、制表符、换行符等）替换为下划线
    """
    name = name.strip()
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    name = re.sub(r'\s+', '_', name)
    return name


def parse_card_ext(ext_str: str) -> str:
    """
    从 ext 中解析卡牌类别（目前只有“月谕圣牌”）。
    返回类别名称。
    """
    category = ""
    try:
        ext_data = json.loads(ext_str)
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("类别/"):
                        category = item.split("/")[-1]
                        break
                break
    except Exception as e:
        logger.warning(f"解析圣牌 ext 失败: {e}")
    return category


# ========== 1. 获取圣牌列表 ==========
def get_card_list():
    """
    获取所有“类别/月谕圣牌”的条目。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("phantom_theater")
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
            logger.warning("未找到外层列表")
            return []

        target_category = None
        for cat in outer_list:
            if cat.get("name") == "幻想真境剧诗":
                target_category = cat
                break
        if not target_category:
            if outer_list:
                target_category = outer_list[0]
            else:
                logger.warning("未找到「幻想真境剧诗」分类")
                return []

        items = target_category.get("list", [])
        card_list = []
        for item in items:
            cid = str(item.get("content_id"))
            cname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if cid and cname:
                category = parse_card_ext(ext_str)
                # 只保留“月谕圣牌”类别的条目
                if category == "月谕圣牌":
                    card_list.append({
                        "id": cid,
                        "name": cname,
                        "summary": summary,
                        "category": category
                    })
        logger.info(f"成功获取 {len(card_list)} 张月谕圣牌 (频道ID: {params['channel_id']})")
        for i, c in enumerate(card_list[:5], 1):
            logger.info(f"  {i}. {c['name']}")
        return card_list
    except Exception as e:
        logger.exception("获取圣牌列表失败")
        return []


# ========== 2. 解析圣牌详情页 ==========
def parse_card_detail(page_data: Dict) -> str:
    """
    从圣牌详情页中提取描述文本（诗歌/释义）。
    只提取 multi_table 中第一行第二列（索引1）的文本。
    返回纯文本。
    """
    modules = page_data.get("modules", [])
    text_parts = []

    for module in modules:
        comps = module.get("components", [])
        if not comps:
            continue

        for comp in comps:
            comp_id = comp.get("component_id", "")
            if comp_id != "multi_table":
                continue

            raw_data = comp.get("data", "{}")
            try:
                data = json.loads(raw_data)
            except Exception:
                continue

            tables = data.get("tables", [])
            for table in tables:
                rows = table.get("row", [])
                for row in rows:
                    if len(row) >= 2:
                        # 第二列（索引1）是文本内容
                        html = row[1]
                        if html:
                            text = clean_html_to_text(html)
                            if text.strip():
                                text_parts.append(text)
                    elif len(row) == 1:
                        # 某些牌可能只有一列（纯文本）
                        html = row[0]
                        if html:
                            text = clean_html_to_text(html)
                            if text.strip():
                                text_parts.append(text)

    return "\n\n".join(text_parts)


# ========== 3. 生成文件内容 ==========
def generate_card_content(card_info: Dict, description: str) -> str:
    """
    根据圣牌信息和描述文本生成纯文本内容。
    """
    lines = []

    lines.append(f"卡牌名称：{card_info['name']}")
    if card_info.get('summary'):
        lines.append(f"简介：{card_info['summary']}")
    lines.append("")

    if description:
        lines.append("【描述】")
        lines.append(description)

    return "\n".join(lines).strip()


# ========== 4. 处理单个圣牌 ==========
def process_card(card: Dict, output_dir="data/cleaned/phantom_theater") -> bool:
    """
    处理单个圣牌：请求详情 API，解析内容，生成文件。
    """
    cid = card["id"]
    cname = card["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": cid,
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
            logger.warning(f"圣牌 {cname} 无页面数据")
            return False

        # 提取描述
        description = parse_card_detail(page)

        if not description:
            logger.info(f"圣牌 {cname} 无描述文本，跳过")
            return False

        # 生成内容
        content = generate_card_content(card, description)
        if not content.strip():
            logger.info(f"圣牌 {cname} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(cname)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存圣牌: {cname}")
        return True

    except Exception as e:
        logger.exception(f"处理圣牌 {cname} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取圣牌列表（仅月谕圣牌）。
    2. 遍历每张圣牌，检查是否已爬取。
    3. 若未爬取，则处理。
    4. 成功则标记已爬取。
    5. 生成爬取报告。
    """
    cards = get_card_list()
    if not cards:
        logger.warning("未获取到圣牌列表，退出")
        return

    stats = {
        "total": len(cards),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    test_limit = 0  # 0 表示全量

    for idx, c in enumerate(cards[:test_limit] if test_limit else cards, start=1):
        cid = c["id"]
        cname = c["name"]
        logger.info(f"[{idx}/{len(cards)}] 正在处理: {cname} (ID: {cid})")

        if is_crawled("phantom_theater", cid):
            logger.info(f"  跳过 (ID {cid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_card(c)
        if success:
            mark_crawled("phantom_theater", cid, cname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": cid, "name": cname})

        time.sleep(random.uniform(1, 2))

    report_path = os.path.join(LOG_DIR, "phantom_theater_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("幻想真境剧诗模块爬取完成！")


if __name__ == "__main__":
    run()