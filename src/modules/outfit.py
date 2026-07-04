"""
装扮爬虫模块
从观测枢爬取所有装扮（衣装/风之翼/武器装扮）的详细信息。
增量策略：使用 crawled.json 记录已爬取的装扮 ID，实现增量更新。
"""

import json
import re
import time
import random
import logging
import os
from typing import Dict, List, Optional

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
        logging.FileHandler(os.path.join(LOG_DIR, "outfit.log"), encoding='utf-8', mode='a'),
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


def parse_outfit_ext(ext_str: str) -> tuple:
    """
    从 ext 中解析装扮的分类标签。
    返回 (category, obtain_method)
    """
    category = ""
    obtain_method = ""

    try:
        ext_data = json.loads(ext_str)
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("装扮分类/"):
                        category = item.split("/")[-1]
                    # 衣装获取方式（优先级高于风之翼获取方式，因为各自只有对应的标签）
                    elif item.startswith("衣装获取方式/"):
                        obtain_method = item.split("/")[-1]
                    elif item.startswith("风之翼获取方式/"):
                        obtain_method = item.split("/")[-1]
                break
    except Exception as e:
        logger.warning(f"解析装扮 ext 失败: {e}")

    return category, obtain_method


# ========== 1. 获取装扮列表 ==========
def get_outfit_list():
    """
    获取所有装扮的 ID、名称、分类和获取方式。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("outfit")
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
            logger.warning("未找到外层装扮列表")
            return []

        target_category = None
        for cat in outer_list:
            if cat.get("name") == "装扮":
                target_category = cat
                break
        if not target_category:
            if outer_list:
                target_category = outer_list[0]
            else:
                logger.warning("未找到「装扮」分类")
                return []

        items = target_category.get("list", [])
        outfit_list = []
        for item in items:
            oid = str(item.get("content_id"))
            oname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if oid and oname:
                category, obtain_method = parse_outfit_ext(ext_str)
                outfit_list.append({
                    "id": oid,
                    "name": oname,
                    "summary": summary,
                    "category": category,
                    "obtain_method": obtain_method
                })
        logger.info(f"成功获取 {len(outfit_list)} 个装扮 (频道ID: {params['channel_id']})")
        for i, o in enumerate(outfit_list[:5], 1):
            logger.info(f"  {i}. {o['name']} (ID: {o['id']}) [{o['category']}]")
        return outfit_list
    except Exception as e:
        logger.exception("获取装扮列表失败")
        return []


# ========== 2. 解析装扮详情页 ==========
def parse_outfit_detail(page_data: Dict, category: str) -> Dict:
    """
    从装扮详情页中提取信息。
    - 衣装：提取衣装故事、衣装简介、角色信息
    - 武器装扮/风之翼：提取描述、获取途径
    返回 dict。
    """
    result = {
        "description": "",          # 描述/介绍
        "story": "",                # 衣装故事（仅衣装）
        "brief": "",                # 衣装简介（仅衣装）
        "obtain_detail": "",        # 获取途径详情
        "scope": "",                # 适用范围（武器装扮）
        "character_info": {}        # 角色信息（仅衣装）
    }

    modules = page_data.get("modules", [])
    for module in modules:
        module_name = module.get("name", "")
        comps = module.get("components", [])
        if not comps:
            continue

        for comp in comps:
            comp_id = comp.get("component_id", "")
            raw_data = comp.get("data", "{}")
            try:
                data = json.loads(raw_data)
            except Exception:
                continue

            # 衣装：衣装故事
            if module_name == "衣装故事" and comp_id == "collapse_panel":
                rich_text = data.get("rich_text", "")
                if rich_text:
                    text = clean_html_to_text(rich_text)
                    if text.strip():
                        result["story"] = text

            # 衣装：衣装简介
            elif module_name == "衣装简介" and comp_id == "collapse_panel":
                rich_text = data.get("rich_text", "")
                if rich_text:
                    text = clean_html_to_text(rich_text)
                    if text.strip():
                        result["brief"] = text

            # 衣装：角色信息（dress_base_info）
            elif comp_id == "dress_base_info":
                attr_list = data.get("attr", [])
                for attr in attr_list:
                    key = attr.get("key", "")
                    values = attr.get("value", [])
                    if key and values:
                        cleaned = [clean_html_to_text(v) for v in values if v]
                        if cleaned:
                            result["character_info"][key] = "；".join(cleaned)

            # 武器装扮 / 风之翼：material_base_info
            elif comp_id == "material_base_info":
                # 提取 attr（描述、获取途径等）
                attr_list = data.get("attr", [])
                for attr in attr_list:
                    key = attr.get("key", "")
                    values = attr.get("value", [])
                    if key and values:
                        cleaned = [clean_html_to_text(v) for v in values if v]
                        if cleaned:
                            if key == "描述":
                                result["description"] = "；".join(cleaned)
                            elif key == "获取途径":
                                result["obtain_detail"] = "；".join(cleaned)

                # 提取 materials（适用范围）
                materials = data.get("materials", {})
                if isinstance(materials, dict):
                    value = materials.get("value", "")
                    if value:
                        text = clean_html_to_text(value)
                        if text.strip():
                            result["scope"] = text

    return result


# ========== 3. 生成文件内容 ==========
def generate_outfit_content(outfit_info: Dict, detail: Dict) -> str:
    """
    根据装扮信息和详情数据生成纯文本内容。
    """
    lines = []

    # 基本信息
    lines.append(f"装扮名称：{outfit_info['name']}")
    if outfit_info.get('category'):
        lines.append(f"装扮分类：{outfit_info['category']}")
    if outfit_info.get('obtain_method'):
        lines.append(f"获取方式：{outfit_info['obtain_method']}")
    if outfit_info.get('summary'):
        lines.append(f"简介：{outfit_info['summary']}")
    lines.append("")

    # ===== 衣装 =====
    if outfit_info.get('category') == "衣装":
        # 角色信息
        if detail.get('character_info'):
            lines.append("【角色信息】")
            for key, value in detail['character_info'].items():
                lines.append(f"{key}：{value}")
            lines.append("")

        # 衣装简介
        if detail.get('brief'):
            lines.append("【衣装简介】")
            lines.append(detail['brief'])
            lines.append("")

        # 衣装故事（核心剧情文本）
        if detail.get('story'):
            lines.append("【衣装故事】")
            lines.append(detail['story'])
            lines.append("")

    # ===== 武器装扮 / 风之翼 =====
    else:
        # 适用范围（武器装扮）
        if detail.get('scope'):
            lines.append(f"适用范围：{detail['scope']}")
            lines.append("")

        # 描述
        if detail.get('description'):
            lines.append("【描述】")
            lines.append(detail['description'])
            lines.append("")

        # 获取途径（详情）
        if detail.get('obtain_detail'):
            lines.append("【获取途径】")
            lines.append(detail['obtain_detail'])
            lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个装扮 ==========
def process_outfit(outfit: Dict, output_dir="data/cleaned/outfit") -> bool:
    """
    处理单个装扮：请求详情 API，解析内容，生成文件。
    """
    oid = outfit["id"]
    oname = outfit["name"]
    category = outfit.get("category", "")

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": oid,
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
            logger.warning(f"装扮 {oname} 无页面数据")
            return False

        # 解析详情
        detail = parse_outfit_detail(page, category)

        # 判断是否有有效文本内容
        has_content = False
        if category == "衣装":
            if detail.get("story") or detail.get("brief"):
                has_content = True
        else:
            if detail.get("description"):
                has_content = True

        if not has_content:
            logger.info(f"装扮 {oname} 无有效文本内容，跳过")
            return False

        # 生成内容
        content = generate_outfit_content(outfit, detail)
        if not content.strip():
            logger.info(f"装扮 {oname} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(oname)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存装扮: {oname}")
        return True

    except Exception as e:
        logger.exception(f"处理装扮 {oname} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取装扮列表。
    2. 遍历每个装扮，检查是否已爬取。
    3. 若未爬取，则处理。
    4. 成功则标记已爬取。
    5. 生成爬取报告。
    """
    outfits = get_outfit_list()
    if not outfits:
        logger.warning("未获取到装扮列表，退出")
        return

    stats = {
        "total": len(outfits),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    test_limit = 0  # 0 表示全量

    for idx, o in enumerate(outfits[:test_limit] if test_limit else outfits, start=1):
        oid = o["id"]
        oname = o["name"]
        logger.info(f"[{idx}/{len(outfits)}] 正在处理: {oname} (ID: {oid})")

        if is_crawled("outfit", oid):
            logger.info(f"  跳过 (ID {oid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_outfit(o)
        if success:
            mark_crawled("outfit", oid, oname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": oid, "name": oname})

        time.sleep(random.uniform(1, 2))

    report_path = os.path.join(LOG_DIR, "outfit_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("装扮模块爬取完成！")


if __name__ == "__main__":
    run()