from blueprints.GLCX.ZNWD6.deferred_doc_bridge import build_doc_query, should_run_deferred_doc


def test_build_doc_query_merges_neo_names():
    q = build_doc_query(
        "试验方法内容",
        [{"properties": {"Name": "高温工作性能测试"}}, {"name": "SYNR-001"}],
    )
    assert "高温工作性能测试" in q
    assert "试验方法内容" in q


def test_should_run_deferred_doc_empty_chroma():
    plan = {"source": "znwd6", "znwd6_deferred_doc": True}
    assert should_run_deferred_doc(plan, []) is True
    assert should_run_deferred_doc(plan, [{"title": "x", "content": "y"}]) is False
    assert should_run_deferred_doc({"source": "znwd4"}, []) is False
