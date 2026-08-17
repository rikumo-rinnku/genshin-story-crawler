"""地图文本爬虫：以稳定条目 ID 保存可用于 RAG 的交互文本。"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any

from src.core.client import get
from src.core.config_loader import get_channel_id
from src.core.dataset import save_document
from src.core.parser import clean_html_to_text
from src.core.runtime import DATA_DIR
from src.core.storage import is_crawled, mark_crawled

logger = logging.getLogger(__name__)

LIST_URL = "https://act-api-takumi-static.mihoyo.com/common/blackboard/ys_obc/v1/home/content/list"
DETAIL_URL = "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def parse_filter_tags(filter_text: str) -> dict[str, str]:
    result = {"region": "", "category": "", "is_event_only": ""}
    try:
        for tag in json.loads(filter_text):
            if tag.startswith("地区/"):
                result["region"] = tag.rsplit("/", 1)[-1]
            elif tag.startswith("分类/"):
                result["category"] = tag.rsplit("/", 1)[-1]
            elif tag.startswith("活动限定/"):
                result["is_event_only"] = tag.rsplit("/", 1)[-1]
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning("解析地图文本筛选标签失败: %s", exc)
    return result


def get_map_text_list() -> list[dict[str, str]]:
    """读取官方地图文本频道，返回条目 ID、标题与筛选元数据。"""
    try:
        response = get(
            LIST_URL,
            headers=HEADERS,
            params={"app_sn": "ys_obc", "channel_id": get_channel_id("map_text")},
        )
        payload = response.json()
    except Exception as exc:
        logger.exception("获取地图文本列表失败: %s", exc)
        return []

    if payload.get("retcode") != 0:
        logger.error("地图文本列表 API 错误: %s", payload.get("message", payload))
        return []

    categories = payload.get("data", {}).get("list", [])
    category = next((item for item in categories if item.get("name") == "地图文本"), None)
    if not category:
        logger.warning("官方列表未找到「地图文本」分类")
        return []

    records: list[dict[str, str]] = []
    for item in category.get("list", []):
        entry_id = str(item.get("content_id") or "")
        title = str(item.get("title") or "")
        if not entry_id or not title:
            continue
        tags: dict[str, str] = {}
        try:
            ext = json.loads(item.get("ext") or "{}")
            for key, value in ext.items():
                if key.startswith("c_"):
                    tags = parse_filter_tags(value.get("filter", {}).get("text", "[]"))
                    break
        except (TypeError, json.JSONDecodeError) as exc:
            logger.warning("解析地图文本 %s 的 ext 失败: %s", entry_id, exc)
        records.append(
            {
                "id": entry_id,
                "name": title,
                "summary": str(item.get("summary") or ""),
                **tags,
            }
        )
    logger.info("成功获取 %d 个地图文本条目", len(records))
    return records


def _dialogue_trees(data: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容新版 list 包装与旧版顶层对话树两种官方结构。"""
    trees = data.get("list")
    if isinstance(trees, list):
        return [tree for tree in trees if isinstance(tree, dict)]
    if data.get("contents"):
        return [data]
    return []


def parse_map_text_detail(page: dict[str, Any]) -> dict[str, str]:
    result = {"content": "", "related_tasks": ""}
    for module in page.get("modules", []):
        module_name = module.get("name", "")
        for component in module.get("components", []):
            component_id = component.get("component_id", "")
            try:
                data = json.loads(component.get("data") or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                logger.warning("解析地图文本模块 %s 失败: %s", component_id, exc)
                continue

            if component_id == "interactive_dialogue" or module_name == "交互文本":
                dialogues: list[str] = []
                for tree in _dialogue_trees(data):
                    for node in tree.get("contents", {}).values():
                        if not isinstance(node, dict):
                            continue
                        option = node.get("option", "")
                        dialogue = node.get("dialogue", "")
                        if option:
                            dialogues.append(clean_html_to_text(option))
                        if dialogue:
                            dialogues.append(clean_html_to_text(dialogue))
                result["content"] = "\n\n".join(text for text in dialogues if text.strip())
            elif component_id == "collapse_panel" and module_name == "相关任务":
                result["related_tasks"] = clean_html_to_text(data.get("rich_text", "")).strip()
    return result


def generate_map_text_content(item: dict[str, str], detail: dict[str, str]) -> str:
    lines = [f"文本名称：{item['name']}"]
    for label, key in (("地区", "region"), ("分类", "category"), ("活动限定", "is_event_only"), ("简介", "summary")):
        if item.get(key):
            lines.append(f"{label}：{item[key]}")
    if detail["content"]:
        lines.extend(("", "【内容】", detail["content"]))
    if detail["related_tasks"]:
        lines.extend(("", "【相关任务】", detail["related_tasks"]))
    return "\n".join(lines).strip()


def fetch_map_text_page(entry_id: str) -> dict[str, Any] | None:
    try:
        response = get(
            DETAIL_URL,
            headers=HEADERS,
            params={"app_sn": "ys_obc", "entry_page_id": entry_id, "lang": "zh-cn"},
        )
        payload = response.json()
    except Exception as exc:
        logger.exception("请求地图文本 %s 失败: %s", entry_id, exc)
        return None
    if payload.get("retcode") != 0:
        logger.warning("地图文本 %s API 错误: %s", entry_id, payload.get("message", payload))
        return None
    return payload.get("data", {}).get("page") or None


def get_legacy_titles() -> set[str]:
    """返回仍采用旧标题文件名、尚未迁移为稳定 ID 的地图文本标题。"""
    directory = DATA_DIR / "map_text"
    if not directory.exists():
        return set()
    id_filename = re.compile(r"^\d+(?:__.+)?$")
    return {
        path.stem
        for path in directory.glob("*.txt")
        if not id_filename.fullmatch(path.stem)
    }


def process_map_text(item: dict[str, str]) -> bool:
    page = fetch_map_text_page(item["id"])
    if not page:
        return False
    content = generate_map_text_content(item, parse_map_text_detail(page))
    if "【内容】" not in content and "【相关任务】" not in content:
        logger.warning("地图文本 %s 无可提取的正文", item["id"])
        return False
    save_document(content, "map_text", item["id"], item["name"])
    mark_crawled("map_text", item["id"], item["name"])
    logger.info("保存地图文本: %s (ID: %s)", item["name"], item["id"])
    return True


def run(
    entry_ids: set[str] | None = None,
    force: bool = False,
    repair_legacy: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    items = get_map_text_list()
    if entry_ids is not None:
        items = [item for item in items if item["id"] in entry_ids]
        missing = sorted(entry_ids - {item["id"] for item in items})
    else:
        missing = []
    legacy_titles: set[str] = set()
    if repair_legacy:
        legacy_titles = get_legacy_titles()
        items = [item for item in items if item["name"] in legacy_titles]
        logger.info("发现 %d 个旧标题文件；官方目录中可按 ID 更新 %d 条", len(legacy_titles), len(items))
    if limit is not None:
        items = items[:limit]
    stats: dict[str, Any] = {"total": len(items), "processed": 0, "skipped": 0, "failed": []}
    for index, item in enumerate(items, start=1):
        entry_id = item["id"]
        logger.info("[%d/%d] 正在处理: %s (ID: %s)", index, len(items), item["name"], entry_id)
        if not force and is_crawled("map_text", entry_id):
            stats["skipped"] += 1
            continue
        if process_map_text(item):
            stats["processed"] += 1
        else:
            stats["failed"].append({"id": entry_id, "name": item["name"]})
        time.sleep(random.uniform(1, 2))
    stats["failed"].extend({"id": entry_id, "name": "未出现在官方目录"} for entry_id in missing)
    if repair_legacy:
        matched_titles = {item["name"] for item in items}
        stats["unmatched_legacy_titles"] = sorted(legacy_titles - matched_titles)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="爬取原神观测枢地图文本")
    parser.add_argument("--entry-id", action="append", dest="entry_ids", help="仅更新指定条目 ID；可重复传入")
    parser.add_argument("--force", action="store_true", help="忽略已爬取标记，覆盖更新")
    parser.add_argument("--repair-legacy", action="store_true", help="仅按官方目录 ID 更新仍使用旧标题文件名的地图文本")
    parser.add_argument("--limit", type=int, help="本次最多处理的条目数，适合分批运行")
    args = parser.parse_args()
    print(json.dumps(
        run(set(args.entry_ids) if args.entry_ids else None, args.force, args.repair_legacy, args.limit),
        ensure_ascii=False,
        indent=2,
    ))
