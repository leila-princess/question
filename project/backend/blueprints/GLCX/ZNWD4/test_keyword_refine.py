"""ZNWD4 keyword_refine：子串展开 + LCS 评分 + refined probe。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from .keyword_refine import (
    expand_span_variants,
    probe_keyword_refined,
    score_entity_match,
)


class TestExpandSpanVariants(unittest.TestCase):
    def test_composite_span_yields_project_token(self):
        q = "无人机A型低温环境测试项目是在什么环境下做"
        span = "无人机A型低温环境测试项目"
        variants = expand_span_variants(span, q)
        self.assertIn(span, variants)
        self.assertIn("低温环境测试项目", variants)

    def test_short_span_unchanged(self):
        span = "低温环境测试项目"
        variants = expand_span_variants(span, span)
        self.assertEqual(variants[0], span)


class TestScoreEntityMatch(unittest.TestCase):
    def test_entity_in_span_scores_higher_than_noise(self):
        span = "无人机A型低温环境测试项目"
        good = score_entity_match(span, "低温环境测试项目", span)
        noise = score_entity_match(span, "无人机", span)
        self.assertGreater(good, noise)

    def test_exact_match_is_one(self):
        self.assertEqual(score_entity_match("低温环境", "低温环境", ""), 1.0)


class TestProbeKeywordRefined(unittest.TestCase):
    def test_picks_canonical_from_variant_probes(self):
        span = "无人机A型低温环境测试项目"
        question = "无人机A型低温环境测试项目是在什么环境下做"

        def fake_probe(driver, keyword, **kwargs):
            if keyword == "低温环境测试项目":
                return {
                    "keyword": keyword,
                    "hits": [
                        {
                            "node_type": "TestProject",
                            "id": "tp-1",
                            "name": "低温环境测试项目",
                            "score": 0.9,
                        }
                    ],
                    "best": {
                        "node_type": "TestProject",
                        "id": "tp-1",
                        "name": "低温环境测试项目",
                        "score": 0.9,
                    },
                    "match_count": 1,
                }
            return {"keyword": keyword, "hits": [], "best": None, "match_count": 0}

        from unittest.mock import patch

        with patch("blueprints.GLCX.ZNWD4.keyword_probe.probe_keyword", side_effect=fake_probe):
            result = probe_keyword_refined(MagicMock(), span, question=question)

        best = result.get("best") or {}
        self.assertEqual(best.get("name"), "低温环境测试项目")
        self.assertEqual(best.get("node_type"), "TestProject")


if __name__ == "__main__":
    unittest.main()
