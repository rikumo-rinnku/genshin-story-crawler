"""
组织剧情爬虫模块
从观测枢爬取所有组织的详细信息，包括简介、重要事件、成员列表等。
增量策略：使用 crawled.json 记录已爬取的组织 ID，实现增量更新。
"""
import json
import re
import time
import random
import logging
import os
import html as html_module
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
        logging.FileHandler(os.path.join(LOG_DIR, "organization.log"), encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ========== 工具函数 ==========
def safe_filename(name: str) -> str:
    """
    清洗非法字符，生成安全的文件名。
    将 Windows 文件名中不允许的字符替换为下划线。
    """
    return re.sub(r'[\\/*?:"<>|]', '_', name)


def parse_ext_info(ext_str: str) -> str:
    """
    解析组织列表中的 ext 字段，提取地区信息。
    返回地区名称，若未找到则返回空字符串。
    """
    region = ""
    try:
        ext_data = json.loads(ext_str)
        # 结构通常为 {"c_255": {"filter": {"text": "[...]"}}}
        for key, value in ext_data.items():
            if key.startswith("c_"):
                filter_text = value.get("filter", {}).get("text", "[]")
                items = json.loads(filter_text)
                for item in items:
                    if item.startswith("地区/"):
                        region = item.split("/")[-1]
                        break
                if region:
                    break
    except Exception as e:
        logger.warning(f"解析组织 ext 失败: {e}")
    return region


def is_collapse_panel_empty(rich_text: str) -> bool:
    """
    判断 collapse_panel 中的 rich_text 是否为空或无实质性内容。
    去除所有 HTML 标签和 &nbsp; 后，若文本长度小于 10 或仅含空白，则视为空。
    """
    if not rich_text:
        return True
    # 去除所有 HTML 标签
    text = re.sub(r'<[^>]+>', '', rich_text)
    text = text.replace('&nbsp;', '').strip()
    if len(text) < 10 or not text.strip():
        return True
    return False


# ========== 1. 获取组织列表 ==========
def get_organization_list():
    """
    获取所有组织的 ID、名称和地区信息。
    返回: list of dict，每个 dict 包含 id, name, region
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("organization")   # 从配置文件读取频道 ID (255)
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
            logger.warning("未找到外层组织列表")
            return []

        # 查找 name 为 "组织" 的分类
        org_category = None
        for cat in outer_list:
            if cat.get("name") == "组织":
                org_category = cat
                break
        if not org_category:
            logger.warning("未找到「组织」分类")
            return []

        items = org_category.get("list", [])
        org_list = []
        for item in items:
            oid = str(item.get("content_id"))
            oname = item.get("title")
            ext_str = item.get("ext", "{}")
            if oid and oname:
                region = parse_ext_info(ext_str)
                org_list.append({
                    "id": oid,
                    "name": oname,
                    "region": region
                })
        logger.info(f"成功获取 {len(org_list)} 个组织 (频道ID: {params['channel_id']})")
        for i, o in enumerate(org_list[:5], 1):
            logger.info(f"  {i}. {o['name']} (ID: {o['id']}) [{o['region']}]")
        return org_list
    except Exception as e:
        logger.exception("获取组织列表失败")
        return []


# ========== 2. 引用替换（核心） ==========
def replace_references_in_html(html: str) -> str:
    """
    将 HTML 中所有 data-type="详情" 的 span 替换为 (相关内容：xxx yyy zzz) 格式的字符串。

    处理逻辑：
    1. 使用正则匹配所有 data-type="详情" 的 <span>。
    2. 提取其 data-name 属性值（HTML 实体编码的字符串）。
    3. 解码 HTML 实体，得到真实的 HTML 片段。
    4. 用 BeautifulSoup 解析解码后的片段。
    5. 遍历所有 <p> 标签，提取每个 custom-entry-wrapper 的完整显示文本（含后缀如“·神之眼”）。
    6. 将所有名称合并为一个括号内，用空格分隔。

    返回值：替换后的 HTML 字符串。
    """
    if not html:
        return html

    def process_detail(match):
        full_tag = match.group(0)
        data_name_match = re.search(r'data-name="([^"]*)"', full_tag)
        if not data_name_match:
            return ""

        encoded = data_name_match.group(1)
        decoded = html_module.unescape(encoded)  # &lt; 变为 <

        soup = BeautifulSoup(decoded, 'lxml')
        names = []
        # 遍历每个 p 标签，因为引用通常包裹在 p 中
        for p in soup.find_all('p'):
            wrappers = p.find_all('span', class_='custom-entry-wrapper')
            if not wrappers:
                continue
            for wrapper in wrappers:
                main_text = wrapper.get_text(strip=True)
                if not main_text:
                    continue
                # 获取 wrapper 后面的纯文本节点（即后缀，如“·神之眼”）
                suffix = ""
                next_sib = wrapper.next_sibling
                if next_sib and isinstance(next_sib, str):
                    suffix = next_sib.strip()
                full_name = main_text + suffix
                if full_name:
                    names.append(full_name)

        if names:
            return f"(相关内容：{' '.join(names)})"
        return ""

    pattern = r'<span[^>]*data-type="详情"[^>]*>.*?</span>'
    return re.sub(pattern, process_detail, html, flags=re.DOTALL)


# ========== 3. 解析组织详情页 ==========
def parse_organization_detail(page_data):
    """
    从组织详情页的 JSON 中提取简介、重要事件和所有有意义的 collapse_panel 模块。
    返回 dict，包含 title, description, events, sections。
    """
    modules = page_data.get("modules", [])
    result = {
        "title": page_data.get("name", ""),
        "description": "",   # 从 good_desc 提取
        "events": [],        # timeline_base_info 事件列表
        "sections": []       # 其他 collapse_panel 模块（含标题和内容）
    }

    for module in modules:
        comps = module.get("components", [])
        if not comps:
            continue
        comp_id = comps[0].get("component_id", "")
        module_name = module.get("name", "")
        raw_data = comps[0].get("data", "{}")

        if comp_id == "good_desc":
            try:
                info = json.loads(raw_data)
                rich_text = info.get("rich_text", "")
                description_parts = []

                if rich_text:
                    rich_text = replace_references_in_html(rich_text)
                    description_parts.append(clean_html_to_text(rich_text))

                # 提取 attr 字段中的额外信息（如“魔神战争”、“尘世七执政”）
                attr_list = info.get("attr", [])
                for attr in attr_list:
                    key = attr.get("key", "")
                    values = attr.get("value", [])
                    if key and values:
                        # 拼接多个 value（通常只有一个）
                        content = "\n".join([clean_html_to_text(v) for v in values if v])
                        if content:
                            description_parts.append(f"{key}：{content}")

                if description_parts:
                    result["description"] = "\n\n".join(description_parts)
            except Exception as e:
                logger.warning(f"解析 good_desc 失败: {e}")

        elif comp_id == "timeline_base_info":
            # 重要事件模块（时间线）
            try:
                info = json.loads(raw_data)
                event_list = info.get("list", [])
                for event_group in event_list:
                    attr_list = event_group.get("attr", [])
                    for attr in attr_list:
                        key = attr.get("key", "")
                        values = attr.get("value", [])
                        if not values:
                            continue
                        processed_values = []
                        for v in values:
                            if v:
                                v = replace_references_in_html(v)  # 处理事件内容中的引用
                                processed_values.append(clean_html_to_text(v))
                        content = "\n".join(processed_values)
                        if content.strip():
                            result["events"].append({
                                "title": key,
                                "content": content
                            })
            except Exception as e:
                logger.warning(f"解析 timeline_base_info 失败: {e}")

        elif comp_id == "collapse_panel":
            # 可折叠面板（包含成员列表、下属组织等）
            try:
                info = json.loads(raw_data)
                rich_text = info.get("rich_text", "")
                if not is_collapse_panel_empty(rich_text):
                    rich_text = replace_references_in_html(rich_text)
                    text = clean_html_to_text(rich_text)
                    if text.strip():
                        if not module_name:
                            module_name = "内容"
                        result["sections"].append({
                            "title": module_name,
                            "content": text
                        })
            except Exception as e:
                logger.warning(f"解析 collapse_panel 失败: {e}")

    return result

# ========== 3. 生成文件内容 ==========
def generate_organization_content(org_info, detail):
    """
    根据组织信息和详情数据生成纯文本内容，用于写入 .txt 文件。

    参数：
        org_info (dict): 包含组织名称和地区等基本信息，如 {"name": "魔神", "region": "跨国家"}
        detail (dict): parse_organization_detail 返回的解析结果，包含 description, events, sections

    返回：
        str: 格式化后的纯文本内容，各部分用空行分隔。
    """
    lines = []
    
    # 1. 组织基本元信息
    lines.append(f"组织名称：{org_info['name']}")
    if org_info.get("region"):
        lines.append(f"地区：{org_info['region']}")
    lines.append("")  # 空行分隔

    # 2. 简介（从 good_desc 提取）
    if detail.get("description"):
        lines.append("【简介】")
        lines.append(detail["description"])
        lines.append("")  # 空行分隔

    # 3. 重要事件（从 timeline_base_info 提取）
    if detail.get("events"):
        lines.append("【重要事件】")
        for event in detail["events"]:
            lines.append(f"{event['title']}：")  # 事件标题（如“安德留斯”）
            lines.append(event["content"])      # 事件描述
            lines.append("")                    # 每个事件后空行
        lines.append("")  # 事件部分结束后额外空行

    # 4. 其他章节（从 collapse_panel 提取的各个板块）
    for section in detail.get("sections", []):
        title = section["title"]
        if title:
            lines.append(f"【{title}】")       # 使用模块名作为标题
        else:
            lines.append("【内容】")            # 如果模块无名称，使用默认标题
        lines.append(section["content"])
        lines.append("")  # 每个板块后空行

    # 去除末尾多余换行（strip 会移除首尾空白，但保留内部换行）
    return "\n".join(lines).strip()


# ========== 4. 处理单个组织 ==========
def process_organization(org, output_dir="data/cleaned/organization"):
    """
    处理单个组织：请求详情 API，解析内容，生成文件。

    参数：
        org (dict): 包含组织的 id、name、region 等信息
        output_dir (str): 输出目录路径

    返回：
        bool: 成功返回 True，失败返回 False（用于主流程判断是否标记已爬取）
    """
    org_id = org["id"]
    org_name = org["name"]

    # 详情 API 地址（与角色、武器等模块相同）
    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": org_id,
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
            logger.warning(f"组织 {org_name} 无页面数据")
            return False

        # 解析详情页内容
        detail = parse_organization_detail(page)

        # 如果没有任何有效内容，视为失败，不生成文件
        if not detail.get("description") and not detail.get("events") and not detail.get("sections"):
            logger.info(f"组织 {org_name} 无任何可提取内容")
            return False

        # 生成纯文本内容
        content = generate_organization_content(org, detail)
        if not content.strip():
            logger.info(f"组织 {org_name} 生成内容为空")
            return False

        # 创建输出目录（如果不存在）
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名（替换非法字符）
        safe_name = safe_filename(org_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存组织: {org_name}")
        return True

    except Exception as e:
        logger.exception(f"处理组织 {org_name} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取组织列表。
    2. 遍历每个组织，检查是否已爬取（基于 crawled.json）。
    3. 若未爬取，则调用 process_organization 处理。
    4. 成功则标记已爬取，失败则记录失败信息。
    5. 生成爬取报告。
    """
    # 获取组织列表
    orgs = get_organization_list()
    if not orgs:
        logger.warning("未获取到组织列表，退出")
        return

    # 统计信息
    stats = {
        "total": len(orgs),
        "processed": 0,      # 成功处理数
        "failed": [],        # 失败列表
        "skipped": 0         # 已爬取跳过数
    }

    # 测试限制：设置为 0 或 None 表示全量爬取
    test_limit = 0  # 可根据需要调整

    for idx, org in enumerate(orgs[:test_limit] if test_limit else orgs, start=1):
        oid = org["id"]
        oname = org["name"]
        logger.info(f"[{idx}/{len(orgs)}] 正在处理: {oname} (ID: {oid})")

        # 增量检查：如果已爬取，跳过
        if is_crawled("organization", oid):
            logger.info(f"  跳过 (ID {oid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        # 处理组织
        success = process_organization(org)
        if success:
            mark_crawled("organization", oid, oname)  # 标记已爬取
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": oid, "name": oname})

        # 控制请求频率，避免触发反爬
        time.sleep(random.uniform(1, 2))

    # 生成爬取报告（JSON 格式）
    report_path = os.path.join(LOG_DIR, "organization_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("组织模块测试完成！")


if __name__ == "__main__":
    run()