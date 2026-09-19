"""LLM 承上启下补全单测。"""

import unittest
from unittest.mock import MagicMock, patch

from .question_route_lock import resolve_route_lock
from .znwd4_llm_context import (
    _clean_resolved_question,
    enrich_question_with_history,
    resolve_effective_question_with_llm,
)


class TestLlmContext(unittest.TestCase):
    def test_clean_resolved_question(self):
        self.assertEqual(
            _clean_resolved_question("无人机A型有哪些考核指标", "x"),
            "无人机A型有哪些考核指标",
        )
        self.assertEqual(
            _clean_resolved_question("问句：坦克有哪些试验项目", "x"),
            "坦克有哪些试验项目",
        )

    def test_resolve_indicator_follow_up(self):
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="无人机A型有哪些考核指标"))]
        )
        rounds = [
            {
                "user": "无人机A型有哪些试验项目",
                "assistant": "设备关联的测试项目…",
            }
        ]
        out = resolve_effective_question_with_llm(
            "有哪些考核指标",
            summary="用户在查无人机A型的试验项目",
            rounds=rounds,
            client=client,
            model="test",
        )
        self.assertEqual(out, "无人机A型有哪些考核指标")
        lock = resolve_route_lock(out)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.purpose, "query_equipment_indicators")

    def test_enrich_with_history_mock(self):
        fake_store = MagicMock()
        fake_store.get_summary.return_value = "用户关注无人机A型"
        fake_store.get_messages.return_value = [
            {"role": "user", "content": "无人机A型有哪些试验项目"},
            {"role": "assistant", "content": "共15条项目…"},
        ]
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="无人机A型有哪些考核指标"))]
        )
        with patch("blueprints.GLCX.ZNWD4.znwd4_llm_context.get_memory_store", return_value=fake_store):
            with patch(
                "blueprints.GLCX.ZNWD4.znwd4_llm_context.load_history_from_store",
                return_value={
                    "rounds": [
                        {
                            "user": "无人机A型有哪些试验项目",
                            "assistant": "共15条…",
                        }
                    ]
                },
            ):
                with patch(
                    "blueprints.GLCX.ZNWD4.znwd4_llm_context.llm_context_enrich_enabled",
                    return_value=True,
                ):
                    effective, snap = enrich_question_with_history(
                        "有哪些考核指标",
                        user_id="u1",
                        session_id="s1",
                        client=client,
                        model="test",
                    )
        self.assertEqual(effective, "无人机A型有哪些考核指标")
        self.assertEqual(snap.get("context_enrich"), "llm")


if __name__ == "__main__":
    unittest.main()
