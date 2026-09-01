"""最小版本元数据契约与 manifest 稳定性测试。"""
from __future__ import annotations

import copy
import unittest

from scripts.build_manifest import build_manifest, release_audit
from src.core.release_metadata import (
    load_version_registry,
    resolve_release,
    unknown_release,
    validate_registry,
    validate_release,
    version_series,
)


class ReleaseMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_version_registry()

    def test_version_must_be_string_and_match_format(self) -> None:
        release = unknown_release()
        release["first_available_version"] = 6.0
        release["version_series"] = "6.x"
        self.assertTrue(any("字符串" in error for error in validate_release(release)))

    def test_version_series_is_derived(self) -> None:
        self.assertEqual(version_series("6.0"), "6.x")
        self.assertEqual(version_series("7.7"), "7.x")
        self.assertIsNone(version_series("6.x"))
        self.assertIsNone(version_series(None))

    def test_unknown_release_is_valid(self) -> None:
        self.assertEqual(validate_release(unknown_release()), [])

    def test_release_contains_only_two_stable_fields(self) -> None:
        release = unknown_release()
        release["released_at"] = None
        self.assertTrue(any("未定义字段" in error for error in validate_release(release)))

    def test_registry_requires_evidence_and_valid_versions(self) -> None:
        invalid_registry = {
            "story_arc_rules": {"空月之歌": {"base_version": "6.x", "stage_versions": {"month_1": "6.x"}}},
            "documents": {"task:1": {"first_available_version": "7.x", "evidence": None}},
        }
        errors = validate_registry(invalid_registry)
        self.assertTrue(any("不合法稳定标识" in error for error in errors))
        self.assertTrue(any("具体版本" in error for error in errors))
        self.assertTrue(any("evidence" in error for error in errors))

    def test_story_arc_rule_is_registered_and_document_is_mapped(self) -> None:
        rule = self.registry["story_arc_rules"]["song_of_the_welkin_moon"]
        self.assertEqual(rule["base_version"], "6.0")
        self.assertEqual(rule["stage_versions"]["month_1"], "6.1")
        self.assertEqual(rule["stage_versions"]["month_8"], "6.8")
        release, errors = resolve_release("task:506587:771", self.registry)
        self.assertEqual(errors, [])
        self.assertEqual(release, {"first_available_version": "6.1", "version_series": "6.x"})

    def test_release_does_not_change_content_hash(self) -> None:
        record = {"content_hash": "sha256:body", "release": unknown_release()}
        changed = copy.deepcopy(record)
        changed["release"]["first_available_version"] = "7.0"
        changed["release"]["version_series"] = "7.x"
        self.assertEqual(record["content_hash"], changed["content_hash"])

    def test_manifest_doc_ids_and_hashes_are_stable_when_rebuilt(self) -> None:
        first, first_issues, first_release_errors = build_manifest()
        second, second_issues, second_release_errors = build_manifest()
        self.assertEqual(first_issues, second_issues)
        self.assertEqual(first_release_errors, second_release_errors)
        self.assertEqual(
            [(item["doc_id"], item["content_hash"]) for item in first],
            [(item["doc_id"], item["content_hash"]) for item in second],
        )

    def test_release_errors_are_emitted_to_audit(self) -> None:
        audit = release_audit(
            [{"doc_id": "task:demo", "module": "task", "status": "ready", "release": unknown_release()}],
            {"task:bad": ["版本格式不合法"]},
        )
        self.assertEqual(audit["错误记录"], [{"doc_id": "task:bad", "errors": ["版本格式不合法"]}])


if __name__ == "__main__":
    unittest.main()
