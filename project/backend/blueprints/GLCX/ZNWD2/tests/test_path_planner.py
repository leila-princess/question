"""ZNWD2 单元测试：路径规划。"""

from __future__ import annotations

import unittest

from blueprints.GLCX.ZNWD2.path_planner import plan
from blueprints.GLCX.ZNWD2.types import IntentJson


class TestPathPlanner(unittest.TestCase):
    def test_indicator_uses_equipment_only_macro(self) -> None:
        intent: IntentJson = {
            "intent_type": "查指标",
            "start_entities": ["Equipment"],
            "start_filters": {"Name": "无人机"},
            "environment_constraint": None,
            "target_entities": ["Indicator"],
        }
        p = plan(intent)
        self.assertEqual(p["mode"], "macro")
        self.assertEqual(p["macro"], "查指标")

    def test_list_equipment_type_macro(self) -> None:
        intent = {
            "intent_type": "列举",
            "target_entities": ["EquipmentType"],
        }
        p = plan(intent)  # type: ignore[arg-type]
        self.assertEqual(p["macro"], "列举")
        self.assertEqual(p["params"]["node_type"], "EquipmentType")

    def test_indicator_ignores_environment_in_planner(self) -> None:
        """validate_intent 会清除环境；规划器侧仍应走查指标宏而非项目宏。"""
        intent: IntentJson = {
            "intent_type": "查指标",
            "start_filters": {"Name": "无人机"},
            "environment_constraint": None,
        }
        p = plan(intent)
        self.assertEqual(p["macro"], "查指标")

    def test_project_with_environment(self) -> None:
        intent: IntentJson = {
            "intent_type": "查项目",
            "start_filters": {"Name": "坦克"},
            "environment_constraint": {"Name": "复杂电磁环境"},
        }
        p = plan(intent)
        self.assertEqual(p["macro"], "查项目_有环境")

    def test_project_without_environment(self) -> None:
        intent: IntentJson = {
            "intent_type": "查项目",
            "start_filters": {"Name": "坦克"},
        }
        p = plan(intent)
        self.assertEqual(p["macro"], "查项目")

    def test_method_via_project(self) -> None:
        intent: IntentJson = {
            "intent_type": "查方法",
            "start_filters": {"Name": "无人机"},
        }
        p = plan(intent)
        self.assertEqual(p["macro"], "查方法_经项目")

    def test_method_via_indicator(self) -> None:
        intent: IntentJson = {
            "intent_type": "查方法",
            "start_filters": {"Name": "无人机"},
            "additional_filters": {"route": "via_indicator"},
        }
        p = plan(intent)
        self.assertEqual(p["macro"], "查方法_经指标")

    def test_method_with_environment(self) -> None:
        intent: IntentJson = {
            "intent_type": "查方法",
            "start_filters": {"Name": "无人机"},
            "environment_constraint": {"Type": "高温"},
        }
        p = plan(intent)
        self.assertEqual(p["macro"], "查方法_经项目_有环境")

    def test_fallback_actions_linear_path(self) -> None:
        intent: IntentJson = {
            "intent_type": "枚举设备",
            "start_entities": ["EquipmentType"],
            "start_filters": {"Name": "无人机"},
            "target_entities": ["Equipment"],
        }
        p = plan(intent)
        self.assertEqual(p["mode"], "actions")
        names = [s["action_name"] for s in p["steps"]]
        self.assertIn("LocateNode", names)
        self.assertIn("ReturnAttributes", names)


if __name__ == "__main__":
    unittest.main()
