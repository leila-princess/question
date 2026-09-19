"""Tests for unified IntentPlan and slot binding."""

from __future__ import annotations

import unittest

from .intent_plan import CHAIN_EQUIP_PROJECT_METHOD, build_intent_plan
from .intent_plan_expand import expand_intent_plan_meta_groups
from .slot_binding import bind_intent_plan, detect_goal_spans


class TestGoalSpanDetection(unittest.TestCase):
    def test_project_and_indicator_connector(self):
        spans = detect_goal_spans("无人机有哪些试验项目和考核指标")
        goals = [s.goal for s in spans]
        self.assertEqual(goals, ["test_projects", "indicators"])

    def test_split_by_comma(self):
        spans = detect_goal_spans("无人机A型有哪些试验项目，坦克有哪些考核指标")
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0].goal, "test_projects")
        self.assertEqual(spans[1].goal, "indicators")


class TestSlotBinding(unittest.TestCase):
    def test_shared_equipment_one_to_many(self):
        spans = detect_goal_spans("无人机有哪些试验项目和考核指标")
        plan = bind_intent_plan("无人机有哪些试验项目和考核指标", spans)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.binding_mode, "shared")
        self.assertEqual(len(plan.steps), 2)
        for st in plan.steps:
            self.assertEqual(st.slots.get("equipment_keywords"), ["无人机"])

    def test_split_equipment_one_to_one(self):
        q = "无人机A型有哪些试验项目，坦克有哪些考核指标"
        spans = detect_goal_spans(q)
        plan = bind_intent_plan(q, spans)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.binding_mode, "split")
        self.assertEqual(plan.steps[0].slots.get("equipment_keywords"), ["无人机A型"])
        self.assertEqual(plan.steps[1].slots.get("equipment_keywords"), ["坦克"])

    def test_merged_equip_project_methods_chain(self):
        q = "无人机A型的高低温试验项目的方法是什么"
        spans = detect_goal_spans(q)
        plan = bind_intent_plan(q, spans)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].chain, CHAIN_EQUIP_PROJECT_METHOD)
        self.assertEqual(plan.steps[0].anchor_slots.get("equipment_keywords"), ["无人机A型"])
        self.assertTrue(plan.steps[0].filter_hints.get("project_keywords"))


class TestBuildIntentPlan(unittest.TestCase):
    def test_single_project_returns_none(self):
        self.assertIsNone(build_intent_plan("无人机有哪些试验项目"))

    def test_multi_returns_plan(self):
        plan = build_intent_plan("无人机有哪些试验项目和考核指标")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.steps), 2)

    def test_please_explain_prefix_equipment(self):
        q = "请分别说明通信设备A的试验项目和考核指标"
        spans = detect_goal_spans(q)
        plan = bind_intent_plan(q, spans)
        self.assertIsNotNone(plan)
        assert plan is not None
        for st in plan.steps:
            self.assertEqual(st.slots.get("equipment_keywords"), ["通信设备A"])
            self.assertEqual(st.slots.get("focus"), "通信设备A")


class TestIntentExpand(unittest.TestCase):
    def test_parallel_groups(self):
        plan = build_intent_plan("无人机有哪些试验项目和考核指标")
        assert plan is not None
        groups = expand_intent_plan_meta_groups(plan)
        self.assertEqual(len(groups), 2)

    def test_chain_has_filter_and_ref(self):
        plan = build_intent_plan("无人机A型的高低温试验项目的方法是什么")
        assert plan is not None
        groups = expand_intent_plan_meta_groups(plan)
        flat = [st for g in groups for st in g]
        ops = [st.get("op_code") for st in flat]
        self.assertIn("neo_property_filter", ops)
        s2 = next(st for st in flat if st.get("step_id") == "s2")
        ref = (s2.get("params") or {}).get("from_values_ref") or {}
        self.assertEqual(ref.get("step_id"), "s1b")


if __name__ == "__main__":
    unittest.main()
