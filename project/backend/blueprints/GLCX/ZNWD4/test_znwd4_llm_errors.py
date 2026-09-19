"""LLM unavailable detection + probe hybrid 502 fallback."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .znwd4_langchain_routing import ROUTE_PLAN_INTERNAL
from .znwd4_llm_errors import is_llm_unavailable_error, is_truthy


class TestLlmUnavailableError(unittest.TestCase):
    def test_detects_502_message(self):
        self.assertTrue(is_llm_unavailable_error(Exception("Error code: 502")))

    def test_detects_bad_gateway(self):
        self.assertTrue(is_llm_unavailable_error(Exception("502 Bad Gateway")))

    def test_detects_status_code_attr(self):
        exc = Exception("upstream")
        exc.status_code = 503  # type: ignore[attr-defined]
        self.assertTrue(is_llm_unavailable_error(exc))

    def test_detects_response_status_code(self):
        exc = Exception("upstream")
        exc.response = SimpleNamespace(status_code=504)  # type: ignore[attr-defined]
        self.assertTrue(is_llm_unavailable_error(exc))

    def test_ignores_validation_error(self):
        self.assertFalse(is_llm_unavailable_error(ValueError("rating must be positive or negative")))

    def test_is_truthy_string(self):
        self.assertTrue(is_truthy("true"))
        self.assertTrue(is_truthy("1"))
        self.assertFalse(is_truthy("false"))


class TestProbeHybrid502Fallback(unittest.TestCase):
    def test_stream_route_plan_502_uses_probe_fallback(self):
        from . import znwd4_probe_hybrid_routing as mod

        fallback_plan = {"route_id": "R-FALLBACK", "params_draft": {"equipment_keywords": ["无人机A型"]}}
        question = "无人机A型在复杂地理环境推荐的环境设置是什么"

        with patch.object(mod, "enrich_short_follow_up", return_value=(question, {})):
            with patch.object(mod, "resolve_route_lock", return_value=None):
                with patch.object(mod, "build_evidence_for_question", return_value=SimpleNamespace()):
                    with patch.object(mod, "score_routes", return_value=[SimpleNamespace(route_id="R-X", score=0.1)]):
                        with patch.object(mod, "should_use_ecs_fast_path", return_value=False):
                            with patch.object(mod, "stream_route_plan", side_effect=Exception("Error code: 502")):
                                with patch.object(mod, "_probe_fallback_plan", return_value=fallback_plan) as mock_fb:
                                    with patch.object(mod, "log_routing_event", return_value="evt-1"):
                                        events = list(
                                            mod.stream_route_plan_probe_hybrid(
                                                None,
                                                "test-model",
                                                question,
                                                driver=None,
                                            )
                                        )

        mock_fb.assert_called_once()
        plan_events = [e for e in events if e[0] == ROUTE_PLAN_INTERNAL]
        self.assertEqual(len(plan_events), 1)
        self.assertEqual(plan_events[0][1]["plan"]["route_id"], "R-FALLBACK")


class TestPipelineCorrectionSkipsRouting(unittest.TestCase):
    def test_answer_correction_does_not_call_stream_route_plan_auto(self):
        from .pipeline import stream_znwd4_chat
        from .routing_catalog import list_routes

        rid = list_routes()[0].route_id
        question = "无人机A型在复杂地理环境推荐的环境设置是什么"
        correction = {
            "turnId": "turn-1",
            "originalQuestion": question,
            "feedbackKind": "wrong_route",
            "correctedRouteId": rid,
            "predictedSlots": {"equipment_keywords": ["无人机A型"]},
            "rerun": True,
        }
        fake_plan = {
            "route_id": rid,
            "params_draft": {"equipment_keywords": ["无人机A型"]},
            "user_answer_correction": True,
        }

        with patch("blueprints.GLCX.ZNWD4.pipeline.stream_route_plan_auto") as mock_route:
            with patch(
                "blueprints.GLCX.ZNWD4.answer_correction.apply_answer_correction",
                return_value=fake_plan,
            ):
                with patch(
                    "blueprints.GLCX.ZNWD4.pipeline.reconcile_route_plan",
                    return_value={"answer_strategy": "direct_llm", "meta_answer": "ok"},
                ):
                    with patch("blueprints.GLCX.ZNWD4.pipeline.log_routing_event", return_value="evt-new"):
                        with patch("blueprints.GLCX.ZNWD4.pipeline.log_answer_correction_rerun"):
                            events = list(
                                stream_znwd4_chat(
                                    None,
                                    "test",
                                    question,
                                    answer_correction=correction,
                                    skip_instant_thinking=True,
                                )
                            )

        mock_route.assert_not_called()
        done = [e for e in events if e[0] == "done"]
        self.assertTrue(done)
        self.assertEqual(done[0][1].get("fullAnswer"), "ok")


if __name__ == "__main__":
    unittest.main()
