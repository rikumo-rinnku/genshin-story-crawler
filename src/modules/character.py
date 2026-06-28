"""
角色剧情爬虫模块 - 从配置文件获取频道 ID，获取角色列表并解析剧情
"""
import json
import time
import random
import logging
import os
from src.core.client import get
from src.core.parser import clean_html_to_text
from src.core.storage import is_crawled, mark_crawled, save_text
from src.core.config_loader import get_channel_id

# ========== 日志配置 ==========
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "character.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数：从 modules 中查找特定模块 ==========
def find_module(modules, name):
    """在 modules 列表中查找第一个名称匹配的模块，返回该模块或 None"""
    for module in modules:
        if module.get("name") == name:
            return module
    return None


# ========== 1. 获取角色列表 ==========
def get_character_list():
    """
    使用频道列表接口获取所有角色的 ID 和名称。
    返回: list of dict [{"id": content_id, "name": title}, ...]
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("character")   # 从配置文件读取频道 ID (25)
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
            logger.warning("未找到外层角色列表")
            return []

        role_category = None
        for cat in outer_list:
            if cat.get("name") == "角色":
                role_category = cat
                break
        if not role_category:
            logger.warning("未找到「角色」分类")
            return []

        items = role_category.get("list", [])
        character_list = []
        for item in items:
            cid = str(item.get("content_id"))
            cname = item.get("title")
            if cid and cname:
                character_list.append({"id": cid, "name": cname})
        logger.info(f"成功获取 {len(character_list)} 个角色 (频道ID: {params['channel_id']})")
        for i, ch in enumerate(character_list[:5], 1):
            logger.info(f"  {i}. {ch['name']} (ID: {ch['id']})")
        return character_list
    except Exception as e:
        logger.exception("获取角色列表失败")
        return []


# ========== 2. 各个模块的解析函数 ==========
def parse_basic_info(components):
    """
    解析“基础信息”模块，提取角色名称、生日、所属、定位、武器类型、命之座、称号等。
    返回格式:
        基础信息
        角色名
        生日8月28日
        所属维茨特兰
        ...
    """
    if not components:
        return ""
    raw_data = components[0].get("data", "{}")
    try:
        info = json.loads(raw_data)
        lines = ["基础信息"]
        if info.get("name"):
            lines.append(info["name"])
        attrs = info.get("attr", [])
        for attr in attrs:
            key = attr.get("key", "")
            values = attr.get("value", [])
            if values:
                val = values[0]
                lines.append(f"{key}{val}")
        return "\n".join(lines)
    except json.JSONDecodeError as e:
        logger.warning(f"解析基础信息 JSON 失败: {e}")
        return ""


def parse_collapse_panel_rich_text(components):
    """
    解析 collapse_panel 组件中的 rich_text 文本，返回纯文本。
    适用于“更多描述”、“角色详细”、“角色故事1”等模块。
    """
    if not components:
        return ""
    raw_data = components[0].get("data", "{}")
    try:
        info = json.loads(raw_data)
        html_content = info.get("rich_text", "")
        if not html_content:
            return ""
        return clean_html_to_text(html_content)
    except json.JSONDecodeError as e:
        logger.warning(f"解析 rich_text 模块失败: {e}")
        return ""


def parse_voice_show(components):
    """
    解析“配音展示”模块，提取各语言语音文本。
    返回格式：
        配音展示
        汉语：
        初次见面…：追寻火焰在时间长河中的足迹...
        闲谈·劈刺：...
        日语：
        初次见面…：...
        ...
    """
    if not components:
        return ""
    raw_data = components[0].get("data", "{}")
    try:
        data = json.loads(raw_data)
        lines = ["配音展示"]
        voice_list = data.get("list", [])
        for lang_item in voice_list:
            lang = lang_item.get("tab_name", "")
            if not lang:
                continue
            lines.append(f"{lang}：")
            table = lang_item.get("table", [])
            for voice in table:
                name = voice.get("name", "")
                content = voice.get("content", "")
                if name and content:
                    lines.append(f"{name}：{content}")
        return "\n".join(lines)
    except json.JSONDecodeError as e:
        logger.warning(f"解析配音展示 JSON 失败: {e}")
        return ""


def parse_role_debut(components):
    """
    解析“角色宣发时间轴”模块，提取“角色登场”部分的文本。
    返回格式：tab_name + 换行 + 文本内容
    """
    if not components:
        return ""
    raw_data = components[0].get("data", "{}")
    try:
        data = json.loads(raw_data)
        items = data.get("list", [])
        for item in items:
            tab_name = item.get("tab_name", "")
            if tab_name.endswith("角色登场"):
                attrs = item.get("attr", [])
                if attrs:
                    values = attrs[0].get("value", [])
                    if values:
                        html = "\n".join(values)
                        text = clean_html_to_text(html)
                        return f"{tab_name}\n{text}"
        return ""
    except json.JSONDecodeError as e:
        logger.warning(f"解析角色登场 JSON 失败: {e}")
        return ""


# ========== 3. 获取单个角色的完整剧情文本 ==========
def get_character_story(character_id: str, character_name: str) -> str:
    """
    请求角色详情 API，按顺序提取：
    1. 基础信息模块
    2. 角色描述分组下的子模块
    3. 配音展示模块
    4. 角色宣发时间轴（角色登场）
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
            return f"【待补充】角色 {character_name} (ID: {character_id}) 的 API 请求失败，请手动检查。"

        modules = data.get("data", {}).get("page", {}).get("modules", [])
        module_dict = {m.get("id"): m for m in modules if m.get("id")}
        story_parts = []

        # ---------- 1. 基础信息 ----------
        basic_module = find_module(modules, "基础信息")
        if basic_module:
            basic_text = parse_basic_info(basic_module.get("components", []))
            if basic_text:
                story_parts.append(basic_text)
            else:
                story_parts.append("【缺失】基础信息（内容为空）")
        else:
            story_parts.append("【缺失】基础信息模块")

        # ---------- 2. 角色描述分组 ----------
        template_layout = data.get("data", {}).get("page", {}).get("template_layout", {})
        tabs = template_layout.get("tab", [])
        target_module_ids = []
        for tab in tabs:
            module_groups = tab.get("module_group", [])
            for group in module_groups:
                if group.get("name") == "角色描述" or group.get("module_group_id") == "98":
                    for module_info in group.get("module", []):
                        mid = str(module_info.get("id"))
                        if mid:
                            target_module_ids.append(mid)
                    break
            if target_module_ids:
                break

        if not target_module_ids:
            story_parts.append("【缺失】角色描述分组（未找到该分组）")
        else:
            any_content = False
            for mid in target_module_ids:
                module = module_dict.get(mid)
                if not module:
                    logger.warning(f"模块 ID {mid} 在 modules 中未找到")
                    continue
                text = parse_collapse_panel_rich_text(module.get("components", []))
                if text:
                    any_content = True
                    module_name = module.get("name", "")
                    if module_name:
                        story_parts.append(f"{module_name}\n{text}")
                    else:
                        story_parts.append(text)
            if not any_content:
                story_parts.append("【缺失】角色描述分组（所有子模块均无有效内容）")

        # ---------- 3. 配音展示 ----------
        voice_module = find_module(modules, "配音展示")
        if voice_module:
            voice_text = parse_voice_show(voice_module.get("components", []))
            if voice_text:
                story_parts.append(voice_text)
            else:
                story_parts.append("【缺失】配音展示（内容为空）")
        else:
            story_parts.append("【缺失】配音展示模块")

        # ---------- 4. 角色宣发时间轴（角色登场） ----------
        debut_module = find_module(modules, "角色宣发时间轴")
        if debut_module:
            debut_text = parse_role_debut(debut_module.get("components", []))
            if debut_text:
                story_parts.append(debut_text)
            else:
                story_parts.append("【缺失】角色登场（内容为空）")
        else:
            story_parts.append("【缺失】角色登场模块")

        if not story_parts:
            return f"【待补充】角色 {character_name} (ID: {character_id}) 的剧情文本完全缺失，请手动补充。"

        return "\n\n".join(story_parts)
    except Exception as e:
        logger.exception(f"请求角色 {character_name} 详情失败")
        return f"【待补充】角色 {character_name} (ID: {character_id}) 的请求异常: {e}"


# ========== 旅行者特殊处理 ==========
def fetch_traveler_story():
    """
    获取旅行者的完整剧情：
    - 基础信息和角色描述：从任意一个“旅行者·X”条目获取（例如第一个）
    - 配音展示：从特殊 ID 505527 获取
    """
    all_chars = get_character_list()
    traveler_items = [ch for ch in all_chars if ch["name"].startswith("旅行者·")]
    if not traveler_items:
        logger.warning("未找到旅行者条目，跳过特殊处理")
        return None

    sample = traveler_items[0]
    logger.info(f"使用 {sample['name']} (ID: {sample['id']}) 获取基础信息和角色描述")

    story_sample = get_character_story(sample["id"], sample["name"])
    parts = story_sample.split("\n\n")
    basic_info = ""
    role_desc_parts = []
    for part in parts:
        if part.startswith("基础信息"):
            basic_info = part
        elif part.startswith("更多描述") or part.startswith("角色详细") or part.startswith("角色故事"):
            role_desc_parts.append(part)
    role_desc = "\n\n".join(role_desc_parts)

    logger.info("从特殊 ID 505527 获取配音展示")
    voice_story = get_character_story("505527", "旅行者")
    voice_part = ""
    for part in voice_story.split("\n\n"):
        if part.startswith("配音展示"):
            voice_part = part
            break

    final_parts = []
    if basic_info:
        final_parts.append(basic_info)
    if role_desc:
        final_parts.append(role_desc)
    if voice_part:
        final_parts.append(voice_part)
    if not final_parts:
        return None
    return "\n\n".join(final_parts)


# ========== 主运行逻辑 ==========
def run():
    characters = get_character_list()
    if not characters:
        logger.warning("未获取到角色列表，退出")
        return

    # 分离普通角色和旅行者
    normal_chars = []
    traveler_chars = []
    for ch in characters:
        if ch["name"].startswith("旅行者·"):
            traveler_chars.append(ch)
        else:
            normal_chars.append(ch)

    # 统计信息（可选）
    stats = {
        "total": len(characters),
        "normal": len(normal_chars),
        "traveler": len(traveler_chars),
        "processed": 0,
        "failed": [],
        "missing": []
    }

    # 1. 爬取普通角色
    logger.info(f"开始爬取 {len(normal_chars)} 个普通角色")
    for idx, char in enumerate(normal_chars, start=1):
        character_id = char["id"]
        character_name = char["name"]
        logger.info(f"[{idx}/{len(normal_chars)}] 正在处理: {character_name} (ID: {character_id})")

        if is_crawled("character", character_id):
            logger.info(f"  跳过 (ID {character_id} 已爬取过)。")
            continue

        story = get_character_story(character_id, character_name)
        if story:
            # 检查是否有缺失标记
            if "【缺失】" in story or story.startswith("【待补充】"):
                filename = f"[缺失]{character_name}"
                logger.warning(f"  生成占位文档，文件名: {filename}")
                stats["missing"].append({"id": character_id, "name": character_name})
            else:
                filename = character_name
            save_text(story, "character", filename, name=character_name)
            mark_crawled("character", character_id, character_name)
            stats["processed"] += 1
        else:
            logger.error(f"  未获取到剧情，跳过标记。")
            stats["failed"].append({"id": character_id, "name": character_name})

        time.sleep(random.uniform(1, 2))

    # 2. 单独处理旅行者
    if traveler_chars:
        logger.info(f"开始处理旅行者（共 {len(traveler_chars)} 个形态）")
        traveler_story = fetch_traveler_story()
        traveler_id = "traveler_main"
        if traveler_story:
            if not is_crawled("character", traveler_id):
                save_text(traveler_story, "character", "旅行者", name="旅行者")
                mark_crawled("character", traveler_id, "旅行者")
                logger.info("旅行者剧情保存成功")
                stats["processed"] += 1
            else:
                logger.info("旅行者已爬取过，跳过")
        else:
            logger.warning("未能获取旅行者剧情，生成占位文档")
            placeholder = "【待补充】旅行者剧情获取失败"
            save_text(placeholder, "character", "旅行者", name="旅行者")
            mark_crawled("character", traveler_id, "旅行者")
            stats["missing"].append({"id": traveler_id, "name": "旅行者"})

    # 生成报告
    report_path = os.path.join(LOG_DIR, "character_report.json")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("角色模块测试完成！")


if __name__ == "__main__":
    run()