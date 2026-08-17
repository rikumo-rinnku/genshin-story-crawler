"""删除未被 manifest 引用、且不采用稳定 ID 命名的历史文本。"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "cleaned"
MANIFEST = ROOT / "data" / "manifest.jsonl"
CANONICAL_NAME = re.compile(r"^\d+(?:__.+)?$")


def find_unlisted_legacy_files() -> list[Path]:
    manifest_paths = {
        record["path"].replace("/", "\\")
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for record in [json.loads(line)]
    }
    targets: list[Path] = []
    for path in DATA_DIR.glob("*/*.txt"):
        relative = str(path.relative_to(ROOT))
        if relative not in manifest_paths and not CANONICAL_NAME.fullmatch(path.stem):
            targets.append(path)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际删除；省略时仅统计")
    args = parser.parse_args()
    targets = find_unlisted_legacy_files()
    print(f"未列入清单的历史文件: {len(targets)}")
    if args.apply:
        for path in targets:
            path.unlink()
        print(f"已删除: {len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
