"""
ZNWD5 元操作：继承 ZNWD4 全部 neo/chroma op，并扩展 zvec_* 元操作。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from blueprints.GLCX.ZNWD4 import meta_ops as _base

NODE_LABELS: List[str] = list(_base.NODE_LABELS)
REL_TYPES: List[str] = list(_base.REL_TYPES)
NODE_ENTITY_FIELDS: Dict[str, List[str]] = dict(_base.NODE_ENTITY_FIELDS)
RELATION_SCHEMA: Dict[str, List[Tuple[str, str]]] = dict(_base.RELATION_SCHEMA)

META_OPS: Dict[str, Dict[str, Any]] = {**_base.META_OPS}

META_OPS.update(
    {
        "zvec_entity_search": {
            "kind": "zvec",
            "description": "BGE 语义检索图实体（Zvec graph_entities + 标量过滤）。",
            "params_schema": {
                "query": {"type": "string", "required": True},
                "top_k": {"type": "int", "required": False, "default": 12, "min": 1, "max": 50},
                "node_types": {"type": "array", "required": False},
            },
        },
        "zvec_entity_fts": {
            "kind": "zvec",
            "description": "Zvec FTS 检索图实体（标准号/精确名兜底）。",
            "params_schema": {
                "query": {"type": "string", "required": True},
                "top_k": {"type": "int", "required": False, "default": 12, "min": 1, "max": 50},
                "node_types": {"type": "array", "required": False},
            },
        },
        "zvec_doc_hybrid_search": {
            "kind": "zvec",
            "description": "Zvec 文档混合检索（向量 + FTS）。",
            "params_schema": {
                "query": {"type": "string", "required": True},
                "top_k": {"type": "int", "required": False, "default": 12, "min": 1, "max": 200},
                "doc_id": {"type": "string", "required": False},
                "title_keyword": {"type": "string", "required": False},
                "std_code": {"type": "string", "required": False},
            },
        },
        "zvec_doc_filter": {
            "kind": "zvec",
            "description": "Zvec 文档结果按 score/distance 过滤。",
            "params_schema": {
                "min_score": {"type": "float", "required": False, "default": 0.0, "min": -1.0, "max": 2.0},
                "max_distance": {"type": "float", "required": False, "default": 2.0, "min": 0.0, "max": 10.0},
            },
        },
        "neo_vector_hybrid_query": {
            "kind": "neo4j",
            "description": "Neo4j 原生向量索引 + 关系遍历（Deep Integration A/B）。",
            "params_schema": {
                "query": {"type": "string", "required": True},
                "node_label": {"type": "string", "required": True},
                "rel_type": {"type": "string", "required": False, "default": "CON"},
                "to_label": {"type": "string", "required": False},
                "top_k": {"type": "int", "required": False, "default": 5, "min": 1, "max": 20},
            },
        },
    }
)

# 透传 ZNWD4 其它导出
for _name in (
    "build_graph_schema_markdown",
    "build_meta_ops_markdown",
    "CHROMA_META_OPS",
    "NEO_META_OPS",
):
    if hasattr(_base, _name):
        globals()[_name] = getattr(_base, _name)
