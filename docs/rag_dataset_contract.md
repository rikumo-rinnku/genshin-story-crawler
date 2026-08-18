# RAG 数据集契约

RAG 使用纯文本正文，不要求 Markdown。每份 `.txt` 必须在数据清单中有唯一记录。

## 文档身份

- `doc_id`：稳定唯一标识，格式为 `module:entry_id`；任务子任务在其后追加稳定的 `section_id`。
- `path`：正文相对路径。新数据应使用 `data/cleaned/<module>/<entry_id>.txt`，展示名称不参与文件命名。
- `content_hash`：正文 UTF-8 内容的 SHA-256，用于判断新增和修改。

## manifest.jsonl

每行一个文档，至少包含：

```json
{"doc_id":"character:503613","module":"character","entry_id":"503613","title":"钟离","path":"data/cleaned/character/503613.txt","source_type":"official_hoyowiki","source_url":null,"crawled_at":"2026-08-16T19:05:41+00:00","content_hash":"sha256:...","status":"ready"}
```

`status=ready` 才能进入 RAG；`needs_review` 用于旧数据中无法由文件名可靠恢复条目 ID 的记录。

## 发布规则

1. 旧名称文件的迁移属于本地一次性维护，不是仓库发布流程的一部分；完成迁移后再运行 `python scripts/build_manifest.py`。
2. 爬虫结束后运行 `python scripts/build_manifest.py --strict`。
3. 只有校验通过的数据集才可被 AI 项目建库。
4. AI 项目以 `manifest.jsonl` 为唯一入口；文本文件仅提供正文。
