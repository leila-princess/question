"""ActionPlan 拼装展开单测（无需 Neo4j）。"""

from __future__ import annotations

import unittest

from blueprints.GLCX.ZNWD5.action_plan_expand import expand_action_plan
from blueprints.GLCX.ZNWD5.action_plan_schema import action_plan_from_dict


class TestActionPlanExpandComposite(unittest.TestCase):
    def test_project_then_method_two_steps(self):
        plan = action_plan_from_dict(
            {
                "question": "汽车2有哪些试验项目和方法",
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "hop_n",
                        "purpose_node": "TestProject",
                        "known_type": "Equipment",
                        "focus": "汽车2",
                    },
                    {
                        "step_id": "s2",
                        "action": "hop_n",
                        "purpose_node": "TestMethod",
                        "known_type": "TestProject",
                        "use_prev": {"step_id": "s1", "side": "to", "field": "Id"},
                    },
                ],
                "answer_mode": "assemble",
            }
        )
        steps, groups = expand_action_plan(plan, question="汽车2有哪些试验项目和方法")
        self.assertGreaterEqual(len(steps), 2)
        self.assertEqual(len(groups), 1)
        ref_steps = [s for s in steps if isinstance(s.get("params"), dict) and s["params"].get("from_values_ref")]
        self.assertGreaterEqual(len(ref_steps), 1)
        ref = ref_steps[0]["params"]["from_values_ref"]
        self.assertEqual(ref.get("step_id"), "s1")
        self.assertIn(ref.get("target"), ("m", "n"))

    def test_sub_questions_expand(self):
        plan = action_plan_from_dict(
            {
                "question": "坦克有哪些试验项目，以及高温环境的分级",
                "sub_questions": [
                    {
                        "question": "坦克有哪些试验项目",
                        "steps": [
                            {
                                "step_id": "s1",
                                "action": "hop_n",
                                "purpose_node": "TestProject",
                                "known_type": "Equipment",
                                "focus": "坦克",
                            }
                        ],
                    },
                    {
                        "question": "高温环境的分级",
                        "steps": [
                            {
                                "step_id": "s1",
                                "action": "node_prop",
                                "purpose_node": "Environment",
                                "focus": "高温",
                                "property_fields": ["Class"],
                            }
                        ],
                    },
                ],
                "steps": [],
                "answer_mode": "synthesize",
            }
        )
        steps, groups = expand_action_plan(plan)
        self.assertGreaterEqual(len(steps), 2)
        self.assertEqual(len(groups), 2)


if __name__ == "__main__":
    unittest.main()
