"""ZNWD1 基准测试：评分逻辑单测 + 可选少量集成冒烟。"""

from __future__ import annotations

import json
import os
import unittest

from .run_znwd1_qa_benchmark import (
    QA_FILE,
    analyze_tool_usage,
    score_answer,
    _extract_list_items,
    _needs_retrieval,
    _threshold_for,
)


class TestZnwd1AnswerScorer(unittest.TestCase):
    def test_list_recall_pass(self):
        exp = "汽车2相关的试验项目有：项目B、项目A"
        act = "汽车2的试验项目包括：项目A、项目B。"
        sr = score_answer(exp, act, "装备-试验项目")
        self.assertTrue(sr.correct)
        self.assertGreaterEqual(sr.recall, 0.9)

    def test_list_recall_fail(self):
        exp = "无人机A型相关的试验项目有：高温环境测试项目、低温环境测试项目"
        act = "未在清单库中找到相关信息。"
        sr = score_answer(exp, act, "装备-试验项目")
        self.assertFalse(sr.correct)
        self.assertEqual(sr.error_type, "no_retrieval")

    def test_chat_pass(self):
        sr = score_answer(
            "您好！我是试验知识智能问答助手",
            "您好，我是智能问答助手，可以帮您查询试验项目和试验方法。",
            "闲聊",
        )
        self.assertTrue(sr.correct)

    def test_meta_numbers(self):
        exp = "Chroma 向量库共 173 条文档片段，涵盖 13 份文档。"
        act = "向量库共有 173 个片段，13 份文档。"
        sr = score_answer(exp, act, "元信息")
        self.assertTrue(sr.correct)

    def test_extract_list_items(self):
        items = _extract_list_items("包括：项目A、项目B、高温试验等（共 3 条）")
        self.assertIn("项目A", items)
        self.assertIn("项目B", items)

    def test_tool_not_called_detection(self):
        info = analyze_tool_usage(
            category="装备-试验项目",
            question="坦克有哪些试验项目",
            telemetry={"answer_strategy": "direct_llm", "business_id": "", "neo_hits": 0, "chroma_hits": 0},
            correct=False,
        )
        self.assertTrue(info["expected_retrieval"])
        self.assertTrue(info["tool_not_called"])
        self.assertTrue(info["failure_due_to_no_tool"])

    def test_tool_called_when_retrieval_ran(self):
        info = analyze_tool_usage(
            category="装备-试验项目",
            question="坦克有哪些试验项目",
            telemetry={"retrieval_ran": True, "neo_hits": 5, "business_id": "BIZ_REL_ONE_HOP"},
            correct=True,
        )
        self.assertFalse(info["tool_not_called"])


class TestZnwd1BenchmarkSmoke(unittest.TestCase):
    """需 Neo4j + LLM 可用；默认跳过。"""

    @unittest.skipUnless(
        os.environ.get("ZNWD1_RUN_INTEGRATION") == "1",
        "set ZNWD1_RUN_INTEGRATION=1 to run",
    )
    def test_smoke_three_questions(self):
        from .run_znwd1_qa_benchmark import bootstrap_pipeline_deps, call_znwd1_pipeline
        import uuid

        deps = bootstrap_pipeline_deps()
        for q in ("你好", "有哪些试验环境", "汽车2有哪些试验项目"):
            ans, _, _, _ = call_znwd1_pipeline(
                q,
                f"smoke-{uuid.uuid4().hex[:8]}",
                client=deps["client"],
                model=deps["model"],
                driver=deps["driver"],
                chroma=deps["chroma"],
                llm_limits=deps["llm_limits"],
            )
            self.assertTrue(len(ans.strip()) > 0, q)


class TestQaPairsFile(unittest.TestCase):
    def test_qa_file_exists_and_count(self):
        if not os.path.isfile(QA_FILE):
            self.skipTest("qa_pairs_500.json not generated")
        with open(QA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("count"), 500)
        self.assertEqual(len(data.get("pairs", [])), 500)


if __name__ == "__main__":
    unittest.main()
