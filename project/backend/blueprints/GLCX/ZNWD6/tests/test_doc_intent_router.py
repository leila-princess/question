from blueprints.GLCX.ZNWD6.intent_router import suggest_template
from blueprints.GLCX.ZNWD6.ontology_loader import load_ontology
from blueprints.GLCX.ZNWD6.plan_builder import build_route_plan


def test_doc_overview_beats_method_content():
    load_ontology.cache_clear()
    q = "机动车暴露于天气老化试验方法标准的主要内容是什么"
    tid, reason = suggest_template(q, {"flat_candidates": []})
    assert tid == "document_lookup"
    assert reason in ("doc_overview", "intent=doc_overview")


def test_doc_section_route():
    load_ontology.cache_clear()
    tid, reason = suggest_template("GBT38924标准第43节讲了什么", {"flat_candidates": []})
    assert tid == "standard_chapter"
    assert reason in ("doc_section", "intent=doc_section")


def test_document_lookup_uses_db_direct_doc():
    load_ontology.cache_clear()
    plan = build_route_plan(
        "机动车暴露于天气老化试验方法标准的主要内容是什么",
        {"flat_candidates": [], "spans": [], "zvec_used": False},
        {"bindings": [], "template_id": "document_lookup", "doc_hints": []},
        template_id="document_lookup",
    )
    assert plan["answer_strategy"] == "db_direct_doc"
    assert plan["answer_mode"] == "database_direct"
