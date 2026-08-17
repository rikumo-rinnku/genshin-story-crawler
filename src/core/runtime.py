"""运行时公共设施：路径、日志和报告。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data" / "cleaned"
CONFIG_DIR = PROJECT_ROOT / "config"

STAT_FIELD_LABELS = {
    "total": "总条目数",
    "total_tasks": "任务总数",
    "total_characters": "角色总数",
    "processed": "本次处理数",
    "skipped": "已跳过数",
    "failed": "失败条目",
    "missing": "缺失条目",
    "subtasks_saved": "已保存子任务数",
    "subtasks_empty": "空子任务数",
    "id": "条目ID",
    "task_id": "任务ID",
    "name": "名称",
    "task_name": "任务名称",
    "subtask_name": "子任务名称",
    "reason": "原因",
    "type": "类型",
}


def translate_stats(value: Any) -> Any:
    """递归转换报告中的常用统计字段，未知字段保持原样。"""
    if isinstance(value, dict):
        return {STAT_FIELD_LABELS.get(str(key), str(key)): translate_stats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [translate_stats(item) for item in value]
    return value


def configure_logging() -> None:
    """配置一次全项目日志，重复调用不会重复添加处理器。"""
    root_logger = logging.getLogger()
    if getattr(configure_logging, "_configured", False):
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s"
    )
    file_handler = logging.FileHandler(LOG_DIR / "run_all.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    configure_logging._configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def save_module_report(module: str, stats: dict[str, Any]) -> Path:
    """补齐公共字段并保存统一的模块报告。"""
    report = {
        "模块": module,
        "运行时间": datetime.now().isoformat(),
        "汇总": {
            "总条目数": stats.get("total", stats.get("total_tasks", stats.get("total_characters", 0))),
            "本次处理数": stats.get("processed", 0),
            "已跳过数": stats.get("skipped", 0),
            "失败条目数": len(stats.get("failed", [])),
        },
        "失败详情": translate_stats(stats.get("failed", [])),
        "统计明细": translate_stats(stats),
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"{module}_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path
