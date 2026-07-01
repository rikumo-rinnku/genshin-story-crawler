"""
书籍爬虫模块
从观测枢爬取所有书籍的详细信息，包括书籍类型、获取方式、描述、作者、各卷正文等。
增量策略：使用 crawled.json 记录已爬取的书籍 ID，实现增量更新。
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
        logging.FileHandler(os.path.join(LOG_DIR, "book.log"), encoding='utf-8', mode='a'),
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
    从获取方式的 HTML 中提取紧凑格式的纯文本。
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_filter_tags(filter_text: str) -> dict:
    """
    解析 ext 中 filter.text 的 JSON 数组，提取书籍类型和获取方式。
    返回 dict。
    """
    result = {
        "book_type": "",   # 童话寓言/小说故事/民俗传说/诗歌哲学/学术作品/手册指南/书信日志
        "obtain": ""       # NPC购买/地图探索/任务获取
    }
    try:
        tags = json.loads(filter_text)
        for tag in tags:
            if tag.startswith("书籍类型/"):
                result["book_type"] = tag.split("/")[-1]
            elif tag.startswith("获取方式/"):
                result["obtain"] = tag.split("/")[-1]
    except Exception as e:
        logger.warning(f"解析 filter 标签失败: {e}")
    return result


# ========== 1. 获取书籍列表 ==========
def get_book_list():
    """
    获取所有书籍的 ID、名称、简介和分类标签。
    返回: list of dict
    """
    url = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
    params = {
        "app_sn": "ys_obc",
        "channel_id": get_channel_id("book")   # 从配置文件读取频道 ID (68)
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
            logger.warning("未找到外层书籍列表")
            return []

        # 查找 name 为 "书籍" 的分类
        target_category = None
        for cat in outer_list:
            if cat.get("name") == "书籍":
                target_category = cat
                break
        if not target_category:
            logger.warning("未找到「书籍」分类")
            return []

        items = target_category.get("list", [])
        book_list = []
        for item in items:
            bid = str(item.get("content_id"))
            bname = item.get("title")
            summary = item.get("summary", "")
            ext_str = item.get("ext", "{}")
            if bid and bname:
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
                    logger.warning(f"解析书籍 ext 失败: {e}")

                book_list.append({
                    "id": bid,
                    "name": bname,
                    "summary": summary,
                    "book_type": tags.get("book_type", ""),
                    "obtain": tags.get("obtain", "")
                })
        logger.info(f"成功获取 {len(book_list)} 本书籍 (频道ID: {params['channel_id']})")
        for i, book in enumerate(book_list[:5], 1):
            logger.info(f"  {i}. {book['name']} (ID: {book['id']}) [{book['book_type']}]")
        return book_list
    except Exception as e:
        logger.exception("获取书籍列表失败")
        return []


# ========== 2. 解析书籍详情页 ==========
def parse_book_detail(page_data):
    """
    从书籍详情页的 JSON 中提取元数据和各卷正文。
    返回 dict，包含 book_info (dict) 和 chapters (list)。
    """
    modules = page_data.get("modules", [])
    
    book_info = {
        "description": "",
        "author": "",
        "obtain": ""
    }
    chapters = []  # 每个元素为 {"title": "第一卷", "content": "..."}
    
    # 用于去重：记录已处理的 material_base_info 的获取方式
    # 同一个书籍可能有多卷，每卷都有一个 material_base_info 包含相同的元数据
    seen_obtain = set()
    
    for module in modules:
        comps = module.get("components", [])
        if not comps:
            continue
        
        comp_id = comps[0].get("component_id", "")
        module_name = module.get("name", "")
        
        # 处理元数据 (material_base_info)
        if comp_id == "material_base_info":
            try:
                data = json.loads(comps[0].get("data", "{}"))
                
                # 提取获取方式
                obtain = ""
                if data.get("materials", {}).get("value"):
                    obtain = extract_materials_text(data["materials"]["value"])
                
                # 如果获取方式已经处理过，跳过（避免重复）
                if obtain and obtain in seen_obtain:
                    continue
                if obtain:
                    seen_obtain.add(obtain)
                    book_info["obtain"] = obtain
                
                # 提取描述和作者
                for attr in data.get("attr", []):
                    key = attr.get("key", "")
                    values = attr.get("value", [])
                    if not values:
                        continue
                    text = clean_html_to_text(values[0]) if values[0] else ""
                    if not text:
                        continue
                    if key == "描述":
                        book_info["description"] = text
                    elif key == "作者":
                        book_info["author"] = text
            except Exception as e:
                logger.warning(f"解析 material_base_info 失败: {e}")
        
        # 处理正文章节 (collapse_panel)
        elif comp_id == "collapse_panel":
            # 有名字的 collapse_panel 才是正文章节（排除空模块）
            if module_name and module_name.strip():
                try:
                    data = json.loads(comps[0].get("data", "{}"))
                    rich_text = data.get("rich_text", "")
                    if rich_text and rich_text.strip():
                        content = clean_html_to_text(rich_text)
                        if content.strip():
                            chapters.append({
                                "title": module_name,
                                "content": content
                            })
                except Exception as e:
                    logger.warning(f"解析 collapse_panel 失败: {e}")

    return book_info, chapters


# ========== 3. 生成文件内容 ==========
def generate_book_content(book_info, book_meta, chapters):
    """
    根据书籍信息和各卷正文生成纯文本内容。
    参数：
        book_info (dict): 包含 id, name, summary, book_type, obtain
        book_meta (dict): 包含 description, author, obtain（详情页获取方式，优先级更高）
        chapters (list): 各卷正文列表
    返回：
        str: 格式化后的纯文本内容
    """
    lines = []

    # 1. 基本信息（优先使用详情页的获取方式）
    lines.append(f"书籍名称：{book_info['name']}")
    if book_info.get("book_type"):
        lines.append(f"书籍类型：{book_info['book_type']}")
    
    # 获取方式：详情页优先，列表页作为备选
    obtain = book_meta.get("obtain") or book_info.get("obtain")
    if obtain:
        lines.append(f"获取方式：{obtain}")
    
    if book_info.get("summary"):
        lines.append(f"简介：{book_info['summary']}")
    lines.append("")

    # 2. 描述（如果有）
    if book_meta.get("description"):
        lines.append("【描述】")
        lines.append(book_meta["description"])
        lines.append("")

    # 3. 作者（如果有）
    if book_meta.get("author"):
        lines.append("【作者】")
        lines.append(book_meta["author"])
        lines.append("")

    # 4. 各卷正文
    for chapter in chapters:
        title = chapter.get("title", "")
        content = chapter.get("content", "")
        if not content:
            continue
        if title:
            lines.append(f"【{title}】")
        else:
            lines.append("【正文】")
        lines.append(content)
        lines.append("")

    return "\n".join(lines).strip()


# ========== 4. 处理单本书籍 ==========
def process_book(book_info, output_dir="data/cleaned/book"):
    """
    处理单本书籍：请求详情 API，解析内容，生成文件。
    参数：
        book_info (dict): 包含 id, name, summary, book_type, obtain
        output_dir (str): 输出目录路径
    返回：
        bool: 成功返回 True，失败返回 False
    """
    book_id = book_info["id"]
    book_name = book_info["name"]

    url = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
    params = {
        "app_sn": "ys_obc",
        "entry_page_id": book_id,
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
            logger.warning(f"书籍 {book_name} 无页面数据")
            return False

        # 解析详情
        book_meta, chapters = parse_book_detail(page)

        # 检查是否有有效内容（至少有一个章节）
        if not chapters:
            logger.info(f"书籍 {book_name} 无正文内容，跳过")
            return False

        # 生成内容
        content = generate_book_content(book_info, book_meta, chapters)
        if not content.strip():
            logger.info(f"书籍 {book_name} 生成内容为空")
            return False

        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)

        # 生成安全文件名
        safe_name = safe_filename(book_name)
        filepath = os.path.join(output_dir, f"{safe_name}.txt")

        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"  保存书籍: {book_name}")
        return True

    except Exception as e:
        logger.exception(f"处理书籍 {book_name} 失败")
        return False


# ========== 5. 主运行逻辑 ==========
def run():
    """
    主流程：
    1. 获取书籍列表。
    2. 遍历每本书籍，检查是否已爬取（基于 crawled.json）。
    3. 若未爬取，则调用 process_book 处理。
    4. 成功则标记已爬取，失败则记录失败信息。
    5. 生成爬取报告。
    """
    # 获取书籍列表
    books = get_book_list()
    if not books:
        logger.warning("未获取到书籍列表，退出")
        return

    # 统计信息
    stats = {
        "total": len(books),
        "processed": 0,
        "failed": [],
        "skipped": 0
    }

    # 测试限制（可调整）
    test_limit = 0  # 设置为 0 或 None 表示全量

    for idx, book in enumerate(books[:test_limit] if test_limit else books, start=1):
        bid = book["id"]
        bname = book["name"]
        logger.info(f"[{idx}/{len(books)}] 正在处理: {bname} (ID: {bid})")

        # 增量检查
        if is_crawled("book", bid):
            logger.info(f"  跳过 (ID {bid} 已爬取过)。")
            stats["skipped"] += 1
            continue

        success = process_book(book)
        if success:
            mark_crawled("book", bid, bname)
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": bid, "name": bname})

        # 控制请求频率
        time.sleep(random.uniform(1, 2))

    # 生成报告
    report_path = os.path.join(LOG_DIR, "book_report.json")
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)
        logger.info(f"爬取报告已保存至 {report_path}")
    except Exception as e:
        logger.error(f"保存报告失败: {e}")

    logger.info("书籍模块爬取完成！")


if __name__ == "__main__":
    run()