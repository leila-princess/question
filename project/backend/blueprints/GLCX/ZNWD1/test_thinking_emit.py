"""思考区里程碑与清洗单测。"""

from __future__ import annotations

import unittest

from blueprints.GLCX.ZNWD1.user_biz_catalog import UBIZ_TEST_PROJECT
from blueprints.GLCX.ZNWD1.znwd1_thinking_emit import (
    milestone_reconcile,
    milestone_route_plan,
    sanitize_thinking_for_sse,
)


class TestThinkingEmit(unittest.TestCase):
    def test_milestone_route_plan_multi_equip_env(self) -> None:
        plan = {
            "user_biz": UBIZ_TEST_PROJECT,
            "scenario": "by_equip_and_env",
            "params_draft": {
                "equipment_keywords": ["无人机", "坦克"],
                "environment_keywords": ["复杂电磁环境"],
            },
        }
        msg = milestone_route_plan(plan)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("无人机", msg)
        self.assertIn("坦克", msg)
        self.assertIn("复杂电磁环境", msg)
        self.assertIn("试验项目问答", msg)
        self.assertNotIn("by_equip", msg)
        self.assertNotIn("match_confidence", msg)

    def test_milestone_reconcile_uses_scenario_title(self) -> None:
        rec = {
            "playbook": True,
            "user_biz": UBIZ_TEST_PROJECT,
            "user_biz_title": "试验项目问答",
            "scenario": "by_equip_and_env",
        }
        msg = milestone_reconcile(rec)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("某装备且某环境下的试验项目", msg)
        self.assertNotIn("by_equip_and_env", msg)

    def test_sanitize_strips_english_route_noise(self) -> None:
        raw = (
            'detectedmultipleequipmentkeywords: ["无人机", "坦克"]and environment keyword: ["复杂电磁环境"].\n'
            "Callingprobe for \"无人机\" and \"坦克\".\n"
            "match_confidence =high.\n"
            "在图谱中定位到：设备「无人机A型」"
        )
        cleaned = sanitize_thinking_for_sse(raw)
        self.assertIn("在图谱中定位到", cleaned)
        self.assertNotIn("match_confidence", cleaned)
        self.assertNotIn("Callingprobe", cleaned)


if __name__ == "__main__":
    unittest.main()
