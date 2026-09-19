"""answer_markdown 单元测试（环境属性 HTML → Markdown）。"""

import unittest

from .answer_markdown import (
    field_value_for_environment_property,
    html_fragment_to_markdown,
    looks_like_environment_properties_answer,
    normalize_answer_markdown,
    property_field_label,
)
from .formatters import format_node_properties


class TestAnswerMarkdown(unittest.TestCase):
    def test_malformed_minio_proxy_url(self):
        bad = "/api/GLCX/JQCX?path=/minio-image&path=%E7%8E%AF%E5%A2%83%2Ff51c6af97156%2Fimage_1.png"
        from .answer_markdown import normalize_minio_image_url

        fixed = normalize_minio_image_url(bad)
        self.assertIn("/api/GLCX/JQCX/minio-image?path=", fixed)
        self.assertNotIn("path=/minio-image", fixed)

    def test_image_and_path(self):
        html = (
            '<div><img src="http://localhost/x.png" alt="环境图片" /></div>'
            '<div style="margin:4px 0">路径：环境/a/image.png</div>'
        )
        md = html_fragment_to_markdown(html)
        self.assertIn("![环境图片](http://localhost/x.png)", md)
        self.assertIn("[下载图片](http://localhost/x.png)", md)
        self.assertIn("存储路径", md)

    def test_table(self):
        html = (
            "<table border=\"1\"><tr><td>组合因子</td><td>参数范围</td></tr>"
            "<tr><td>坡度+湿度</td><td>15°+80%RH</td></tr></table>"
        )
        md = html_fragment_to_markdown(html)
        self.assertIn("| 组合因子 | 参数范围 |", md)
        self.assertIn("| 坡度+湿度 | 15°+80%RH |", md)

    def test_property_label(self):
        self.assertEqual(property_field_label("Type"), "分类")
        self.assertEqual(property_field_label("Unknown"), "Unknown")

    def test_env_five_elements_preserve_html(self):
        html = '<table><tr><td>气候</td></tr></table>'
        out = field_value_for_environment_property("Type", html)
        self.assertIn("<table>", out)
        self.assertIn("气候", out)

    def test_normalize_env_answer_keeps_html(self):
        raw = '环境「水文环境」的分类：\n\n<table><tr><td>水域</td></tr></table>'
        self.assertTrue(looks_like_environment_properties_answer(raw))
        out = normalize_answer_markdown(raw)
        self.assertIn("<table>", out)

    def test_format_node_properties_html_type(self):
        rows = [
            {
                "n": {
                    "properties": {
                        "Name": "水文环境",
                        "Type": "<table><tr><td>水域环境</td></tr></table>",
                    }
                }
            }
        ]
        text = format_node_properties(
            rows,
            node_type="Environment",
            property_fields=["Type"],
            focus="水文环境",
        )
        self.assertIn("<table>", text)
        self.assertIn("水域环境", text)


if __name__ == "__main__":
    unittest.main()
