"""从现有任务正文中同步官方词条已提取的数字版本号。"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FILE = ROOT / "data" / "manifest.jsonl"
REGISTRY_FILE = ROOT / "metadata" / "release" / "version_registry.json"
DEFAULT_REPORT = ROOT / "logs" / "task_version_sync_audit.json"
VERSION_LINE_RE = re.compile(r"^版本号：(\d+\.\d+)$", re.MULTILINE)
STAGE_LINE_RE = re.compile(r"^版本号：月之([一二三四五六七八])$", re.MULTILINE)
STAGE_NUMBER = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sync_task_versions(*, apply: bool) -> dict:
    """仅同步正文中已明确写出的数字版本，不从日期或标题推测。"""
    registry = load_json(REGISTRY_FILE)
    documents = registry.setdefault("documents", {})
    stages = registry.get("story_arc_rules", {}).get("song_of_the_welkin_moon", {}).get("stage_versions", {})
    records = [
        json.loads(line)
        for line in MANIFEST_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    discovered: dict[str, str] = {}
    skipped_unknown = 0
    numeric_versions = 0
    stage_versions = 0
    for record in records:
        if record.get("status") != "ready" or record.get("module") != "task":
            continue
        content = (ROOT / record["path"]).read_text(encoding="utf-8")
        version = VERSION_LINE_RE.search(content)
        if version is not None:
            discovered[record["doc_id"]] = (version.group(1), "official_hoyowiki_entry_page_field", "官方词条页的“版本号”字段直接给出数字版本。")
            numeric_versions += 1
            continue
        stage = STAGE_LINE_RE.search(content)
        if stage is None:
            skipped_unknown += 1
            continue
        stage_id = f"month_{STAGE_NUMBER[stage.group(1)]}"
        mapped_version = stages.get(stage_id)
        if not isinstance(mapped_version, str):
            skipped_unknown += 1
            continue
        discovered[record["doc_id"]] = (
            mapped_version,
            "manual_verified_story_arc_rule",
            f"官方词条页的“版本号”字段为“月之{stage.group(1)}”；已人工确认空月之歌月之{stage.group(1)}对应 {mapped_version}。",
        )
        stage_versions += 1

    added = 0
    unchanged = 0
    conflicts = []
    for doc_id, (version, source_type, note) in sorted(discovered.items()):
        existing = documents.get(doc_id)
        if existing is not None and existing.get("first_available_version") != version:
            conflicts.append({
                "doc_id": doc_id,
                "existing_version": existing.get("first_available_version"),
                "discovered_version": version,
            })
            continue
        if existing is not None:
            unchanged += 1
            continue
        entry_id = doc_id.split(":", 2)[1]
        documents[doc_id] = {
            "first_available_version": version,
            "evidence": {
                "source_type": source_type,
                "source_url": (
                    "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
                    f"?app_sn=ys_obc&entry_page_id={entry_id}&lang=zh-cn"
                ),
                "note": note,
            },
        }
        added += 1

    report = {
        "生成时间": datetime.now(timezone.utc).isoformat(),
        "扫描范围": "现有 task 正文中的官方“版本号”字段",
        "任务文档数": sum(record.get("status") == "ready" and record.get("module") == "task" for record in records),
        "识别到数字版本数": numeric_versions,
        "识别到已确认阶段映射数": stage_versions,
        "可登记版本总数": len(discovered),
        "新增登记数": added,
        "已有登记数": unchanged,
        "无法确认版本数": skipped_unknown,
        "冲突数": len(conflicts),
        "冲突详情": conflicts,
        "按版本分布": dict(sorted(Counter(version for version, _, _ in discovered.values()).items())),
        "执行方式": "已写入版本登记表" if apply else "仅预览，未写入版本登记表",
    }
    if apply and not conflicts:
        REGISTRY_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="确认写入版本登记表")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report = sync_task_versions(apply=args.apply)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"可登记 {report['可登记版本总数']} 篇；新增 {report['新增登记数']} 篇；"
        f"冲突 {report['冲突数']} 项。{report['执行方式']}：{args.report}"
    )
    return 1 if report["冲突数"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
