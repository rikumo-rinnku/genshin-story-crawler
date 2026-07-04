"""
冒险家协会（委托）爬虫模块
从观测枢爬取所有委托（每日委托、突发事件、声望任务等）的详细信息。
增量策略：使用 crawled.json 记录已爬取的委托 ID，实现增量更新。
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
        logging.FileHandler(os.path.join(LOG_DIR, "commission.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name: str) -> str:
    """清洗非法字符，生成安全的文件名"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_commission_ext(ext_str: str) -> tuple:
    """
    从 ext 中解析委托类型、区域、有无成就。
    返回 (commission_type, region, has_achievement)
    """
    commission_type = ""
    region = ""
    has_achievement = ""
    try:
        ext_data = json.loads(ext_str)
        # 结构通常为 {"c_55": {"filter": {"text": "[...]"}}}
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("委托类型/"):
                        commission_type = item.split("/")[-1]
                    elif item.startswith("区域/"):
                        region = item.split("/")[-1]
                    elif item.startswith("有无成就/"):
                        has_achievement = item.split("/")[-1]
                break
    except Exception as e:
        logger.warning(f"解析委托 ext 失败: {e}")
    return commission_type, region, has_achievement

def parse_interactive_dialogue(comp_data: Dict) -> str:
    """
    解析 interactive_dialogue 组件，提取对话和选项，返回纯文本。
    每个对话/选项条目独立成行，条目之间用空行分隔。
    说话人和对话内容在同一行，冒号保留。
    """
    list_data = comp_data.get("list", [])
    all_entries = []  # 存放每个独立条目的文本（已压缩内部换行）
    
    for item in list_data:
        root_id = item.get("root_id")
        child_ids = item.get("child_ids", {})
        contents = item.get("contents", {})
        if not root_id or not contents:
            continue

        def traverse(node_id, visited=None):
            if visited is None:
                visited = set()
            if node_id in visited or node_id not in contents:
                return
            visited.add(node_id)
            node = contents[node_id]
            option = node.get("option", "")
            dialogue = node.get("dialogue", "")
            
            # 处理选项
            if option:
                clean = clean_html_to_text(option)
                clean = " ".join(clean.split())  # 压缩内部换行
                all_entries.append(f"[选项] {clean}")
            
            # 处理对话
            if dialogue:
                clean = clean_html_to_text(dialogue)
                clean = " ".join(clean.split())  # 压缩内部换行
                all_entries.append(clean)
            
            for child_id in child_ids.get(node_id, []):
                traverse(child_id, visited)

        traverse(root_id)

    # 每个条目之间用空行分隔
    return "\n\n".join(all_entries)


# ========== 1. 获取委托列表 ==========
def get_commission_list():
    """
    获取所有委托的 ID、名称、类型、区域、有无成就。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("commission")  # 需要在 config/channels.json 中添加
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
            logger.warning("未找到外层委托列表")
            return []

        # 查找 name 为 "冒险家协会" 的分类（或直接取第一个，因为频道ID已指定）
        target_category = None
        for cat in outer_list:
            if cat.get("name") == "冒险家协会":
                target_category = cat
                break
        if not target_category:
            # 如果没找到，可能直接是列表本身，取第一个
            if outer_list:
                target_category = outer_list[0]
            else:
                logger.warning("未找到「冒险家协会」分类")
                return []

        items = target_category.get("list", [])
        commission_list = []
        for item in items:
            cid = str(item.get("content_id"))
            cname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if cid and cname:
                c_type, region, has_achievement = parse_commission_ext(ext_str)
                commission_list.append({
                    "id": cid,
                    "name": cname,
                    "summary": summary,
                    "commission_type": c_type,
                    "region": region,
                    "has_achievement": has_achievement
                })
        logger.info(f"成功获取 {len(commission_list)} 个委托 (频道ID: {params['channel_id']})")
        for i, c in enumerate(commission_list[:5], 1):
            logger.info(f"  {i}. {c['name']} (ID: {c['id']}) [{c['commission_type']}]")
        return commission_list
    except Exception as e:
        logger.exception("获取委托列表失败")
        return []


# ========== 2. 解析委托详情页 ==========
def parse_commission_detail(page_data: Dict) -> Dict:
    """
    从委托详情页的 JSON 中提取所有模块的内容。
    返回 dict，结构为 {"module_name": "content_text", ...}
    同时保留一个特殊键 "base_info" 用于基本信息。
    """
    modules = page_data.get("modules", [])
    result = {}

    for module in modules:
        module_name = module.get("name", "未命名模块")
        # ===== 新增：跳过奖励模块，减少无用数据 =====
        if module_name == "任务奖励":
            continue
        comps = module.get("components", [])
        if not comps:
            continue

        # 遍历该模块下的所有组件（通常只有一个）
        for comp in comps:
            comp_id = comp.get("component_id", "")
            raw_data = comp.get("data", "{}")
            try:
                data = json.loads(raw_data)
            except Exception:
                data = {}

            if comp_id == "base_info":
                # 基本信息列表
                list_data = data.get("list", [])
                parts = []
                for entry in list_data:
                    key = entry.get("key", "")
                    values = entry.get("value", [])
                    if values:
                        # 每个 value 是 HTML 字符串
                        cleaned_values = [clean_html_to_text(v) for v in values if v]
                        if cleaned_values:
                            parts.append(f"{key}：{'；'.join(cleaned_values)}")
                if parts:
                    result["base_info"] = "\n".join(parts)

            elif comp_id in ["collapse_panel", "rich_base_info"]:
                # 折叠面板或富文本基本信息
                rich_text = data.get("rich_text", "")
                if rich_text:
                    text = clean_html_to_text(rich_text)
                    if text.strip():
                        # 用模块名作为 key
                        key = module_name if module_name not in ["", "未命名模块"] else "内容"
                        # 如果 key 已存在，追加内容
                        if key in result:
                            result[key] += "\n\n" + text
                        else:
                            result[key] = text

            elif comp_id == "multi_table":
                # 表格：提取所有表格的文本
                tables = data.get("tables", [])
                for table in tables:
                    # 表格可能有多行，直接拼接 HTML 清洗
                    # 但我们也可以尝试解析 header 和 row，但简单起见，将整个表格的 html 拼接
                    # 注意：表格数据可能包含 rich_text 字段？实际样例中 data 没有直接的 rich_text，
                    # 但 row 里的每项都是 HTML 字符串。我们可以将整个 tables 转成字符串再清洗。
                    # 更好的做法：提取所有 cell 文本
                    rows_text = []
                    for row in table.get("row", []):
                        cell_texts = [clean_html_to_text(cell) for cell in row if cell]
                        rows_text.append(" | ".join(cell_texts))
                    if rows_text:
                        # 可以加上表头
                        header = table.get("header", [])
                        if header:
                            header_text = " | ".join([clean_html_to_text(h) for h in header])
                            rows_text.insert(0, header_text)
                        table_content = "\n".join(rows_text)
                        # 如果有模块名，用模块名；否则用 "表格"
                        key = module_name if module_name not in ["", "未命名模块"] else "表格"
                        if key in result:
                            result[key] += "\n\n" + table_content
                        else:
                            result[key] = table_content

            elif comp_id == "interactive_dialogue":
                # 对话树
                dialogue_text = parse_interactive_dialogue(data)
                if dialogue_text:
                    key = module_name if module_name not in ["", "未命名模块"] else "对话"
                    if key in result:
                        result[key] += "\n\n" + dialogue_text
                    else:
                        result[key] = dialogue_text

            elif comp_id == "map_desc":
                # 地图描述，通常只含图片，忽略文本
                # 但如果有图注，可以提取，但样例中无文本
                pass

            else:
                # 其他未知组件，尝试提取通用字段（如 rich_text 或 list）
                rich_text = data.get("rich_text", "")
                if rich_text:
                    text = clean_html_to_text(rich_text)
                    if text.strip():
                        key = module_name if module_name not in ["", "未命名模块"] else comp_id
                        if key in result:
                            result[key] += "\n\n" + text
                        else:
                            result[key] = text
                else:
                    # 尝试 list
                    list_data = data.get("list", [])
                    if list_data:
                        parts = []
                        for entry in list_data:
                            # 可能是键值对
                            if isinstance(entry, dict):
                                key = entry.get("key", "")
                                values = entry.get("value", [])
                                if values:
                                    cleaned = [clean_html_to_text(v) for v in values if v]
                                    if cleaned:
                                        parts.append(f"{key}：{'；'.join(cleaned)}")
                            else:
                                # 简单列表
                                parts.append(clean_html_to_text(str(entry)))
                        if parts:
                            key = module_name if module_name not in ["", "未命名模块"] else comp_id
                            if key in result:
                                result[key] += "\n\n" + "\n".join(parts)
                            else:
                                result[key] = "\n".join(parts)

    return result


# ========== 3. 生成文件内容 ==========
def generate_commission_content(commission_info: Dict, detail: Dict) -> str:
    """
    根据委托信息和详情数据生成纯文本内容。
    """
    lines = []
    # 基本信息
    lines.append(f"委托名称：{commission_info['name']}")
    if commission_info.get('commission_type'):
        lines.append(f"委托类型：{commission_info['commission_type']}")
    if commission_info.get('region'):
        lines.append(f"区域：{commission_info['region']}")
    if commission_info.get('has_achievement'):
        lines.append(f"有无成就：{commission_info['has_achievement']}")
    if commission_info.get('summary'):
        lines.append(f"简介：{commission_info['summary']}")
    lines.append("")

    # 详情内容（按模块分组）
    for key, content in detail.items():
        if key == "base_info":
            lines.append("【基本信息】")
            lines.append(content)
            lines.append("")
        else:
            # 其他模块用模块名作为标题
            lines.append(f"【{key}】")
            lines.append(content)
            lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个委托 ==========
def process_commission(commission: Dict, output_dir="data/cleaned/commission") -> bool:
    """
    处理单个委托：请求详情 API，解析内容，生成文件。
    """
    cid = commission["id"]
    cname = commission["name"]

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
            logger.warning(f"委托 {cname} 无页面数据")
            return False

        # 解析详情
        detail = parse_commission_detail(page)

        # 如果没有任何有效内容，视为失败
        if not detail:
            logger.info(f"委托 {cname} 无任何可提取内容")
            return False

        # 生成内容
        content = generate_commission_content(commission, detail)
        if not content.strip():
            logger.info(f"委托 {cname} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(cname)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存委托: {cname}")
        return True

    except Exception as e:
        logger.exception(f"处理委托 {cname} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取委托列表。
    2. 遍历每个委托，检查是否已爬取。
    3. 若未爬取，则处理。
    4. 成功则标记已爬取。
    5. 生成爬取报告。
    """
    commissions = get_commission_list()
    if not commissions:
        logger.warning("未获取到委托列表，退出")
        return

    stats = {
        "total": len(commissions),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    test_limit = 10  # 0 表示全量

    for idx, c in enumerate(commissions[:test_limit] if test_limit else commissions, start=1):
        cid = c["id"]
        cname = c["name"]
        logger.info(f"[{idx}/{len(commissions)}] 正在处理: {cname} (ID: {cid})")

        if is_crawled("commission", cid):
            logger.info(f"  跳过 (ID {cid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_commission(c)
        if success:
            mark_crawled("commission", cid, cname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": cid, "name": cname})

        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "commission_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("冒险家协会模块爬取完成！")


if __name__ == "__main__":
    run()