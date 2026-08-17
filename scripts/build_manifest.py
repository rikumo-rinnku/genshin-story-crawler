"""为现有纯文本语料生成 RAG manifest，并阻止模糊文档进入索引。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "cleaned"
CRAWLED = ROOT / "config" / "crawled.json"
MANIFEST_DB = ROOT / "data" / "manifest.sqlite3"
DEFAULT_OUTPUT = ROOT / "data" / "manifest.jsonl"


def load_ids_by_title() -> dict[str, dict[str, list[str]]]:
    data = json.loads(CRAWLED.read_text(encoding="utf-8")) if CRAWLED.exists() else {}
    result: dict[str, dict[str, list[str]]] = {}
    for module, entries in data.items():
        by_title: dict[str, list[str]] = defaultdict(list)
        if isinstance(entries, dict):
            for entry_id, title in entries.items():
                by_title[str(title)].append(str(entry_id))
        result[module] = by_title
    return result


def load_registered_ids() -> dict[str, set[str]]:
    """读取当前爬虫运行已登记的稳定条目 ID。"""
    result: dict[str, set[str]] = defaultdict(set)
    if not MANIFEST_DB.exists():
        return result
    with sqlite3.connect(MANIFEST_DB) as conn:
        for module, entry_id in conn.execute("SELECT module, entry_id FROM documents"):
            result[str(module)].add(str(entry_id))
    return result


def title_from_text(path: Path, module: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if module == "task":
        for line in lines:
            if line.startswith("任务名称："):
                return line.removeprefix("任务名称：").strip()
    for line in lines:
        line = line.strip().lstrip("#").strip()
        if line:
            return line.split("：", 1)[-1].strip()
    return path.stem


def build_manifest() -> tuple[list[dict], list[str]]:
    ids_by_title = load_ids_by_title()
    registered_ids = load_registered_ids()
    records: list[dict] = []
    issues: list[str] = []
    for path in sorted(DATA_DIR.glob("*/*.txt")):
        source_module = path.parent.name
        module = "character_anecdote" if source_module == "anecdote" else source_module
        title = title_from_text(path, source_module)
        stem_id, separator, section_id = path.stem.partition("__")
        identity_module = "character" if module == "character_anecdote" else module
        known_ids = {
            entry_id
            for ids in ids_by_title.get(identity_module, {}).values()
            for entry_id in ids
        }
        known_ids.update(registered_ids.get(module, set()))
        direct_id = stem_id if stem_id in known_ids else None
        candidates = [direct_id] if direct_id else ids_by_title.get(module, {}).get(title, [])
        if source_module == "anecdote" and not direct_id:
            character_name = path.stem.split("_", 1)[0]
            candidates = ids_by_title.get("character", {}).get(character_name, [])
        # 旧版多数以名称为文件名；仅在名称唯一时恢复条目 ID。
        if not candidates:
            candidates = ids_by_title.get(module, {}).get(path.stem, [])
        entry_id = candidates[0] if len(candidates) == 1 else None
        section_id = section_id if entry_id and separator else None
        if entry_id and not section_id and source_module in {"task", "anecdote"}:
            section_id = "legacy-" + hashlib.sha256(path.as_posix().encode("utf-8")).hexdigest()[:12]
        status = "ready" if entry_id else "needs_review"
        if status != "ready":
            issues.append(f"{path.relative_to(ROOT)}: 无法唯一匹配条目 ID")
        content = path.read_bytes()
        records.append({
            "doc_id": (
                f"{module}:{entry_id}:{section_id}"
                if entry_id and section_id
                else f"{module}:{entry_id}"
                if entry_id
                else f"legacy:{module}:{hashlib.sha256(path.as_posix().encode('utf-8')).hexdigest()[:16]}"
            ),
            "module": module,
            "entry_id": entry_id,
            "section_id": section_id,
            "title": title,
            "path": path.relative_to(ROOT).as_posix(),
            "source_type": "third_party_wiki" if module == "namecard" else "official_hoyowiki",
            "source_url": None,
            "crawled_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "content_hash": "sha256:" + hashlib.sha256(content).hexdigest(),
            "status": status,
            "_canonical_identity": bool(direct_id),
        })

    # 已存在稳定 ID 副本时，旧文件只是保留在文件系统中的备份，不能作为第二份
    # 数据集文档。在检查真实身份冲突前，先从 Manifest 中排除这些备份。
    canonical_ids = {
        record["doc_id"]
        for record in records
        if record["status"] == "ready" and record["_canonical_identity"]
    }
    records = [
        record
        for record in records
        if not (
            record["status"] == "ready"
            and not record["_canonical_identity"]
            and record["doc_id"] in canonical_ids
        )
    ]

    # 旧文件名可能与另一份文本映射到同一个游戏条目。它们可保留供审计，
    # 但不能以 ready 状态进入 RAG 索引。
    by_doc_id: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if record["status"] == "ready":
            by_doc_id[record["doc_id"]].append(record)
    for doc_id, duplicates in by_doc_id.items():
        if len(duplicates) < 2:
            continue
        canonical = [record for record in duplicates if record["_canonical_identity"]]
        downgrade = [record for record in duplicates if not record["_canonical_identity"]] if canonical else duplicates
        for record in downgrade:
            path_digest = hashlib.sha256(record["path"].encode("utf-8")).hexdigest()[:16]
            record["doc_id"] = f"legacy:{record['module']}:{path_digest}"
            record["entry_id"] = None
            record["section_id"] = None
            record["status"] = "needs_review"
        issues.append(f"duplicate document identity downgraded for review: {doc_id}")
    for record in records:
        record.pop("_canonical_identity", None)
    return records, issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    records, issues = build_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")
    print(f"已写入 {len(records)} 条 manifest：{args.output}")
    print(f"可用于 RAG：{sum(x['status'] == 'ready' for x in records)}；需复核：{len(issues)}")
    for issue in issues[:20]: print("-", issue)
    return 1 if args.strict and issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
