"""ZNWD2 单元测试：本体代价模型。"""

from __future__ import annotations

import unittest

from blueprints.GLCX.ZNWD2.ontology import adjust_edge_cost, find_best_linear_path, get_schema_graph
from blueprints.GLCX.ZNWD2.types import IntentJson


class TestOntology(unittest.TestCase):
    def test_schema_graph_edges(self) -> None:
        g = get_schema_graph()
        self.assertTrue(g.has_edge("Equipment", "Indicator"))
        self.assertTrue(g.has_edge("Equipment", "TestProject"))
        self.assertFalse(g.has_edge("TestProject", "Indicator"))

    def test_indicator_direct_path_rewarded(self) -> None:
        intent: IntentJson = {
            "intent_type": "查指标",
            "environment_constraint": {"Type": "高原"},
        }
        ind_cost = adjust_edge_cost("Equipment", "Indicator", {"cost": 1.0}, intent)
        tp_cost = adjust_edge_cost("Equipment", "TestProject", {"cost": 1.0}, intent)
        self.assertEqual(ind_cost, 0.5)
        self.assertEqual(tp_cost, 5.0)

    def test_project_path_rewarded(self) -> None:
        intent: IntentJson = {"intent_type": "查项目"}
        cost = adjust_edge_cost("Equipment", "TestProject", {"cost": 1.0}, intent)
        self.assertEqual(cost, 0.5)

    def test_best_path_equipment_to_indicator(self) -> None:
        intent: IntentJson = {"intent_type": "查指标"}
        found = find_best_linear_path(intent, "Equipment", "Indicator")
        self.assertIsNotNone(found)
        path, _total = found  # type: ignore[misc]
        self.assertEqual(path, ["Equipment", "Indicator"])


if __name__ == "__main__":
    unittest.main()
