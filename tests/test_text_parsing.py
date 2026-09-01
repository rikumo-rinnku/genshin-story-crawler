from __future__ import annotations

import json
import unittest

from src.core.parser import clean_html_to_inline_text, clean_html_to_text
from src.modules.npc import parse_npc_detail


class HtmlCleaningTests(unittest.TestCase):
    def test_block_tags_and_br_preserve_line_breaks(self) -> None:
        html = "<div>第一段<br>第二行</div><p>第三段</p>"
        self.assertEqual(clean_html_to_text(html), "第一段\n第二行\n第三段")

    def test_inline_tags_do_not_create_false_line_breaks(self) -> None:
        html = "字段：<span>至冬</span><a href='#'>冒险家协会</a>"
        self.assertEqual(clean_html_to_text(html), "字段：至冬冒险家协会")

    def test_inline_cleaner_collapses_field_content(self) -> None:
        html = "<div>至冬 <span>城</span></div><div>冒险家协会</div>"
        self.assertEqual(clean_html_to_inline_text(html), "至冬 城 冒险家协会")


class NpcParsingTests(unittest.TestCase):
    def test_npc_fields_are_rendered_on_one_line(self) -> None:
        page = {
            "modules": [{
                "name": "基本资料",
                "components": [{
                    "component_id": "npc_base_info",
                    "data": json.dumps({
                        "attr": [{"key": "位置", "value": ["<span>至冬</span><br>冒险家协会"]}],
                        "extra_attr": [],
                    }),
                }],
            }],
        }
        self.assertEqual(parse_npc_detail(page)["基本信息"], "位置：至冬 冒险家协会")


if __name__ == "__main__":
    unittest.main()
