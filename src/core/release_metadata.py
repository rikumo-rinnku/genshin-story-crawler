"""语料版本元数据的最小结构、版本规则与校验。"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from src.core.runtime import PROJECT_ROOT

METADATA_DIR = PROJECT_ROOT / "metadata" / "release"
VERSION_REGISTRY_FILE = METADATA_DIR / "version_registry.json"

VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
RELEASE_KEYS = {"first_available_version", "version_series"}


def unknown_release() -> dict[str, None]:
    """返回版本未知时仍字段齐全的最小 release 对象。"""
    return {"first_available_version": None, "version_series": None}


def version_series(version: str | None) -> str | None:
    """由明确版本号生成版本系列；未知或格式无效时不推测。"""
    if not isinstance(version, str):
        return None
    matched = VERSION_RE.fullmatch(version)
    return f"{matched.group(1)}.x" if matched else None


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(default)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"版本配置必须是 JSON 对象：{path}")
    return value


def load_version_registry() -> dict[str, Any]:
    """读取人工确认的版本依据与篇章阶段映射，不把依据写入主 manifest。"""
    return _load_json(VERSION_REGISTRY_FILE, {"story_arc_rules": {}, "documents": {}})


def validate_registry(registry: dict[str, Any]) -> list[str]:
    """校验本地规则配置，错误会进入审计报告而非静默写入。"""
    errors: list[str] = []
    rules = registry.get("story_arc_rules")
    documents = registry.get("documents")
    if not isinstance(rules, dict):
        errors.append("story_arc_rules 必须是对象")
    else:
        for arc_id, rule in rules.items():
            if not isinstance(arc_id, str) or not IDENTIFIER_RE.fullmatch(arc_id):
                errors.append(f"篇章规则包含不合法稳定标识：{arc_id}")
            if not isinstance(rule, dict):
                errors.append(f"篇章规则 {arc_id} 必须是对象")
                continue
            base_version = rule.get("base_version")
            if not isinstance(base_version, str) or not VERSION_RE.fullmatch(base_version):
                errors.append(f"篇章规则 {arc_id}.base_version 必须是具体版本字符串")
            stages = rule.get("stage_versions")
            if not isinstance(stages, dict):
                errors.append(f"篇章规则 {arc_id}.stage_versions 必须是对象")
            elif any(not isinstance(stage, str) or not isinstance(version, str) or not VERSION_RE.fullmatch(version) for stage, version in stages.items()):
                errors.append(f"篇章规则 {arc_id}.stage_versions 必须映射到具体版本字符串")
    if not isinstance(documents, dict):
        errors.append("documents 必须是对象")
    else:
        for doc_id, record in documents.items():
            if not isinstance(doc_id, str) or not isinstance(record, dict):
                errors.append("documents 的键和值必须分别是 doc_id 字符串和对象")
                continue
            version = record.get("first_available_version")
            if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
                errors.append(f"{doc_id}.first_available_version 必须是具体版本字符串")
            evidence = record.get("evidence")
            if not isinstance(evidence, dict) or not all(isinstance(evidence.get(key), str) and evidence[key].strip() for key in ("source_type", "source_url", "note")):
                errors.append(f"{doc_id}.evidence 必须包含非空 source_type、source_url、note")
    return errors


def validate_release(release: dict[str, Any]) -> list[str]:
    """校验下游 manifest 的最小 release 结构。"""
    errors: list[str] = []
    if set(release) != RELEASE_KEYS:
        missing = sorted(RELEASE_KEYS - set(release))
        extra = sorted(set(release) - RELEASE_KEYS)
        if missing:
            errors.append(f"缺少字段：{', '.join(missing)}")
        if extra:
            errors.append(f"存在未定义字段：{', '.join(extra)}")
    version = release.get("first_available_version")
    if version is not None and (not isinstance(version, str) or not VERSION_RE.fullmatch(version)):
        errors.append("first_available_version 必须是形如 6.0 的字符串或 null")
    if release.get("version_series") != version_series(version):
        errors.append("version_series 必须由 first_available_version 自动计算，未知版本时必须为 null")
    return errors


def resolve_release(doc_id: str, registry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """只按人工确认的 doc_id 规则写入版本，不从时间或正文猜测。"""
    release = unknown_release()
    record = registry.get("documents", {}).get(doc_id)
    if record is not None:
        if not isinstance(record, dict):
            return release, ["版本登记项必须是对象"]
        release["first_available_version"] = record.get("first_available_version")
    release["version_series"] = version_series(release["first_available_version"])
    return release, validate_release(release)
