"""ZNWD5 单元测试：fallback store 与 probe 合并逻辑。"""

from __future__ import annotations

import os
import tempfile
import unittest

from blueprints.GLCX.ZNWD5.fallback_store import FallbackVectorStore
from blueprints.GLCX.ZNWD5.zvec_entity_probe import merge_zvec_into_probe_result


class TestFallbackStore(unittest.TestCase):
    def test_vector_and_fts(self):
        path = tempfile.mkdtemp()
        store = FallbackVectorStore(path, "test_ge")
        store.upsert_batch(
            [
                {
                    "id": "Equipment_1",
                    "vector": [1.0, 0.0, 0.0] + [0.0] * 509,
                    "fields": {
                        "neo4j_id": "1",
                        "node_type": "Equipment",
                        "name": "多旋翼无人机",
                        "status": "enabled",
                        "text": "名称：多旋翼无人机",
                    },
                },
                {
                    "id": "Equipment_2",
                    "vector": [0.0, 1.0, 0.0] + [0.0] * 509,
                    "fields": {
                        "neo4j_id": "2",
                        "node_type": "Equipment",
                        "name": "固定翼",
                        "status": "enabled",
                        "text": "名称：固定翼",
                    },
                },
            ]
        )
        hits = store.query_vector([0.9, 0.1, 0.0] + [0.0] * 509, topk=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["fields"]["name"], "多旋翼无人机")
        fts = store.query_fts("多旋翼", field="text", topk=1)
        self.assertTrue(fts)


class TestProbeMerge(unittest.TestCase):
    def test_merge_adds_zvec_hits(self):
        base = {"keyword": "多旋翼", "hits": [], "best": None, "match_count": 0}
        zvec_rows = [
            {
                "node_type": "Equipment",
                "id": "eq1",
                "name": "某型多旋翼无人机",
                "properties": {"type": "无人机"},
                "zvec_score": 0.88,
            }
        ]
        merged = merge_zvec_into_probe_result(base, zvec_rows, keyword="多旋翼")
        self.assertGreater(merged.get("match_count", 0), 0)
        self.assertTrue(merged.get("zvec_used"))


class TestBgeEmbedding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from blueprints.GLCX.ZNWD5.embedding_service import (
            embedding_backend,
            encode_texts,
            using_hash_fallback,
        )

        cls._backend = embedding_backend()
        if using_hash_fallback():
            raise unittest.SkipTest("BGE 不可用，跳过嵌入测试")
        cls._vecs = encode_texts(["通信设备A需要做试验", "通信设备A的试验项目", "今天天气很好"])
        cls._backend = embedding_backend()

    def test_embedding_backend_not_hash(self):
        from blueprints.GLCX.ZNWD5.embedding_service import using_hash_fallback

        self.assertFalse(using_hash_fallback())
        self.assertIn(self._backend, ("transformers", "sentence_transformers"))

    def test_bge_encode_not_hash(self):
        import numpy as np

        sim_related = float(np.dot(self._vecs[0], self._vecs[1]))
        sim_unrelated = float(np.dot(self._vecs[0], self._vecs[2]))
        self.assertGreater(sim_related, 0.5)
        self.assertLess(sim_unrelated, 0.55)


if __name__ == "__main__":
    unittest.main()
