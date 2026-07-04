"""
洞天（摆设/家具）爬虫模块
从观测枢爬取所有洞天摆设的详细信息，包括分类标签、物品描述、获取途径和相关套装。
增量策略：使用 crawled.json 记录已爬取的摆设 ID，实现增量更新。
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
        logging.FileHandler(os.path.join(LOG_DIR, "furnishing.log"), encoding='utf-8', mode='a'),
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
    # 去除首尾空白字符
    name = name.strip()
    # 替换 Windows 非法字符
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    # 将剩余的空白字符（空格、制表符、换行符等）替换为下划线
    name = re.sub(r'\s+', '_', name)
    return name

def parse_furnishing_ext(ext_str: str) -> tuple:
    """
    从 ext 中解析摆设的分类标签。
    返回 (region, furn_type, quality, obtain_method, blueprint_obtain)
    """
    region = ""
    furn_type = ""
    quality = ""
    obtain_method = ""
    blueprint_obtain = ""

    try:
        ext_data = json.loads(ext_str)
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("区域/"):
                        region = item.split("/")[-1]
                    elif item.startswith("类型/"):
                        furn_type = item.split("/")[-1]
                    elif item.startswith("品质/"):
                        quality = item.split("/")[-1]
                    elif item.startswith("摆设获取方式/"):
                        obtain_method = item.split("/")[-1]
                    elif item.startswith("图纸获取方式/"):
                        blueprint_obtain = item.split("/")[-1]
                break
    except Exception as e:
        logger.warning(f"解析摆设 ext 失败: {e}")

    return region, furn_type, quality, obtain_method, blueprint_obtain


# ========== 1. 获取摆设列表 ==========
def get_furnishing_list():
    """
    获取所有摆设的 ID、名称、摘要和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("furnishing")
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
            logger.warning("未找到外层摆设列表")
            return []

        target_category = None
        for cat in outer_list:
            if cat.get("name") == "洞天":
                target_category = cat
                break
        if not target_category:
            if outer_list:
                target_category = outer_list[0]
            else:
                logger.warning("未找到「洞天」分类")
                return []

        items = target_category.get("list", [])
        furnishing_list = []
        for item in items:
            fid = str(item.get("content_id"))
            fname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if fid and fname:
                region, f_type, quality, obtain, blueprint = parse_furnishing_ext(ext_str)
                furnishing_list.append({
                    "id": fid,
                    "name": fname,
                    "summary": summary,
                    "region": region,
                    "type": f_type,
                    "quality": quality,
                    "obtain_method": obtain,
                    "blueprint_obtain": blueprint
                })
        logger.info(f"成功获取 {len(furnishing_list)} 个摆设 (频道ID: {params['channel_id']})")
        for i, f in enumerate(furnishing_list[:5], 1):
            logger.info(f"  {i}. {f['name']} (ID: {f['id']}) [{f['type']}]")
        return furnishing_list
    except Exception as e:
        logger.exception("获取摆设列表失败")
        return []


# ========== 2. 解析摆设详情页 ==========
def parse_furnishing_detail(page_data: Dict) -> Dict:
    """
    从摆设详情页中提取：
      - 物品描述（good_desc.rich_text）
      - 获取途径（good_desc.attr）
      - 相关套装（rich_base_info 中的“相关套装”）
    返回 dict。
    """
    result = {
        "description": "",
        "obtain_info": [],      # 来自 good_desc.attr 的键值对
        "related_sets": ""      # 相关套装
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

            # 1. 物品描述模块
            if module_name == "物品描述" and comp_id == "good_desc":
                # 提取 rich_text
                rich_text = data.get("rich_text", "")
                if rich_text:
                    result["description"] = clean_html_to_text(rich_text)
                # 提取 attr（获取途径）
                attr_list = data.get("attr", [])
                for attr in attr_list:
                    key = attr.get("key", "")
                    values = attr.get("value", [])
                    if key and values:
                        cleaned = [clean_html_to_text(v) for v in values if v]
                        if cleaned:
                            result["obtain_info"].append({
                                "key": key.strip("：:"),
                                "value": "；".join(cleaned)
                            })

            # 2. 基础属性模块（提取相关套装）
            elif module_name == "基础属性" and comp_id == "rich_base_info":
                list_data = data.get("list", [])
                # 防御：如果 list 为 None 或空，跳过
                if not list_data:
                    continue
                for entry in list_data:
                    key = entry.get("key", "")
                    values = entry.get("value", [])
                    if "套装" in key and values:
                        cleaned = [clean_html_to_text(v) for v in values if v]
                        if cleaned:
                            result["related_sets"] = "；".join(cleaned)
                        break

    return result

# ========== 3. 生成文件内容 ==========
def generate_furnishing_content(furnishing_info: Dict, detail: Dict) -> str:
    """
    根据摆设信息和详情数据生成纯文本内容。
    """
    lines = []

    # 基本信息
    lines.append(f"摆设名称：{furnishing_info['name']}")
    if furnishing_info.get('region'):
        lines.append(f"区域：{furnishing_info['region']}")
    if furnishing_info.get('type'):
        lines.append(f"类型：{furnishing_info['type']}")
    if furnishing_info.get('quality'):
        lines.append(f"品质：{furnishing_info['quality']}")
    if furnishing_info.get('obtain_method'):
        lines.append(f"摆设获取方式：{furnishing_info['obtain_method']}")
    if furnishing_info.get('blueprint_obtain'):
        lines.append(f"图纸获取方式：{furnishing_info['blueprint_obtain']}")
    if detail.get('related_sets'):
        lines.append(f"相关套装：{detail['related_sets']}")
    lines.append("")

    # 物品描述（核心剧情文本）
    if detail.get('description'):
        lines.append("【物品描述】")
        lines.append(detail['description'])
        lines.append("")

    # 获取途径（来自 good_desc.attr）
    if detail.get('obtain_info'):
        lines.append("【获取途径】")
        for item in detail['obtain_info']:
            lines.append(f"{item['key']}：{item['value']}")
        lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个摆设 ==========
def process_furnishing(furnishing: Dict, output_dir="data/cleaned/furnishing") -> bool:
    """
    处理单个摆设：请求详情 API，解析内容，生成文件。
    """
    fid = furnishing["id"]
    fname = furnishing["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": fid,
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
            logger.warning(f"摆设 {fname} 无页面数据")
            return False

        # 解析详情
        detail = parse_furnishing_detail(page)

        # 如果没有物品描述，视为失败
        if not detail.get("description"):
            logger.info(f"摆设 {fname} 无物品描述，跳过")
            return False

        # 生成内容
        content = generate_furnishing_content(furnishing, detail)
        if not content.strip():
            logger.info(f"摆设 {fname} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(fname)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存摆设: {fname}")
        return True

    except Exception as e:
        logger.exception(f"处理摆设 {fname} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取摆设列表。
    2. 遍历每个摆设，检查是否已爬取。
    3. 若未爬取，则处理。
    4. 成功则标记已爬取。
    5. 生成爬取报告。
    """
    furnishings = get_furnishing_list()
    if not furnishings:
        logger.warning("未获取到摆设列表，退出")
        return

    stats = {
        "total": len(furnishings),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    test_limit = 0  # 0 表示全量

    for idx, f in enumerate(furnishings[:test_limit] if test_limit else furnishings, start=1):
        fid = f["id"]
        fname = f["name"]
        logger.info(f"[{idx}/{len(furnishings)}] 正在处理: {fname} (ID: {fid})")

        if is_crawled("furnishing", fid):
            logger.info(f"  跳过 (ID {fid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_furnishing(f)
        if success:
            mark_crawled("furnishing", fid, fname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": fid, "name": fname})

        # time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "furnishing_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("洞天模块爬取完成！")


if __name__ == "__main__":
    run()