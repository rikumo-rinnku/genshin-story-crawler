"""
角色剧情爬虫模块 - 从配置文件获取频道 ID，获取角色列表并解析剧情
"""
import json
import time
import random
from src.core.client import get
from src.core.parser import clean_html_to_text
from src.core.storage import is_crawled, mark_crawled, save_text
from src.core.config_loader import get_channel_id


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
            print(f"API 错误: {data}")
            return []

        outer_list = data.get("data", {}).get("list", [])
        if not outer_list:
            print("未找到外层角色列表")
            return []
        # 查找 name 为 "角色" 的分类（更健壮）
        role_category = None
        for cat in outer_list:
            if cat.get("name") == "角色":
                role_category = cat
                break
        if not role_category:
            print("未找到「角色」分类")
            return []
        items = role_category.get("list", [])
        character_list = []
        for item in items:
            cid = str(item.get("content_id"))
            cname = item.get("title")
            if cid and cname:
                character_list.append({"id": cid, "name": cname})
        print(f"成功获取 {len(character_list)} 个角色 (频道ID: {params['channel_id']})")
        for i, ch in enumerate(character_list[:5], 1):
            print(f"  {i}. {ch['name']} (ID: {ch['id']})")
        return character_list
    except Exception as e:
        print(f"获取角色列表失败: {e}")
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
        print(f"  解析基础信息 JSON 失败: {e}")
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
        print(f"  解析 rich_text 模块失败: {e}")
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
        print(f"  解析配音展示 JSON 失败: {e}")
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
            # 匹配以“角色登场”结尾的条目
            if tab_name.endswith("角色登场"):
                attrs = item.get("attr", [])
                if attrs:
                    values = attrs[0].get("value", [])
                    if values:
                        html = "\n".join(values)
                        text = clean_html_to_text(html)
                        # 关键修改：加上标题和换行
                        return f"{tab_name}\n{text}"
        return ""
    except json.JSONDecodeError as e:
        print(f"  解析角色登场 JSON 失败: {e}")
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
            print(f"  API 错误: {data.get('message')}")
            return f"【待补充】角色 {character_name} (ID: {character_id}) 的 API 请求失败，请手动检查。"

        modules = data.get("data", {}).get("page", {}).get("modules", [])
        module_dict = {m.get("id"): m for m in modules if m.get("id")}
        story_parts = []

        # ---------- 1. 基础信息 ----------
        basic_found = False
        for module in modules:
            if module.get("name") == "基础信息":
                basic_text = parse_basic_info(module.get("components", []))
                if basic_text:
                    story_parts.append(basic_text)
                    basic_found = True
                else:
                    story_parts.append("【缺失】基础信息（内容为空）")
                break
        if not basic_found:
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
                    print(f"  警告：模块 ID {mid} 在 modules 中未找到")
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
        voice_found = False
        for module in modules:
            if module.get("name") == "配音展示":
                voice_text = parse_voice_show(module.get("components", []))
                if voice_text:
                    story_parts.append(voice_text)
                    voice_found = True
                else:
                    story_parts.append("【缺失】配音展示（内容为空）")
                break
        if not voice_found:
            story_parts.append("【缺失】配音展示模块")

        # ---------- 4. 角色宣发时间轴（角色登场） ----------
        debut_found = False
        for module in modules:
            if module.get("name") == "角色宣发时间轴":
                debut_text = parse_role_debut(module.get("components", []))
                if debut_text:
                    story_parts.append(debut_text)
                    debut_found = True
                else:
                    story_parts.append("【缺失】角色登场（内容为空）")
                break
        if not debut_found:
            story_parts.append("【缺失】角色登场模块")

        if not story_parts:
            return f"【待补充】角色 {character_name} (ID: {character_id}) 的剧情文本完全缺失，请手动补充。"

        return "\n\n".join(story_parts)
    except Exception as e:
        print(f"  请求详情失败: {e}")
        return f"【待补充】角色 {character_name} (ID: {character_id}) 的请求异常: {e}"
    

# ========== 旅行者特殊处理 ==========
def fetch_traveler_story():
    """
    获取旅行者的完整剧情：
    - 基础信息和角色描述：从任意一个“旅行者·X”条目获取（例如第一个）
    - 配音展示：从特殊 ID 505527 获取
    """
    # 1. 获取所有角色列表，从中筛选旅行者条目
    all_chars = get_character_list()
    traveler_items = [ch for ch in all_chars if ch["name"].startswith("旅行者·")]
    if not traveler_items:
        print("未找到旅行者条目，跳过特殊处理")
        return None

    # 取第一个旅行者作为基础信息来源
    sample = traveler_items[0]
    print(f"使用 {sample['name']} (ID: {sample['id']}) 获取基础信息和角色描述")

    # 获取基础信息和角色描述（调用通用函数，但会包含配音展示缺失标记，后面我们会覆盖）
    story_sample = get_character_story(sample["id"], sample["name"])
    # 拆分模块，只保留“基础信息”和角色描述相关的部分（例如“更多描述”、“角色详细”、“角色故事1”等）
    parts = story_sample.split("\n\n")
    basic_info = ""
    role_desc_parts = []
    for part in parts:
        if part.startswith("基础信息"):
            basic_info = part
        elif part.startswith("更多描述") or part.startswith("角色详细") or part.startswith("角色故事"):
            role_desc_parts.append(part)
    role_desc = "\n\n".join(role_desc_parts)

    # 2. 从特殊 ID 获取配音展示
    print("从特殊 ID 505527 获取配音展示")
    voice_story = get_character_story("505527", "旅行者")
    # 提取“配音展示”模块
    voice_part = ""
    for part in voice_story.split("\n\n"):
        if part.startswith("配音展示"):
            voice_part = part
            break

    # 合并最终内容（顺序：基础信息 → 角色描述 → 配音展示）
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

# ========== 修改后的主运行逻辑 ==========
def run():
    characters = get_character_list()
    if not characters:
        return

    # 分离普通角色和旅行者
    normal_chars = []
    traveler_chars = []
    for ch in characters:
        if ch["name"].startswith("旅行者·"):
            traveler_chars.append(ch)
        else:
            normal_chars.append(ch)

    # 1. 爬取普通角色
    test_limit = len(normal_chars)  # 正式运行时爬取所有
    for idx, char in enumerate(normal_chars[:test_limit], start=1):
        character_id = char["id"]
        character_name = char["name"]
        print(f"[{idx}/{len(normal_chars)}] 正在处理: {character_name} (ID: {character_id})")

        if is_crawled("character", character_id):
            print(f"  跳过 (ID {character_id} 已爬取过)。")
            continue

        story = get_character_story(character_id, character_name)
        if story:
            if "【缺失】" in story or story.startswith("【待补充】"):
                filename = f"[缺失]{character_name}"
                print(f"  生成占位文档，文件名: {filename}")
            else:
                filename = character_name
            save_text(story, "character", filename, name=character_name)
            mark_crawled("character", character_id, character_name)
        else:
            print(f"  未获取到剧情，跳过标记。")

        time.sleep(random.uniform(1, 2))

    # 2. 单独处理旅行者（如果存在）
    if traveler_chars:
        print("\n开始处理旅行者（主角）...")
        traveler_story = fetch_traveler_story()
        if traveler_story:
            # 保存为“旅行者”文件，ID 使用固定字符串 "traveler_main"
            traveler_id = "traveler_main"
            if not is_crawled("character", traveler_id):
                save_text(traveler_story, "character", "旅行者", name="旅行者")
                mark_crawled("character", traveler_id, "旅行者")
                print("旅行者剧情保存成功")
            else:
                print("旅行者已爬取过，跳过")
        else:
            print("未能获取旅行者剧情，生成占位文档")
            placeholder = "【待补充】旅行者剧情获取失败"
            save_text(placeholder, "character", "旅行者", name="旅行者")
            mark_crawled("character", "traveler_main", "旅行者")

    print("\n角色模块测试完成！")
    
if __name__ == "__main__":
    run()