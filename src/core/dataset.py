"""RAG 数据集的稳定写入与 manifest 发布。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.core.release_metadata import load_version_registry, resolve_release, validate_registry
from src.core.runtime import DATA_DIR, PROJECT_ROOT

MANIFEST_DB = PROJECT_ROOT / "data" / "manifest.sqlite3"
MANIFEST_FILE = PROJECT_ROOT / "data" / "manifest.jsonl"


def save_document(content: str, module: str, entry_id: str, title: str, *, section_id: str | None = None, source_type: str = "official_hoyowiki") -> Path:
    """按稳定 ID 保存正文，并以事务方式登记元数据。"""
    safe_id = str(entry_id)
    filename = f"{safe_id}__{section_id}.txt" if section_id else f"{safe_id}.txt"
    path = DATA_DIR / module / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    doc_id = f"{module}:{safe_id}" + (f":{section_id}" if section_id else "")
    record = (doc_id, module, safe_id, section_id, title, path.relative_to(PROJECT_ROOT).as_posix(), source_type, datetime.now(timezone.utc).isoformat(), "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest())
    with sqlite3.connect(MANIFEST_DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS documents (doc_id TEXT PRIMARY KEY, module TEXT, entry_id TEXT, section_id TEXT, title TEXT, path TEXT, source_type TEXT, crawled_at TEXT, content_hash TEXT)")
        conn.execute("INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", record)
    return path


def publish_manifest() -> Path:
    """从事务性登记表导出带 release 元数据的 JSONL。"""
    if not MANIFEST_DB.exists():
        return MANIFEST_FILE
    with sqlite3.connect(MANIFEST_DB) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(documents)")]
        rows = [dict(zip(columns, row)) for row in conn.execute("SELECT * FROM documents ORDER BY doc_id")]
    registry = load_version_registry()
    registry_errors = validate_registry(registry)
    if registry_errors:
        raise ValueError(f"版本规则配置校验失败：{'；'.join(registry_errors)}")
    records = []
    for row in rows:
        source_type = row.get("source_type", "official_hoyowiki")
        entry_id = row.get("entry_id")
        source_url = None
        if entry_id and source_type == "official_hoyowiki":
            source_url = (
                "https://act-api-takumi-static.mihoyo.com/hoyowiki/genshin/wapi/entry_page"
                f"?app_sn=ys_obc&entry_page_id={entry_id}&lang=zh-cn"
            )
        release, errors = resolve_release(row["doc_id"], registry)
        if errors:
            raise ValueError(f"release 元数据校验失败：{row['doc_id']}：{'；'.join(errors)}")
        records.append({**row, "source_url": source_url, "status": "ready", "release": release})
    MANIFEST_FILE.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return MANIFEST_FILE
