"""Unit tests for hierarchical equipment-type disambiguation."""

from __future__ import annotations

import os
import unittest

from .entity_clarify import (
    apply_entity_selection,
    build_entity_clarify_payload,
    entity_clarify_from_plan,
    needs_entity_clarify,
)
from .entity_disambiguation import disambiguate_equipment_type_hits, disambiguate_probe_snapshot
from .hierarchy_focus import apply_hierarchy_to_params, parse_equipment_type_hierarchy
from .result_guard import (
    compute_match_rate,
    filter_rows_by_anchor,
    parent_type_from_rec,
    row_matches_hierarchy,
)


def _et_hit(node_id: str, name: str, type_value: str, score: float = 5.0) -> dict:
    return {
        "node_type": "EquipmentType",
        "id": node_id,
        "name": name,
        "type_value": type_value,
        "score": score,
    }


def _row(parent_type: str, et_name: str, eq_name: str) -> dict:
    return {
        "n": {
            "labels": ["EquipmentType"],
            "properties": {"Name": et_name, "Type": parent_type},
        },
        "m": {
            "labels": ["Equipment"],
            "properties": {"Name": eq_name},
        },
    }


class TestHierarchyFocus(unittest.TestCase):
    def test_parent_child_under(self):
        q = "航空装备下无人机有什么具体装备"
        hf = parse_equipment_type_hierarchy(q)
        self.assertIsNotNone(hf)
        assert hf is not None
        self.assertEqual(hf.parent_type, "航空装备")
        self.assertEqual(hf.child_name, "无人机")

    def test_parent_child_de(self):
        q = "陆地设备的无人机有哪些装备"
        hf = parse_equipment_type_hierarchy(q)
        self.assertIsNotNone(hf)
        assert hf is not None
        self.assertEqual(hf.parent_type, "陆地设备")
        self.assertEqual(hf.child_name, "无人机")

    def test_child_only(self):
        q = "无人机有哪些具体装备"
        hf = parse_equipment_type_hierarchy(q)
        self.assertIsNotNone(hf)
        assert hf is not None
        self.assertEqual(hf.parent_type, "")
        self.assertEqual(hf.child_name, "无人机")

    def test_apply_hierarchy_to_params(self):
        params = apply_hierarchy_to_params({}, "航空装备下无人机有什么具体装备")
        self.assertEqual(params.get("equipment_type_parent"), "航空装备")
        self.assertEqual(params.get("equipment_type_keywords"), ["无人机"])
        self.assertEqual(params.get("focus"), "无人机")


class TestProbeDisambiguation(unittest.TestCase):
    def test_same_name_prefers_parent_in_question(self):
        hits = [
            _et_hit("et_land", "无人机", "陆地设备", score=8.0),
            _et_hit("et_air", "无人机", "航空装备", score=7.0),
        ]
        q = "航空装备下无人机有什么具体装备"
        result = disambiguate_equipment_type_hits(hits, question=q)
        self.assertIsNotNone(result.best)
        assert result.best is not None
        self.assertEqual(result.best["id"], "et_air")
        self.assertEqual(result.best["type_value"], "航空装备")
        self.assertFalse(result.ambiguous)

    def test_same_name_low_margin_is_ambiguous(self):
        os.environ["ZNWD4_ENTITY_CLARIFY"] = "1"
        os.environ["ZNWD4_ENTITY_CLARIFY_MARGIN"] = "3"
        hits = [
            _et_hit("et_land", "无人机", "陆地设备", score=8.0),
            _et_hit("et_air", "无人机", "航空装备", score=8.0),
        ]
        q = "无人机有哪些具体装备"
        result = disambiguate_equipment_type_hits(hits, question=q)
        self.assertTrue(result.ambiguous)
        self.assertGreaterEqual(len(result.candidates), 2)

    def test_disambiguate_probe_snapshot_updates_best(self):
        snap = {
            "keyword": "无人机",
            "hits": [
                _et_hit("et_land", "无人机", "陆地设备"),
                _et_hit("et_air", "无人机", "航空装备"),
            ],
            "best": _et_hit("et_land", "无人机", "陆地设备"),
            "match_count": 2,
        }
        out = disambiguate_probe_snapshot(
            snap,
            question="航空装备下无人机有什么具体装备",
        )
        self.assertEqual(out["best"]["id"], "et_air")


class TestEntityClarify(unittest.TestCase):
    def test_build_entity_clarify_payload(self):
        cands = [
            {"id": "et1", "name": "无人机", "type_value": "航空装备", "label": "航空装备 · 无人机"},
            {"id": "et2", "name": "无人机", "type_value": "陆地设备", "label": "陆地设备 · 无人机"},
        ]
        payload = build_entity_clarify_payload(
            question="无人机有哪些具体装备",
            slot_key="equipment_type_keywords",
            child_name="无人机",
            candidates=cands,
            scenario="under_type",
        )
        self.assertEqual(len(payload["options"]), 2)
        self.assertIn("无人机", payload["prompt"])

    def test_entity_clarify_from_plan(self):
        plan = {
            "scenario": "under_type",
            "probe_snapshot": {
                "entity_disambiguation": {
                    "ambiguous": True,
                    "candidates": [
                        {"id": "et1", "name": "无人机", "type_value": "航空装备", "label": "航空装备 · 无人机"},
                        {"id": "et2", "name": "无人机", "type_value": "陆地设备", "label": "陆地设备 · 无人机"},
                    ],
                },
            },
        }
        rec = {
            "scenario": "under_type",
            "params": {"equipment_type_keywords": ["无人机"], "focus": "无人机"},
        }
        self.assertTrue(needs_entity_clarify(rec, plan))
        payload = entity_clarify_from_plan(plan, rec, question="无人机有哪些具体装备")
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(len(payload["options"]), 2)

    def test_apply_entity_selection(self):
        plan = {
            "params_draft": {"equipment_type_keywords": ["无人机"]},
            "probe_snapshot": {"best": {"id": "old", "name": "无人机"}},
        }
        out = apply_entity_selection(
            plan,
            node_id="et_air",
            parent_type="航空装备",
            child_name="无人机",
        )
        self.assertEqual(out["params_draft"]["focus_id"], "et_air")
        self.assertEqual(out["params_draft"]["equipment_type_parent"], "航空装备")
        self.assertEqual(out["probe_snapshot"]["best"]["type_value"], "航空装备")


class TestResultGuardHierarchy(unittest.TestCase):
    def test_row_matches_hierarchy(self):
        row = _row("航空装备", "无人机", "固定翼无人机")
        self.assertTrue(row_matches_hierarchy(row, "航空装备", "无人机"))
        self.assertFalse(row_matches_hierarchy(row, "陆地设备", "无人机"))

    def test_compute_match_rate_with_parent(self):
        rows = [
            _row("航空装备", "无人机", "固定翼无人机"),
            _row("陆地设备", "无人机", "多旋翼无人机"),
        ]
        rate = compute_match_rate(rows, "无人机", parent="航空装备")
        self.assertAlmostEqual(rate, 0.5)

    def test_filter_rows_by_parent(self):
        rows = [
            _row("航空装备", "无人机", "固定翼无人机"),
            _row("陆地设备", "无人机", "多旋翼无人机"),
        ]
        filtered = filter_rows_by_anchor(rows, "无人机", parent="航空装备")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(
            filtered[0]["n"]["properties"]["Type"],
            "航空装备",
        )

    def test_parent_type_from_rec(self):
        rec = {"params": {"equipment_type_parent": "航空装备"}}
        self.assertEqual(parent_type_from_rec(rec), "航空装备")


if __name__ == "__main__":
    unittest.main()
