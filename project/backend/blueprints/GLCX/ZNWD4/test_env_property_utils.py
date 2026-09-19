"""环境属性问答工具单测。"""

from __future__ import annotations

import unittest

from .env_property_utils import (
    detect_environment_property_fields,
    narrow_environment_property_rows,
    resolve_environment_property_fields,
    sanitize_environment_keywords,
)
from .formatters import format_node_properties


class TestEnvPropertyUtils(unittest.TestCase):
    def test_detect_type_only(self):
        self.assertEqual(detect_environment_property_fields("水文试验环境的分类是什么"), ["Type"])
        self.assertEqual(resolve_environment_property_fields("低温环境有哪些分类"), ["Type"])

    def test_sanitize_keywords(self):
        self.assertEqual(
            sanitize_environment_keywords(["分类", "水文试验环境", "环境"]),
            ["水文试验环境"],
        )

    def test_narrow_by_focus_id(self):
        rows = [
            {"n": {"properties": {"Id": "1", "Name": "环境A", "Type": "气候"}}},
            {"n": {"properties": {"Id": "2", "Name": "环境B", "Type": "力学"}}},
        ]
        out = narrow_environment_property_rows(rows, focus_id="2")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["n"]["properties"]["Name"], "环境B")

    def test_format_single_type_field(self):
        rows = [{"n": {"properties": {"Name": "水文试验环境", "Type": "水域环境"}}}]
        text = format_node_properties(
            rows,
            node_type="Environment",
            property_fields=["Type"],
            focus="水文试验环境",
        )
        self.assertIn("水域环境", text)
        self.assertIn("分类", text)
        self.assertNotIn("力学", text)


if __name__ == "__main__":
    unittest.main()
