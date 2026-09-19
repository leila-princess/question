"""考核指标格式化：neo_equipment_indicators 返回扁平 map，无 labels。"""
import unittest

from .formatters import _collect_indicator_names, format_rel_one_hop


class TestIndicatorFormatter(unittest.TestCase):
    def test_map_projection_rows(self):
        rows = [
            {
                "n": {"labels": ["Equipment"], "properties": {"Name": "多旋翼无人机"}},
                "m": {
                    "Name": "高温工作稳定性",
                    "hierarchy_type": "test",
                    "parent_name": "环境适应性",
                },
            },
            {
                "n": {"labels": ["Equipment"], "properties": {"Name": "多旋翼无人机"}},
                "m": {
                    "Name": "高温贮存耐久性",
                    "hierarchy_type": "test",
                    "parent_name": "环境适应性",
                },
            },
        ]
        names = _collect_indicator_names(rows)
        self.assertEqual(set(names), {"高温工作稳定性", "高温贮存耐久性"})
        text = format_rel_one_hop(
            rows, from_type="Equipment", to_type="Indicator", focus="多旋翼无人机"
        )
        self.assertIn("多旋翼无人机的考核指标包括", text)
        self.assertIn("高温工作稳定性", text)
        self.assertIn("高温贮存耐久性", text)

    def test_labeled_node_rows(self):
        rows = [
            {
                "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
                "m": {
                    "labels": ["Indicator"],
                    "properties": {"Name": "指标A"},
                },
            }
        ]
        text = format_rel_one_hop(
            rows, from_type="Equipment", to_type="Indicator", focus="汽车2"
        )
        self.assertIn("指标A", text)


if __name__ == "__main__":
    unittest.main()
