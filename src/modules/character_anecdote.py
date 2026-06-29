"""
角色逸闻爬虫模块
从观测枢爬取所有角色的逸闻纪事（游逸旅闻），每个逸闻保存为独立的文本文件。

增量策略：
- 每次运行时重新请求所有角色详情。
- 通过检查文件是否存在来跳过已保存的逸闻。
- 如果官方更新了内容，需手动删除对应 `.txt` 文件后重新运行。
"""

import json
import re
import time
import random
import logging
import os
from src.core.client import get
from src.core.parser import clean_html_to_text
from src.core.config_loader import get_channel_id

# ========== 日志配置 ==========
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "anecdote.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========== 工具函数 ==========

def parse_interactive_dialogue(components):
    """
    解析 interactive_dialogue 组件，按树形结构顺序提取对话。

    该组件存储了带有分支选项的对话树，需要从根节点开始深度优先遍历，
    确保选项文本（option）出现在其对应的对话（dialogue）之前。

    Args:
        components: 模块的 components 列表，通常只有一个元素。

    Returns:
        str: 按顺序拼接的对话文本，节点间用换行符分隔。
    """
    if not components:
        return ""
    raw = components[0].get("data", "{}")
    try:
        data = json.loads(raw)
        list_data = data.get("list", [])
        all_lines = []
        for item in list_data:
            root_id = item.get("root_id")
            child_ids = item.get("child_ids", {})
            contents = item.get("contents", {})
            if not root_id or not contents:
                continue

            def traverse(node_id, visited=None):
                """
                深度优先遍历对话树。

                Args:
                    node_id: 当前节点 ID。
                    visited: 已访问节点集合，用于防止循环引用。
                """
                if visited is None:
                    visited = set()
                if node_id in visited or node_id not in contents:
                    return
                visited.add(node_id)
                node = contents[node_id]
                option = node.get("option", "")
                dialogue = node.get("dialogue", "")
                if option:
                    all_lines.append(clean_html_to_text(option))
                if dialogue:
                    all_lines.append(clean_html_to_text(dialogue))
                for child_id in child_ids.get(node_id, []):
                    traverse(child_id, visited)

            traverse(root_id)

        return "\n".join(all_lines)
    except Exception as e:
        logger.warning(f"解析 interactive_dialogue 失败: {e}")
        return ""


def safe_filename(name):
    """
    清洗非法字符，生成安全的文件名。

    Windows 文件系统不允许以下字符：\ / : * ? " < > |
    将它们统一替换为下划线。

    Args:
        name: 原始文件名（可能包含非法字符）。

    Returns:
        str: 安全的文件名。
    """
    return re.sub(r'[\\/*?:"<>|]', '_', name)


# ========== 1. 获取逸闻角色列表 ==========

def get_anecdote_character_list():
    """
    获取所有包含逸闻的角色条目（即角色逸闻页面列表）。

    请求观测枢频道 API，返回角色逸闻频道的所有条目。

    Returns:
        list of dict: [{"id": content_id, "name": title}, ...]
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("anecdote")   # 从配置文件读取频道 ID (261)
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

        # 查找分类名称为"角色逸闻"的条目
        category = None
        for cat in outer_list:
            if cat.get("name") == "角色逸闻":
                category = cat
                break
        if not category:
            logger.warning("未找到「角色逸闻」分类")
            return []

        items = category.get("list", [])
        character_list = []
        for item in items:
            cid = str(item.get("content_id"))
            cname = item.get("title")
            if cid and cname:
                character_list.append({"id": cid, "name": cname})
        logger.info(f"成功获取 {len(character_list)} 个角色逸闻条目")
        for i, ch in enumerate(character_list[:5], 1):
            logger.info(f"  {i}. {ch['name']} (ID: {ch['id']})")
        return character_list
    except Exception as e:
        logger.exception("获取角色逸闻列表失败")
        return []


# ========== 2. 解析单个角色详情页，提取所有内容 ==========

def parse_anecdotes_from_page(page_data):
    """
    从角色逸闻详情页的 JSON 数据中提取所有内容项。

    解析 template_layout 中的标签页，提取以下类型的内容：
    - 逸闻纪事
    - 剧情彩蛋
    - 角色洞天
    - 七圣召唤
    - 幻想真境剧诗
    - 生日邮件

    Args:
        page_data: 角色详情 API 返回的 page 字段（包含 modules 和 template_layout）。

    Returns:
        list of dict: 每个 dict 包含 title, description, dialogue, type。
    """
    modules = page_data.get("modules", [])
    module_dict = {str(m.get("id")): m for m in modules if m.get("id")}
    template_layout = page_data.get("template_layout", {})
    tabs = template_layout.get("tab", [])

    items = []  # 统一存储所有内容项

    # 需要处理的标签列表
    target_tabs = ["逸闻纪事", "剧情彩蛋", "角色洞天", "七圣召唤", "幻想真境剧诗", "生日邮件"]

    for tab in tabs:
        tab_name = tab.get("tab_name")
        if tab_name not in target_tabs:
            continue

        module_groups = tab.get("module_group", [])
        for group in module_groups:
            group_modules = group.get("module", [])
            group_ids = [str(m.get("id")) for m in group_modules if m.get("id")]
            if not group_ids:
                continue

            meta_module = None   # 存储元信息模块（rich_base_info）
            dialogue_module = None  # 存储对话模块（interactive_dialogue）

            for mid in group_ids:
                mod = module_dict.get(mid)
                if not mod:
                    continue
                comps = mod.get("components", [])
                if not comps:
                    continue
                comp_id = comps[0].get("component_id", "")
                mod_name = mod.get("name", "")

                if comp_id == "rich_base_info":
                    # 跳过地图展示、角色展示等辅助模块
                    if "地图" in mod_name or "展示" in mod_name:
                        continue
                    meta_module = mod
                elif comp_id == "interactive_dialogue":
                    dialogue_module = mod

            # 如果既有元信息模块又有对话模块，正常解析。
            # 如果只有对话模块，单独处理（常见于七圣召唤、洞天对话等）。
            title = ""
            description = ""

            if meta_module:
                # 从 rich_base_info 中提取元信息
                comps = meta_module.get("components", [])
                if comps and comps[0].get("component_id") == "rich_base_info":
                    raw = comps[0].get("data", "{}")
                    try:
                        info = json.loads(raw)
                        list_data = info.get("list", [])
                        for item in list_data:
                            key = item.get("key", "")
                            values = item.get("value", [])
                            if not values:
                                continue
                            text = clean_html_to_text(values[0])
                            if key == "名称":
                                title = text
                            elif key == "简介":
                                description = text
                            elif key == "关键词":
                                title = f"彩蛋：{text}"
                            elif key == "备注":
                                description = text
                    except Exception as e:
                        logger.warning(f"解析元信息失败: {e}")

            # 如果 meta_module 为空但有 dialogue_module，使用模块名作为标题
            if not title and dialogue_module:
                title = dialogue_module.get("name", tab_name)

            # 如果仍然没有标题，使用标签名作为后备
            if not title:
                title = tab_name

            # 解析对话
            dialogue_text = ""
            if dialogue_module:
                dialogue_text = parse_interactive_dialogue(dialogue_module.get("components", []))

            # 如果既没有对话也没有描述，跳过该条目
            if not dialogue_text and not description:
                continue

            items.append({
                "title": title,
                "description": description,
                "dialogue": dialogue_text,
                "type": tab_name
            })

    return items


# ========== 3. 处理单个角色 ==========

def process_character(character_id, character_name, output_dir="data/cleaned/anecdote"):
    """
    处理单个角色逸闻页面，提取所有内容并保存为文件。

    Args:
        character_id: 角色的 content_id。
        character_name: 角色的 title（如"闲云【逸闻】"）。
        output_dir: 输出目录，默认为 data/cleaned/anecdote。

    增量逻辑：
        - 检查文件是否已存在，若存在则跳过，避免重复保存。
        - 因此，新增内容会自动保存，已存在内容不会被覆盖。
    """
    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": character_id,
        "lang": "zh-cn"
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = get(url, headers=headers, params=params)
        data = resp.json()
        if data.get("retcode") != 0:
            logger.error(f"API 错误: {data.get('message')}")
            return

        page = data.get("data", {}).get("page", {})
        if not page:
            logger.warning(f"角色 {character_name} 无页面数据")
            return

        items = parse_anecdotes_from_page(page)
        if not items:
            logger.info(f"角色 {character_name} 没有可提取的内容")
            return

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 提取角色名（去掉【逸闻】后缀）
        base_name = character_name.replace("【逸闻】", "").strip()
        if not base_name:
            base_name = character_name

        # 类型 -> 文件名前缀映射（逸闻不加前缀，其余类型加前缀以区分）
        prefix_map = {
            "逸闻纪事": "",
            "剧情彩蛋": "彩蛋_",
            "角色洞天": "洞天_",
            "七圣召唤": "七圣召唤_",
            "幻想真境剧诗": "剧诗_",
            "生日邮件": "生日邮件_",
        }

        safe_base = safe_filename(base_name)  # 每个角色只计算一次
        saved_count = 0

        for item in items:
            title = item["title"]
            item_type = item.get("type", "")
            prefix = prefix_map.get(item_type, "")

            safe_title = safe_filename(title)

            if prefix:
                filename = f"{safe_base}_{prefix}{safe_title}.txt"
            else:
                filename = f"{safe_base}_{safe_title}.txt"
            filepath = os.path.join(output_dir, filename)

            # 增量检查：文件已存在则跳过
            if os.path.exists(filepath):
                logger.debug(f"  内容已存在，跳过: {filename}")
                continue

            # 构建文件内容
            content_lines = []
            content_lines.append(f"# {title}")
            if item.get("description"):
                content_lines.append(f"简介：{item['description']}")
            if item.get("dialogue"):
                content_lines.append("")
                content_lines.append(item["dialogue"])
            content = "\n".join(content_lines)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"  保存: {filename}")
            saved_count += 1

        if saved_count:
            logger.info(f"角色 {character_name} 共保存 {saved_count} 个新内容")
        else:
            logger.info(f"角色 {character_name} 无新内容")

    except Exception as e:
        logger.exception(f"处理角色 {character_name} 失败")


# ========== 4. 主运行逻辑 ==========

def run():
    """
    主流程：
    1. 获取角色逸闻列表。
    2. 遍历每个角色，调用 process_character 提取内容。
    3. 生成爬取报告。
    """
    characters = get_anecdote_character_list()
    if not characters:
        logger.warning("未获取到角色逸闻列表，退出")
        return

    # 统计信息
    stats = {
        "total_characters": len(characters),
        "processed": 0,
        "failed": [],
        "total_new_anecdotes": 0
    }

    # 测试限制：设置为 0 或 None 表示全量爬取
    # 可通过修改此值控制测试范围
    test_limit = 0

    for idx, char in enumerate(characters[:test_limit] if test_limit else characters, start=1):
        char_id = char["id"]
        char_name = char["name"]
        logger.info(f"[{idx}/{len(characters)}] 正在处理: {char_name} (ID: {char_id})")

        try:
            # 注意：process_character 内部不再返回保存数量，需在内部累加
            # 为简化，这里不精确统计 total_new_anecdotes，但可通过日志查看
            process_character(char_id, char_name)
            stats["processed"] += 1
        except Exception as e:
            logger.error(f"处理 {char_name} 时发生异常: {e}")
            stats["failed"].append({"id": char_id, "name": char_name})

        # 控制请求间隔，避免触发反爬
        time.sleep(random.uniform(1, 2))

    # 生成爬取报告
    report_path = os.path.join(LOG_DIR, "anecdote_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("角色逸闻模块测试完成！")


if __name__ == "__main__":
    # 直接运行本模块时，执行主流程
    # 如需测试单个角色，可在此处调用 process_character("角色ID", "角色名")
    run()