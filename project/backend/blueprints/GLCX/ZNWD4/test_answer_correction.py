"""Unit tests for post-answer correction hints and telemetry."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .answer_correction import (
    apply_answer_correction,
    answer_feedback_enabled,
    build_correction_hints,
    validate_correction_payload,
    validate_feedback_payload,
)
from .routing_telemetry import log_answer_feedback, telemetry_dir


class TestAnswerCorrection(unittest.TestCase):
    def test_answer_feedback_enabled_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZNWD4_ANSWER_FEEDBACK", None)
            self.assertTrue(answer_feedback_enabled())

    def test_build_correction_hints_shape(self):
        plan = {
            "route_id": "R-CHAT-direct",
            "params_draft": {"equipment_keywords": ["无人机A型"]},
        }
        with patch.dict(os.environ, {"ZNWD4_ANSWER_FEEDBACK": "1"}):
            hints = build_correction_hints(plan, {"params": plan["params_draft"]}, "无人机有哪些项目", driver=None)
        self.assertTrue(hints.get("enabled"))
        self.assertIn("feedbackKinds", hints)
        self.assertEqual(hints.get("predictedRouteId"), "R-CHAT-direct")
        self.assertTrue(any(c.get("slotKey") == "equipment_keywords" for c in hints.get("slotChips") or []))

    def test_validate_feedback_positive(self):
        ok, err = validate_feedback_payload({"turnId": "t1", "rating": "positive"})
        self.assertTrue(ok, err)

    def test_validate_feedback_negative_requires_kind(self):
        ok, err = validate_feedback_payload({"turnId": "t1", "rating": "negative"})
        self.assertFalse(ok)
        self.assertIn("feedbackKind", err)

    def test_apply_answer_correction_requires_route(self):
        payload = {
            "turnId": "t1",
            "originalQuestion": "坦克有哪些试验项目",
            "feedbackKind": "wrong_route",
            "rerun": True,
        }
        ok, err = validate_correction_payload({**payload, "correctedRouteId": ""})
        self.assertFalse(ok)

    def test_validate_correction_rerun_without_rating(self):
        from .routing_catalog import list_routes

        rid = list_routes()[0].route_id
        ok, err = validate_correction_payload(
            {
                "turnId": "t1",
                "originalQuestion": "无人机A型在复杂地理环境推荐的环境设置是什么",
                "feedbackKind": "wrong_route",
                "correctedRouteId": rid,
                "rerun": True,
            }
        )
        self.assertTrue(ok, err)

    def test_validate_correction_rerun_string_true(self):
        from .routing_catalog import list_routes

        rid = list_routes()[0].route_id
        ok, err = validate_correction_payload(
            {
                "turnId": "t1",
                "originalQuestion": "无人机A型在复杂地理环境推荐的环境设置是什么",
                "feedbackKind": "wrong_route",
                "correctedRouteId": rid,
                "rerun": "true",
            }
        )
        self.assertTrue(ok, err)

    def test_apply_answer_correction_builds_plan(self):
        # Use a catalog route that exists in routing_catalog
        from .routing_catalog import list_routes

        routes = list_routes()
        self.assertTrue(len(routes) > 0)
        rid = routes[0].route_id
        payload = {
            "turnId": "turn-abc",
            "routingEventId": "evt-1",
            "originalQuestion": "示例问题",
            "feedbackKind": "wrong_route",
            "correctedRouteId": rid,
            "correctedSlots": {"equipment_keywords": ["坦克"]},
            "rerun": True,
        }
        plan = apply_answer_correction(payload, question="示例问题", driver=None)
        self.assertEqual(plan["route_id"], rid)
        self.assertEqual(plan["match_confidence"], "high")
        self.assertTrue(plan.get("user_answer_correction"))
        self.assertEqual(plan["params_draft"].get("equipment_keywords"), ["坦克"])

    def test_apply_answer_correction_preserves_user_slots_without_driver(self):
        from .routing_catalog import list_routes

        rid = "R-EQUIPMENT-under_type"
        if not any(r.route_id == rid for r in list_routes()):
            rid = list_routes()[0].route_id
        payload = {
            "turnId": "turn-abc",
            "routingEventId": "evt-1",
            "originalQuestion": "无人机有哪些具体装备",
            "feedbackKind": "wrong_slots",
            "correctedRouteId": rid,
            "correctedSlots": {"equipment_type_keywords": ["无人机"], "focus": "无人机"},
            "rerun": True,
        }
        plan = apply_answer_correction(payload, question="无人机有哪些具体装备", driver=None)
        draft = plan.get("params_draft") or {}
        self.assertEqual(draft.get("equipment_type_keywords"), ["无人机"])


class TestAnswerFeedbackTelemetry(unittest.TestCase):
    def test_log_answer_feedback_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "ZNWD4_ROUTING_TELEMETRY": "1",
                    "ZNWD4_ANSWER_FEEDBACK": "1",
                    "ZNWD4_ROUTING_TELEMETRY_DIR": tmp,
                },
            ):
                fid = log_answer_feedback(
                    turn_id="turn-1",
                    routing_event_id="evt-1",
                    rating="negative",
                    feedback_kind="wrong_route",
                    question="Q1",
                    predicted_route_id="R-A",
                    corrected_route_id="R-B",
                    comment="不对",
                    answer_preview="预览" * 200,
                    rerun_requested=True,
                    user_id="u1",
                    session_id="s1",
                )
                self.assertTrue(fid)
                files = list(Path(telemetry_dir()).glob("routing_events_*.jsonl"))
                self.assertEqual(len(files), 1)
                line = files[0].read_text(encoding="utf-8").strip()
                rec = json.loads(line)
                self.assertEqual(rec["type"], "answer_feedback")
                self.assertEqual(rec["turn_id"], "turn-1")
                self.assertEqual(rec["rating"], "negative")
                self.assertEqual(rec["feedback_kind"], "wrong_route")
                self.assertTrue(rec["rerun_requested"])
                self.assertLessEqual(len(rec["answer_preview"]), 500)


if __name__ == "__main__":
    unittest.main()
