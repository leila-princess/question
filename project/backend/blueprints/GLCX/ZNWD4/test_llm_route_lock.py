"""LLM 意图锁实验单测。"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from . import question_route_lock as qrl
from .llm_route_lock import resolve_route_lock_llm
from .route_lock_experiment import apply_route_lock_experiment_patch
from .route_lock_resolver import resolve_route_lock as resolve_unified
from .routing_context import clear_routing_llm_context, set_routing_llm_context


class _FakeChoice:
    def __init__(self, content: str):
        self.message = MagicMock()
        self.message.content = content


class _FakeResp:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class TestLlmRouteLock(unittest.TestCase):
    def setUp(self):
        clear_routing_llm_context()

    def tearDown(self):
        clear_routing_llm_context()

    def test_llm_parse_to_route_lock(self):
        payload = {
            "purpose": "query_equipment_by_project",
            "route_id": "R-EQUIPMENT-by_project",
            "keywords": ["低温环境测试项目", "振动环境测试项目"],
            "slot_key": "project_keywords",
            "confidence": 0.92,
            "reason": "反查装备",
        }
        client = MagicMock()
        client.chat.completions.create.return_value = _FakeResp(json.dumps(payload))
        set_routing_llm_context(client, "test-model")

        lock = resolve_route_lock_llm("低温环境测试项目和振动环境测试项目都有哪些设备要做")
        self.assertIsNotNone(lock)
        assert lock is not None
        self.assertEqual(lock.route_id, "R-EQUIPMENT-by_project")
        self.assertEqual(lock.slot_key, "project_keywords")
        self.assertEqual(len(lock.keywords), 2)

    def test_llm_no_context_returns_none(self):
        lock = resolve_route_lock_llm("无人机有哪些试验项目")
        self.assertIsNone(lock)

    def test_rules_mode_uses_unified_resolver(self):
        with patch.dict(os.environ, {"ZNWD4_ROUTE_LOCK_MODE": "rules"}, clear=False):
            from . import route_lock_experiment as exp_mod

            exp_mod._PATCHED = False
            apply_route_lock_experiment_patch()
            self.assertIs(qrl.resolve_route_lock, resolve_unified)
            lock = resolve_unified("无人机有哪些试验项目")
            self.assertIsNotNone(lock)

    def test_hybrid_uses_rules_without_llm(self):
        with patch.dict(os.environ, {"ZNWD4_ROUTE_LOCK_MODE": "hybrid"}, clear=False):
            with patch("blueprints.GLCX.ZNWD4.route_lock_resolver.resolve_route_lock_llm") as mock_llm:
                lock = resolve_unified("无人机有哪些试验项目")
                self.assertIsNotNone(lock)
                mock_llm.assert_not_called()

    def test_hybrid_log_compare_skips_llm_when_rules_hit(self):
        with patch.dict(
            os.environ,
            {"ZNWD4_ROUTE_LOCK_MODE": "hybrid", "ZNWD4_ROUTE_LOCK_LOG_COMPARE": "1"},
            clear=False,
        ):
            with patch("blueprints.GLCX.ZNWD4.route_lock_resolver.resolve_route_lock_llm") as mock_llm:
                lock = resolve_unified("无人机有哪些试验项目")
                self.assertIsNotNone(lock)
                mock_llm.assert_not_called()

    def test_request_cache_dedupes_llm_calls(self):
        with patch.dict(os.environ, {"ZNWD4_ROUTE_LOCK_MODE": "llm"}, clear=False):
            payload = {
                "purpose": "query_environment_properties",
                "route_id": "R-ENVIRONMENT-properties",
                "keywords": ["复杂电磁环境"],
                "slot_key": "environment_keywords",
                "confidence": 0.95,
                "reason": "test",
            }
            client = MagicMock()
            client.chat.completions.create.return_value = _FakeResp(json.dumps(payload))
            set_routing_llm_context(client, "test-model")
            from .route_lock_resolver import begin_route_lock_request_cache, clear_route_lock_request_cache

            begin_route_lock_request_cache()
            try:
                q = "复杂电磁环境约束有哪些?"
                lock1 = resolve_unified(q)
                lock2 = resolve_unified(q)
                self.assertIsNotNone(lock1)
                self.assertEqual(lock1.route_id, lock2.route_id if lock2 else "")
                self.assertEqual(client.chat.completions.create.call_count, 1)
            finally:
                clear_route_lock_request_cache()

    def test_llm_mode_fallback_rules(self):
        with patch.dict(os.environ, {"ZNWD4_ROUTE_LOCK_MODE": "llm"}, clear=False):
            with patch(
                "blueprints.GLCX.ZNWD4.route_lock_resolver.resolve_route_lock_llm",
                return_value=None,
            ):
                lock = resolve_unified("无人机有哪些试验项目")
                self.assertIsNotNone(lock)
                self.assertEqual(lock.route_id, "R-TEST_PROJECT-by_equipment")


if __name__ == "__main__":
    unittest.main()
