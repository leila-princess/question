"""Hybrid 路由：LLM Step2 置信度触发 clarify。"""

import unittest

from .route_clarify import (
    build_route_clarify_payload_from_llm,
    needs_llm_route_clarify,
    topk_from_step2,
)
from .route_plan_v2 import parse_step2_json


class TestLlmRouteClarify(unittest.TestCase):
    def test_parse_step2_with_confidence(self):
        raw = """{
          "step": 2,
          "route_id": "R-TEST_PROJECT-by_equipment",
          "confidence": 0.74,
          "route_candidates": [
            {"route_id": "R-TEST_PROJECT-by_equipment", "confidence": 0.74, "reason": "a"},
            {"route_id": "R-INDICATOR-by_equipment", "confidence": 0.71, "reason": "b"}
          ],
          "keywords": ["无人机A型"],
          "params_draft": {"equipment_keywords": ["无人机A型"]}
        }"""
        step2 = parse_step2_json(raw)
        self.assertAlmostEqual(step2["confidence"], 0.74)
        self.assertEqual(len(step2["route_candidates"]), 2)
        self.assertTrue(needs_llm_route_clarify(step2))

    def test_high_confidence_no_clarify(self):
        step2 = {
            "route_id": "R-TEST_PROJECT-by_equipment",
            "confidence": 0.93,
            "route_candidates": [
                {"route_id": "R-TEST_PROJECT-by_equipment", "confidence": 0.93, "reason": "a"},
                {"route_id": "R-INDICATOR-by_equipment", "confidence": 0.10, "reason": "b"},
            ],
        }
        self.assertFalse(needs_llm_route_clarify(step2))

    def test_topk_from_step2(self):
        topk = topk_from_step2(
            {
                "route_candidates": [
                    {"route_id": "A", "confidence": 0.6},
                    {"route_id": "B", "confidence": 0.58},
                ]
            }
        )
        self.assertEqual(topk[0][0], "A")
        self.assertEqual(topk[1][0], "B")

    def test_build_payload_from_llm(self):
        step2 = {
            "route_id": "R-TEST_PROJECT-by_equipment",
            "confidence": 0.70,
            "route_candidates": [
                {"route_id": "R-TEST_PROJECT-by_equipment", "confidence": 0.70, "reason": "a"},
                {"route_id": "R-INDICATOR-by_equipment", "confidence": 0.68, "reason": "b"},
            ],
        }
        payload = build_route_clarify_payload_from_llm("无人机有哪些项目", step2, driver=None)
        self.assertIn("options", payload)
        self.assertGreaterEqual(len(payload["options"]), 2)
        self.assertEqual(payload.get("clarifySource"), "llm")


if __name__ == "__main__":
    unittest.main()
