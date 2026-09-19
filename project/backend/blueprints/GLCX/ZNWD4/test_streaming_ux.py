"""Tests for streaming UX user copy and iter_meta_steps."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from .executor import iter_meta_steps
from .znwd4_user_copy import (
    milestone_ecs_fast_path,
    pack_step_payload,
    retrieval_done_message,
    sanitize_user_text,
    step_message,
    step_start_purpose,
)


class TestUserCopy(unittest.TestCase):
    def test_sanitize_strips_neo4j(self):
        self.assertEqual(
            sanitize_user_text("检索完成 Neo4j=8 Chroma=0"),
            "正在处理…",
        )

    def test_retrieval_done_message(self):
        msg = retrieval_done_message(8, 2)
        self.assertIn("清单数据 8 条", msg)
        self.assertIn("相关文档 2 份", msg)
        self.assertNotIn("Neo4j", msg)

    def test_step_message_retrieval_loading(self):
        msg = step_message("retrieval", "loading", purpose="航空装备 · 无人机 下的具体设备")
        self.assertIn("正在检索", msg)
        self.assertNotIn("neo_", msg)

    def test_pack_step_payload(self):
        p = pack_step_payload("routing", "done", userBizTitle="装备查询")
        self.assertEqual(p["type"], "routing")
        self.assertIn("message", p)
        self.assertNotIn("Neo4j", p["message"])

    def test_step_start_purpose(self):
        p = step_start_purpose({"purpose": "航空装备 · 无人机 下全部具体设备", "op_code": "neo_equipment_under_type"})
        self.assertIn("航空装备", p)

    def test_milestone_ecs_fast_path_no_route_id(self):
        msg = milestone_ecs_fast_path("R-EQUIPMENT-under_type", 0.78)
        self.assertNotIn("R-EQUIPMENT", msg)
        self.assertNotIn("score", msg.lower())
        self.assertIn("装备类型", msg)

    def test_pack_generating_done_payload(self):
        p = pack_step_payload("generating", "done", debugMessage="完成")
        self.assertEqual(p["type"], "generating")
        self.assertEqual(p["status"], "done")


class TestIterMetaSteps(unittest.TestCase):
    def test_yields_complete_on_empty_steps(self):
        events = list(iter_meta_steps(None, None, []))
        kinds = [e[0] for e in events]
        self.assertEqual(kinds, ["complete"])

    def test_skip_db_no_start_event(self):
        steps = [
            {
                "step_id": "s0",
                "op_code": "neo_node_query",
                "skip_db": True,
                "params": {},
            }
        ]
        events = list(iter_meta_steps(None, None, steps))
        self.assertEqual(events[-1][0], "complete")
        self.assertEqual(len(events[-1][1]["step_results"]), 1)


if __name__ == "__main__":
    unittest.main()
