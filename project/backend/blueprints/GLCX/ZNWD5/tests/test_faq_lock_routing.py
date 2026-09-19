"""ZNWD5 三层路由白名单与 FAQ 路径单测。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from blueprints.GLCX.ZNWD4.question_route_lock import resolve_route_lock
from blueprints.GLCX.ZNWD5.faq_lock_allowlist import (
    FAQ_ROUTE_IDS,
    filter_lock_for_planner_mode,
    is_composite_lock,
    is_faq_whitelist_lock,
    is_tier0_lock,
    lock_tier,
)


class TestFaqLockAllowlist(unittest.TestCase):
    def test_faq_route_ids_from_registry(self):
        self.assertIn("R-TEST_PROJECT-by_equipment", FAQ_ROUTE_IDS)
        self.assertIn("R-TEST_METHOD-by_project", FAQ_ROUTE_IDS)
        self.assertNotIn("R-C-PROJECT_THEN_METHOD-default", FAQ_ROUTE_IDS)

    def test_tier0_enum(self):
        lock = resolve_route_lock("有哪些试验项目")
        self.assertIsNotNone(lock)
        self.assertEqual(lock_tier(lock), "tier0")
        self.assertTrue(is_tier0_lock(lock))

    def test_faq_equipment_projects(self):
        lock = resolve_route_lock("无人机有哪些试验项目")
        self.assertIsNotNone(lock)
        self.assertEqual(lock_tier(lock), "faq")
        self.assertTrue(is_faq_whitelist_lock(lock))

    def test_composite_filtered_in_planner_mode(self):
        os.environ["ZNWD5_ROUTING_MODE"] = "action_planner"
        lock = resolve_route_lock("无人机有哪些试验项目，对应的试验方法是什么")
        if lock and is_composite_lock(lock):
            filtered = filter_lock_for_planner_mode(lock)
            self.assertIsNone(filtered)
        os.environ.pop("ZNWD5_ROUTING_MODE", None)

    def test_equip_env_is_faq_not_composite(self):
        lock = resolve_route_lock("汽车2在test环境下有哪些试验项目")
        self.assertIsNotNone(lock)
        self.assertFalse(is_composite_lock(lock))
        self.assertEqual(lock_tier(lock), "faq")

    def test_project_method_combo_not_faq(self):
        q = "无人机有哪些试验项目，对应的试验方法是什么"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        self.assertFalse(is_faq_whitelist_lock(lock, q))


class TestFaqLockRouting(unittest.TestCase):
    def test_slot_verify_fail_escalates_to_planner(self):
        from blueprints.GLCX.ZNWD4.znwd4_langchain_routing import ROUTE_PLAN_INTERNAL
        from blueprints.GLCX.ZNWD5.faq_lock_routing import stream_faq_lock_route

        lock = resolve_route_lock("无人机有哪些试验项目")
        self.assertIsNotNone(lock)
        events = []
        with patch(
            "blueprints.GLCX.ZNWD5.faq_lock_routing.verify_faq_slots",
            return_value=(False, {}, ""),
        ), patch(
            "blueprints.GLCX.ZNWD5.faq_lock_routing.build_evidence_for_question",
        ) as mock_ev, patch(
            "blueprints.GLCX.ZNWD5.faq_lock_routing.pcsr_slots",
            return_value={"equipment_keywords": ["无人机"]},
        ), patch(
            "blueprints.GLCX.ZNWD5.action_planner_routing.stream_action_plan_route",
        ) as mock_ap:
            from blueprints.GLCX.ZNWD4.probe_preflight import EvidenceBundle

            mock_ev.return_value = EvidenceBundle(probe_ran=True)
            mock_ap.return_value = iter([(ROUTE_PLAN_INTERNAL, {"plan": {"routing_tier": "action_planner"}})])
            for ev in stream_faq_lock_route(
                MagicMock(),
                "test-model",
                "无人机有哪些试验项目",
                lock,
                driver=MagicMock(),
            ):
                events.append(ev)
            mock_ap.assert_called_once()

    def test_slot_verify_ok_finalize(self):
        from blueprints.GLCX.ZNWD4.znwd4_langchain_routing import ROUTE_PLAN_INTERNAL
        from blueprints.GLCX.ZNWD5.faq_lock_routing import stream_faq_lock_route

        lock = resolve_route_lock("无人机有哪些试验项目")
        self.assertIsNotNone(lock)
        plan_out = None
        with patch(
            "blueprints.GLCX.ZNWD5.faq_lock_routing.verify_faq_slots",
            return_value=(True, {"equipment_keywords": ["无人机A型"], "focus": "无人机A型"}, ""),
        ), patch(
            "blueprints.GLCX.ZNWD5.faq_lock_routing.build_evidence_for_question",
        ) as mock_ev, patch(
            "blueprints.GLCX.ZNWD5.faq_lock_routing.pcsr_slots",
            return_value={"equipment_keywords": ["无人机A型"]},
        ), patch(
            "blueprints.GLCX.ZNWD5.faq_lock_routing.finalize_locked_plan",
            return_value={"route_id": lock.route_id, "params_draft": {}},
        ):
            from blueprints.GLCX.ZNWD4.probe_preflight import EvidenceBundle

            mock_ev.return_value = EvidenceBundle(probe_ran=True, zvec_used=True)
            for ev, data in stream_faq_lock_route(
                MagicMock(),
                "test-model",
                "无人机有哪些试验项目",
                lock,
                driver=MagicMock(),
            ):
                if ev == ROUTE_PLAN_INTERNAL:
                    plan_out = data.get("plan")
            self.assertIsNotNone(plan_out)
            self.assertEqual(plan_out.get("routing_tier"), "faq")
            self.assertTrue(plan_out.get("faq_slot_verified"))


class TestZvecGrounding(unittest.TestCase):
    def test_string_focus_slices_no_crash(self):
        from unittest.mock import patch

        from blueprints.GLCX.ZNWD5.zvec_grounding import ground_question

        with patch(
            "blueprints.GLCX.ZNWD5.zvec_grounding.extract_focus_candidates",
            return_value=["无人机", "试验项目"],
        ), patch(
            "blueprints.GLCX.ZNWD5.zvec_grounding.zvec_semantic_probe",
            return_value=[],
        ):
            g = ground_question("无人机有哪些试验项目", driver=None)
        self.assertIn("focus_slices", g)
        self.assertEqual(g["focus_slices"], ["无人机", "试验项目"])


if __name__ == "__main__":
    unittest.main()
