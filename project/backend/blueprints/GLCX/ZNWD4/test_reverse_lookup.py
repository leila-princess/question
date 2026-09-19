"""反查路由：指标→装备、方法→项目。"""

from __future__ import annotations

import unittest

from .question_route_lock import (
    extract_indicator_keywords,
    extract_method_keywords,
    resolve_route_lock,
)
from .rel_inference import (
    looks_like_equipment_by_indicator,
    looks_like_projects_by_method,
)
from .route_validator import validate_and_align_route


class TestReverseLookup(unittest.TestCase):
    def test_equipment_by_indicator_lock(self):
        q = "工作电压指标有哪些设备要做"
        self.assertTrue(looks_like_equipment_by_indicator(q))
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        assert lock is not None
        self.assertEqual(lock.route_id, "R-EQUIPMENT-by_indicator")
        kws = extract_indicator_keywords(q)
        self.assertTrue(kws)
        self.assertIn("指标", kws[0])

    def test_projects_by_method_lock(self):
        q = "恒定湿热试验方法涉及哪些试验项目"
        self.assertTrue(looks_like_projects_by_method(q))
        lock = resolve_route_lock(q)
        self.assertIsNotNone(lock)
        assert lock is not None
        self.assertEqual(lock.route_id, "R-TEST_PROJECT-by_method")

    def test_validator_realign_equipment_by_indicator(self):
        q = "绝缘电阻指标关联哪些装备"
        rid, conf = validate_and_align_route(
            "R-INDICATOR-list_all",
            purpose="enum_list",
            probe_snapshot={},
            question=q,
        )
        self.assertEqual(rid, "R-EQUIPMENT-by_indicator")
        self.assertEqual(conf, "high")

    def test_validator_realign_projects_by_method(self):
        q = "哪些试验项目采用了低温试验方法"
        rid, conf = validate_and_align_route(
            "R-TEST_METHOD-by_project",
            purpose="query_project_methods",
            probe_snapshot={},
            question=q,
        )
        self.assertEqual(rid, "R-TEST_PROJECT-by_method")
        self.assertEqual(conf, "high")

    def test_extract_method_keywords_named(self):
        kws = extract_method_keywords("恒定湿热试验方法有哪些试验项目")
        self.assertTrue(kws)
        self.assertIn("恒定湿热", kws[0])


if __name__ == "__main__":
    unittest.main()
