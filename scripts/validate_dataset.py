"""阻止不完整或不可追溯的语料进入 RAG。"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "manifest.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--allow-review", action="store_true", help="允许存在 needs_review 旧数据")
    args = parser.parse_args()
    if not args.manifest.exists():
        print(f"缺少 manifest：{args.manifest}")
        return 2

    records = []
    errors: list[str] = []
    for number, line in enumerate(args.manifest.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"第 {number} 行不是合法 JSON：{exc.msg}")
            continue
        records.append(record)

    ids = Counter(record.get("doc_id") for record in records)
    errors.extend(f"重复 doc_id：{doc_id}" for doc_id, count in ids.items() if doc_id and count > 1)
    ready = [record for record in records if record.get("status") == "ready"]
    review = [record for record in records if record.get("status") != "ready"]
    for record in ready:
        required = ("doc_id", "module", "entry_id", "title", "path", "content_hash")
        missing = [key for key in required if not record.get(key)]
        if missing:
            errors.append(f"{record.get('doc_id')}: 缺少 {', '.join(missing)}")
            continue
        path = ROOT / record["path"]
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"{record['doc_id']}: 正文不存在或为空")

    print(f"文档总数：{len(records)}；可发布：{len(ready)}；待复核：{len(review)}；错误：{len(errors)}")
    for module, count in sorted(Counter(record.get("module", "unknown") for record in ready).items()):
        print(f"- {module}: {count}")
    for error in errors[:30]:
        print("错误：", error)
    if errors or (review and not args.allow_review):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
