"""
食物剧情爬虫模块
从观测枢爬取所有食物的详细信息，包括食物类型、星级、获取方式、各变体的描述、效果、材料等。
一个食物条目可能包含多个变体：奇怪版、普通版、美味版、特殊料理。
增量策略：使用 crawled.json 记录已爬取的食物 ID，实现增量更新。
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
        logging.FileHandler(os.path.join(LOG_DIR, "food.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name: str) -> str:
    """清洗非法字符，生成安全的文件名"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)

def extract_materials_text(html: str) -> str:
    """
    从加工材料的 HTML 中提取紧凑格式的文本。
    例如：<span>薄荷</span>*2 <span>落落莓</span>*2 <span>糖</span>*1
    返回：薄荷*2、落落莓*2、糖*1
    """
    if not html:
        return ""
    
    soup = BeautifulSoup(html, 'lxml')
    # 获取纯文本，用空格分隔
    text = soup.get_text(separator=' ', strip=True)
    # 压缩多余空格
    text = re.sub(r'\s+', ' ', text).strip()
    # 将空格分隔的多个材料用顿号连接
    if text:
        parts = text.split()
        return "、".join(parts)
    return ""


def parse_filter_tags(filter_text: str) -> dict:
    """
    解析 ext 中 filter.text 的 JSON 数组，提取食物类型、星级、获取方式、是否特殊料理。
    返回 dict。
    """
    result = {
        "food_type": "",        # 回复类/复活类/体力类/攻击类/防御类/暴击类/生命上限/治疗加成/合成药剂/特殊食物
        "star": "",             # 其他/一星/二星/三星/四星/五星
        "obtain": "",           # NPC购买/任务获取/宝箱获取/其他
        "has_special": ""       # 是/否
    }
    try:
        tags = json.loads(filter_text)
        for tag in tags:
            if tag.startswith("食物类型/"):
                result["food_type"] = tag.split("/")[-1]
            elif tag.startswith("食物星级/"):
                result["star"] = tag.split("/")[-1]
            elif tag.startswith("食物获取方式/"):
                result["obtain"] = tag.split("/")[-1]
            elif tag.startswith("是否产出特殊料理/"):
                result["has_special"] = tag.split("/")[-1]
    except Exception as e:
        logger.warning(f"解析 filter 标签失败: {e}")
    return result


# ========== 1. 获取食物列表 ==========
def get_food_list():
    """
    获取所有食物的 ID、名称、简介和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("food")   # 从配置文件读取频道 ID (21)
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
            logger.warning("未找到外层食物列表")
            return []

        # 查找 name 为 "食物" 的分类
        target_category = None
        for cat in outer_list:
            if cat.get("name") == "食物":
                target_category = cat
                break
        if not target_category:
            logger.warning("未找到「食物」分类")
            return []

        items = target_category.get("list", [])
        food_list = []
        for item in items:
            fid = str(item.get("content_id"))
            fname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if fid and fname:
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
                    logger.warning(f"解析食物 ext 失败: {e}")

                food_list.append({
                    "id": fid,
                    "name": fname,
                    "summary": summary,
                    "food_type": tags.get("food_type", ""),
                    "star": tags.get("star", ""),
                    "obtain": tags.get("obtain", ""),
                    "has_special": tags.get("has_special", "")
                })
        logger.info(f"成功获取 {len(food_list)} 个食物 (频道ID: {params['channel_id']})")
        for i, item in enumerate(food_list[:5], 1):
            logger.info(f"  {i}. {item['name']} (ID: {item['id']}) [{item['star']} {item['food_type']}]")
        return food_list
    except Exception as e:
        logger.exception("获取食物列表失败")
        return []


# ========== 2. 解析食物详情页 ==========
def parse_food_detail(page_data):
    """
    从食物详情页的 JSON 中提取所有变体的信息。
    返回 list of dict，每个 dict 对应一个变体（奇怪/普通/美味/特殊料理）。
    """
    modules = page_data.get("modules", [])
    variants = []

    for module in modules:
        comps = module.get("components", [])
        for comp in comps:
            if comp.get("component_id") == "material_base_info":
                try:
                    data = json.loads(comp.get("data", "{}"))
                    
                    # 提取变体名称（如 "奇怪薄荷泡泡糖"、"薄荷泡泡糖"、"解闷泡泡糖"）
                    variant_name = data.get("name", "")
                    if not variant_name:
                        continue
                    
                    # 提取加工材料
                    materials_text = ""
                    materials = data.get("materials", {})
                    if materials.get("value"):
                        materials_text = extract_materials_text(materials["value"])
                    
                    # 提取 attr 字段
                    description = ""
                    effect = ""
                    obtain = ""
                    recipe = ""
                    
                    for attr in data.get("attr", []):
                        key = attr.get("key", "")
                        values = attr.get("value", [])
                        if not values:
                            continue
                        # 取第一个 value（通常只有一个）
                        text = clean_html_to_text(values[0]) if values[0] else ""
                        if not text:
                            continue
                        
                        if key == "描述":
                            description = text
                        elif key == "使用效果":
                            effect = text
                        elif key == "获得方式":
                            obtain = text
                        elif key == "食谱获得":
                            recipe = text
                    
                    # 只有至少有一个有效文本字段才保留
                    if description or effect or obtain or recipe or materials_text:
                        variants.append({
                            "name": variant_name,
                            "description": description,
                            "effect": effect,
                            "obtain": obtain,
                            "recipe": recipe,
                            "materials": materials_text
                        })
                except Exception as e:
                    logger.warning(f"解析 material_base_info 失败: {e}")
                    continue

    return variants

# ========== 3. 生成文件内容 ==========
def generate_food_content(food_info, variants):
    """
    根据食物信息和变体列表生成纯文本内容。
    参数：
        food_info (dict): 包含 id, name, summary, food_type, star, obtain, has_special
        variants (list): 各变体的 dict 列表
    返回：
        str: 格式化后的纯文本内容
    """
    lines = []

    # 1. 基本信息
    lines.append(f"食物名称：{food_info['name']}")
    if food_info.get("food_type"):
        lines.append(f"食物类型：{food_info['food_type']}")
    if food_info.get("star"):
        lines.append(f"星级：{food_info['star']}")
    if food_info.get("obtain"):
        lines.append(f"获取方式：{food_info['obtain']}")
    if food_info.get("has_special"):
        lines.append(f"是否产出特殊料理：{food_info['has_special']}")
    if food_info.get("summary"):
        lines.append(f"简介：{food_info['summary']}")
    lines.append("")

    # 2. 各变体信息
    for idx, variant in enumerate(variants):
        if not variant.get("name"):
            continue
        
        # 判断变体类型用于标签
        name = variant["name"]
        if name.startswith("奇怪"):
            label = "奇怪版"
        elif name.startswith("美味的"):
            label = "美味版"
        elif "特殊" in name or "特色" in name or "专属" in name:
            # 特殊料理通常由特定角色制作
            label = "特殊料理"
        else:
            label = "普通版"
        
        lines.append(f"【{label}·{name}】")
        
        if variant.get("description"):
            lines.append(f"描述：{variant['description']}")
        if variant.get("effect"):
            lines.append(f"使用效果：{variant['effect']}")
        if variant.get("obtain"):
            lines.append(f"获得方式：{variant['obtain']}")
        if variant.get("recipe"):
            lines.append(f"食谱获得：{variant['recipe']}")
        if variant.get("materials"):
            lines.append(f"加工材料：{variant['materials']}")
        lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单个食物 ==========
def process_food(food_info, output_dir="data/cleaned/food"):
    """
    处理单个食物：请求详情 API，解析内容，生成文件。
    参数：
        food_info (dict): 包含 id, name, summary, food_type, star, obtain, has_special
        output_dir (str): 输出目录路径
    返回：
        bool: 成功返回 True，失败返回 False
    """
    food_id = food_info["id"]
    food_name = food_info["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": food_id,
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
            logger.warning(f"食物 {food_name} 无页面数据")
            return False

        # 解析变体
        variants = parse_food_detail(page)
        if not variants:
            logger.info(f"食物 {food_name} 无任何变体内容，跳过")
            return False

        # 生成内容
        content = generate_food_content(food_info, variants)
        if not content.strip():
            logger.info(f"食物 {food_name} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(food_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存食物: {food_name}")
        return True

    except Exception as e:
        logger.exception(f"处理食物 {food_name} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取食物列表。
    2. 遍历每个食物，检查是否已爬取（基于 crawled.json）。
    3. 若未爬取，则调用 process_food 处理。
    4. 成功则标记已爬取，失败则记录失败信息。
    5. 生成爬取报告。
    """
    # 获取食物列表
    items = get_food_list()
    if not items:
        logger.warning("未获取到食物列表，退出")
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
        fid = item["id"]
        fname = item["name"]
        logger.info(f"[{idx}/{len(items)}] 正在处理: {fname} (ID: {fid})")

        # 增量检查
        if is_crawled("food", fid):
            logger.info(f"  跳过 (ID {fid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_food(item)
        if success:
            mark_crawled("food", fid, fname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": fid, "name": fname})

        # 控制请求频率
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "food_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("食物模块爬取完成！")


if __name__ == "__main__":
    run()