"""仅修复已保存文本中缺少接口对话内容的任务分段。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRAWLED = ROOT / "config" / "crawled.json"
sys.path.insert(0, str(ROOT))

from src.core.dataset import save_document
from src.modules.task import extract_main_info, generate_subtask_content, get_task_detail, parse_subtasks


def repair_task(task_id: str) -> tuple[int, int]:
    detail_data = get_task_detail(task_id)
    page = detail_data.get("data", {}).get("page", {}) if detail_data else {}
    if not page:
        raise RuntimeError(f"任务 {task_id}：没有页面数据")

    main_info = extract_main_info(page)
    module_dict = {str(module.get("id")): module for module in page.get("modules", []) if module.get("id")}
    updated = skipped = 0
    for subtask in parse_subtasks(page):
        section_id = str(subtask["group_id"])
        path = ROOT / "data" / "cleaned" / "task" / f"{task_id}__{section_id}.txt"
        if not path.exists():
            skipped += 1
            continue
        previous = path.read_text(encoding="utf-8")
        content = generate_subtask_content(main_info, subtask, module_dict)
        if "【剧情对话】" not in content or "【剧情对话】" in previous:
            skipped += 1
            continue
        save_document(content, "task", task_id, subtask["name"], section_id=section_id)
        print(f"已更新：{path.relative_to(ROOT)}")
        updated += 1
    return updated, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_ids", nargs="*", help="需要修复的官方任务内容 ID")
    parser.add_argument(
        "--all-from-crawled",
        action="store_true",
        help="扫描当前已记录的全部任务，仅更新缺少对话的分段",
    )
    args = parser.parse_args()
    if args.all_from_crawled:
        crawled = json.loads(CRAWLED.read_text(encoding="utf-8"))
        task_ids = list(crawled.get("task", {}))
    else:
        task_ids = args.task_ids
    if not task_ids:
        parser.error("请提供任务 ID，或使用 --all-from-crawled")

    total_updated = total_skipped = 0
    failures: list[str] = []
    for index, task_id in enumerate(task_ids, 1):
        try:
            updated, skipped = repair_task(task_id)
            total_updated += updated
            total_skipped += skipped
        except Exception as exc:
            failures.append(task_id)
            print(f"处理失败：{task_id}：{exc}")
        if index % 50 == 0 or index == len(task_ids):
            print(f"进度：{index}/{len(task_ids)}；已更新：{total_updated}；失败：{len(failures)}")
    print(f"完成：已更新 {total_updated}；跳过 {total_skipped}；失败 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
