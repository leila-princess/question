"""装备类型三级结构 & belongs_to 单测。"""

from __future__ import annotations

import unittest

from .business_catalog import BIZ_REL_ONE_HOP
from .business_expand import expand_business
from .formatters import format_equipment_belongs_to_type, format_rel_one_hop
from .plan_reconcile import _rel_params_from_draft
from .playbook_resolver import expand_playbook, expand_playbook_meta_steps
from .rel_inference import (
    looks_like_equipment_belongs_to_type,
    resolve_equipment_belongs_to_type_params,
)
from .user_biz_catalog import UBIZ_EQUIPMENT_TYPE


class TestEquipmentBelongsToType(unittest.TestCase):
    def test_looks_like_belongs_to(self):
        self.assertTrue(looks_like_equipment_belongs_to_type("无人机属于什么装备类型"))
        self.assertFalse(looks_like_equipment_belongs_to_type("地面装备有哪些具体装备"))

    def test_resolve_belongs_to_params(self):
        p = resolve_equipment_belongs_to_type_params(
            "无人机属于什么装备类型",
            node_type="Equipment",
            node_name="无人机A型",
            node_id="ZB_0001",
        )
        self.assertEqual(p["where_side"], "to")
        self.assertEqual(p["focus_id"], "ZB_0001")
        self.assertEqual(p["result_type"], "EquipmentType")

    def test_rel_params_from_draft_preserves_where_side_to(self):
        p = _rel_params_from_draft(
            {
                "from_type": "EquipmentType",
                "to_type": "Equipment",
                "rel_type": "HAS",
                "where_side": "to",
                "focus": "无人机",
            },
            node_type="Equipment",
            node_name="无人机A型",
            node_id="ZB_0001",
            q_plain="无人机属于什么装备类型",
            rq="无人机属于什么装备类型",
        )
        self.assertEqual(p["where_side"], "to")
        self.assertEqual(p["to_type"], "Equipment")
        self.assertEqual(p["result_type"], "EquipmentType")

    def test_expand_belongs_to_uses_relation_query(self):
        steps = expand_business(
            BIZ_REL_ONE_HOP,
            {
                "from_type": "EquipmentType",
                "to_type": "Equipment",
                "rel_type": "HAS",
                "where_side": "to",
                "focus": "无人机",
            },
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["op_code"], "neo_relation_query")
        self.assertEqual(steps[0]["params"]["where_side"], "to")

    def test_playbook_belongs_to_expand(self):
        plan = {
            "user_biz": UBIZ_EQUIPMENT_TYPE,
            "scenario": "belongs_to",
            "match_confidence": "high",
            "params_draft": {"equipment_keywords": ["无人机"], "focus": "无人机"},
            "keyword": "无人机",
        }
        probe = {"node_type": "Equipment", "id": "ZB_0001", "name": "无人机A型"}
        pb = expand_playbook(plan, question="无人机属于什么装备类型", probe_best=probe)
        self.assertIsNotNone(pb)
        assert pb is not None
        steps = expand_playbook_meta_steps(pb)
        self.assertEqual(steps[0]["op_code"], "neo_relation_query")
        self.assertEqual(steps[0]["params"]["where_side"], "to")

    def test_format_belongs_to_type(self):
        rows = [
            {
                "n": {
                    "labels": ["EquipmentType"],
                    "properties": {"Type": "空中装备", "Name": "无人机类", "Id": "ET_001"},
                },
                "m": {
                    "labels": ["Equipment"],
                    "properties": {"Name": "无人机A型", "Id": "ZB_0001"},
                },
            }
        ]
        text = format_equipment_belongs_to_type(rows, focus="无人机")
        self.assertIn("一级类别「空中装备」", text)
        self.assertIn("二级装备类型「无人机类」", text)
        rel_text = format_rel_one_hop(
            rows,
            from_type="EquipmentType",
            to_type="Equipment",
            focus="无人机",
            where_side="to",
        )
        self.assertIn("一级类别", rel_text)


if __name__ == "__main__":
    unittest.main()
