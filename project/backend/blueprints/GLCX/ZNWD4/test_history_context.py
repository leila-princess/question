"""历史承上补全：防止完整问句被槽位重组吞字。"""

import unittest
from unittest.mock import MagicMock, patch

from .question_route_lock import resolve_route_lock
from .rel_inference import looks_like_type_equipment_relation
from .znwd4_history_context import (
    _question_has_explicit_query_target,
    _slots_to_question,
    enrich_short_follow_up,
)


class TestHistoryContext(unittest.TestCase):
    def test_explicit_query_target_detected(self):
        self.assertTrue(_question_has_explicit_query_target("无人机有哪些装备"))
        self.assertTrue(_question_has_explicit_query_target("无人机有哪些试验项目"))
        self.assertFalse(_question_has_explicit_query_target("坦克呢"))

    def test_slots_to_question_preserves_list_tail(self):
        slots = {"equipment": ["无人机"], "environment": [], "project": [], "indicator": [], "standard": [], "equipment_type": []}
        out = _slots_to_question(slots, "无人机有哪些装备")
        self.assertEqual(out, "无人机有哪些装备")

    def test_enrich_skips_complete_question_with_history(self):
        from .znwd4_history_context import _enrich_short_follow_up_rules

        fake_store = MagicMock()
        fake_store.get_messages.return_value = [
            {"role": "user", "content": "复杂地理环境的分类有哪些"},
            {"role": "assistant", "content": "环境属性如下…"},
        ]
        with patch("blueprints.GLCX.ZNWD4.znwd4_history_context.get_memory_store", return_value=fake_store):
            enriched, _ = _enrich_short_follow_up_rules(
                "无人机有哪些装备",
                user_id="u1",
                session_id="s1",
            )
        self.assertEqual(enriched, "无人机有哪些装备")

    def test_route_lock_on_full_question(self):
        q = "无人机有哪些装备"
        self.assertTrue(looks_like_type_equipment_relation(q))
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.purpose, "query_equipment_under_type")
        self.assertEqual(lock.focus, "")

    def test_truncated_question_loses_lock(self):
        q = "无人机有哪些"
        self.assertFalse(looks_like_type_equipment_relation(q))


if __name__ == "__main__":
    unittest.main()
