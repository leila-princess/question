"""LLM 整合回答（问句 + 检索证据）单测。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from .znwd4_experiment_config import znwd4_answer_synthesis_mode, znwd4_use_llm_answer_synthesis
from .znwd4_langchain_synthesis import (
    _build_synthesis_system_prompt,
    _build_synthesis_user_prompt,
    _compress_evidence,
    _evidence_has_substance,
    _format_slot_hints,
    _has_retrieval_evidence,
)


class TestAnswerSynthesis(unittest.TestCase):
    def test_default_mode_is_template(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(znwd4_answer_synthesis_mode(), "template")
            self.assertFalse(znwd4_use_llm_answer_synthesis("BIZ_REL_ONE_HOP"))

    def test_llm_mode_skips_template_business(self):
        with patch.dict(os.environ, {"ZNWD4_ANSWER_SYNTHESIS": "llm"}, clear=False):
            self.assertFalse(znwd4_use_llm_answer_synthesis("BIZ_DOCUMENT"))
            self.assertFalse(znwd4_use_llm_answer_synthesis("BIZ_DOCUMENT", strategy="db_direct_doc"))
            self.assertFalse(znwd4_use_llm_answer_synthesis("BIZ_REL_ONE_HOP"))
            self.assertTrue(
                znwd4_use_llm_answer_synthesis(
                    "BIZ_NODE_PROP",
                    user_biz="UBIZ_ENVIRONMENT",
                )
            )
            self.assertFalse(znwd4_use_llm_answer_synthesis("BIZ_NODE_PROP"))

    def test_llm_mode_composite_user_biz(self):
        with patch.dict(os.environ, {"ZNWD4_ANSWER_SYNTHESIS": "llm"}, clear=False):
            from .user_biz_catalog import COMP_INDICATOR_THEN_METHOD

            self.assertTrue(
                znwd4_use_llm_answer_synthesis(
                    "BIZ_REL_ONE_HOP",
                    user_biz=COMP_INDICATOR_THEN_METHOD,
                )
            )

    def test_slot_hints_in_user_prompt(self):
        hints = _format_slot_hints(
            {
                "equipment_type_parent": ["航空装备"],
                "equipment_type_keywords": ["无人机"],
            }
        )
        self.assertIn("航空装备", hints)
        user = _build_synthesis_user_prompt(
            "航空装备下无人机有什么具体装备",
            "[EquipUnderType] 一级类别=航空装备 二级类型=无人机 具体装备=察打一体无人机",
            params={"equipment_type_parent": ["航空装备"], "equipment_type_keywords": ["无人机"]},
        )
        self.assertIn("【用户问题】", user)
        self.assertIn("【已识别槽位】", user)
        self.assertIn("航空装备", user)
        self.assertIn("【检索证据】", user)

    def test_compress_equip_under_type_evidence(self):
        rows = [
            {
                "n": {
                    "labels": ["EquipmentType"],
                    "properties": {"Type": "航空装备", "Name": "无人机"},
                },
                "m": {
                    "labels": ["Equipment"],
                    "properties": {"Name": "察打一体无人机"},
                },
            }
        ]
        blob = _compress_evidence(rows, [])
        self.assertIn("EquipUnderType", blob)
        self.assertIn("航空装备", blob)
        self.assertIn("察打一体无人机", blob)

    def test_has_evidence_strict_prompt(self):
        rows = [{"n": {"labels": ["Equipment"], "properties": {"Name": "无人机"}}}]
        blob = _compress_evidence(rows, [])
        self.assertTrue(_has_retrieval_evidence(rows, [], blob))
        prompt = _build_synthesis_system_prompt(
            has_evidence=True,
            method_content_mode=False,
            env_settings_mode=False,
            project_method_generate_mode=False,
        )
        self.assertIn("严格据证", prompt)
        self.assertIn("禁止", prompt)

    def test_no_evidence_prompt(self):
        self.assertFalse(_has_retrieval_evidence([], [], ""))
        self.assertFalse(_evidence_has_substance(""))
        prompt = _build_synthesis_system_prompt(
            has_evidence=False,
            method_content_mode=False,
            env_settings_mode=False,
            project_method_generate_mode=False,
        )
        self.assertIn("无检索证据", prompt)


if __name__ == "__main__":
    unittest.main()
