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
        "module": module,
        "run_time": datetime.now().isoformat(),
        "total": stats.get("total", stats.get("total_tasks", stats.get("total_characters", 0))),
        "processed": stats.get("processed", 0),
        "skipped": stats.get("skipped", 0),
        "failed": stats.get("failed", []),
        "details": stats,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"{module}_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path
