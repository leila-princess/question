"""Probe-First Hybrid 路由回归：route-only 锁 + PCSR 填槽。"""

from __future__ import annotations

import unittest

from .pcsr_assembly import lock_probe_has_primary_hit, pcsr_slots, slots_ready_for_route
from .probe_preflight import EvidenceBundle, build_evidence_bundle, _rank_candidates
from .question_route_lock import lock_is_slotless, resolve_route_lock, skip_llm_for_lock
from .route_scorer import looks_like_multi_intent_question, should_use_ecs_fast_path, score_routes
from .routing_dispatcher import znwd4_routing_mode


class TestRouteOnlyLocks(unittest.TestCase):
    def test_equipment_indicators_route_only(self):
        lock = resolve_route_lock("无人机A型有哪些考核指标")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-INDICATOR-by_equipment")
        self.assertEqual(lock.slot_key, "equipment_keywords")
        self.assertEqual(lock.focus, "")
        self.assertEqual(lock.keywords, [])
        self.assertFalse(skip_llm_for_lock(lock))

    def test_equipment_test_projects_route_only(self):
        lock = resolve_route_lock("无人机有哪些试验项目")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-TEST_PROJECT-by_equipment")
        self.assertEqual(lock.focus, "")

    def test_enum_indicator_not_equipment_scoped(self):
        lock = resolve_route_lock("系统里有哪些考核指标")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-INDICATOR-list_all")
        self.assertTrue(lock_is_slotless(lock))
        self.assertTrue(skip_llm_for_lock(lock))

    def test_enum_equipment_slotless(self):
        lock = resolve_route_lock("系统里登记的全部装备有哪些")
        self.assertIsNotNone(lock)
        self.assertTrue(skip_llm_for_lock(lock))


class TestPCSRSlots(unittest.TestCase):
    def test_lock_probe_has_primary_hit(self):
        from .question_route_lock import resolve_route_lock

        lock = resolve_route_lock("低温环境有哪些试验项目")
        self.assertIsNotNone(lock)
        evidence = EvidenceBundle(
            probe_ran=True,
            best_by_label={
                "Environment": {"node_type": "Environment", "name": "低温环境", "id": "env-1"},
            },
        )
        self.assertTrue(lock_probe_has_primary_hit(lock, evidence))

    def test_pcsr_prefers_probe_over_empty_lock(self):
        lock = resolve_route_lock("无人机A型有哪些考核指标")
        self.assertIsNotNone(lock)
        evidence = EvidenceBundle(
            probe_ran=True,
            best_by_label={
                "Equipment": {"node_type": "Equipment", "name": "无人机A型", "id": "eq-1"},
            },
        )
        slots = pcsr_slots(
            route_id=lock.route_id,
            lock=lock,
            evidence=evidence,
            raw_slots={},
        )
        self.assertEqual(slots.get("equipment_keywords"), ["无人机A型"])
        self.assertEqual(slots.get("focus"), "无人机A型")

    def test_slots_ready_when_probe_hits(self):
        evidence = EvidenceBundle(
            probe_ran=True,
            best_by_label={"Equipment": {"node_type": "Equipment", "name": "无人机A型"}},
        )
        self.assertTrue(slots_ready_for_route("R-INDICATOR-by_equipment", evidence))

    def test_slots_not_ready_without_probe(self):
        evidence = EvidenceBundle(probe_ran=False)
        self.assertFalse(slots_ready_for_route("R-INDICATOR-by_equipment", evidence))


class TestProbeCandidateRank(unittest.TestCase):
    def test_rank_prefers_stripped_entity(self):
        q = "无人机A型有哪些考核指标"
        cands = _rank_candidates(["无人机A型有哪些", "无人机A型"], q)
        self.assertEqual(cands[0], "无人机A型")

    def test_build_bundle_without_driver(self):
        bundle = build_evidence_bundle("无人机A型有哪些考核指标", driver=None)
        self.assertIn("无人机A型", bundle.candidates)


class TestLockTypedProbe(unittest.TestCase):
    def test_lock_seed_and_typed_targets_equip_env(self):
        from .pcsr_assembly import _lock_seed_keywords, _lock_typed_probe_targets

        lock = resolve_route_lock("汽车2在test环境下有哪些试验项目")
        self.assertIsNotNone(lock)
        seeds = _lock_seed_keywords(lock)
        self.assertIn("汽车2", seeds)
        self.assertIn("test", seeds)
        targets = _lock_typed_probe_targets(lock)
        self.assertIn(("汽车2", "Equipment"), targets)
        self.assertIn(("test", "Environment"), targets)

    def test_pcsr_fills_secondary_slots_from_evidence(self):
        lock = resolve_route_lock("汽车2在test环境下有哪些试验项目")
        self.assertIsNotNone(lock)
        evidence = EvidenceBundle(
            probe_ran=True,
            zvec_used=True,
            best_by_label={
                "Equipment": {"node_type": "Equipment", "name": "汽车2", "id": "eq-car2"},
                "Environment": {"node_type": "Environment", "name": "test环境", "id": "env-test"},
            },
        )
        slots = pcsr_slots(
            route_id=lock.route_id,
            lock=lock,
            evidence=evidence,
            raw_slots={},
            question="汽车2在test环境下有哪些试验项目",
        )
        self.assertEqual(slots.get("equipment_keywords"), ["汽车2"])
        self.assertEqual(slots.get("environment_keywords"), ["test环境"])
        self.assertEqual(slots.get("environment_focus_id"), "env-test")
        self.assertEqual(slots.get("focus_id"), "eq-car2")

    def test_build_evidence_for_lock_sets_force_zvec(self):
        from unittest.mock import MagicMock, patch

        from .pcsr_assembly import build_evidence_for_question

        lock = resolve_route_lock("汽车2在test环境下有哪些试验项目")
        with patch("blueprints.GLCX.ZNWD4.pcsr_assembly.build_evidence_bundle") as mock_build:
            mock_build.return_value = EvidenceBundle(probe_ran=True)
            build_evidence_for_question("汽车2在test环境下有哪些试验项目", driver=MagicMock(), lock=lock)
            kwargs = mock_build.call_args.kwargs
            self.assertTrue(kwargs.get("force_zvec"))
            typed = kwargs.get("typed_probes") or []
            self.assertIn(("汽车2", "Equipment"), typed)
            self.assertIn(("test", "Environment"), typed)


class TestRoutingDispatcher(unittest.TestCase):
    def test_default_mode_probe_hybrid(self):
        self.assertEqual(znwd4_routing_mode(), "probe_hybrid")


class TestECSGuards(unittest.TestCase):
    def test_multi_intent_blocks_ecs(self):
        q = "低温环境测试项目有哪些相关标准？有哪些试验方法"
        self.assertTrue(looks_like_multi_intent_question(q))
        evidence = EvidenceBundle(
            probe_ran=True,
            best_by_label={"TestProject": {"node_type": "TestProject", "name": "低温环境测试项目"}},
        )
        scores = score_routes(q, evidence)
        self.assertFalse(should_use_ecs_fast_path(q, evidence, scores))

    def test_score_entity_match_prefers_embedded_canonical(self):
        from .keyword_refine import score_entity_match

        span = "无人机A型低温环境测试项目"
        good = score_entity_match(span, "低温环境测试项目", span)
        noise = score_entity_match(span, "人机", span)
        self.assertGreater(good, noise)


if __name__ == "__main__":
    unittest.main()
