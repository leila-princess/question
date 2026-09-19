"""多项目反查装备：路由锁、槽位展开、纠正重抽槽位。"""

import unittest
from unittest.mock import MagicMock, patch

from .answer_correction import apply_answer_correction, reslot_for_route
from .business_catalog import BIZ_REL_REVERSE_ONE_HOP
from .business_expand import expand_business
from .playbook_resolver import _pb_rel_eq_tp_rev
from .question_route_lock import extract_project_keywords, resolve_route_lock
from .route_validator import validate_and_align_route


SAMPLE_Q = "低温环境测试项目和振动环境测试项目都有哪些设备要做"


class TestEquipmentByProject(unittest.TestCase):
    def test_extract_project_keywords_two_named(self):
        kws = extract_project_keywords(SAMPLE_Q)
        self.assertEqual(
            kws,
            ["低温环境测试项目", "振动环境测试项目"],
        )

    def test_extract_filters_quantifier_stub(self):
        self.assertEqual(extract_project_keywords("两个项目都有哪些设备要做"), [])

    def test_route_lock(self):
        lock = resolve_route_lock(SAMPLE_Q)
        self.assertIsNotNone(lock)
        self.assertEqual(lock.route_id, "R-EQUIPMENT-by_project")
        self.assertEqual(
            lock.keywords,
            ["低温环境测试项目", "振动环境测试项目"],
        )

    def test_validate_align_wrong_route(self):
        rid, conf = validate_and_align_route(
            "R-TEST_PROJECT-by_equipment",
            {},
            question=SAMPLE_Q,
        )
        self.assertEqual(rid, "R-EQUIPMENT-by_project")
        self.assertEqual(conf, "high")

    def test_pb_rel_eq_tp_rev_multi_fuzzy_any(self):
        slots = {
            "project_keywords": ["低温环境测试项目", "振动环境测试项目"],
        }
        bid, params = _pb_rel_eq_tp_rev(slots, None)
        self.assertEqual(bid, BIZ_REL_REVERSE_ONE_HOP)
        self.assertEqual(params.get("match_mode"), "fuzzy_any")
        self.assertEqual(
            params.get("value_list"),
            ["低温环境测试项目", "振动环境测试项目"],
        )
        self.assertEqual(params.get("focus"), "")

    def test_expand_business_reverse_multi(self):
        steps = expand_business(
            BIZ_REL_REVERSE_ONE_HOP,
            {
                "from_type": "Equipment",
                "to_type": "TestProject",
                "rel_type": "CON",
                "where_side": "to",
                "match_mode": "fuzzy_any",
                "value_list": ["低温环境测试项目", "振动环境测试项目"],
                "result_type": "Equipment",
                "anchor_type": "TestProject",
            },
        )
        self.assertEqual(len(steps), 1)
        p = steps[0]["params"]
        self.assertEqual(p.get("match_mode"), "fuzzy_any")
        self.assertEqual(len(p.get("value_list") or []), 2)

    def test_reslot_on_route_change(self):
        out = reslot_for_route(
            SAMPLE_Q,
            "R-EQUIPMENT-by_project",
            existing_slots={
                "equipment_keywords": ["无人机A型"],
                "focus": "无人机A型",
            },
            predicted_route_id="R-TEST_PROJECT-by_equipment",
            user_edited_slots=False,
            driver=None,
        )
        self.assertEqual(
            out.get("project_keywords"),
            ["低温环境测试项目", "振动环境测试项目"],
        )
        self.assertNotIn("equipment_keywords", out)

    @patch("blueprints.GLCX.ZNWD4.route_validator.assemble_legacy_plan")
    @patch("blueprints.GLCX.ZNWD4.answer_correction.reslot_for_route")
    def test_apply_correction_strips_wrong_slots(self, mock_reslot, mock_assemble):
        mock_reslot.return_value = {
            "project_keywords": ["低温环境测试项目", "振动环境测试项目"],
            "focus": "低温环境测试项目",
        }
        mock_assemble.return_value = {"route_id": "R-EQUIPMENT-by_project", "params_draft": mock_reslot.return_value}
        payload = {
            "turnId": "t1",
            "originalQuestion": SAMPLE_Q,
            "feedbackKind": "wrong_route",
            "correctedRouteId": "R-EQUIPMENT-by_project",
            "predictedRouteId": "R-TEST_PROJECT-by_equipment",
            "predictedSlots": {"equipment_keywords": ["无人机A型"], "focus": "无人机A型"},
            "correctedSlots": {"equipment_keywords": ["无人机A型"], "focus": "无人机A型"},
            "rerun": True,
        }
        apply_answer_correction(payload, question=SAMPLE_Q, driver=MagicMock())
        mock_reslot.assert_called_once()
        kw = mock_reslot.call_args.kwargs
        self.assertEqual(kw.get("predicted_route_id"), "R-TEST_PROJECT-by_equipment")
        self.assertFalse(kw.get("user_edited_slots"))


if __name__ == "__main__":
    unittest.main()
