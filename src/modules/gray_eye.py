"""「灰眸」图鉴爬虫模块。

抓取「灰眸」频道内的说明、臻冰造物及其外观信息。
增量状态由 config/crawled.json 维护，正文按稳定词条 ID 保存。
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

from bs4 import BeautifulSoup

from src.core.client import get
from src.core.config_loader import get_channel_id
from src.core.dataset import save_document
from src.core.parser import clean_html_to_text
from src.core.storage import is_crawled, mark_crawled

logger = logging.getLogger(__name__)

LIST_URL = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
DETAIL_URL = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def clean_inline_html(html: str) -> str:
    """将字段值压缩为单行，避免内联标签造成伪换行。"""
    return " ".join(clean_html_to_text(html).split())


def get_gray_eye_list() -> list[dict[str, str]]:
    """从频道目录获取全部「灰眸」词条。"""
    params = {"app_sn": "ys_obc", "channel_id": get_channel_id("gray_eye")}
    try:
        response = get(LIST_URL, headers=HEADERS, params=params)
        payload = response.json()
        if payload.get("retcode") != 0:
            logger.error("获取「灰眸」目录失败：%s", payload.get("message", "未知错误"))
            return []

        entries: list[dict[str, str]] = []
        for category in payload.get("data", {}).get("list", []):
            for item in category.get("list", []):
                entry_id = str(item.get("content_id", ""))
                title = str(item.get("title", "")).strip()
                if entry_id and title:
                    entries.append({
                        "id": entry_id,
                        "name": title,
                        "summary": str(item.get("summary", "")).strip(),
                    })
        logger.info("成功获取 %s 个「灰眸」词条（频道 ID：%s）", len(entries), params["channel_id"])
        return entries
    except Exception:
        logger.exception("获取「灰眸」目录失败")
        return []


def extract_values(field: dict[str, Any]) -> list[str]:
    """提取组件字段中的文本数组，并仅保留可读文本。"""
    values = field.get("value", []) if isinstance(field, dict) else []
    if not isinstance(values, list):
        values = [values]
    return [clean_inline_html(str(value)) for value in values if value and clean_inline_html(str(value))]


def parse_multi_table(data: dict[str, Any]) -> list[str]:
    """解析表格，自动忽略全是图片或空内容的列。"""
    rendered_tables: list[str] = []
    for table in data.get("tables", []):
        headers = [clean_inline_html(str(value)) for value in table.get("header", [])]
        rows = [
            [clean_inline_html(str(cell)) if cell else "" for cell in row]
            for row in table.get("row", [])
        ]
        width = max([len(headers), *(len(row) for row in rows)], default=0)
        if not width:
            continue

        # 图片列在文本清洗后为空；无可读内容的列不应写进语料。
        included_columns = [
            index for index in range(width)
            if (headers[index] if index < len(headers) else "")
            or any(index < len(row) and row[index] for row in rows)
        ]
        # 即使图片列表头有文字，只要整列没有可读单元格也排除。
        included_columns = [
            index for index in included_columns
            if any(index < len(row) and row[index] for row in rows)
        ]
        if not included_columns:
            continue

        lines: list[str] = []
        visible_headers = [headers[index] if index < len(headers) else f"字段{index + 1}" for index in included_columns]
        if any(visible_headers):
            lines.append(" | ".join(visible_headers))
        for row in rows:
            values = [row[index] if index < len(row) else "" for index in included_columns]
            if any(values):
                lines.append(" | ".join(values))
        if lines:
            rendered_tables.append("\n".join(lines))
    return rendered_tables


def parse_interactive_dialogue(data: dict[str, Any]) -> list[str]:
    """按对话树顺序提取「灰眸」讯息中的选项和正文。"""
    list_data = data.get("list")
    if not list_data:
        root_id = data.get("root_id")
        contents = data.get("contents")
        if root_id and contents:
            list_data = [{
                "root_id": root_id,
                "child_ids": data.get("child_ids", {}),
                "contents": contents,
            }]
        else:
            return []

    paragraphs: list[str] = []
    for item in list_data:
        if not isinstance(item, dict):
            continue
        root_id = item.get("root_id")
        child_ids = item.get("child_ids", {})
        contents = item.get("contents", {})
        if not root_id or not isinstance(contents, dict):
            continue

        def traverse(node_id: str, visited: set[str]) -> None:
            if node_id in visited or node_id not in contents:
                return
            visited.add(node_id)
            node = contents[node_id]
            option = clean_inline_html(str(node.get("option", "")))
            dialogue = clean_html_to_text(str(node.get("dialogue", ""))).strip()
            if option:
                paragraphs.append(f"[选项] {option}")
            if dialogue:
                paragraphs.append(dialogue)
            for child_id in child_ids.get(node_id, []):
                traverse(str(child_id), visited)

        traverse(str(root_id), set())
    return paragraphs


def parse_gray_eye_detail(page: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """按页面模块顺序提取「灰眸」正文，返回（模块名，段落列表）。"""
    sections: list[tuple[str, list[str]]] = []
    for module in page.get("modules", []):
        module_name = str(module.get("name", "")).strip() or "内容"
        paragraphs: list[str] = []
        for component in module.get("components", []):
            try:
                data = json.loads(component.get("data", "{}"))
            except (TypeError, json.JSONDecodeError):
                logger.warning("词条模块「%s」的组件数据不是有效 JSON", module_name)
                continue

            component_id = component.get("component_id", "")
            if component_id == "dress_base_info":
                for attr in data.get("attr", []):
                    key = str(attr.get("key", "")).strip()
                    values = extract_values(attr)
                    if not values:
                        continue
                    text = "；".join(values)
                    paragraphs.append(text if not key or key == module_name else f"{key}：{text}")
            elif component_id == "artifact_list_v2":
                for field_name, output_name in (
                    ("name", "造物名称"),
                    ("desc", "制造需求"),
                    ("story", "造物描述"),
                ):
                    values = extract_values(data.get(field_name, {}))
                    if values:
                        paragraphs.append(f"{output_name}：{'；'.join(values)}")
            elif component_id == "multi_table":
                paragraphs.extend(parse_multi_table(data))
            elif component_id == "interactive_dialogue":
                paragraphs.extend(parse_interactive_dialogue(data))
            else:
                rich_text = data.get("rich_text", "")
                if rich_text:
                    text = clean_html_to_text(str(rich_text))
                    if text.strip():
                        paragraphs.append(text.strip())
        if paragraphs:
            sections.append((module_name, paragraphs))
    return sections


def generate_gray_eye_content(entry: dict[str, str], sections: list[tuple[str, list[str]]]) -> str:
    """生成统一、可直接入库的纯文本正文。"""
    lines = [f"灰眸词条：{entry['name']}"]
    if entry.get("summary"):
        lines.append(f"简介：{entry['summary']}")

    for section_name, paragraphs in sections:
        lines.extend(("", f"【{section_name}】", *paragraphs))
    return "\n".join(lines).strip()


def process_gray_eye_entry(entry: dict[str, str]) -> bool:
    """请求并保存一个「灰眸」词条。"""
    try:
        response = get(
            DETAIL_URL,
            headers=HEADERS,
            params={"app_sn": "ys_obc", "entry_page_id": entry["id"], "lang": "zh-cn"},
        )
        payload = response.json()
        if payload.get("retcode") != 0:
            logger.error("词条 %s 请求失败：%s", entry["name"], payload.get("message", "未知错误"))
            return False

        page = payload.get("data", {}).get("page", {})
        sections = parse_gray_eye_detail(page)
        if not sections:
            logger.warning("词条 %s 没有可保存的文本内容", entry["name"])
            return False

        content = generate_gray_eye_content(entry, sections)
        save_document(content, "gray_eye", entry["id"], entry["name"])
        logger.info("保存「灰眸」词条：%s", entry["name"])
        return True
    except Exception:
        logger.exception("处理「灰眸」词条失败：%s", entry["name"])
        return False


def run() -> dict[str, Any]:
    """增量抓取全部「灰眸」词条。"""
    entries = get_gray_eye_list()
    stats: dict[str, Any] = {"total": len(entries), "processed": 0, "skipped": 0, "failed": []}
    if not entries:
        return stats

    for index, entry in enumerate(entries, start=1):
        entry_id = entry["id"]
        logger.info("[%s/%s] 正在处理「灰眸」词条：%s（ID：%s）", index, len(entries), entry["name"], entry_id)
        if is_crawled("gray_eye", entry_id):
            stats["skipped"] += 1
            continue
        if process_gray_eye_entry(entry):
            mark_crawled("gray_eye", entry_id, entry["name"])
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": entry_id, "name": entry["name"]})
        time.sleep(random.uniform(1, 2))
    return stats


if __name__ == "__main__":
    run()
