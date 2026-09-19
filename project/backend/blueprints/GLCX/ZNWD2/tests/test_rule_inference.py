"""ZNWD2 单元测试：规则推断与列举。"""

from __future__ import annotations

import unittest

from blueprints.GLCX.ZNWD2.intent_parser import parse_intent_from_dict, validate_intent
from blueprints.GLCX.ZNWD2.path_planner import plan
from blueprints.GLCX.ZNWD2.rule_inference import infer_intent_from_rules


class TestRuleInference(unittest.TestCase):
    def test_enumerate_equipment_type(self) -> None:
        raw = infer_intent_from_rules("有哪些设备类型")
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(raw["intent_type"], "列举")
        self.assertEqual(raw["target_entities"], ["EquipmentType"])

    def test_enumerate_environment(self) -> None:
        raw = infer_intent_from_rules("列出全部试验环境")
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(raw["target_entities"], ["Environment"])

    def test_type_to_equipment_relation(self) -> None:
        raw = infer_intent_from_rules("地面装备有哪些具体设备")
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(raw["intent_type"], "查关联")

    def test_plan_list_equipment_type(self) -> None:
        intent = validate_intent(infer_intent_from_rules("有哪些设备类型") or {})
        p = plan(intent)
        self.assertEqual(p["mode"], "macro")
        self.assertEqual(p["macro"], "列举")
        self.assertEqual(p["params"]["node_type"], "EquipmentType")

    def test_indicator_not_for_equipment_type_question(self) -> None:
        """LLM 误填查指标时，validate + correct 应通过 rule 路径规避。"""
        from blueprints.GLCX.ZNWD2.rule_inference import correct_intent_from_rules

        wrong = parse_intent_from_dict(
            {"intent_type": "查指标", "start_filters": {"Name": "设备类型"}}
        )
        fixed = correct_intent_from_rules("有哪些设备类型", wrong)
        self.assertEqual(fixed["intent_type"], "列举")
        self.assertEqual(fixed["target_entities"], ["EquipmentType"])


if __name__ == "__main__":
    unittest.main()
