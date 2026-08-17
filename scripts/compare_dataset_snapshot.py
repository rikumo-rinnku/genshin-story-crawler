"""对比标题命名的旧语料快照与当前基于 Manifest 的数据集。

报告会明确区分正文完全一致、新出现在快照后、以及仅格式变化或被修复的文档。
新出现不等同于游戏版本新增；版本归属仍需官方版本标记或人工复核确认。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "data" / "backups" / "cleaned-pre-manifest-20260817"
DEFAULT_MANIFEST = ROOT / "data" / "manifest.jsonl"
DEFAULT_CURRENT_CRAWLED = ROOT / "config" / "crawled.json"
DEFAULT_BASELINE_CRAWLED = ROOT / "config" / "crawled.20260816-200306_backup.json"
DEFAULT_OUTPUT = ROOT / "logs" / "dataset_snapshot_comparison.json"
VERSION_7 = re.compile(r"版本(?:号)?[：:]\s*7\.0(?:\D|$)")
CATEGORY_LABELS = {
    "unchanged_exact": "与更新前正文完全一致",
    "existing_changed_or_repaired": "旧标题可匹配，但正文已变化或已修复",
    "known_before_snapshot_changed_or_missing": "更新前已知，正文变化或旧副本缺失",
    "new_since_snapshot_version_7_confirmed": "确认属于 7.0 的新增文本",
    "new_since_snapshot_version_unverified": "快照后新出现，版本归属待确认",
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_module(module: str) -> str:
    return "anecdote" if module == "character_anecdote" else module


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_baseline(root: Path) -> tuple[dict[str, Counter[str]], dict[str, set[str]]]:
    hashes: dict[str, Counter[str]] = defaultdict(Counter)
    names: dict[str, set[str]] = defaultdict(set)
    for module_dir in root.iterdir():
        if not module_dir.is_dir():
            continue
        for path in module_dir.glob("*.txt"):
            hashes[module_dir.name][file_hash(path)] += 1
            names[module_dir.name].add(path.stem)
    return hashes, names


def normalized_title(value: str) -> str:
    return re.sub(r"[\s《》【】\[\]（）()]", "", value)


def load_crawled_titles(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        module: {str(entry_id): str(title) for entry_id, title in entries.items()}
        for module, entries in payload.items()
        if isinstance(entries, dict)
    }


def existed_by_title(title: str, module: str, names: set[str], parent_title: str = "") -> bool:
    """为旧标题命名文本提供保守的标题匹配兜底。

    普通模块使用精确标题。任务和角色逸闻的备份文件通常带有父级标题前缀，
    因此通过后缀匹配识别可能已存在的条目，但不将其当作正文完全一致。
    """
    if title in names or parent_title in names:
        return True
    if module == "task" and parent_title:
        normalized_parent = normalized_title(parent_title)
        return any(normalized_title(name).startswith(f"{normalized_parent}_") for name in names)
    if module == "character" and parent_title:
        normalized_parent = normalized_title(parent_title)
        return any(normalized_parent and normalized_parent in normalized_title(name) for name in names)
    if module == "character_anecdote":
        return any(name.endswith(f"_{title}") for name in names)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--current-crawled", type=Path, default=DEFAULT_CURRENT_CRAWLED)
    parser.add_argument("--baseline-crawled", type=Path, default=DEFAULT_BASELINE_CRAWLED)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.baseline.is_dir():
        raise SystemExit(f"baseline directory not found: {args.baseline}")
    if not args.manifest.is_file():
        raise SystemExit(f"manifest not found: {args.manifest}")

    baseline_hashes, baseline_names = load_baseline(args.baseline)
    crawled_titles = load_crawled_titles(args.current_crawled)
    baseline_crawled = load_crawled_titles(args.baseline_crawled)
    records = load_manifest(args.manifest)
    remaining = {module: counts.copy() for module, counts in baseline_hashes.items()}
    summary: dict[str, Counter[str]] = defaultdict(Counter)
    details: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for record in records:
        module = str(record["module"])
        old_module = source_module(module)
        text_path = ROOT / record["path"]
        text = text_path.read_text(encoding="utf-8")
        digest = file_hash(text_path)
        old_count = remaining.get(old_module, Counter())
        parent_module = "character" if module == "character_anecdote" else module
        parent_title = crawled_titles.get(parent_module, {}).get(str(record.get("entry_id", "")), "")

        if old_count[digest] > 0:
            category = "unchanged_exact"
            old_count[digest] -= 1
        elif (
            module != "character_anecdote"
            and str(record.get("entry_id", "")) in baseline_crawled.get(parent_module, {})
        ):
            category = "known_before_snapshot_changed_or_missing"
        elif module == "task" and VERSION_7.search(text):
            # 任务 ID 有可靠的更新前增量状态。带有官方 7.0 标记的新任务 ID，
            # 比偶然撞名的旧子任务标题更能证明它属于该版本新增。
            category = "new_since_snapshot_version_7_confirmed"
        elif existed_by_title(
            str(record.get("title", "")), module, baseline_names.get(old_module, set()), parent_title
        ):
            category = "existing_changed_or_repaired"
        elif VERSION_7.search(text):
            category = "new_since_snapshot_version_7_confirmed"
        else:
            category = "new_since_snapshot_version_unverified"

        summary[module][f"{category}_documents"] += 1
        summary[module][f"{category}_characters"] += len(text)
        if category != "unchanged_exact":
            details[category].append(
                {
                    "doc_id": record["doc_id"],
                    "module": module,
                    "entry_id": record.get("entry_id"),
                    "section_id": record.get("section_id"),
                    "title": record.get("title"),
                    "path": record["path"],
                    "characters": len(text),
                }
            )

    totals: Counter[str] = Counter()
    for counters in summary.values():
        totals.update(counters)
    category_keys = list(CATEGORY_LABELS)
    payload = {
        "报告说明": [
            "“正文完全一致”使用一对一的 SHA-256 正文哈希对比。",
            "“更新前已知”表示旧增量状态已有该 ID，即使旧副本缺失，也不应算作新游戏条目。",
            "“确认属于 7.0”要求新任务 ID 的当前正文中存在明确的 7.0 标记。",
            "“版本归属待确认”表示文本不在旧快照中，仍需官方目录或人工复核来区分 7.0 新增与历史漏抓。",
        ],
        "对比基线目录": str(args.baseline.relative_to(ROOT)),
        "当前清单文件": str(args.manifest.relative_to(ROOT)),
        "基线文档数": sum(sum(counter.values()) for counter in baseline_hashes.values()),
        "当前文档数": len(records),
        "统计摘要": [
            {
                "类别": CATEGORY_LABELS[key],
                "文档数": totals[f"{key}_documents"],
                "正文字符数": totals[f"{key}_characters"],
            }
            for key in category_keys
        ],
        "按模块统计": [
            {
                "模块": module,
                "统计": [
                    {
                        "类别": CATEGORY_LABELS[key],
                        "文档数": counters[f"{key}_documents"],
                        "正文字符数": counters[f"{key}_characters"],
                    }
                    for key in category_keys
                    if counters[f"{key}_documents"]
                ],
            }
            for module, counters in sorted(summary.items())
        ],
        "待复核明细": {
            CATEGORY_LABELS[key]: [
                {
                    "文档ID": item["doc_id"],
                    "模块": item["module"],
                    "条目ID": item["entry_id"],
                    "分段ID": item["section_id"],
                    "标题": item["title"],
                    "正文路径": item["path"],
                    "正文字符数": item["characters"],
                }
                for item in values
            ]
            for key, values in details.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入对比报告：{args.output}")
    for item in payload["统计摘要"]:
        print(f"{item['类别']}：{item['文档数']} 份，{item['正文字符数']} 字符")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
