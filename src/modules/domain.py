"""
秘境爬虫模块
从观测枢爬取所有秘境的简要信息及剧情简述（仅保留文本故事）。
增量策略：使用 crawled.json 记录已爬取的秘境 ID，实现增量更新。
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
        logging.FileHandler(os.path.join(LOG_DIR, "domain.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name: str) -> str:
    """清洗非法字符，生成安全的文件名"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_domain_ext(ext_str: str) -> tuple:
    """
    从 ext 中解析秘境的分类标签。
    返回 (domain_type, cost, can_coop, is_task, recommended_elements)
    """
    domain_type = ""
    cost = ""
    can_coop = ""
    is_task = ""
    recommended_elements = []

    try:
        ext_data = json.loads(ext_str)
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("秘境类型/"):
                        domain_type = item.split("/")[-1]
                    elif item.startswith("秘境消耗/"):
                        cost = item.split("/")[-1]
                    elif item.startswith("是否可联机/"):
                        can_coop = item.split("/")[-1]
                    elif item.startswith("是否为任务本/"):
                        is_task = item.split("/")[-1]
                    elif item.startswith("推荐元素/"):
                        recommended_elements.append(item.split("/")[-1])
                break
    except Exception as e:
        logger.warning(f"解析秘境 ext 失败: {e}")

    return domain_type, cost, can_coop, is_task, recommended_elements


# ========== 1. 获取秘境列表 ==========
def get_domain_list():
    """
    获取所有秘境的 ID、名称、摘要和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("domain")
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
            logger.warning("未找到外层秘境列表")
            return []

        target_category = None
        for cat in outer_list:
            if cat.get("name") == "秘境":
                target_category = cat
                break
        if not target_category:
            if outer_list:
                target_category = outer_list[0]
            else:
                logger.warning("未找到「秘境」分类")
                return []

        items = target_category.get("list", [])
        domain_list = []
        for item in items:
            did = str(item.get("content_id"))
            dname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if did and dname:
                d_type, cost, can_coop, is_task, elements = parse_domain_ext(ext_str)
                domain_list.append({
                    "id": did,
                    "name": dname,
                    "summary": summary,
                    "domain_type": d_type,
                    "cost": cost,
                    "can_coop": can_coop,
                    "is_task": is_task,
                    "recommended_elements": elements
                })
        logger.info(f"成功获取 {len(domain_list)} 个秘境 (频道ID: {params['channel_id']})")
        for i, d in enumerate(domain_list[:5], 1):
            logger.info(f"  {i}. {d['name']} (ID: {d['id']}) [{d['domain_type']}]")
        return domain_list
    except Exception as e:
        logger.exception("获取秘境列表失败")
        return []


# ========== 2. 解析秘境详情页（仅简述模块） ==========
def parse_domain_detail(page_data: Dict) -> str:
    """
    从秘境详情页中提取“简述”模块的叙述文本。
    返回拼接后的纯文本。
    """
    modules = page_data.get("modules", [])
    summary_parts = []

    for module in modules:
        module_name = module.get("name", "")
        if module_name != "简述":
            continue  # 只处理“简述”模块

        comps = module.get("components", [])
        if not comps:
            continue

        for comp in comps:
            comp_id = comp.get("component_id", "")
            raw_data = comp.get("data", "{}")
            if comp_id != "multi_table":
                continue

            try:
                data = json.loads(raw_data)
            except Exception:
                continue

            tables = data.get("tables", [])
            for table in tables:
                tab_name = table.get("tab_name", "")
                rows = table.get("row", [])
                if not rows:
                    continue
                # 取第一行第一列（多行时可能还有第二行，但简述通常只有一行）
                if rows and rows[0]:
                    cell_text = rows[0][0] if rows[0] else ""
                    if cell_text:
                        clean_text = clean_html_to_text(cell_text)
                        if clean_text.strip():
                            if tab_name:
                                # 用 tab_name 作为小标题
                                summary_parts.append(f"【{tab_name}】")
                            summary_parts.append(clean_text)

    return "\n\n".join(summary_parts)


# ========== 3. 生成文件内容 ==========
def generate_domain_content(domain_info: Dict, summary: str) -> str:
    """
    根据秘境信息和简述生成纯文本内容。
    """
    lines = []

    # 基本信息
    lines.append(f"秘境名称：{domain_info['name']}")
    if domain_info.get('domain_type'):
        lines.append(f"秘境类型：{domain_info['domain_type']}")
    if domain_info.get('cost'):
        lines.append(f"秘境消耗：{domain_info['cost']}")
    if domain_info.get('can_coop'):
        lines.append(f"是否可联机：{domain_info['can_coop']}")
    if domain_info.get('is_task'):
        lines.append(f"是否为任务本：{domain_info['is_task']}")
    if domain_info.get('recommended_elements'):
        lines.append(f"推荐元素：{'、'.join(domain_info['recommended_elements'])}")
    if domain_info.get('summary'):
        lines.append(f"简介：{domain_info['summary']}")
    lines.append("")

    # 简述（核心剧情文本）
    if summary:
        lines.append("【简述】")
        lines.append(summary)
        lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个秘境 ==========
def process_domain(domain: Dict, output_dir="data/cleaned/domain") -> bool:
    """
    处理单个秘境：请求详情 API，提取简述，生成文件。
    """
    did = domain["id"]
    dname = domain["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": did,
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
            logger.warning(f"秘境 {dname} 无页面数据")
            return False

        # 提取简述
        summary = parse_domain_detail(page)

        # 如果没有简述，视为失败（不生成文件）
        if not summary:
            logger.info(f"秘境 {dname} 无简述内容，跳过")
            return False

        # 生成内容
        content = generate_domain_content(domain, summary)
        if not content.strip():
            logger.info(f"秘境 {dname} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(dname)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存秘境: {dname}")
        return True

    except Exception as e:
        logger.exception(f"处理秘境 {dname} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取秘境列表。
    2. 遍历每个秘境，检查是否已爬取。
    3. 若未爬取，则处理。
    4. 成功则标记已爬取。
    5. 生成爬取报告。
    """
    domains = get_domain_list()
    if not domains:
        logger.warning("未获取到秘境列表，退出")
        return

    stats = {
        "total": len(domains),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    test_limit = 0  # 0 表示全量

    for idx, d in enumerate(domains[:test_limit] if test_limit else domains, start=1):
        did = d["id"]
        dname = d["name"]
        logger.info(f"[{idx}/{len(domains)}] 正在处理: {dname} (ID: {did})")

        if is_crawled("domain", did):
            logger.info(f"  跳过 (ID {did} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_domain(d)
        if success:
            mark_crawled("domain", did, dname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": did, "name": dname})

        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "domain_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("秘境模块爬取完成！")


if __name__ == "__main__":
    run()