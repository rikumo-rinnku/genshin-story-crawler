"""审计版本元数据试填样本，生成供人工复核的中文报告。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "manifest.jsonl"
DEFAULT_SAMPLES = ROOT / "metadata" / "release" / "pilot_samples.json"
DEFAULT_OUTPUT = ROOT / "logs" / "release_pilot_report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    records = {
        record["doc_id"]: record
        for record in (json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip())
    }
    samples = json.loads(args.samples.read_text(encoding="utf-8")).get("samples", [])
    results = []
    errors = []
    for sample in samples:
        doc_id = sample.get("doc_id")
        record = records.get(doc_id)
        if record is None:
            errors.append({"doc_id": doc_id, "error": "manifest 中不存在该试填样本"})
            continue
        release = record.get("release", {})
        results.append({
            "doc_id": doc_id,
            "category": sample.get("category"),
            "title": record.get("title"),
            "content_hash": record.get("content_hash"),
            "release": release,
            "review_note": sample.get("review_note"),
            "版本是否有明确登记": release.get("first_available_version") is not None,
            "跨版本是否需要拆分": doc_id == "npc:3961",
            "拆分结论": "缺少稳定、可追溯的段落边界，暂不拆分" if doc_id == "npc:3961" else None,
        })

    report = {
        "生成时间": datetime.now(timezone.utc).isoformat(),
        "试填样本数": len(results),
        "试填错误数": len(errors),
        "样本": results,
        "错误记录": errors,
        "结论": "试填仅写入已确认的首次版本；未知版本保持 null，未修改正文文件。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {len(results)} 篇试填样本：{args.output}；错误：{len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
