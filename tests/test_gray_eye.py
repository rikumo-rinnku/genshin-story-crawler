from __future__ import annotations

import json
import unittest

from src.modules.gray_eye import parse_gray_eye_detail, parse_interactive_dialogue, parse_multi_table


class GrayEyeParsingTests(unittest.TestCase):
    def test_multi_table_filters_image_only_column(self) -> None:
        data = {
            "tables": [{
                "header": ["图片", "<span>外观名称</span>", "获取方式"],
                "row": [["<img src='x'>", "<b>简易暖源</b>", "默认外观"]],
            }],
        }
        self.assertEqual(parse_multi_table(data), ["外观名称 | 获取方式\n简易暖源 | 默认外观"])

    def test_interactive_dialogue_uses_stable_depth_first_order(self) -> None:
        data = {
            "root_id": "root",
            "child_ids": {"root": ["a", "b"], "a": ["shared"], "b": ["shared"]},
            "contents": {
                "root": {"dialogue": "开场"},
                "a": {"option": "选项 A", "dialogue": "回答 A"},
                "b": {"option": "选项 B", "dialogue": "回答 B"},
                "shared": {"dialogue": "共同结尾"},
            },
        }
        self.assertEqual(
            parse_interactive_dialogue(data),
            ["开场", "[选项] 选项 A", "回答 A", "共同结尾", "[选项] 选项 B", "回答 B"],
        )

    def test_detail_parses_rich_text_table_and_dialogue(self) -> None:
        page = {
            "modules": [{
                "name": "综合",
                "components": [
                    {"component_id": "rich_text", "data": json.dumps({"rich_text": "<p>说明<span>文本</span></p>"})},
                    {"component_id": "multi_table", "data": json.dumps({"tables": [{"header": ["图片", "名称"], "row": [["<img>", "造物"]]}]})},
                    {"component_id": "interactive_dialogue", "data": json.dumps({"root_id": "1", "contents": {"1": {"dialogue": "你好"}}})},
                ],
            }],
        }
        self.assertEqual(parse_gray_eye_detail(page), [("综合", ["说明文本", "名称\n造物", "你好"])])


if __name__ == "__main__":
    unittest.main()
