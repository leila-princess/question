"""ZNWD2 单元测试：意图解析。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from blueprints.GLCX.ZNWD2.intent_parser import IntentParseError, parse_intent, parse_intent_from_dict, validate_intent


class TestIntentParser(unittest.TestCase):
    def test_validate_intent_defaults(self) -> None:
        raw = {"intent_type": "查指标", "start_filters": {"Name": "无人机"}}
        intent = validate_intent(raw)
        self.assertEqual(intent["intent_type"], "查指标")
        self.assertEqual(intent["target_entities"], ["Indicator"])
        self.assertEqual(intent["start_entities"], ["Equipment"])

    def test_indicator_clears_environment(self) -> None:
        raw = {
            "intent_type": "查指标",
            "start_filters": {"Name": "无人机"},
            "environment_constraint": {"Type": "高原"},
        }
        intent = validate_intent(raw)
        self.assertIsNone(intent.get("environment_constraint"))

    def test_validate_invalid_intent_type(self) -> None:
        with self.assertRaises(IntentParseError):
            validate_intent({"intent_type": "无效"})

    def test_parse_intent_from_dict(self) -> None:
        intent = parse_intent_from_dict(
            {
                "intent_type": "查项目",
                "start_filters": {"Name": "坦克"},
            }
        )
        self.assertEqual(intent["target_entities"], ["TestProject"])

    def test_parse_intent_mock_llm(self) -> None:
        client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = (
            '{"intent_type":"查方法","start_filters":{"Name":"无人机"},'
            '"environment_constraint":null,"target_entities":["TestMethod"]}'
        )
        client.chat.completions.create.return_value = mock_resp

        intent = parse_intent("无人机有哪些试验方法", client=client, model="test-model")
        self.assertEqual(intent["intent_type"], "查方法")
        self.assertEqual(intent["target_entities"], ["TestMethod"])

    def test_parse_intent_all_types(self) -> None:
        for itype in ("列举", "查关联", "查指标", "查项目", "查方法", "查文件"):
            intent = validate_intent({"intent_type": itype, "target_entities": ["EquipmentType"]})
            self.assertEqual(intent["intent_type"], itype)


if __name__ == "__main__":
    unittest.main()
