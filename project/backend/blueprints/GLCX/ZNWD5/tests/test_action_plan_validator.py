"""ActionPlan 校验单元测试（无需 Neo4j / LLM）。"""

from __future__ import annotations

import unittest

from blueprints.GLCX.ZNWD5.action_plan_schema import (
    ActionPlan,
    ActionStep,
    UsePrevRef,
    action_plan_from_dict,
    normalize_answer_mode,
)
from blueprints.GLCX.ZNWD5.action_plan_validator import validate_action_plan


class TestActionPlanValidator(unittest.TestCase):
    def test_valid_single_hop(self):
        plan = action_plan_from_dict(
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
                "answer_mode": "direct",
                "confidence": "high",
            }
        )
        errs, patched = validate_action_plan(
            plan,
            question="坦克有哪些试验项目",
            candidates=[{"name": "坦克", "node_type": "Equipment", "score": 0.9}],
        )
        self.assertEqual(errs, [])
        self.assertEqual(patched.steps[0].focus, "坦克")

    def test_invalid_hop_relation(self):
        plan = action_plan_from_dict(
            {
                "question": "x",
                "steps": [
                    {
                        "step_id": "s1",
                        "action": "hop_n",
                        "purpose_node": "File",
                        "known_type": "Equipment",
                        "focus": "坦克",
                    }
                ],
            }
        )
        errs, _ = validate_action_plan(plan, question="坦克", candidates=[])
        self.assertTrue(any("illegal hop" in e for e in errs))

    def test_use_prev_chain(self):
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
        errs, patched = validate_action_plan(
            plan,
            question="汽车2有哪些试验项目和方法",
            candidates=[{"name": "汽车2", "node_type": "Equipment", "score": 0.95}],
        )
        self.assertEqual(errs, [])
        self.assertIn(patched.answer_mode, ("assemble", "synthesize"))

    def test_use_prev_cycle_rejected(self):
        plan = ActionPlan(
            question="bad",
            steps=[
                ActionStep(
                    step_id="s1",
                    action="hop_n",
                    purpose_node="TestProject",
                    known_type="Equipment",
                    focus="坦克",
                    use_prev=UsePrevRef(step_id="s2", side="to", field="Id"),
                ),
                ActionStep(
                    step_id="s2",
                    action="hop_n",
                    purpose_node="TestMethod",
                    known_type="TestProject",
                    use_prev=UsePrevRef(step_id="s1", side="to", field="Id"),
                ),
            ],
        )
        errs, _ = validate_action_plan(plan, question="bad", candidates=[])
        self.assertTrue(any("earlier step" in e or "unknown step" in e for e in errs))

    def test_normalize_answer_mode_multi_step(self):
        plan = action_plan_from_dict(
            {
                "question": "q",
                "steps": [
                    {"step_id": "s1", "action": "hop_n", "purpose_node": "TestProject", "known_type": "Equipment", "focus": "a"},
                    {"step_id": "s2", "action": "hop_n", "purpose_node": "TestMethod", "known_type": "TestProject", "use_prev": {"step_id": "s1", "side": "to", "field": "Id"}},
                ],
                "answer_mode": "direct",
            }
        )
        self.assertEqual(normalize_answer_mode(plan), "synthesize")


if __name__ == "__main__":
    unittest.main()
