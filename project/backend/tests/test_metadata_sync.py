"""元数据同步辅助函数单元测试。"""

from blueprints.XTGL.SJKGL.metadata_sync import (
    _minio_tag_type,
    _neo4j_prop_type,
    _normalize_minio_tag_key,
)


def test_neo4j_prop_type_mapping():
    assert _neo4j_prop_type(True) == "BOOLEAN"
    assert _neo4j_prop_type(42) == "INTEGER"
    assert _neo4j_prop_type(3.14) == "FLOAT"
    assert _neo4j_prop_type(["a"]) == "LIST"
    assert _neo4j_prop_type("text") == "STRING"
    assert _neo4j_prop_type(None) == "STRING"


def test_minio_tag_type_mapping():
    assert _minio_tag_type("true") == "BOOLEAN"
    assert _minio_tag_type("123") == "INTEGER"
    assert _minio_tag_type("1.5") == "FLOAT"
    assert _minio_tag_type("abc") == "STRING"


def test_normalize_minio_tag_key():
    assert _normalize_minio_tag_key("x-amz-meta-doc_id") == "doc_id"
    assert _normalize_minio_tag_key("X-Amz-Meta-title") == "title"
    assert _normalize_minio_tag_key("doc_id") == "doc_id"
