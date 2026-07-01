"""
背包物品爬虫模块
从观测枢爬取所有背包物品的详细信息，包括道具类型、获取方式、星级、描述、用途、阅读内容等。
一个物品可能包含多个变体（不同来源/版本），也可能包含多章节阅读内容或表格数据。
增量策略：使用 crawled.json 记录已爬取的物品 ID，实现增量更新。
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
        logging.FileHandler(os.path.join(LOG_DIR, "item.log"), encoding='utf-8', mode='a'),
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
    解析 ext 中 filter.text 的 JSON 数组，提取道具类型、获取方式、星级。
    返回 dict。
    """
    result = {
        "item_type": "",   # 角色经验素材/任务道具/食材/小道具等
        "obtain": "",      # 任务获取/活动获取/地图探索等
        "star": ""         # 无/一星/二星/三星/四星/五星
    }
    try:
        tags = json.loads(filter_text)
        for tag in tags:
            if tag.startswith("道具类型/"):
                result["item_type"] = tag.split("/")[-1]
            elif tag.startswith("获取方式/"):
                result["obtain"] = tag.split("/")[-1]
            elif tag.startswith("星级/"):
                result["star"] = tag.split("/")[-1]
    except Exception as e:
        logger.warning(f"解析 filter 标签失败: {e}")
    return result


def extract_materials_text(html: str) -> str:
    """
    从获取方式的 HTML 中提取紧凑格式的文本。
    例如：<span>往事追迹·北</span>
    返回：往事追迹·北
    """
    if not html:
        return ""
    
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def clean_text_compact(text: str) -> str:
    """
    将多行文本压缩为单行，去除多余换行和空白。
    适用于描述、用途、获取方式等短文本。
    """
    if not text:
        return ""
    return " ".join(text.split())


# ========== 1. 获取物品列表 ==========
def get_item_list():
    """
    获取所有物品的 ID、名称、简介和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("item")   # 从配置文件读取频道 ID (13)
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
            logger.warning("未找到外层物品列表")
            return []

        # 查找 name 为 "背包" 的分类
        target_category = None
        for cat in outer_list:
            if cat.get("name") == "背包":
                target_category = cat
                break
        if not target_category:
            logger.warning("未找到「背包」分类")
            return []

        items = target_category.get("list", [])
        item_list = []
        for item in items:
            iid = str(item.get("content_id"))
            iname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if iid and iname:
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
                    logger.warning(f"解析物品 ext 失败: {e}")

                item_list.append({
                    "id": iid,
                    "name": iname,
                    "summary": summary,
                    "item_type": tags.get("item_type", ""),
                    "obtain": tags.get("obtain", ""),
                    "star": tags.get("star", "")
                })
        logger.info(f"成功获取 {len(item_list)} 个物品 (频道ID: {params['channel_id']})")
        for i, item in enumerate(item_list[:5], 1):
            logger.info(f"  {i}. {item['name']} (ID: {item['id']}) [{item['star']} {item['item_type']}]")
        return item_list
    except Exception as e:
        logger.exception("获取物品列表失败")
        return []


# ========== 2. 解析物品详情页 ==========
def parse_item_detail(page_data):
    """
    从物品详情页的 JSON 中提取所有变体的信息。
    返回 dict，包含 variants (list), reading_content (str), table_content (list)。
    """
    modules = page_data.get("modules", [])
    result = {
        "variants": [],       # material_base_info 变体列表
        "reading_content": "", # 所有 collapse_panel 阅读内容的合并
        "table_content": []   # multi_table 各 tab 的内容
    }

    # 临时存储阅读内容片段
    reading_parts = []

    for module in modules:
        comps = module.get("components", [])
        module_name = module.get("name", "")
        
        for comp in comps:
            comp_id = comp.get("component_id", "")
            
            # 1. 处理 material_base_info（变体信息）
            if comp_id == "material_base_info":
                try:
                    data = json.loads(comp.get("data", "{}"))
                    
                    variant_name = data.get("name", "")
                    if not variant_name:
                        continue
                    
                    # 提取获取方式（materials.value）
                    obtain = ""
                    materials = data.get("materials", {})
                    if materials.get("value"):
                        obtain = extract_materials_text(materials["value"])
                    
                    # 提取 attr
                    description = ""
                    purpose = ""
                    for attr in data.get("attr", []):
                        key = attr.get("key", "")
                        values = attr.get("value", [])
                        if not values:
                            continue
                        text = clean_html_to_text(values[0]) if values[0] else ""
                        if not text:
                            continue
                        if key == "描述":
                            description = clean_text_compact(text)
                        elif key == "用途":
                            purpose = clean_text_compact(text)
                    
                    # 只要有一个有效字段就保留
                    if description or purpose or obtain:
                        result["variants"].append({
                            "name": variant_name,
                            "description": description,
                            "purpose": purpose,
                            "obtain": obtain
                        })
                except Exception as e:
                    logger.warning(f"解析 material_base_info 失败: {e}")
                    continue
            
            # 2. 处理 collapse_panel（阅读章节）
            elif comp_id == "collapse_panel":
                try:
                    data = json.loads(comp.get("data", "{}"))
                    rich_text = data.get("rich_text", "")
                    if rich_text and rich_text.strip():
                        # 检查是否是空内容（只有 <p></p> 或空白）
                        text = clean_html_to_text(rich_text)
                        if text.strip():
                            # 如果有模块名（如"阅读（1/9）"），作为标题添加
                            if module_name and module_name.strip():
                                reading_parts.append(f"【{module_name}】")
                            reading_parts.append(text)
                except Exception as e:
                    logger.warning(f"解析 collapse_panel 失败: {e}")
            
            # 3. 处理 multi_table（表格数据）
            elif comp_id == "multi_table":
                try:
                    data = json.loads(comp.get("data", "{}"))
                    tables = data.get("tables", [])
                    for table in tables:
                        tab_name = table.get("tab_name", "")
                        rows = table.get("row", [])
                        header = table.get("header", [])
                        
                        # 寻找文案列的索引（通常是 "文案" 或最后一列）
                        # 但更可靠的是取每行的最后一个元素作为文案
                        # 因为 header 可能是 ["故事", "插图", "文案"] 或 ["曲调", "插图", "文案"]
                        # 文案列通常是最后一列
                        if not rows:
                            continue
                        
                        # 构建该 tab 的内容
                        tab_content = []
                        for row in rows:
                            if not row:
                                continue
                            # 取最后一个元素作为文案，倒数第二个作为标题
                            if len(row) >= 2:
                                title = clean_html_to_text(row[-2]) if len(row) >= 2 else ""
                                content = clean_html_to_text(row[-1]) if row[-1] else ""
                            else:
                                title = ""
                                content = clean_html_to_text(row[0]) if row else ""
                            
                            if content or title:
                                tab_content.append({
                                    "title": title,
                                    "content": content
                                })
                        
                        if tab_content:
                            result["table_content"].append({
                                "tab_name": tab_name,
                                "entries": tab_content
                            })
                except Exception as e:
                    logger.warning(f"解析 multi_table 失败: {e}")

    # 合并阅读内容
    if reading_parts:
        result["reading_content"] = "\n\n".join(reading_parts)

    return result


# ========== 3. 生成文件内容 ==========
def generate_item_content(item_info, detail):
    """
    根据物品信息和详情数据生成纯文本内容。
    参数：
        item_info (dict): 包含 id, name, summary, item_type, obtain, star
        detail (dict): 包含 variants, reading_content, table_content
    返回：
        str: 格式化后的纯文本内容
    """
    lines = []

    # 1. 基本信息（不包含获取方式，由变体提供更详细的信息）
    lines.append(f"物品名称：{item_info['name']}")
    if item_info.get("item_type"):
        lines.append(f"道具类型：{item_info['item_type']}")
    if item_info.get("star"):
        lines.append(f"星级：{item_info['star']}")
    if item_info.get("summary"):
        lines.append(f"简介：{item_info['summary']}")
    lines.append("")

    # 2. 各变体信息
    variants = detail.get("variants", [])
    for idx, variant in enumerate(variants):
        if not variant.get("name"):
            continue
        
        # 如果有多个变体，用标签区分
        if len(variants) > 1:
            lines.append(f"【版本 {idx+1}·{variant['name']}】")
        else:
            lines.append(f"【{variant['name']}】")
        
        if variant.get("description"):
            lines.append(f"描述：{variant['description']}")
        if variant.get("purpose"):
            lines.append(f"用途：{variant['purpose']}")
        if variant.get("obtain"):
            lines.append(f"获取方式：{variant['obtain']}")
        lines.append("")

    # 3. 阅读内容（如果有）
    if detail.get("reading_content"):
        lines.append("【阅读内容】")
        lines.append(detail["reading_content"])
        lines.append("")

    # 4. 表格内容（如果有）
    for table in detail.get("table_content", []):
        tab_name = table.get("tab_name", "")
        entries = table.get("entries", [])
        if not entries:
            continue
        
        if tab_name:
            lines.append(f"【{tab_name}】")
        else:
            lines.append("【记录内容】")
        
        for entry in entries:
            title = entry.get("title", "")
            content = entry.get("content", "")
            if title and content:
                lines.append(title)
                lines.append(content)
                lines.append("")
            elif content:
                lines.append(content)
                lines.append("")
        
        lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个物品 ==========
def process_item(item_info, output_dir="data/cleaned/item"):
    """
    处理单个物品：请求详情 API，解析内容，生成文件。
    参数：
        item_info (dict): 包含 id, name, summary, item_type, obtain, star
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
            logger.warning(f"物品 {item_name} 无页面数据")
            return False

        # 解析详情
        detail = parse_item_detail(page)

        # 检查是否有有效内容（至少有一个变体或阅读内容或表格内容）
        has_content = (
            detail.get("variants") or 
            detail.get("reading_content") or 
            detail.get("table_content")
        )
        if not has_content:
            logger.info(f"物品 {item_name} 无任何可提取内容")
            return False

        # 生成内容
        content = generate_item_content(item_info, detail)
        if not content.strip():
            logger.info(f"物品 {item_name} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(item_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存物品: {item_name}")
        return True

    except Exception as e:
        logger.exception(f"处理物品 {item_name} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取物品列表。
    2. 遍历每个物品，检查是否已爬取（基于 crawled.json）。
    3. 若未爬取，则调用 process_item 处理。
    4. 成功则标记已爬取，失败则记录失败信息。
    5. 生成爬取报告。
    """
    # 获取物品列表
    items = get_item_list()
    if not items:
        logger.warning("未获取到物品列表，退出")
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
        iid = item["id"]
        iname = item["name"]
        logger.info(f"[{idx}/{len(items)}] 正在处理: {iname} (ID: {iid})")

        # 增量检查
        if is_crawled("item", iid):
            logger.info(f"  跳过 (ID {iid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_item(item)
        if success:
            mark_crawled("item", iid, iname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": iid, "name": iname})

        # 控制请求频率
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "item_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("背包物品模块爬取完成！")


if __name__ == "__main__":
    run()