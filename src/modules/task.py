"""
任务剧情爬虫模块 - 获取任务列表、详情及子任务拆分
"""
import json
import time
import random
import re
from src.core.client import get
from src.core.storage import is_crawled, mark_crawled, save_text
from src.core.config_loader import get_channel_id
from src.core.parser import clean_html_to_text


# ========== 1. 获取任务列表 ==========
def get_task_list():
    """
    使用频道列表接口获取所有任务的 ID、名称及 ext 中的基本信息。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("task")   # 从配置文件读取任务频道 ID（如 43）
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = get(url, headers=headers, params=params)
        data = resp.json()
        if data.get("retcode") != 0:
            print(f"API 错误: {data}")
            return []

        # 数据路径：data.data.list[0].list（与角色类似，通常第一个分类是“任务”）
        outer_list = data.get("data", {}).get("list", [])
        if not outer_list:
            print("未找到外层任务列表")
            return []
        # 查找 name 为 "任务" 的分类
        task_category = None
        for cat in outer_list:
            if cat.get("name") == "任务":
                task_category = cat
                break
        if not task_category:
            print("未找到「任务」分类")
            return []
        items = task_category.get("list", [])
        task_list = []
        for item in items:
            tid = str(item.get("content_id"))
            tname = item.get("title")
            ext_str = item.get("ext", "{}")
            if tid and tname:
                # 解析 ext 获取任务类型、区域、版本号
                task_type, task_region, task_version = parse_ext_info(ext_str)
                task_list.append({
                    "id": tid,
                    "name": tname,
                    "type": task_type,
                    "region": task_region,
                    "version": task_version
                })
        print(f"成功获取 {len(task_list)} 个任务 (频道ID: {params['channel_id']})")
        for i, t in enumerate(task_list[:5], 1):
            print(f"  {i}. {t['name']} (ID: {t['id']})")
        return task_list
    except Exception as e:
        print(f"获取任务列表失败: {e}")
        return []


# ========== 辅助解析函数 ==========
def parse_ext_info(ext_str: str):
    """从 ext 中提取任务类型、区域、版本号"""
    task_type = ""
    task_region = ""
    task_version = ""
    try:
        ext_data = json.loads(ext_str)
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("任务类型/"):
                        task_type = item.split("/")[-1]
                    elif item.startswith("任务区域/"):
                        task_region = item.split("/")[-1]
                    elif item.startswith("版本号/"):
                        task_version = item.split("/")[-1]
                break
    except Exception:
        pass
    return task_type, task_region, task_version


def parse_base_info(components):
    """解析 base_info 或 rich_base_info 组件，返回键值对文本"""
    if not components:
        return ""
    raw = components[0].get("data", "{}")
    try:
        info = json.loads(raw)
        list_data = info.get("list", [])
        lines = []
        for item in list_data:
            key = item.get("key", "")
            values = item.get("value", [])
            if values:
                cleaned = [clean_html_to_text(v) for v in values if v]
                lines.append(f"{key}：{'；'.join(cleaned)}")
        return "\n".join(lines) if lines else ""
    except:
        return ""


def parse_collapse_panel(components):
    """解析 collapse_panel 组件，返回 rich_text 纯文本"""
    if not components:
        return ""
    raw = components[0].get("data", "{}")
    try:
        info = json.loads(raw)
        html = info.get("rich_text", "")
        return clean_html_to_text(html)
    except:
        return ""


def parse_interactive_dialogue(components):
    """
    解析 interactive_dialogue 组件，按顺序提取对话
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

            # 深度优先遍历
            def traverse(node_id, visited=None):
                if visited is None:
                    visited = set()
                if node_id in visited or node_id not in contents:
                    return
                visited.add(node_id)
                node = contents[node_id]
                dialogue = node.get("dialogue", "")
                option = node.get("option", "")
                if dialogue:
                    all_lines.append(clean_html_to_text(dialogue))
                if option:
                    all_lines.append(clean_html_to_text(option))
                # 遍历子节点
                for child_id in child_ids.get(node_id, []):
                    traverse(child_id, visited)

            traverse(root_id)

        return "\n".join(all_lines)
    except Exception as e:
        print(f"解析对话失败: {e}")
        return ""


# ========== 任务详情解析 ==========
def extract_main_info(page):
    """从 page 中提取主任务信息"""
    info = {
        "id": page.get("id"),
        "name": page.get("name"),
        "desc": page.get("desc", ""),
        "type": "",
        "region": "",
        "version": "",
    }
    ext = page.get("ext", {})
    fe_ext = ext.get("fe_ext", "")
    if fe_ext:
        task_type, task_region, task_version = parse_ext_info(fe_ext)
        info["type"] = task_type
        info["region"] = task_region
        info["version"] = task_version
    return info


def parse_subtasks(page):
    """
    从 page 解析子任务列表
    返回 list of dict: [{"name": "子任务名", "module_ids": [id1, id2, ...]}]
    """
    template_layout = page.get("template_layout", {})
    tabs = template_layout.get("tab", [])
    modules = page.get("modules", [])
    module_dict = {str(m.get("id")): m for m in modules if m.get("id")}

    EXCLUDED = {"地图说明", "任务奖励"}

    subtasks = []
    for tab in tabs:
        for group in tab.get("module_group", []):
            group_id = group.get("module_group_id")
            parent_id = group.get("parent_group_id", "0")
            group_name = group.get("name", "")
            modules_in_group = group.get("module", [])

            if not group_name:
                for mod in modules_in_group:
                    mid = str(mod.get("id"))
                    if mid in module_dict:
                        mname = module_dict[mid].get("name", "")
                        if mname and mname not in ["任务概述", "任务过程", "剧情对话", "任务奖励", "地图说明"]:
                            group_name = mname
                            break
            if not group_name:
                group_name = f"子任务_{group_id}"

            filtered_ids = []
            for mod in modules_in_group:
                mid = str(mod.get("id"))
                if mid in module_dict:
                    mname = module_dict[mid].get("name", "")
                    if mname not in EXCLUDED:
                        filtered_ids.append(mid)
                else:
                    filtered_ids.append(mid)

            subtasks.append({
                "name": group_name,
                "group_id": group_id,
                "parent_id": parent_id,
                "module_ids": filtered_ids,
            })

    return subtasks


def get_task_detail(task_id: str) -> dict:
    """请求任务详情 API"""
    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": task_id,
        "lang": "zh-cn"
    }
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = get(url, headers=headers, params=params)
    data = resp.json()
    if data.get("retcode") != 0:
        print(f"  API 错误: {data.get('message')}")
        return None
    return data


def generate_subtask_content(main_info, subtask, module_dict):
    """
    根据子任务包含的模块 ID，生成内容文本
    """
    lines = []
    lines.append(f"任务名称：{main_info['name']}")
    lines.append(f"任务类型：{main_info['type'] or '未知'}")
    lines.append(f"任务区域：{main_info['region'] or '未知'}")
    lines.append(f"版本号：{main_info['version'] or '未知'}")
    lines.append("")
    lines.append(f"=== {subtask['name']} ===")

    for mid in subtask['module_ids']:
        module = module_dict.get(mid)
        if not module:
            continue
        name = module.get("name", "")
        comps = module.get("components", [])
        if not comps:
            continue

        comp_id = comps[0].get("component_id", "")
        if comp_id in ["base_info", "rich_base_info"]:
            text = parse_base_info(comps)
            if text:
                lines.append(f"\n【{name}】")
                lines.append(text)
        elif comp_id == "collapse_panel":
            text = parse_collapse_panel(comps)
            if text:
                lines.append(f"\n【{name}】")
                lines.append(text)
        elif comp_id == "interactive_dialogue":
            text = parse_interactive_dialogue(comps)
            if text:
                lines.append(f"\n【{name}】")
                lines.append(text)
        else:
            pass

    return "\n".join(lines)


# ========== 主流程 ==========
def run():
    tasks = get_task_list()
    if not tasks:
        return

    test_limit = 20  # 可调整
    for idx, task in enumerate(tasks[:test_limit], start=1):
        task_id = task["id"]
        task_name = task["name"]
        print(f"[{idx}/{len(tasks)}] 正在处理: {task_name} (ID: {task_id})")

        if is_crawled("task", task_id):
            print(f"  跳过 (ID {task_id} 已爬取过)。")
            continue

        detail_data = get_task_detail(task_id)
        if not detail_data:
            continue

        page = detail_data.get("data", {}).get("page", {})
        if not page:
            print("  未找到页面数据")
            continue

        main_info = extract_main_info(page)
        modules = page.get("modules", [])
        module_dict = {str(m.get("id")): m for m in modules if m.get("id")}

        subtasks = parse_subtasks(page)
        if not subtasks:
            print("  未解析到子任务，跳过")
            continue

        print(f"  发现 {len(subtasks)} 个子任务")

        for subtask in subtasks:
            content = generate_subtask_content(main_info, subtask, module_dict)
            if not content.strip():
                print(f"    子任务 {subtask['name']} 无内容，跳过")
                continue

            safe_main = re.sub(r'[\\/*?:"<>|]', '_', main_info['name'])
            safe_sub = re.sub(r'[\\/*?:"<>|]', '_', subtask['name'])
            filename = f"{safe_main}_{safe_sub}"
            save_text(content, "task", filename, name=filename)
            print(f"    已保存子任务: {filename}")

        mark_crawled("task", task_id, task_name)
        print(f"  任务 {task_name} 处理完成")

        time.sleep(random.uniform(1, 2))

    print("\n任务模块测试完成！")


if __name__ == "__main__":
    run()