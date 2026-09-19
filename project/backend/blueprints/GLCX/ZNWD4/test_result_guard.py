"""Unit tests for post-retrieval result guard."""

from __future__ import annotations

import unittest

from .result_guard import (
    apply_result_guard,
    compute_match_rate,
    filter_rows_by_anchor,
    row_matches_anchor,
    should_guard_under_type,
)


def _row(et_name: str, eq_name: str, parent_type: str = "") -> dict:
    props = {"Name": et_name}
    if parent_type:
        props["Type"] = parent_type
    return {
        "n": {
            "labels": ["EquipmentType"],
            "properties": props,
        },
        "m": {
            "labels": ["Equipment"],
            "properties": {"Name": eq_name},
        },
    }


class TestResultGuard(unittest.TestCase):
    def test_compute_match_rate(self):
        rows = [
            _row("无人机", "多旋翼无人机"),
            _row("通信设备", "通信设备A"),
        ]
        self.assertAlmostEqual(compute_match_rate(rows, "无人机"), 0.5)

    def test_filter_rows_by_anchor(self):
        rows = [
            _row("无人机", "多旋翼无人机"),
            _row("通信设备", "通信设备A"),
        ]
        filtered = filter_rows_by_anchor(rows, "无人机")
        self.assertEqual(len(filtered), 1)
        self.assertTrue(row_matches_anchor(filtered[0], "无人机"))

    def test_should_guard_under_type(self):
        rec = {"scenario": "under_type"}
        steps = [{"op_code": "neo_equipment_under_type", "params": {"keyword": "无人机"}}]
        self.assertTrue(should_guard_under_type(rec, steps))

    def test_apply_result_guard_filters_without_driver(self):
        rows = [
            _row("无人机", "多旋翼无人机"),
            _row("通信设备", "通信设备A"),
        ]
        rec = {
            "scenario": "under_type",
            "params": {"equipment_type_keywords": ["无人机"], "focus": "无人机"},
        }
        steps = [{"op_code": "neo_equipment_under_type", "params": {"keyword": "陆地设备"}}]
        out = apply_result_guard(
            question="无人机有哪些具体装备",
            rec=rec,
            steps=steps,
            neo_rows=rows,
            driver=None,
            chroma_collection=None,
        )
        self.assertTrue(out.filtered or out.action == "filter_rows")
        self.assertEqual(len(out.neo_rows), 1)

    def test_apply_result_guard_filters_by_parent(self):
        rows = [
            _row("无人机", "固定翼无人机", parent_type="航空装备"),
            _row("无人机", "多旋翼无人机", parent_type="陆地设备"),
        ]
        rec = {
            "scenario": "under_type",
            "params": {
                "equipment_type_keywords": ["无人机"],
                "equipment_type_parent": "航空装备",
                "focus": "无人机",
            },
        }
        steps = [{"op_code": "neo_equipment_under_type", "params": {"keyword": "无人机"}}]
        out = apply_result_guard(
            question="航空装备下无人机有什么具体装备",
            rec=rec,
            steps=steps,
            neo_rows=rows,
            driver=None,
            chroma_collection=None,
        )
        self.assertTrue(out.filtered or out.action == "filter_rows")
        self.assertEqual(len(out.neo_rows), 1)
        self.assertEqual(out.neo_rows[0]["n"]["properties"]["Type"], "航空装备")


if __name__ == "__main__":
    unittest.main()
