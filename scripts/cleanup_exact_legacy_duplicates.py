"""清理与稳定 ID 文件字节完全一致的旧标题副本。"""
from __future__ import annotations

import argparse
import hashlib
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "cleaned"
CANONICAL_NAME = re.compile(r"^\d+(?:__.+)?$")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find_duplicates(modules: list[str]) -> list[Path]:
    canonical_hashes: dict[str, set[str]] = defaultdict(set)
    legacy: list[tuple[Path, str]] = []
    for module in modules:
        directory = DATA_DIR / module
        if not directory.is_dir():
            raise ValueError(f"不存在的数据模块: {module}")
        for path in directory.glob("*.txt"):
            if CANONICAL_NAME.fullmatch(path.stem):
                canonical_hashes[module].add(digest(path))
            else:
                legacy.append((path, digest(path)))
    return [path for path, content_hash in legacy if content_hash in canonical_hashes[path.parent.name]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--module", help="要清理的数据模块，例如 map_text")
    scope.add_argument("--all-modules", action="store_true", help="检查 data/cleaned 下全部数据模块")
    parser.add_argument("--apply", action="store_true", help="实际删除；省略时仅预览")
    parser.add_argument("--list", action="store_true", help="输出目标文件列表")
    args = parser.parse_args()
    modules = [args.module] if args.module else [path.name for path in DATA_DIR.iterdir() if path.is_dir()]
    targets = find_duplicates(modules)
    print(f"精确重复旧文件: {len(targets)}")
    if args.list:
        for path in targets:
            print(path.relative_to(ROOT))
    if args.apply:
        for path in targets:
            path.unlink()
        print(f"已删除: {len(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
