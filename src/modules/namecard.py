"""
名片爬虫模块（第三方Wiki）
从 Bilibili Wiki 爬取所有名片的详细信息，包括类型、获取方式、介绍和实装版本。
数据来源：https://wiki.biligame.com/ys/名片

注意：本模块使用第三方 Wiki 的 MediaWiki API，与官方观测枢 API 不同。
增量策略：使用 crawled.json 记录已爬取的名片 ID，实现增量更新。
"""

import json
import re
import time
import random
import logging
import os
from typing import Dict, List, Any, Optional

import requests
from bs4 import BeautifulSoup

from src.core.storage import is_crawled, mark_crawled

# ========== 日志配置 ==========
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "namecard.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== 常量 ==========
WIKI_API_URL = "https://wiki.biligame.com/ys/api.php"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


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


def get_all_cards() -> List[Dict]:
    """
    使用 MediaWiki API 获取所有名片的列表（支持分页）。
    返回: list of dict，每个 dict 包含 pageid 和 title
    """
    all_cards = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": "Category:名片",
        "cmlimit": "500",
        "format": "json"
    }
    headers = {"User-Agent": USER_AGENT}

    while True:
        try:
            resp = requests.get(WIKI_API_URL, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"请求名片列表失败: {e}")
            break

        cards = data.get("query", {}).get("categorymembers", [])
        if not cards:
            break

        all_cards.extend(cards)
        logger.info(f"已获取 {len(all_cards)} 张名片...")

        # 检查是否有下一页
        if "continue" not in data:
            break
        params["cmcontinue"] = data["continue"]["cmcontinue"]

        # 礼貌请求
        time.sleep(0.5)

    return all_cards


def parse_namecard_detail(html_content: str) -> Dict:
    """
    从名片页面的 HTML 中解析详细信息。
    返回 dict，包含 type, obtain, description, version。
    """
    result = {
        "type": "",
        "obtain": "",
        "description": "",
        "version": ""
    }

    soup = BeautifulSoup(html_content, 'lxml')

    # 查找名片信息表格
    table = soup.find('table', class_='wikitable')
    if not table:
        return result

    rows = table.find_all('tr')
    for row in rows:
        cells = row.find_all(['th', 'td'])
        if len(cells) < 2:
            continue

        label = cells[0].get_text(strip=True)
        value = cells[1].get_text(strip=True) if len(cells) > 1 else ""

        if label == "类型":
            result["type"] = value
        elif label == "获取方式":
            result["obtain"] = value
        elif label == "介绍":
            result["description"] = value
        elif label == "实装版本":
            result["version"] = value

    return result


def get_card_detail(pageid: int) -> Optional[Dict]:
    """
    获取单张名片的详情页 HTML，并解析为结构化数据。
    返回 dict，包含 type, obtain, description, version。
    """
    params = {
        "action": "parse",
        "pageid": pageid,
        "format": "json"
    }
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(WIKI_API_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"请求名片详情失败 (pageid={pageid}): {e}")
        return None

    html_content = data.get("parse", {}).get("text", {}).get("*", "")
    if not html_content:
        logger.warning(f"名片 pageid={pageid} 无 HTML 内容")
        return None

    return parse_namecard_detail(html_content)


# ========== 1. 获取名片列表 ==========
def get_namecard_list():
    """
    获取所有名片的 ID、名称。
    返回: list of dict
    """
    cards = get_all_cards()
    if not cards:
        logger.warning("未获取到名片列表")
        return []

    namecard_list = []
    for card in cards:
        pageid = str(card.get("pageid"))
        title = card.get("title", "")
        if pageid and title:
            namecard_list.append({
                "id": pageid,
                "name": title
            })

    logger.info(f"成功获取 {len(namecard_list)} 张名片")
    for i, c in enumerate(namecard_list[:5], 1):
        logger.info(f"  {i}. {c['name']} (ID: {c['id']})")
    return namecard_list


# ========== 2. 生成文件内容 ==========
def generate_namecard_content(namecard_info: Dict, detail: Dict) -> str:
    """
    根据名片信息和详情数据生成纯文本内容。
    """
    lines = []

    lines.append(f"名片名称：{namecard_info['name']}")
    if detail.get('type'):
        lines.append(f"类型：{detail['type']}")
    if detail.get('version'):
        lines.append(f"实装版本：{detail['version']}")
    if detail.get('obtain'):
        lines.append(f"获取方式：{detail['obtain']}")
    lines.append("")

    if detail.get('description'):
        lines.append("【介绍】")
        lines.append(detail['description'])

    return "\n".join(lines).strip()


# ========== 3. 处理单个名片 ==========
def process_namecard(namecard: Dict, output_dir="data/cleaned/namecard") -> bool:
    """
    处理单个名片：获取详情，生成文件。
    """
    pageid = namecard["id"]
    name = namecard["name"]

    # 获取详情
    detail = get_card_detail(int(pageid))
    if not detail:
        logger.info(f"名片 {name} 无详情数据，跳过")
        return False

    # 如果没有介绍文本，视为无价值内容，跳过
    if not detail.get("description"):
        logger.info(f"名片 {name} 无介绍文本，跳过")
        return False

    # 生成内容
    content = generate_namecard_content(namecard, detail)
    if not content.strip():
        logger.info(f"名片 {name} 生成内容为空")
        return False

    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)

    # 生成安全文件名
    safe_name = safe_filename(name)
    filepath = os.path.join(output_dir, f"{safe_name}.txt")

    # 写入文件
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    logger.info(f"  保存名片: {name}")
    return True


# ========== 4. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取名片列表。
    2. 遍历每个名片，检查是否已爬取。
    3. 若未爬取，则处理。
    4. 成功则标记已爬取。
    5. 生成爬取报告。
    """
    namecards = get_namecard_list()
    if not namecards:
        logger.warning("未获取到名片列表，退出")
        return

    stats = {
        "total": len(namecards),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    test_limit = 0  # 0 表示全量

    for idx, c in enumerate(namecards[:test_limit] if test_limit else namecards, start=1):
        cid = c["id"]
        cname = c["name"]
        logger.info(f"[{idx}/{len(namecards)}] 正在处理: {cname} (ID: {cid})")

        if is_crawled("namecard", cid):
            logger.info(f"  跳过 (ID {cid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_namecard(c)
        if success:
            mark_crawled("namecard", cid, cname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": cid, "name": cname})

        # 控制请求频率
        # time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "namecard_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("名片模块爬取完成！")


if __name__ == "__main__":
    run()