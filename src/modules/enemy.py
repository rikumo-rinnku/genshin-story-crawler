"""
敌人剧情爬虫模块
从观测枢爬取所有敌人的详细信息，包括名称、类型、元素、攻击方式、简介、背景故事（含折叠面板展开）、位置信息。
增量策略：使用 crawled.json 记录已爬取的敌人 ID，实现增量更新。
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
from src.core.storage import is_crawled, mark_crawled, save_text, sanitize_filename

# ========== 日志配置 ==========
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "enemy.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def expand_details_in_html(html: str) -> str:
    """
    将 HTML 中所有的 <details> 折叠面板展开，将其内容替换为可见文本。
    保留 summary 作为小标题，并将内部的 div[data-type="detailsContent"] 内容提取出来，
    使 clean_html_to_text 能够获取全部文本。
    支持嵌套 details。
    """
    if not html:
        return html

    soup = BeautifulSoup(html, 'lxml')

    def expand_details_recursively(element):
        """递归处理 element 内的所有 details 标签"""
        # 查找当前元素下的所有 details 标签（直接或间接）
        details_list = element.find_all('details', recursive=True)
        for details in details_list:
            # 获取 summary 文本作为标题
            summary_tag = details.find('summary')
            title = summary_tag.get_text(strip=True) if summary_tag else "详情"

            # 获取内容容器：通常是 div[data-type="detailsContent"]，也可能直接是 div
            content_div = details.find('div', attrs={'data-type': 'detailsContent'})
            if not content_div:
                # 尝试找普通的 div
                content_div = details.find('div')
            if content_div:
                # 递归处理内容中的 details（嵌套）
                expand_details_recursively(content_div)
                # 提取内容 HTML
                content_html = str(content_div)
            else:
                # 如果没有内容 div，则提取 details 内部的全部文本（但保留结构）
                content_html = ''.join(str(child) for child in details.children if child.name != 'summary')

            # 构建新的 HTML 片段：标题 + 内容
            # 用 <p> 包裹标题，使其在文本中独立一行
            new_html = f'<p><strong>{title}</strong></p>{content_html}'

            # 替换原来的 details 标签
            details.replace_with(BeautifulSoup(new_html, 'lxml'))

    expand_details_recursively(soup)
    return str(soup)


def parse_filter_tags(filter_text: str) -> dict:
    """
    解析 ext 中 filter.text 的 JSON 数组，提取类型、元素、攻击方式。
    返回 dict，包含 type, elements, attack_types 列表。
    """
    result = {
        "type": "",        # BOSS / 精英 / 普通
        "elements": [],    # 火、水、风、雷、冰、岩、草、无
        "attack_types": [] # 近战、远程、混合
    }
    try:
        tags = json.loads(filter_text)  # 例如 ["类型/BOSS", "元素/火", "元素/雷", "攻击方式/混合"]
        for tag in tags:
            if tag.startswith("类型/"):
                result["type"] = tag.split("/")[-1]
            elif tag.startswith("元素/"):
                elem = tag.split("/")[-1]
                if elem and elem not in result["elements"]:
                    result["elements"].append(elem)
            elif tag.startswith("攻击方式/"):
                atk = tag.split("/")[-1]
                if atk and atk not in result["attack_types"]:
                    result["attack_types"].append(atk)
    except Exception as e:
        logger.warning(f"解析 filter 标签失败: {e}")
    return result


# ========== 1. 获取敌人列表 ==========
def get_enemy_list():
    """
    获取所有敌人的 ID、名称、简介、分类标签等。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("enemy")   # 从配置文件读取频道 ID (6)
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
            logger.warning("未找到外层敌人列表")
            return []

        # 查找 name 为 "敌人" 的分类
        enemy_category = None
        for cat in outer_list:
            if cat.get("name") == "敌人":
                enemy_category = cat
                break
        if not enemy_category:
            logger.warning("未找到「敌人」分类")
            return []

        items = enemy_category.get("list", [])
        enemy_list = []
        for item in items:
            eid = str(item.get("content_id"))
            ename = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if eid and ename:
                # 解析 ext 中的 filter 标签
                tags = {}
                try:
                    ext_data = json.loads(ext_str)
                    # 结构为 {"c_6": {"filter": {"text": "[...]"}, ...}}
                    for key, value in ext_data.items():
                        if key.startswith("c_"):
                            filter_text = value.get("filter", {}).get("text", "[]")
                            tags = parse_filter_tags(filter_text)
                            break
                except Exception as e:
                    logger.warning(f"解析敌人 ext 失败: {e}")

                enemy_list.append({
                    "id": eid,
                    "name": ename,
                    "summary": summary,
                    "type": tags.get("type", ""),
                    "elements": tags.get("elements", []),
                    "attack_types": tags.get("attack_types", [])
                })
        logger.info(f"成功获取 {len(enemy_list)} 个敌人 (频道ID: {params['channel_id']})")
        for i, e in enumerate(enemy_list[:5], 1):
            logger.info(f"  {i}. {e['name']} (ID: {e['id']}) [{e['type']}]")
        return enemy_list
    except Exception as e:
        logger.exception("获取敌人列表失败")
        return []



# ========== 2. 解析敌人详情页 ==========
def parse_enemy_detail(page_data):
    """
    从敌人详情页的 JSON 中提取背景故事和位置信息。
    返回 dict，包含 background (str), location (str)。
    """
    modules = page_data.get("modules", [])
    result = {
        "background": "",   # 背景故事（展开后）
        "location": ""      # 位置描述
    }

    for module in modules:
        comps = module.get("components", [])
        if not comps:
            continue
        
        # 更可靠的方式：用 module name 来识别模块
        module_name = module.get("name", "")
        
        # 背景故事 (good_desc) - module name 为 "背景故事"
        if module_name == "背景故事":
            for comp in comps:
                if comp.get("component_id") == "good_desc":
                    try:
                        info = json.loads(comp.get("data", "{}"))
                        rich_text = info.get("rich_text", "")
                        if rich_text:
                            expanded_html = expand_details_in_html(rich_text)
                            clean_text = clean_html_to_text(expanded_html)
                            if clean_text.strip():
                                result["background"] = clean_text
                    except Exception as e:
                        logger.warning(f"解析背景故事失败: {e}")
                    break
        
        # 位置信息 (timeline_base_info) - module name 为 "位置导览"
        elif module_name == "位置导览":
            for comp in comps:
                if comp.get("component_id") == "timeline_base_info":
                    try:
                        info = json.loads(comp.get("data", "{}"))
                        list_data = info.get("list", [])
                        location_parts = []
                        for group in list_data:
                            attr_list = group.get("attr", [])
                            for attr in attr_list:
                                # 不再依赖 key 的值，直接提取所有 value
                                values = attr.get("value", [])
                                for v in values:
                                    if v:
                                        clean_text = clean_html_to_text(v)
                                        if clean_text.strip():
                                            location_parts.append(clean_text)
                        if location_parts:
                            result["location"] = "\n".join(location_parts)
                    except Exception as e:
                        logger.warning(f"解析位置导览失败: {e}")
                    break

    return result



# ========== 3. 生成文件内容 ==========
def generate_enemy_content(enemy_info, detail):
    """
    根据敌人信息和详情数据生成纯文本内容。
    参数：
        enemy_info (dict): 包含 id, name, summary, type, elements, attack_types
        detail (dict): 包含 background, location
    返回：
        str: 格式化后的纯文本内容
    """
    lines = []

    # 1. 基本信息
    lines.append(f"敌人名称：{enemy_info['name']}")
    if enemy_info.get("type"):
        lines.append(f"类型：{enemy_info['type']}")
    if enemy_info.get("elements"):
        elements_str = "、".join(enemy_info["elements"])
        lines.append(f"元素：{elements_str}")
    if enemy_info.get("attack_types"):
        atk_str = "、".join(enemy_info["attack_types"])
        lines.append(f"攻击方式：{atk_str}")
    if enemy_info.get("summary"):
        lines.append(f"简介：{enemy_info['summary']}")
    lines.append("")  # 空行分隔

    # 2. 背景故事（核心）
    if detail.get("background"):
        lines.append("【背景故事】")
        lines.append(detail["background"])
        lines.append("")

    # 3. 位置信息
    if detail.get("location"):
        # 压缩所有换行和多余空白为单个空格，让句子连贯
        clean_location = " ".join(detail["location"].split())
        lines.append("【位置信息】")
        lines.append(clean_location)
        lines.append("")

    # 去除末尾多余换行
    return "\n".join(lines).strip()


# ========== 4. 处理单个敌人 ==========
def process_enemy(enemy_info, output_dir="data/cleaned/enemy"):
    """
    处理单个敌人：请求详情 API，解析内容，生成文件。
    参数：
        enemy_info (dict): 包含敌人的 id, name, summary, type, elements, attack_types
        output_dir (str): 输出目录路径
    返回：
        bool: 成功返回 True，失败返回 False
    """
    enemy_id = enemy_info["id"]
    enemy_name = enemy_info["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": enemy_id,
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
            logger.warning(f"敌人 {enemy_name} 无页面数据")
            return False

        # 解析详情
        detail = parse_enemy_detail(page)

        # 如果没有任何有效内容（至少要有背景故事或位置），视为失败
        if not detail.get("background") and not detail.get("location"):
            logger.info(f"敌人 {enemy_name} 无任何可提取内容")
            return False

        # 生成内容
        content = generate_enemy_content(enemy_info, detail)
        if not content.strip():
            logger.info(f"敌人 {enemy_name} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = sanitize_filename(enemy_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存敌人: {enemy_name}")
        return True

    except Exception as e:
        logger.exception(f"处理敌人 {enemy_name} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取敌人列表。
    2. 遍历每个敌人，检查是否已爬取（基于 crawled.json）。
    3. 若未爬取，则调用 process_enemy 处理。
    4. 成功则标记已爬取，失败则记录失败信息。
    5. 生成爬取报告。
    """
    # 获取敌人列表
    enemies = get_enemy_list()
    if not enemies:
        logger.warning("未获取到敌人列表，退出")
        return

    # 统计信息
    stats = {
        "total": len(enemies),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    # 测试限制（可调整）
    test_limit = 0  # 设置为 0 或 None 表示全量

    for idx, enemy in enumerate(enemies[:test_limit] if test_limit else enemies, start=1):
        eid = enemy["id"]
        ename = enemy["name"]
        logger.info(f"[{idx}/{len(enemies)}] 正在处理: {ename} (ID: {eid})")

        # 增量检查
        if is_crawled("enemy", eid):
            logger.info(f"  跳过 (ID {eid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_enemy(enemy)
        if success:
            mark_crawled("enemy", eid, ename)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": eid, "name": ename})

        # 控制请求频率
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "enemy_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("敌人模块爬取完成！")


if __name__ == "__main__":
    run()