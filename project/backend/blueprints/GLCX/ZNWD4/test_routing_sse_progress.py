"""路由 SSE 进度与工具轮里程碑单测。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from .znwd4_langchain_routing import _stream_tool_round, stream_route_plan
from .question_route_lock import RouteLock


class TestRoutingSseProgress(unittest.TestCase):
    def test_stream_tool_round_emits_progress_and_milestones(self):
        llm_tools = MagicMock()
        ai = AIMessage(content='{"purpose":"query_document"}')
        llm_tools.invoke.return_value = ai
        dispatch: dict = {}
        events = list(
            _stream_tool_round(
                llm_tools,
                [],
                dispatch,
                max_rounds=0,
                stage="step1",
            )
        )
        types = [e[0] for e in events]
        self.assertIn("route_progress", types)
        self.assertIn("thinking", types)
        self.assertEqual(types[-1], "_tool_round_result")
        llm_tools.invoke.assert_called_once()

    def test_complete_lock_skips_step1_tool_round(self):
        lock = RouteLock(
            purpose="query_environment_properties",
            route_id="R-ENVIRONMENT-properties",
            probe_labels=["Environment"],
            slot_key="environment_keywords",
            focus="低温",
            reason="test",
            keywords=["低温"],
        )
        client = MagicMock()
        events = []
        with patch(
            "blueprints.GLCX.ZNWD4.znwd4_langchain_routing.safe_resolve_route_lock",
            return_value=lock,
        ), patch(
            "blueprints.GLCX.ZNWD4.znwd4_langchain_routing.assemble_legacy_plan",
            return_value={"route_id": lock.route_id, "purpose_node": "Environment"},
        ), patch(
            "blueprints.GLCX.ZNWD4.routing_telemetry.log_routing_event",
            return_value="evt-1",
        ):
            for ev in stream_route_plan(
                client,
                "test-model",
                "低温环境的分类是什么",
                _skip_enrich=True,
                _effective_question="低温环境的分类是什么",
                _route_lock=lock,
            ):
                events.append(ev)
        types = [e[0] for e in events]
        self.assertIn("route_plan_internal", types)
        self.assertTrue(any(t == "route_progress" and d.get("stage") == "step1" for t, d in events))
        contents = "".join(d.get("content", "") for t, d in events if t == "thinking")
        self.assertNotIn("【Step1】", contents)


if __name__ == "__main__":
    unittest.main()
