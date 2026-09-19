"""Tests for broad under_type (no parent, same-name EquipmentType across parents)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from .executor import _execute_neo_step, _neo_row_one_line
from .result_guard import apply_result_guard
from .route_validator import (
    _apply_broad_under_type_if_needed,
    _distinct_parent_types_for_child_name,
)


def _et_hit(node_id: str, name: str, type_value: str) -> dict:
    return {
        "node_type": "EquipmentType",
        "id": node_id,
        "name": name,
        "type_value": type_value,
    }


class TestBroadUnderTypeHelpers(unittest.TestCase):
    def test_distinct_parent_types_for_child_name(self):
        snap = {
            "hits": [
                _et_hit("et1", "无人机", "陆地设备"),
                _et_hit("et2", "无人机", "航空装备"),
            ]
        }
        parents = _distinct_parent_types_for_child_name(snap, "无人机")
        self.assertEqual(set(parents), {"陆地设备", "航空装备"})

    def test_apply_broad_clears_focus_id_when_multi_parent(self):
        out = {
            "equipment_type_keywords": ["无人机"],
            "focus": "无人机",
            "focus_id": "et1",
            "equipment_type_parent": "陆地设备",
        }
        snap = {
            "hits": [
                _et_hit("et1", "无人机", "陆地设备"),
                _et_hit("et2", "无人机", "航空装备"),
            ]
        }
        result = _apply_broad_under_type_if_needed(
            out,
            probe_snapshot=snap,
            scenario="under_type",
            question="无人机有哪些具体装备",
        )
        self.assertTrue(result.get("match_all_equipment_types"))
        self.assertNotIn("focus_id", result)
        self.assertNotIn("equipment_type_parent", result)

    def test_apply_broad_skips_when_parent_in_question(self):
        out = {"equipment_type_keywords": ["无人机"], "focus_id": "et_air"}
        snap = {"hits": [_et_hit("et1", "无人机", "陆地设备"), _et_hit("et2", "无人机", "航空装备")]}
        result = _apply_broad_under_type_if_needed(
            out,
            probe_snapshot=snap,
            scenario="under_type",
            question="航空装备下无人机有什么具体装备",
        )
        self.assertNotIn("match_all_equipment_types", result)
        self.assertEqual(result.get("focus_id"), "et_air")


class TestExecutorMatchAll(unittest.TestCase):
    def test_match_all_cypher_branch(self):
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value = []

        rows, cypher, params = _execute_neo_step(
            driver,
            "neo_equipment_under_type",
            {"keyword": "无人机", "match_all_equipment_types": True, "limit": 10},
        )
        self.assertEqual(rows, [])
        self.assertIn("CONTAINS toLower($kw)", cypher or "")
        self.assertNotIn("type_vals", cypher or "")
        self.assertNotIn("focus_id", params)


class TestNeoRowPreview(unittest.TestCase):
    def test_under_type_row_user_friendly(self):
        row = {
            "n": {
                "labels": ["EquipmentType"],
                "properties": {"Name": "无人机", "Type": "陆地设备"},
            },
            "m": {
                "labels": ["Equipment"],
                "properties": {"Name": "多旋翼无人机"},
            },
        }
        line = _neo_row_one_line(row)
        self.assertIn("多旋翼无人机", line)
        self.assertNotIn("n:EquipmentType", line)


class TestResultGuardMatchAll(unittest.TestCase):
    def test_skips_retry_when_match_all(self):
        rec = {
            "scenario": "under_type",
            "params": {"match_all_equipment_types": True, "focus": "无人机"},
        }
        rows = [{"n": {"labels": ["EquipmentType"], "properties": {"Name": "无人机", "Type": "陆地设备"}}}]
        outcome = apply_result_guard(
            question="无人机有哪些具体装备",
            rec=rec,
            steps=[{"op_code": "neo_equipment_under_type", "params": {"keyword": "无人机", "match_all_equipment_types": True}}],
            neo_rows=rows,
            driver=MagicMock(),
            chroma_collection=None,
        )
        self.assertFalse(outcome.retried)
        self.assertEqual(outcome.neo_rows, rows)


if __name__ == "__main__":
    unittest.main()
