"""
NPC & 商店爬虫模块
从观测枢爬取所有 NPC 和商店的详细信息，包括地区、类型、背景故事、对话等。
增量策略：使用 crawled.json 记录已爬取的 NPC ID，实现增量更新。
"""

import json
import re
import time
import random
import logging
import os
from typing import Dict, List, Any, Optional

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
        logging.FileHandler(os.path.join(LOG_DIR, "npc.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name: str) -> str:
    """清洗非法字符，生成安全的文件名"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_npc_ext(ext_str: str) -> tuple:
    """
    从 ext 中解析 NPC 的分类标签。
    返回 (region, npc_type, has_card_challenge, has_dialogue_reward, special_effect)
    """
    region = ""
    npc_type = ""
    has_card_challenge = ""
    has_dialogue_reward = ""
    special_effect = ""

    try:
        ext_data = json.loads(ext_str)
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("地区/"):
                        region = item.split("/")[-1]
                    elif item.startswith("类型/"):
                        npc_type = item.split("/")[-1]
                    elif item.startswith("牌手挑战/"):
                        has_card_challenge = item.split("/")[-1]
                    elif item.startswith("对话奖励/"):
                        has_dialogue_reward = item.split("/")[-1]
                    elif item.startswith("特殊效果/"):
                        special_effect = item.split("/")[-1]
                break
    except Exception as e:
        logger.warning(f"解析 NPC ext 失败: {e}")

    return region, npc_type, has_card_challenge, has_dialogue_reward, special_effect

def parse_interactive_dialogue(comp_data: Dict) -> str:
    """
    解析 interactive_dialogue 组件，提取对话和选项，返回纯文本。
    支持两种数据结构：
      1. 标准格式：{"list": [{"root_id": "...", "child_ids": {...}, "contents": {...}}]}
      2. 扁平格式：{"root_id": "...", "child_ids": {...}, "contents": {...}, "list": null}
    """
    list_data = comp_data.get("list")
    
    # 如果 list 为 None 或空列表，尝试使用顶层字段构造
    if not list_data:
        root_id = comp_data.get("root_id")
        contents = comp_data.get("contents")
        child_ids = comp_data.get("child_ids", {})
        if root_id and contents:
            # 构造一个虚拟的 list 项
            list_data = [{
                "root_id": root_id,
                "child_ids": child_ids,
                "contents": contents
            }]
        else:
            return ""
    
    # 如果 list_data 是 None 或空列表（但已经有顶层数据且在上面被处理了）
    if not list_data:
        return ""

    all_entries = []
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

            if option:
                clean = clean_html_to_text(option)
                clean = " ".join(clean.split())
                all_entries.append(f"[选项] {clean}")

            if dialogue:
                clean = clean_html_to_text(dialogue)
                clean = " ".join(clean.split())
                all_entries.append(clean)

            for child_id in child_ids.get(node_id, []):
                traverse(child_id, visited)

        traverse(root_id)

    return "\n\n".join(all_entries)


# ========== 1. 获取 NPC 列表 ==========
def get_npc_list():
    """
    获取所有 NPC 的 ID、名称和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("npc")
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
            logger.warning("未找到外层 NPC 列表")
            return []

        # 查找 name 为 "NPC&商店" 的分类
        target_category = None
        for cat in outer_list:
            if cat.get("name") == "NPC&商店":
                target_category = cat
                break
        if not target_category:
            # 如果没找到，取第一个（因为频道 ID 已指定）
            if outer_list:
                target_category = outer_list[0]
            else:
                logger.warning("未找到「NPC&商店」分类")
                return []

        items = target_category.get("list", [])
        npc_list = []
        for item in items:
            nid = str(item.get("content_id"))
            nname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if nid and nname:
                region, npc_type, has_card, has_reward, special = parse_npc_ext(ext_str)
                npc_list.append({
                    "id": nid,
                    "name": nname,
                    "summary": summary,
                    "region": region,
                    "type": npc_type,
                    "has_card_challenge": has_card,
                    "has_dialogue_reward": has_reward,
                    "special_effect": special
                })
        logger.info(f"成功获取 {len(npc_list)} 个 NPC (频道ID: {params['channel_id']})")
        for i, n in enumerate(npc_list[:5], 1):
            logger.info(f"  {i}. {n['name']} (ID: {n['id']}) [{n['region']}]")
        return npc_list
    except Exception as e:
        logger.exception("获取 NPC 列表失败")
        return []


# ========== 2. 解析 NPC 详情页 ==========
def parse_npc_detail(page_data: Dict) -> Dict:
    """
    从 NPC 详情页的 JSON 中提取所有模块的内容。
    返回 dict，结构为 {"module_name": "content_text", ...}
    """
    modules = page_data.get("modules", [])
    result = {}

    for module in modules:
        module_name = module.get("name", "未命名模块")
        # 跳过同伴赠礼模块（好感套装表格，RAG 不需要）
        if module_name == "同伴赠礼":
            continue
        comps = module.get("components", [])
        if not comps:
            continue

        for comp in comps:
            comp_id = comp.get("component_id", "")
            raw_data = comp.get("data", "{}")
            try:
                data = json.loads(raw_data)
            except Exception:
                data = {}

            if comp_id == "npc_base_info":
                # NPC 基本信息（性别、位置、职业、功能等）
                parts = []
                attr_list = data.get("attr", [])
                for attr in attr_list:
                    key = attr.get("key", "")
                    values = attr.get("value", [])
                    if key and values:
                        cleaned = [clean_html_to_text(v) for v in values if v]
                        if cleaned:
                            parts.append(f"{key}：{'；'.join(cleaned)}")
                extra_attr_list = data.get("extra_attr", [])
                for attr in extra_attr_list:
                    key = attr.get("key", "")
                    values = attr.get("value", [])
                    if key and values:
                        cleaned = [clean_html_to_text(v) for v in values if v]
                        if cleaned:
                            parts.append(f"{key}：{'；'.join(cleaned)}")
                if parts:
                    result["基本信息"] = "\n".join(parts)

            elif comp_id == "base_info":
                # 普通基本信息键值对（备用）
                list_data = data.get("list", [])
                parts = []
                for entry in list_data:
                    key = entry.get("key", "")
                    values = entry.get("value", [])
                    if values:
                        cleaned_values = [clean_html_to_text(v) for v in values if v]
                        if cleaned_values:
                            parts.append(f"{key}：{'；'.join(cleaned_values)}")
                if parts:
                    result["基本信息"] = "\n".join(parts)
        
            elif comp_id in ["collapse_panel", "rich_base_info"]:
                rich_text = data.get("rich_text", "")
                if rich_text:
                    # 检测是否包含表格
                    if "<table" in rich_text.lower():
                        try:
                            soup = BeautifulSoup(rich_text, 'lxml')
                            tables = soup.find_all('table')
                            table_texts = []
                            for table in tables:
                                rows = table.find_all('tr')
                                if not rows:
                                    continue
                                
                                # 第一步：提取所有行的单元格信息（文本、rowspan、colspan）
                                parsed_rows = []
                                max_cols = 0
                                for tr in rows:
                                    cells = tr.find_all(['th', 'td'])
                                    row_cells = []
                                    col_idx = 0
                                    for cell in cells:
                                        text = clean_html_to_text(str(cell)).strip()
                                        rowspan = int(cell.get('rowspan', 1))
                                        colspan = int(cell.get('colspan', 1))
                                        row_cells.append({
                                            'text': text,
                                            'rowspan': rowspan,
                                            'colspan': colspan
                                        })
                                        col_idx += colspan
                                    parsed_rows.append(row_cells)
                                    max_cols = max(max_cols, col_idx)
                                
                                # 第二步：展开 rowspan/colspan，构建规整的二维数组
                                expanded_rows = []
                                pending = {}  # col -> remaining rows for rowspan
                                
                                for row_cells in parsed_rows:
                                    expanded_row = [''] * max_cols
                                    col_idx = 0
                                    for cell in row_cells:
                                        # 跳过被 rowspan 占用的列
                                        while col_idx < max_cols and col_idx in pending and pending[col_idx] > 0:
                                            pending[col_idx] -= 1
                                            if pending[col_idx] == 0:
                                                del pending[col_idx]
                                            col_idx += 1
                                        
                                        # 如果 col_idx 超出当前行长度，扩展行
                                        if col_idx >= len(expanded_row):
                                            expanded_row.extend([''] * (col_idx - len(expanded_row) + 1))
                                        
                                        # 填充单元格内容（处理 colspan）
                                        for c in range(cell['colspan']):
                                            if col_idx + c < len(expanded_row):
                                                expanded_row[col_idx + c] = cell['text']
                                            else:
                                                expanded_row.append(cell['text'])
                                        
                                        # 记录 rowspan
                                        if cell['rowspan'] > 1:
                                            pending[col_idx] = cell['rowspan'] - 1
                                        
                                        col_idx += cell['colspan']
                                    
                                    # 补齐行长度到 max_cols
                                    while len(expanded_row) < max_cols:
                                        expanded_row.append('')
                                    expanded_rows.append(expanded_row)
                                
                                # 格式化输出
                                formatted_rows = []
                                for row in expanded_rows:
                                    if any(cell.strip() for cell in row):
                                        formatted_rows.append(" | ".join(row))
                                if formatted_rows:
                                    table_texts.append("\n".join(formatted_rows))
                            
                            if table_texts:
                                text = "\n\n".join(table_texts)
                            else:
                                text = clean_html_to_text(rich_text)
                        except Exception as e:
                            # 如果表格解析失败，回退到简单清洗
                            logger.warning(f"表格解析失败，回退到简单清洗: {e}")
                            text = clean_html_to_text(rich_text)
                    else:
                        text = clean_html_to_text(rich_text)
                    
                    if text.strip():
                        key = module_name if module_name not in ["", "未命名模块"] else "内容"
                        if key in result:
                            result[key] += "\n\n" + text
                        else:
                            result[key] = text
            
            elif comp_id == "multi_table":
                tables = data.get("tables", [])
                for table in tables:
                    # 获取表头
                    header = table.get("header", [])
                    header_text = [clean_html_to_text(h) for h in header if h]
                    rows = table.get("row", [])
                    
                    # 如果表头为空，尝试从第一行数据推断列数，但这种情况较少见
                    if not header_text and rows:
                        # 取第一行，按单元格数量作为列数
                        first_row = rows[0] if rows else []
                        header_text = [f"列{i+1}" for i in range(len(first_row))]
                    
                    col_count = len(header_text)
                    
                    # 提取所有行数据
                    all_rows_text = []
                    # 如果有表头，先加入表头
                    if header_text:
                        all_rows_text.append(" | ".join(header_text))
                    
                    for row in rows:
                        # 每个单元格单独清洗
                        cells = [clean_html_to_text(cell) if cell else "" for cell in row]
                        # 如果单元格数量少于列数，补空
                        while len(cells) < col_count:
                            cells.append("")
                        # 如果单元格数量多于列数，截断（一般不会发生）
                        cells = cells[:col_count]
                        all_rows_text.append(" | ".join(cells))
                    
                    table_content = "\n".join(all_rows_text)
                    if table_content.strip():
                        key = module_name if module_name not in ["", "未命名模块"] else "表格"
                        if key in result:
                            result[key] += "\n\n" + table_content
                        else:
                            result[key] = table_content
            
            elif comp_id == "interactive_dialogue":
                dialogue_text = parse_interactive_dialogue(data)
                if dialogue_text:
                    key = module_name if module_name not in ["", "未命名模块"] else "对话"
                    if key in result:
                        result[key] += "\n\n" + dialogue_text
                    else:
                        result[key] = dialogue_text

            elif comp_id in ["map_desc", "card_group_info"]:
                # 地图描述（纯图片）和卡片组（纯链接列表），忽略文本内容
                pass

            else:
                # 未知组件，尝试提取 rich_text 或 list
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
                    list_data = data.get("list", [])
                    if list_data:
                        parts = []
                        for entry in list_data:
                            if isinstance(entry, dict):
                                key = entry.get("key", "")
                                values = entry.get("value", [])
                                if values:
                                    cleaned = [clean_html_to_text(v) for v in values if v]
                                    if cleaned:
                                        parts.append(f"{key}：{'；'.join(cleaned)}")
                            else:
                                parts.append(clean_html_to_text(str(entry)))
                        if parts:
                            key = module_name if module_name not in ["", "未命名模块"] else comp_id
                            if key in result:
                                result[key] += "\n\n" + "\n".join(parts)
                            else:
                                result[key] = "\n".join(parts)

    return result



# ========== 3. 生成文件内容 ==========
def generate_npc_content(npc_info: Dict, detail: Dict) -> str:
    """
    根据 NPC 信息和详情数据生成纯文本内容。
    """
    lines = []

    # 基本信息
    lines.append(f"NPC名称：{npc_info['name']}")
    if npc_info.get('region'):
        lines.append(f"地区：{npc_info['region']}")
    if npc_info.get('type'):
        lines.append(f"类型：{npc_info['type']}")
    if npc_info.get('has_card_challenge'):
        lines.append(f"牌手挑战：{npc_info['has_card_challenge']}")
    if npc_info.get('has_dialogue_reward'):
        lines.append(f"对话奖励：{npc_info['has_dialogue_reward']}")
    if npc_info.get('special_effect'):
        lines.append(f"特殊效果：{npc_info['special_effect']}")
    if npc_info.get('summary'):
        lines.append(f"简介：{npc_info['summary']}")
    lines.append("")

    # 详情内容
    for key, content in detail.items():
        lines.append(f"【{key}】")
        lines.append(content)
        lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个 NPC ==========
def process_npc(npc: Dict, output_dir="data/cleaned/npc") -> bool:
    """
    处理单个 NPC：请求详情 API，解析内容，生成文件。
    """
    nid = npc["id"]
    nname = npc["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": nid,
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
            logger.warning(f"NPC {nname} 无页面数据")
            return False

        # 解析详情
        detail = parse_npc_detail(page)

        # 如果没有任何有效内容，视为失败
        if not detail:
            logger.info(f"NPC {nname} 无任何可提取内容")
            return False

        # 生成内容
        content = generate_npc_content(npc, detail)
        if not content.strip():
            logger.info(f"NPC {nname} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(nname)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存 NPC: {nname}")
        return True

    except Exception as e:
        logger.exception(f"处理 NPC {nname} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取 NPC 列表。
    2. 遍历每个 NPC，检查是否已爬取。
    3. 若未爬取，则处理。
    4. 成功则标记已爬取。
    5. 生成爬取报告。
    """
    npcs = get_npc_list()
    if not npcs:
        logger.warning("未获取到 NPC 列表，退出")
        return

    stats = {
        "total": len(npcs),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    test_limit = 0  # 0 表示全量

    for idx, n in enumerate(npcs[:test_limit] if test_limit else npcs, start=1):
        nid = n["id"]
        nname = n["name"]
        logger.info(f"[{idx}/{len(npcs)}] 正在处理: {nname} (ID: {nid})")

        if is_crawled("npc", nid):
            logger.info(f"  跳过 (ID {nid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_npc(n)
        if success:
            mark_crawled("npc", nid, nname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": nid, "name": nname})

        # time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "npc_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("NPC & 商店模块爬取完成！")


if __name__ == "__main__":
    run()