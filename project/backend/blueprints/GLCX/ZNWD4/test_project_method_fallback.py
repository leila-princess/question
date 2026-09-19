"""项目无试验方法时的兜底检测与上下文整理。"""

from __future__ import annotations

import unittest

from .project_method_fallback import (
    attach_method_create_action,
    build_method_create_action,
    compress_project_method_generate_context,
    default_method_label,
    extract_graph_entities_from_neo_rows,
    indicator_names_from_params,
    is_project_method_list_query,
    project_method_rows_empty,
    project_names_from_params,
    resolve_method_draft_focus_names,
    resolve_project_names,
    should_offer_method_draft,
)


class TestProjectMethodFallback(unittest.TestCase):
    def test_empty_when_no_testmethod(self):
        self.assertTrue(project_method_rows_empty([]))
        self.assertTrue(
            project_method_rows_empty(
                [{"n": {"labels": ["TestProject"], "properties": {"Name": "低温试验"}}}]
            )
        )
        self.assertFalse(
            project_method_rows_empty(
                [
                    {
                        "m": {
                            "labels": ["TestMethod"],
                            "properties": {"Name": "方法A"},
                        }
                    }
                ]
            )
        )

    def test_project_names_from_params(self):
        names = project_names_from_params(
            {"project_keywords": ["低温试验", "振动试验"], "focus": "x"}
        )
        self.assertEqual(names, ["低温试验", "振动试验"])

    def test_compress_context_includes_sections(self):
        blob = compress_project_method_generate_context(
            [
                {
                    "n": {"labels": ["Equipment"], "properties": {"Name": "坦克A"}},
                    "m": {"labels": ["TestProject"], "properties": {"Name": "低温试验"}},
                }
            ],
            [{"title": "GJB150", "content": "试验条件应满足…"}],
            project_names=["低温试验"],
            question="低温试验有哪些试验方法",
        )
        self.assertIn("关联装备", blob)
        self.assertIn("坦克A", blob)
        self.assertIn("文档片段", blob)

    def test_extract_graph_entities(self):
        neo = [
            {
                "n": {
                    "labels": ["Equipment"],
                    "properties": {"Name": "坦克A", "Id": "EQ1"},
                },
                "m": {
                    "labels": ["TestProject"],
                    "properties": {"Name": "低温试验", "Id": "TP1"},
                },
            }
        ]
        rel = extract_graph_entities_from_neo_rows(neo, project_keywords=["低温试验"])
        self.assertEqual(len(rel["equipments"]), 1)
        self.assertEqual(rel["equipments"][0]["id"], "EQ1")
        self.assertEqual(rel["projects"][0]["name"], "低温试验")

    def test_resolve_project_names_from_question(self):
        names = resolve_project_names({}, question="飞行振动测试的试验方法是什么")
        self.assertTrue(names)
        self.assertIn("飞行振动测试", names[0])

    def test_is_project_method_list_query(self):
        self.assertTrue(
            is_project_method_list_query(
                business_id="BIZ_PROJECT_TO_METHOD",
                user_biz="UBIZ_TEST_METHOD",
                route_id="R-TEST_METHOD-by_project",
            )
        )
        self.assertTrue(
            should_offer_method_draft(
                business_id="BIZ_PROJECT_TO_METHOD",
                user_biz="UBIZ_TEST_METHOD",
                route_id="R-TEST_METHOD-by_project",
                scenario="by_project",
                neo_rows=[],
                params={"project_name": "低温试验"},
                question="低温试验有哪些试验方法",
            )[0]
        )

    def test_build_method_create_action_label(self):
        neo = [
            {
                "n": {
                    "labels": ["Equipment"],
                    "properties": {"Name": "坦克A", "Id": "EQ1"},
                },
                "m": {
                    "labels": ["TestProject"],
                    "properties": {"Name": "低温试验", "Id": "TP1"},
                },
            }
        ]
        action = build_method_create_action(
            question="低温试验有哪些试验方法",
            plan={"route_id": "R-TEST_METHOD-by_project"},
            rec={"route_id": "R-TEST_METHOD-by_project", "params": {"project_keywords": ["低温试验"]}},
            neo_rows=neo,
            full_answer="**（一）试验条件**\n\n…",
        )
        self.assertIsNotNone(action)
        assert action is not None
        self.assertTrue(action["enabled"])
        self.assertEqual(action["draft"]["method"]["name"], "低温试验试验方法")
        self.assertEqual(action["label"], "新增为试验方法")
        self.assertEqual(default_method_label("低温试验"), "低温试验")

    def test_should_offer_for_method_content_without_synr(self):
        neo = [
            {
                "m": {
                    "labels": ["TestMethod"],
                    "properties": {"Name": "低温试验方法"},
                }
            }
        ]
        offer, names = should_offer_method_draft(
            business_id="BIZ_METHOD_CONTENT",
            user_biz="UBIZ_TEST_METHOD",
            route_id="R-TEST_METHOD-content",
            scenario="content",
            neo_rows=neo,
            params={"method_name": "低温试验方法"},
            question="低温试验方法的试验条件是什么",
        )
        self.assertTrue(offer)
        self.assertEqual(names, ["低温试验方法"])

    def test_should_offer_for_indicator_methods(self):
        offer, names = should_offer_method_draft(
            business_id="BIZ_REL_ONE_HOP",
            user_biz="UBIZ_TEST_METHOD",
            route_id="R-TEST_METHOD-by_indicator",
            scenario="by_indicator",
            neo_rows=[],
            params={"indicator_keywords": ["工作电压"]},
            question="工作电压指标有哪些试验方法",
        )
        self.assertTrue(offer)
        self.assertEqual(names, ["工作电压"])

    def test_attach_method_create_action(self):
        action_payload = attach_method_create_action(
            {"fullAnswer": "正文", "fullThinking": "思考"},
            question="低温试验有哪些试验方法",
            plan={"route_id": "R-TEST_METHOD-by_project"},
            rec={"params": {"project_keywords": ["低温试验"]}},
            neo_rows=[],
            full_answer="**（一）试验条件**\n\n…",
            project_method_generate_mode=True,
        )
        self.assertTrue(action_payload["methodCreateAction"]["enabled"])
        self.assertEqual(action_payload["fullAnswer"], "正文")


if __name__ == "__main__":
    unittest.main()
