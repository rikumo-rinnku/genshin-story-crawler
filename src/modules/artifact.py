"""
圣遗物剧情爬虫模块
从观测枢爬取所有圣遗物套装的详细信息，包括套装效果、简介和五个部件的背景故事。
增量策略：使用 crawled.json 记录已爬取的套装 ID，实现增量更新。
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
from src.core.storage import is_crawled, mark_crawled

# ========== 日志配置 ==========
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "artifact.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name):
    """清洗非法字符，生成安全的文件名"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_ext_info(ext_str: str):
    """
    解析圣遗物列表中的 ext 字段，提取星级、获取方式、2件套和4件套效果。
    返回 dict。
    """
    result = {
        "star": "",
        "source": [],
        "set_2": "",
        "set_4": ""
    }
    try:
        ext_data = json.loads(ext_str)
        # 结构为 {"c_218": {"filter": {"text": "[...]"}, "table": {"list": [...]}}}
        for key, value in ext_data.items():
            if key.startswith("c_"):
                # 提取星级和获取方式
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("星级/"):
                        result["star"] = item.split("/")[-1]
                    elif item.startswith("获取方式/"):
                        source = item.split("/")[-1]
                        if source not in result["source"]:
                            result["source"].append(source)
                # 提取套装效果
                table = value.get("table", {})
                table_list = table.get("list", [])
                for entry in table_list:
                    key_text = entry.get("key", "")
                    if "2件套" in key_text:
                        result["set_2"] = entry.get("value", "")
                    elif "4件套" in key_text:
                        result["set_4"] = entry.get("value", "")
                break
    except Exception as e:
        logger.warning(f"解析圣遗物 ext 失败: {e}")
    return result


# ========== 1. 获取圣遗物列表 ==========
def get_artifact_list():
    """
    获取所有圣遗物套装的 ID、名称、summary 和 ext 信息。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("artifact")   # 从配置文件读取频道 ID (218)
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
            logger.warning("未找到外层圣遗物列表")
            return []

        # 查找 name 为 "圣遗物" 的分类
        artifact_category = None
        for cat in outer_list:
            if cat.get("name") == "圣遗物":
                artifact_category = cat
                break
        if not artifact_category:
            logger.warning("未找到「圣遗物」分类")
            return []

        items = artifact_category.get("list", [])
        artifact_list = []
        for item in items:
            aid = str(item.get("content_id"))
            aname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if aid and aname:
                ext_info = parse_ext_info(ext_str)
                artifact_list.append({
                    "id": aid,
                    "name": aname,
                    "summary": summary,
                    "star": ext_info["star"],
                    "source": ext_info["source"],
                    "set_2": ext_info["set_2"],
                    "set_4": ext_info["set_4"]
                })
        logger.info(f"成功获取 {len(artifact_list)} 个圣遗物套装 (频道ID: {params['channel_id']})")
        for i, a in enumerate(artifact_list[:5], 1):
            logger.info(f"  {i}. {a['name']} (ID: {a['id']}) [{a['star']}]")
        return artifact_list
    except Exception as e:
        logger.exception("获取圣遗物列表失败")
        return []


# ========== 2. 解析圣遗物详情页 ==========
def parse_artifact_detail(page_data):
    """
    从圣遗物详情页的 JSON 中提取五个部件的名称、描述和故事。
    返回 dict，键为部件名称，值为包含 desc 和 story 的 dict。
    """
    modules = page_data.get("modules", [])
    parts = {}  # 存储部件信息

    for module in modules:
        comps = module.get("components", [])
        if not comps:
            continue
        comp_id = comps[0].get("component_id", "")
        raw_data = comps[0].get("data", "{}")

        if comp_id == "artifact_list_v2":
            try:
                info = json.loads(raw_data)
                # 部件名称
                name_info = info.get("name", {})
                part_name = name_info.get("value", [""])[0] if name_info.get("value") else ""
                if not part_name:
                    # 如果没有 name，可能该模块不是部件，跳过
                    continue

                # 描述
                desc_info = info.get("desc", {})
                desc_value = desc_info.get("value", [""])[0] if desc_info.get("value") else ""
                desc = clean_html_to_text(desc_value) if desc_value else ""

                # 故事
                story_info = info.get("story", {})
                story_value = story_info.get("value", [""])[0] if story_info.get("value") else ""
                story = clean_html_to_text(story_value) if story_value else ""

                # 使用部件名称作为 key（可能包含特殊字符，但后续输出时保留）
                # 但为了区分，我们使用模块名称（生之花、死之羽等）作为标识，但模块名称在 module 的 name 中
                # 而 component 数据中没有直接给出部件类型，但我们可以从模块的 name 获取
                # 模块的 name 是 "生之花"、"死之羽" 等
                module_name = module.get("name", "")
                if module_name:
                    part_key = module_name
                else:
                    # 如果模块名缺失，使用部件名称
                    part_key = part_name

                parts[part_key] = {
                    "name": part_name,
                    "desc": desc,
                    "story": story
                }
            except Exception as e:
                logger.warning(f"解析 artifact_list_v2 失败: {e}")

    return parts


# ========== 3. 获取单个圣遗物套装详情并保存 ==========
def process_artifact(artifact_id, artifact_name, output_dir="data/cleaned/artifact"):
    """
    处理单个圣遗物套装，提取详情并保存为文件。
    由调用方通过 is_crawled 控制增量。
    """
    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": artifact_id,
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
            logger.warning(f"圣遗物 {artifact_name} 无页面数据")
            return False

        # 提取部件故事
        parts = parse_artifact_detail(page)
        if not parts:
            logger.info(f"圣遗物 {artifact_name} 没有部件故事，跳过")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 获取列表中的信息（我们需要传入 artifact 对象，但这里只有 ID 和名称，所以要在调用前传入更多信息）
        # 但我们将在 run 中传递完整 artifact 对象，所以此处需要调整接口
        # 为了保持函数通用，我们将从全局或参数获取，但最简单是传入 artifact 字典
        # 这里我们改为在 run 中处理，所以这个函数将接收 artifact 字典
        # 但为了向后兼容，我们保留参数，并在 run 中直接调用 process_artifact_with_info
        # 我们稍后重构

        # 由于设计变更，我们将在 run 中直接调用新的函数，这里保留但不再使用
        # 但为了代码完整，我们保留此函数并调整

        # 实际上，我们将在 run 中直接调用一个新函数，所以这个函数可以废弃或改为接收完整信息
        # 为了不破坏结构，我们直接移除这个函数，在 run 中实现
        pass

    except Exception as e:
        logger.exception(f"处理圣遗物 {artifact_name} 失败")
        return False


# 我们重新实现处理函数，接收 artifact 字典
def process_artifact_with_info(artifact, output_dir="data/cleaned/artifact"):
    """处理单个圣遗物套装，使用 artifact 字典中的信息"""
    artifact_id = artifact["id"]
    artifact_name = artifact["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": artifact_id,
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
            logger.warning(f"圣遗物 {artifact_name} 无页面数据")
            return False

        # 提取部件故事
        parts = parse_artifact_detail(page)
        if not parts:
            logger.info(f"圣遗物 {artifact_name} 没有部件故事，跳过")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 构建文件内容
        content_lines = []
        content_lines.append(f"圣遗物套装：{artifact_name}")
        if artifact.get("summary"):
            content_lines.append(f"简介：{artifact['summary']}")
        if artifact.get("star"):
            content_lines.append(f"星级：{artifact['star']}")
        if artifact.get("source"):
            sources = "、".join(artifact["source"])
            content_lines.append(f"获取途径：{sources}")
        if artifact.get("set_2"):
            content_lines.append(f"2件套效果：{artifact['set_2']}")
        if artifact.get("set_4"):
            content_lines.append(f"4件套效果：{artifact['set_4']}")
        content_lines.append("")

        # 按顺序输出部件（生之花、死之羽、时之沙、空之杯、理之冠）
        part_order = ["生之花", "死之羽", "时之沙", "空之杯", "理之冠"]
        for part_key in part_order:
            if part_key in parts:
                part = parts[part_key]
                content_lines.append(f"【{part_key}·{part['name']}】")
                if part["desc"]:
                    content_lines.append(f"描述：{part['desc']}")
                if part["story"]:
                    content_lines.append("故事：")
                    content_lines.append(part["story"])
                content_lines.append("")

        content = "\n".join(content_lines)
        safe_name = safe_filename(artifact_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存圣遗物: {artifact_name}")
        return True

    except Exception as e:
        logger.exception(f"处理圣遗物 {artifact_name} 失败")
        return False


# ========== 4. 主运行逻辑 ==========
def run():
    artifacts = get_artifact_list()
    if not artifacts:
        logger.warning("未获取到圣遗物列表，退出")
        return

    # 统计信息
    stats = {
        "total": len(artifacts),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    # 测试限制（可调整）
    test_limit = 0  # 设置为 0 或 None 表示全量

    for idx, artifact in enumerate(artifacts[:test_limit] if test_limit else artifacts, start=1):
        aid = artifact["id"]
        aname = artifact["name"]
        logger.info(f"[{idx}/{len(artifacts)}] 正在处理: {aname} (ID: {aid})")

        # 检查是否已爬取
        if is_crawled("artifact", aid):
            logger.info(f"  跳过 (ID {aid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_artifact_with_info(artifact)
        if success:
            mark_crawled("artifact", aid, aname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": aid, "name": aname})

        # 控制请求间隔
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "artifact_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("圣遗物模块测试完成！")


if __name__ == "__main__":
    run()