"""Playbook 展开单测（无需 Neo4j）。"""

from __future__ import annotations

import unittest

from .playbook_resolver import (
    expand_playbook,
    expand_playbook_meta_steps,
    normalize_slots,
    resolve_environment_focus_id,
)
from .user_biz_catalog import COMP_EQUIP_PROJECT_ENV_SETTINGS, COMP_INDICATOR_THEN_METHOD, COMP_PROJECT_THEN_METHOD, UBIZ_TEST_PROJECT


class TestPlaybookResolver(unittest.TestCase):
    def test_normalize_slots_multi_kw(self):
        slots = normalize_slots(
            {"equipment_keywords": ["坦克", "装甲车"], "focus": ""},
            keyword="坦克",
        )
        self.assertEqual(slots["equipment_value_list"], ["坦克", "装甲车"])

    def test_ubiz_test_project_by_equipment(self):
        plan = {
            "user_biz": UBIZ_TEST_PROJECT,
            "scenario": "by_equipment",
            "match_confidence": "high",
            "params_draft": {"equipment_keywords": ["坦克"], "focus": "坦克"},
            "keyword": "坦克",
        }
        pb = expand_playbook(plan, question="坦克有哪些试验项目")
        self.assertIsNotNone(pb)
        assert pb is not None
        self.assertTrue(pb.get("playbook"))
        steps = expand_playbook_meta_steps(pb, retrieval_question="坦克有哪些试验项目")
        self.assertGreaterEqual(len(steps), 1)
        self.assertEqual(steps[0].get("op_code"), "neo_relation_query")

    def test_composite_indicator_then_method(self):
        plan = {
            "user_biz": COMP_INDICATOR_THEN_METHOD,
            "scenario": "default",
            "match_confidence": "high",
            "params_draft": {"equipment_keywords": ["多旋翼无人机"]},
        }
        pb = expand_playbook(plan, question="多旋翼无人机有哪些考核指标和试验方法")
        self.assertIsNotNone(pb)
        assert pb is not None
        steps = expand_playbook_meta_steps(pb)
        self.assertGreaterEqual(len(steps), 2)
        s1 = steps[0]
        self.assertEqual(s1.get("op_code"), "neo_equipment_indicators")
        ref = steps[-1].get("params", {}).get("from_values_ref")
        self.assertIsInstance(ref, dict)
        self.assertEqual(ref.get("target"), "m")

    def test_composite_project_then_method(self):
        plan = {
            "user_biz": COMP_PROJECT_THEN_METHOD,
            "scenario": "default",
            "match_confidence": "high",
            "params_draft": {"equipment_keywords": ["坦克"]},
        }
        pb = expand_playbook(plan, question="坦克有哪些试验项目和方法")
        self.assertIsNotNone(pb)
        assert pb is not None
        steps = expand_playbook_meta_steps(pb)
        self.assertGreaterEqual(len(steps), 2)
        ref = steps[-1].get("params", {}).get("from_values_ref")
        self.assertIsInstance(ref, dict)

    def test_by_equip_and_env_has_env_filter_step(self):
        plan = {
            "user_biz": UBIZ_TEST_PROJECT,
            "scenario": "by_equip_and_env",
            "match_confidence": "high",
            "params_draft": {
                "equipment_keywords": ["坦克96B"],
                "environment_keywords": ["复杂电磁环境"],
                "environment_focus_id": "HJ_0001",
            },
        }
        pb = expand_playbook(plan, question="坦克96B在复杂电磁环境下的试验项目")
        self.assertIsNotNone(pb)
        assert pb is not None
        steps = expand_playbook_meta_steps(pb)
        self.assertGreaterEqual(len(steps), 2)
        filt = [s for s in steps if s.get("op_code") == "neo_property_filter"]
        self.assertEqual(len(filt), 1)
        self.assertEqual(filt[0]["params"].get("field"), "EnvironmentId")
        self.assertEqual(filt[0]["params"].get("value"), "HJ_0001")

    def test_resolve_environment_from_probe_hits(self):
        plan = {
            "params_draft": {"environment_keywords": ["复杂电磁环境"]},
            "probe_snapshot": {
                "best": {"node_type": "Equipment", "id": "ZB_0005", "name": "坦克96B"},
                "hits": [
                    {"node_type": "Equipment", "id": "ZB_0005", "name": "坦克96B"},
                    {"node_type": "Environment", "id": "HJ_0001", "name": "复杂电磁环境"},
                ],
            },
        }
        slots = normalize_slots(plan["params_draft"])
        eid = resolve_environment_focus_id(slots, plan, driver=None)
        self.assertEqual(eid, "HJ_0001")

    def test_composite_by_environment_playbook_steps(self):
        plan = {
            "user_biz": COMP_EQUIP_PROJECT_ENV_SETTINGS,
            "scenario": "by_environment",
            "match_confidence": "high",
            "params_draft": {
                "equipment_keywords": ["无人机A型"],
                "environment_keywords": ["复杂地理环境"],
                "environment_focus_id": "HJ_0001",
                "scenario": "by_environment",
            },
        }
        pb = expand_playbook(plan, question="无人机A型在复杂地理环境推荐的环境设置是什么")
        self.assertIsNotNone(pb)
        assert pb is not None
        steps = expand_playbook_meta_steps(pb)
        name_filters = [
            s
            for s in steps
            if s.get("op_code") == "neo_property_filter" and s.get("params", {}).get("field") == "Name"
        ]
        self.assertEqual(len(name_filters), 0, "by_environment 不应按项目名过滤")
        env_id_filters = [
            s
            for s in steps
            if s.get("op_code") == "neo_property_filter"
            and s.get("params", {}).get("field") == "EnvironmentId"
        ]
        self.assertEqual(len(env_id_filters), 1)
        node_queries = [s for s in steps if s.get("op_code") == "neo_node_query"]
        self.assertGreaterEqual(len(node_queries), 1)

    def test_composite_by_project_playbook_steps(self):
        plan = {
            "user_biz": COMP_EQUIP_PROJECT_ENV_SETTINGS,
            "scenario": "by_project",
            "match_confidence": "high",
            "params_draft": {
                "equipment_keywords": ["无人机A型"],
                "project_keywords": ["低温启动测试"],
                "scenario": "by_project",
            },
        }
        pb = expand_playbook(plan, question="无人机A型做低温启动测试试验时的环境条件是什么")
        self.assertIsNotNone(pb)
        assert pb is not None
        steps = expand_playbook_meta_steps(pb)
        proj_filters = [
            s
            for s in steps
            if s.get("op_code") == "neo_property_filter"
            and s.get("params", {}).get("field") == "Name"
            and s.get("params", {}).get("value") == "低温启动测试"
        ]
        self.assertEqual(len(proj_filters), 1)

    def test_meta_steps_ref_remap_s1b(self):
        rec = {
            "business_steps": [],
            "meta_step_groups": [
                [{"step_id": "s1", "op_code": "neo_relation_query", "params": {}}],
                [
                    {
                        "step_id": "s1b",
                        "op_code": "neo_property_filter",
                        "params": {"target": "m", "field": "Name", "value": "低温启动测试"},
                    }
                ],
                [
                    {
                        "step_id": "s2",
                        "op_code": "neo_relation_query",
                        "params": {
                            "from_values_ref": {"step_id": "s1b", "target": "m", "field": "Id"},
                        },
                    }
                ],
            ],
        }
        steps = expand_playbook_meta_steps(rec)
        self.assertEqual(len(steps), 3)
        ref = steps[2].get("params", {}).get("from_values_ref")
        self.assertEqual(ref.get("step_id"), "s2")

    def test_composite_env_settings_by_project_no_equip_name_filter(self):
        plan = {
            "user_biz": COMP_EQUIP_PROJECT_ENV_SETTINGS,
            "scenario": "by_project",
            "match_confidence": "high",
            "params_draft": {
                "equipment_keywords": ["无人机A型"],
                "project_keywords": ["无人机A型"],
            },
        }
        pb = expand_playbook(plan, question="无人机A型在复杂地理环境推荐的环境设置是什么")
        self.assertIsNotNone(pb)
        assert pb is not None
        steps = expand_playbook_meta_steps(pb)
        bad = [
            s
            for s in steps
            if s.get("op_code") == "neo_property_filter"
            and s.get("params", {}).get("value") == "无人机A型"
        ]
        self.assertEqual(len(bad), 0)


    def test_multi_equipment_expand_uses_fuzzy_any(self):
        from .business_expand import expand_business
        from .business_catalog import BIZ_REL_ONE_HOP

        steps = expand_business(
            BIZ_REL_ONE_HOP,
            {
                "from_type": "Equipment",
                "to_type": "TestProject",
                "rel_type": "CON",
                "where_side": "from",
                "match_mode": "fuzzy_any",
                "value_list": ["坦克96B", "汽车1"],
            },
        )
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["params"].get("match_mode"), "fuzzy_any")
        self.assertEqual(steps[0]["params"].get("value_list"), ["坦克96B", "汽车1"])

    def test_merge_playbook_multi_equip_env_params(self):
        from .playbook_params import merge_playbook_answer_params

        p = merge_playbook_answer_params(
            {"from_type": "Equipment", "to_type": "TestProject"},
            {
                "equipment_keywords": ["坦克96B", "汽车1"],
                "environment_keywords": ["复杂电磁环境"],
                "focus": "复杂电磁环境",
            },
            user_biz="UBIZ_TEST_PROJECT",
            scenario="by_equip_and_env",
        )
        self.assertEqual(p.get("value_list"), ["坦克96B", "汽车1"])
        self.assertEqual(p.get("focus"), "")
        self.assertEqual(p.get("environment_label"), "复杂电磁环境")


if __name__ == "__main__":
    unittest.main()
