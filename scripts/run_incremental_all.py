"""非交互式运行全部已注册模块，用于例行增量更新。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import run


def main() -> int:
    results = [run.run_module(name, module, key) for name, module, key in run.MODULES]
    run.save_report(results)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(result["status"] != "failed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
