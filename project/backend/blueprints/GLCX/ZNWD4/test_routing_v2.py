"""ZNWD4 问句意图锁 + 路由校验回归测试。"""

from __future__ import annotations

import unittest

from .playbook_resolver import expand_playbook, playbook_guardrail_question
from .question_route_lock import (
    apply_route_lock_to_plan,
    inject_locked_route,
    resolve_route_lock,
)
from .route_plan_v2 import merge_slots_from_keywords, normalize_to_legacy_plan, parse_step1_json, parse_step2_json
from .route_validator import assemble_legacy_plan, sanitize_params_draft, validate_and_align_route
from .routing_catalog import (
    PURPOSE_CHAT,
    filter_routes_by_purpose,
    get_route,
    guess_purpose_from_question,
    resolve_route,
)
from .user_biz_catalog import UBIZ_EQUIPMENT, UBIZ_EQUIPMENT_TYPE, UBIZ_ENVIRONMENT, UBIZ_TEST_METHOD, UBIZ_TEST_PROJECT


class TestRouteLock(unittest.TestCase):
    def test_under_type_lock(self):
        lock = resolve_route_lock("无人机有哪些具体装备")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-EQUIPMENT-under_type")
        self.assertEqual(lock.purpose, "query_equipment_under_type")
        self.assertEqual(lock.slot_key, "equipment_type_keywords")
        self.assertEqual(lock.focus, "")

    def test_level2_lock(self):
        lock = resolve_route_lock("地面装备都有哪些二级装备")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-EQUIPMENT_TYPE-level2_under_level1")
        self.assertEqual(lock.focus, "")

    def test_level2_equipment_type_phrasing(self):
        lock = resolve_route_lock("地面装备有什么二级装备类型")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-EQUIPMENT_TYPE-level2_under_level1")
        self.assertEqual(lock.focus, "")
        self.assertNotEqual(lock.route_id, "R-EQUIPMENT_TYPE-belongs_to")

    def test_belongs_to_not_level2(self):
        lock = resolve_route_lock("无人机属于什么装备类型")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-EQUIPMENT_TYPE-belongs_to")

    def test_assemble_level2_plan(self):
        plan = assemble_legacy_plan(
            question="地面装备有什么二级装备类型",
            step1={"purpose": "query_equipment_belongs_to_type", "keywords": ["地面装备有什么二级"]},
            step2={
                "route_id": "R-EQUIPMENT_TYPE-belongs_to",
                "params_draft": {"equipment_keywords": ["地面装备有什么二级"]},
                "focus": "地面装备有什么二级",
            },
            probe_snapshot={
                "best": {
                    "node_type": "EquipmentType",
                    "name": "地面装备",
                    "type_value": "地面装备",
                }
            },
            driver=None,
        )
        self.assertEqual(plan.get("route_id"), "R-EQUIPMENT_TYPE-level2_under_level1")
        draft = plan.get("params_draft") or {}
        self.assertEqual(draft.get("equipment_type_keywords"), ["地面装备"])
        self.assertEqual(draft.get("focus"), "地面装备")

    def test_belongs_to_lock(self):
        lock = resolve_route_lock("无人机属于什么装备类型")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-EQUIPMENT_TYPE-belongs_to")

    def test_test_project_lock(self):
        lock = resolve_route_lock("无人机有哪些试验项目")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-TEST_PROJECT-by_equipment")

    def test_test_project_lock_explicit(self):
        lock = resolve_route_lock("无人机有哪些试验项目")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-TEST_PROJECT-by_equipment")
        self.assertNotEqual(lock.route_id, "R-EQUIPMENT-under_type")

    def test_project_methods_lock_multi(self):
        q = "低温环境测试项目和振动环境测试项目分别有什么试验方法"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-TEST_METHOD-by_project")
        self.assertEqual(lock.purpose, "query_project_methods")
        self.assertEqual(lock.slot_key, "project_keywords")
        self.assertEqual(lock.keywords, [])

    def test_project_methods_lock_single(self):
        lock = resolve_route_lock("飞行振动测试有什么试验方法")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-TEST_METHOD-by_project")
        self.assertEqual(lock.keywords, [])

    def test_project_standards_not_condition_param(self):
        q = "低温环境测试项目有哪些相关标准？有哪些试验方法"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-STANDARD-for_projects")
        self.assertEqual(lock.slot_key, "project_keywords")
        self.assertNotEqual(lock.route_id, "R-STANDARD-condition_param")

    def test_project_environment_not_properties(self):
        q = "无人机A型低温环境测试项目是在什么环境下做"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-ENVIRONMENT-for_project")
        self.assertEqual(lock.slot_key, "project_keywords")
        self.assertNotEqual(lock.route_id, "R-ENVIRONMENT-properties")

    def test_enum_equipment_lock(self):
        lock = resolve_route_lock("系统里登记的全部装备有哪些")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-EQUIPMENT-list")
        self.assertEqual(lock.extra.get("node_type"), "Equipment")

    def test_meta_info_lock(self):
        lock = resolve_route_lock("图数据库有多少数据")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-META-stats")

    def test_equipment_indicators_lock(self):
        lock = resolve_route_lock("汽车2要考核哪些指标")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-INDICATOR-by_equipment")
        self.assertEqual(lock.focus, "")

    def test_environment_properties_lock(self):
        lock = resolve_route_lock("介绍一下水文试验环境")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-ENVIRONMENT-properties")

    def test_environment_test_projects_lock(self):
        lock = resolve_route_lock("低温环境有哪些试验项目")
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-TEST_PROJECT-by_environment")

    def test_equip_and_env_test_projects_lock(self):
        cases = (
            ("汽车2在test环境下有哪些试验项目", "汽车2", "test"),
            ("汽车2在test条件下需要做哪些试验", "汽车2", "test"),
            ("查询汽车2在test环境下的试验项目清单", "汽车2", "test"),
            ("通信设备A在复杂电磁环境环境下有哪些试验项目", "通信设备A", "复杂电磁环境"),
        )
        for q, equip, env in cases:
            lock = resolve_route_lock(q)
            self.assertIsNotNone(lock, q)
            assert lock is not None
            self.assertEqual(lock.route_id, "R-TEST_PROJECT-by_equip_and_env", q)
            self.assertEqual(lock.extra.get("scenario"), "by_equip_and_env", q)
            self.assertEqual(lock.extra.get("equipment_keywords"), [equip], q)
            self.assertEqual(lock.extra.get("environment_keywords"), [env], q)

    def test_equip_and_env_not_enum_or_by_environment(self):
        q = "查询汽车2在test环境下的试验项目清单"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        assert lock is not None
        self.assertNotEqual(lock.route_id, "R-TEST_PROJECT-enum")
        self.assertNotEqual(lock.route_id, "R-TEST_PROJECT-by_environment")

    def test_assemble_project_methods_plan(self):
        q = "低温环境测试项目和振动环境测试项目分别有什么试验方法"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        plan = assemble_legacy_plan(
            question=q,
            step1={"purpose": "query_project_methods", "keywords": ["低温环境测试项目"]},
            step2={
                "route_id": "R-TEST_METHOD-by_project",
                "params_draft": {
                    "project_keywords": ["低温环境测试项目", "振动环境测试项目"],
                    "focus": "低温环境测试项目",
                },
                "focus": "低温环境测试项目",
            },
            probe_snapshot={},
            driver=None,
        )
        self.assertEqual(plan.get("route_id"), "R-TEST_METHOD-by_project")
        draft = plan.get("params_draft") or {}
        self.assertEqual(
            draft.get("project_keywords"),
            ["低温环境测试项目", "振动环境测试项目"],
        )

    def test_project_methods_playbook_multi_steps(self):
        q = "低温环境测试项目和振动环境测试项目分别有什么试验方法"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        plan = apply_route_lock_to_plan(
            {
                "user_biz": UBIZ_TEST_METHOD,
                "scenario": "by_project",
                "match_confidence": "high",
                "keyword": "低温环境测试项目",
                "params_draft": {
                    "project_keywords": ["低温环境测试项目", "振动环境测试项目"],
                    "focus": "低温环境测试项目",
                },
            },
            lock,
        )
        pb = expand_playbook(plan, question=q)
        self.assertIsNotNone(pb)
        steps = pb.get("business_steps") or []
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0][1].get("project_name"), "低温环境测试项目")
        self.assertEqual(steps[1][1].get("project_name"), "振动环境测试项目")

    def test_sanitize_preserves_llm_multi_project(self):
        q = "低温环境测试项目和振动环境测试项目分别有什么试验方法"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        entry = get_route("R-TEST_METHOD-by_project")
        params = sanitize_params_draft(
            question=q,
            step1={"keyword": "低温环境测试项目", "keywords": ["低温环境测试项目"]},
            step2={
                "focus": "低温环境测试项目",
                "params_draft": {
                    "project_keywords": ["低温环境测试项目", "振动环境测试项目"],
                },
            },
            params={
                "project_keywords": ["低温环境测试项目", "振动环境测试项目"],
                "focus": "低温环境测试项目",
            },
            entry=entry,
            route_lock=lock,
        )
        self.assertEqual(
            params.get("project_keywords"),
            ["低温环境测试项目", "振动环境测试项目"],
        )
        self.assertNotIn("environment_keywords", params)

    def test_validate_align_project_methods(self):
        route_id, conf = validate_and_align_route(
            "R-ENVIRONMENT-for_project",
            {},
            question="低温环境测试项目和振动环境测试项目分别有什么试验方法",
        )
        self.assertEqual(route_id, "R-TEST_METHOD-by_project")
        self.assertEqual(conf, "high")

    def test_guess_purpose_under_type(self):
        self.assertEqual(guess_purpose_from_question("无人机有哪些具体装备"), "query_equipment_under_type")

    def test_guess_purpose_level2(self):
        self.assertEqual(guess_purpose_from_question("地面装备都有哪些二级装备"), "query_level2_equipment_types")

    def test_guess_purpose_project_methods(self):
        q = "低温环境测试项目和振动环境测试项目分别有什么试验方法"
        self.assertEqual(guess_purpose_from_question(q), "query_project_methods")

    def test_inject_locked_route(self):
        lock = resolve_route_lock("无人机有哪些具体装备")
        self.assertIsNotNone(lock)
        candidates = filter_routes_by_purpose("query_equipment_test_projects")
        injected = inject_locked_route(candidates, lock)
        self.assertEqual(injected[0].route_id, "R-EQUIPMENT-under_type")

    def test_apply_route_lock_to_plan(self):
        lock = resolve_route_lock("无人机有哪些具体装备")
        self.assertIsNotNone(lock)
        plan = apply_route_lock_to_plan(
            {
                "route_id": "R-TEST_PROJECT-by_equipment",
                "user_biz": UBIZ_TEST_PROJECT,
                "scenario": "by_equipment",
                "params_draft": {"equipment_keywords": ["无人机"]},
            },
            lock,
        )
        self.assertEqual(plan["route_id"], "R-EQUIPMENT-under_type")
        self.assertEqual(plan["user_biz"], UBIZ_EQUIPMENT)
        self.assertEqual(plan["scenario"], "under_type")
        self.assertNotIn("equipment_type_keywords", plan["params_draft"])


class TestRouteValidatorLock(unittest.TestCase):
    def test_validate_align_by_question(self):
        route_id, conf = validate_and_align_route(
            "R-TEST_PROJECT-by_equipment",
            {"best": {"node_type": "Equipment", "name": "无人机A型", "id": "ZB_0001"}},
            question="无人机有哪些具体装备",
        )
        self.assertEqual(route_id, "R-EQUIPMENT-under_type")
        self.assertEqual(conf, "high")

    def test_composite_env_settings_route_lock(self):
        q = "无人机A型在复杂地理环境推荐的环境设置是什么"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-C-EQUIP_PROJECT_ENV_SETTINGS-default")
        self.assertEqual(lock.extra.get("scenario"), "by_environment")
        self.assertEqual(lock.extra.get("environment_keywords"), ["复杂地理环境"])

    def test_composite_env_by_project_route_lock(self):
        q = "无人机A型做低温启动测试试验时的环境条件是什么"
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.extra.get("scenario"), "by_project")
        self.assertEqual(lock.extra.get("project_keywords"), ["低温启动测试"])

    def test_merge_slots_no_project_pollution(self):
        from .route_plan_v2 import merge_slots_from_keywords

        params = merge_slots_from_keywords(
            step1={"keywords": ["无人机A型"], "keyword": "无人机A型"},
            step2={
                "route_id": "R-C-EQUIP_PROJECT_ENV_SETTINGS-default",
                "params_draft": {
                    "equipment_keywords": ["无人机A型"],
                    "environment_keywords": ["复杂地理环境"],
                    "scenario": "by_environment",
                },
            },
            route_id="R-C-EQUIP_PROJECT_ENV_SETTINGS-default",
        )
        self.assertEqual(params.get("equipment_keywords"), ["无人机A型"])
        self.assertNotIn("project_keywords", params)

    def test_validate_align_composite_not_realigned_on_environment_probe(self):
        route_id, conf = validate_and_align_route(
            "R-C-EQUIP_PROJECT_ENV_SETTINGS-default",
            {
                "best": {
                    "node_type": "Environment",
                    "name": "复杂地理环境",
                    "id": "HJ_0001",
                }
            },
            purpose="composite_equip_project_env_settings",
            question="无人机A型在复杂地理环境推荐的环境设置是什么",
        )
        self.assertEqual(route_id, "R-C-EQUIP_PROJECT_ENV_SETTINGS-default")
        self.assertEqual(conf, "high")

    def test_sanitize_drops_hallucinated_equipment_types(self):
        entry = get_route("R-EQUIPMENT_TYPE-level2_under_level1")
        lock = resolve_route_lock("地面装备都有哪些二级装备")
        params = sanitize_params_draft(
            question="地面装备都有哪些二级装备",
            step1={"keyword": "地面装备", "keywords": ["地面装备"]},
            step2={"focus": "地面装备", "params_draft": {"equipment_type_keywords": ["汽车", "坦克"]}},
            params={"equipment_type_keywords": ["汽车", "坦克"], "focus": "地面装备"},
            entry=entry,
            route_lock=lock,
        )
        self.assertEqual(params.get("equipment_type_keywords"), ["地面装备"])

    def test_assemble_under_type_plan(self):
        lock = resolve_route_lock("无人机有哪些具体装备")
        self.assertIsNotNone(lock)
        plan = assemble_legacy_plan(
            question="无人机有哪些具体装备",
            step1={"purpose": "query_equipment_test_projects", "keywords": ["无人机"], "keyword": "无人机"},
            step2={
                "route_id": "R-TEST_PROJECT-by_equipment",
                "params_draft": {"equipment_keywords": ["无人机"]},
                "focus": "无人机",
            },
            probe_snapshot={
                "best": {"node_type": "Equipment", "name": "无人机A型", "id": "ZB_0001", "score": 0.91}
            },
            driver=None,
        )
        self.assertEqual(plan.get("route_id"), "R-EQUIPMENT-under_type")
        self.assertEqual(plan.get("user_biz"), UBIZ_EQUIPMENT)
        self.assertEqual(plan.get("scenario"), "under_type")
        draft = plan.get("params_draft") or {}
        self.assertEqual(draft.get("equipment_type_keywords"), ["无人机"])
        self.assertEqual(draft.get("focus"), "无人机")

    def test_assemble_chat_plan(self):
        plan = assemble_legacy_plan(
            question="你好",
            step1={"purpose": PURPOSE_CHAT, "keywords": []},
            step2={"route_id": "R-CHAT-direct", "params_draft": {}, "keywords": []},
            probe_snapshot={},
            driver=None,
        )
        self.assertEqual(plan.get("answer_strategy"), "direct_llm")


class TestPlaybookGuardrail(unittest.TestCase):
    def test_rejects_test_project_for_under_type_question(self):
        ok = playbook_guardrail_question(UBIZ_TEST_PROJECT, "by_equipment", "无人机有哪些具体装备")
        self.assertFalse(ok)

    def test_accepts_under_type_for_under_type_question(self):
        ok = playbook_guardrail_question(UBIZ_EQUIPMENT, "under_type", "无人机有哪些具体装备")
        self.assertTrue(ok)

    def test_et_type_children_uses_focus_not_hallucination(self):
        plan = {
            "user_biz": UBIZ_EQUIPMENT_TYPE,
            "scenario": "level2_under_level1",
            "match_confidence": "high",
            "keyword": "地面装备",
            "params_draft": {
                "equipment_type_keywords": ["汽车", "坦克"],
                "focus": "地面装备",
            },
        }
        pb = expand_playbook(plan, question="地面装备都有哪些二级装备")
        self.assertIsNotNone(pb)
        steps = pb.get("business_steps") or []
        self.assertTrue(steps)
        _bid, params = steps[0]
        self.assertEqual(params.get("value"), "地面装备")

    def test_under_type_playbook_rel_direction(self):
        plan = apply_route_lock_to_plan(
            {
                "user_biz": UBIZ_EQUIPMENT,
                "scenario": "under_type",
                "match_confidence": "high",
                "keyword": "无人机",
                "params_draft": {"equipment_type_keywords": ["无人机"], "focus": "无人机"},
            },
            resolve_route_lock("无人机有哪些具体装备"),
        )
        pb = expand_playbook(plan, question="无人机有哪些具体装备")
        self.assertIsNotNone(pb)
        bid, params = (pb.get("business_steps") or [])[0]
        self.assertEqual(params.get("from_type"), "EquipmentType")
        self.assertEqual(params.get("to_type"), "Equipment")
        self.assertEqual(params.get("where_side"), "from")
        self.assertEqual(params.get("focus"), "无人机")

    def test_sanitize_under_type_uses_equipment_type_name(self):
        lock = resolve_route_lock("无人机有哪些具体装备")
        entry = get_route("R-EQUIPMENT-under_type")
        self.assertIsNotNone(entry)
        params = sanitize_params_draft(
            question="无人机有哪些具体装备",
            step1={"keyword": "无人机", "keywords": ["无人机"]},
            step2={
                "focus": "无人机",
                "params_draft": {"equipment_type_keywords": ["无人机"]},
            },
            params={"equipment_type_keywords": ["无人机"], "focus": "无人机"},
            entry=entry,
            probe_snapshot={
                "best": {
                    "node_type": "EquipmentType",
                    "name": "无人机",
                    "type_value": "陆地设备",
                    "id": "ET-001",
                }
            },
            route_lock=lock,
        )
        self.assertEqual(params.get("equipment_type_keywords"), ["无人机"])
        self.assertEqual(params.get("focus_id"), "ET-001")

    def test_sanitize_user_correction_preserves_slots(self):
        lock = resolve_route_lock("无人机有哪些具体装备")
        entry = get_route("R-EQUIPMENT-under_type")
        params = sanitize_params_draft(
            question="无人机有哪些具体装备",
            step1={"keyword": "无人机", "keywords": ["无人机"]},
            step2={
                "focus": "无人机",
                "params_draft": {"equipment_type_keywords": ["无人机"]},
            },
            params={"equipment_type_keywords": ["无人机"], "focus": "无人机"},
            entry=entry,
            probe_snapshot={
                "best": {
                    "node_type": "EquipmentType",
                    "name": "无人机",
                    "type_value": "陆地设备",
                    "id": "ET-001",
                }
            },
            route_lock=lock,
            slot_source="user_correction",
        )
        self.assertEqual(params.get("equipment_type_keywords"), ["无人机"])

    def test_under_type_playbook_passes_focus_id(self):
        plan = apply_route_lock_to_plan(
            {
                "user_biz": UBIZ_EQUIPMENT,
                "scenario": "under_type",
                "match_confidence": "high",
                "keyword": "无人机",
                "params_draft": {
                    "equipment_type_keywords": ["无人机"],
                    "focus": "无人机",
                    "focus_id": "ET-001",
                },
                "probe_snapshot": {
                    "best": {
                        "node_type": "EquipmentType",
                        "name": "无人机",
                        "id": "ET-001",
                    }
                },
            },
            resolve_route_lock("无人机有哪些具体装备"),
        )
        pb = expand_playbook(plan, question="无人机有哪些具体装备")
        self.assertIsNotNone(pb)
        bid, params = (pb.get("business_steps") or [])[0]
        self.assertEqual(params.get("focus"), "无人机")
        self.assertEqual(params.get("focus_id"), "ET-001")


class TestRoutingCatalog(unittest.TestCase):
    def test_resolve_playbook_route(self):
        resolved = resolve_route("R-EQUIPMENT_TYPE-belongs_to")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["user_biz"], UBIZ_EQUIPMENT_TYPE)
        self.assertEqual(resolved["scenario"], "belongs_to")

    def test_parse_step1_json(self):
        raw = '{"purpose":"query_equipment_under_type","keywords":["无人机"]}'
        s1 = parse_step1_json(raw)
        self.assertEqual(s1["purpose"], "query_equipment_under_type")


if __name__ == "__main__":
    unittest.main()
