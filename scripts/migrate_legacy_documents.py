"""将身份明确的旧文本安全复制到稳定 ID 路径。

默认只预览；使用 --apply 才会创建副本。该脚本不会删除或覆盖旧文件。
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "manifest.jsonl"


def destination(record: dict) -> Path:
    filename = str(record["entry_id"])
    if record.get("section_id"):
        filename += f"__{record['section_id']}"
    return ROOT / "data" / "cleaned" / record["module"] / f"{filename}.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.manifest.exists():
        print(f"未找到 Manifest：{args.manifest}")
        return 2

    copied = skipped = conflicts = review = 0
    for line in args.manifest.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("status") != "ready":
            review += 1
            continue
        source = ROOT / record["path"]
        target = destination(record)
        if source.resolve() == target.resolve():
            skipped += 1
            continue
        if target.exists():
            if target.read_bytes() == source.read_bytes():
                skipped += 1
            else:
                conflicts += 1
                print(f"内容冲突：{target.relative_to(ROOT)}")
            continue
        copied += 1
        if args.apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    action = "已复制" if args.apply else "预览将复制"
    print(f"{action}：{copied}；无需处理：{skipped}；内容冲突：{conflicts}；仅待复核：{review}")
    return 1 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
