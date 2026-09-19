"""
ZNWD5 本地向量存储回退：当 zvec 不可用时（如 Python 3.9）使用 numpy 持久化索引。
API 与 ZvecIndexManager 的查询接口对齐。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _safe_doc_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", raw or "doc")


class FallbackVectorStore:
    """轻量 numpy 向量库，支持向量检索、简易 FTS 与标量过滤。"""

    def __init__(self, path: str, name: str):
        self.path = path
        self.name = name
        self._meta_path = os.path.join(path, f"{name}_meta.json")
        self._vec_path = os.path.join(path, f"{name}_vectors.npy")
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.vectors: Optional[np.ndarray] = None
        self._id_list: List[str] = []
        os.makedirs(path, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if os.path.isfile(self._meta_path):
            with open(self._meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.docs = data.get("docs") or {}
            self._id_list = list(self.docs.keys())
        if os.path.isfile(self._vec_path) and self._id_list:
            self.vectors = np.load(self._vec_path)
        else:
            self.vectors = np.zeros((0, 512), dtype=np.float32)

    def _save(self) -> None:
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump({"docs": self.docs}, f, ensure_ascii=False)
        if self.vectors is not None and len(self._id_list):
            np.save(self._vec_path, self.vectors)

    def upsert_batch(self, items: List[Dict[str, Any]]) -> int:
        if not items:
            return 0
        dim = 512
        for it in items:
            doc_id = _safe_doc_id(str(it.get("id") or it.get("chunk_id") or ""))
            if not doc_id:
                continue
            vec = it.get("vector")
            fields = dict(it.get("fields") or {})
            if not fields:
                fields = {
                    k: str(it.get(k) or "")
                    for k in ("neo4j_id", "node_type", "name", "status", "text", "doc_id", "title", "section", "std_code", "content")
                    if it.get(k) is not None
                }
            if isinstance(vec, list) and vec:
                dim = len(vec)
            self.docs[doc_id] = {"id": doc_id, "fields": fields, "vector": vec}
            if doc_id not in self._id_list:
                self._id_list.append(doc_id)

        mat = np.zeros((len(self._id_list), dim), dtype=np.float32)
        for i, doc_id in enumerate(self._id_list):
            vec = (self.docs.get(doc_id) or {}).get("vector")
            if isinstance(vec, list) and len(vec) == dim:
                mat[i] = np.asarray(vec, dtype=np.float32)
        self.vectors = mat
        self._save()
        return len(items)

    def _match_filter(self, fields: Dict[str, Any], filter_expr: Optional[str]) -> bool:
        if not filter_expr:
            return True
        expr = filter_expr.strip()
        # 支持 node_type == 'Equipment' / status == 'enabled'
        m = re.match(r"(\w+)\s*==\s*'([^']*)'", expr)
        if m:
            key, val = m.group(1), m.group(2)
            return str(fields.get(key) or "") == val
        m = re.match(r"(\w+)\s+IN\s+\[([^\]]+)\]", expr, re.I)
        if m:
            key = m.group(1)
            vals = [x.strip().strip("'\"") for x in m.group(2).split(",")]
            return str(fields.get(key) or "") in vals
        return True

    def _fts_score(self, text: str, query: str) -> float:
        if not text or not query:
            return 0.0
        tl, ql = text.lower(), query.lower()
        if ql in tl:
            return 1.0
        hits = sum(1 for tok in re.split(r"\s+", ql) if tok and tok in tl)
        return hits / max(1, len(re.split(r"\s+", ql)))

    def query_vector(
        self,
        vector: List[float],
        *,
        topk: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self.vectors is None or not len(self._id_list):
            return []
        q = np.asarray(vector, dtype=np.float32)
        qn = np.linalg.norm(q) + 1e-8
        sims = (self.vectors @ q) / (np.linalg.norm(self.vectors, axis=1) * qn + 1e-8)
        order = np.argsort(-sims)
        out: List[Dict[str, Any]] = []
        for idx in order:
            doc_id = self._id_list[int(idx)]
            doc = self.docs.get(doc_id) or {}
            fields = doc.get("fields") or {}
            if not self._match_filter(fields, filter_expr):
                continue
            out.append({"id": doc_id, "score": float(sims[int(idx)]), "fields": fields})
            if len(out) >= topk:
                break
        return out

    def query_fts(
        self,
        text: str,
        *,
        field: str = "text",
        topk: int = 10,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        scored: List[Dict[str, Any]] = []
        for doc_id, doc in self.docs.items():
            fields = doc.get("fields") or {}
            if not self._match_filter(fields, filter_expr):
                continue
            sc = self._fts_score(str(fields.get(field) or ""), text)
            if sc > 0:
                scored.append({"id": doc_id, "score": sc, "fields": fields})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:topk]

    def query_hybrid(
        self,
        vector: List[float],
        fts_text: str,
        *,
        fts_field: str = "content",
        topk: int = 10,
        filter_expr: Optional[str] = None,
        vector_weight: float = 0.7,
    ) -> List[Dict[str, Any]]:
        v_hits = {h["id"]: h for h in self.query_vector(vector, topk=topk * 3, filter_expr=filter_expr)}
        f_hits = {h["id"]: h for h in self.query_fts(fts_text, field=fts_field, topk=topk * 3, filter_expr=filter_expr)}
        all_ids = set(v_hits) | set(f_hits)
        merged: List[Dict[str, Any]] = []
        for doc_id in all_ids:
            vs = float((v_hits.get(doc_id) or {}).get("score") or 0.0)
            fs = float((f_hits.get(doc_id) or {}).get("score") or 0.0)
            fields = (v_hits.get(doc_id) or f_hits.get(doc_id) or {}).get("fields") or {}
            merged.append(
                {
                    "id": doc_id,
                    "score": vector_weight * vs + (1.0 - vector_weight) * fs,
                    "fields": fields,
                }
            )
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:topk]

    def count(self) -> int:
        return len(self.docs)
