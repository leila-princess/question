from blueprints.GLCX.ZNWD6.entity_search import load_guided_schema
from blueprints.GLCX.ZNWD6.guided_flow_builder import build_guided_flow_response
from blueprints.GLCX.ZNWD6.ontology_loader import load_guided_intents, load_ontology


def test_build_guided_flow_includes_all_intents():
    guided = load_guided_intents()
    ont = load_ontology()
    flow = build_guided_flow_response(
        guided,
        ont.get("node_labels") or [],
        guided.get("node_labels_zh") or {},
    )
    intent_ids = {i["id"] for i in (guided.get("intents") or [])}
    composite_ids = {c["id"] for c in (guided.get("composites") or [])}
    all_ids = intent_ids | composite_ids
    flow_ids = set()
    for nt in flow.get("nodeTypes") or []:
        for a in nt.get("aspects") or []:
            flow_ids.add(a.get("intentId"))
    assert all_ids <= flow_ids


def test_load_guided_schema_has_aspects_per_node():
    schema = load_guided_schema()
    flow = schema.get("guidedFlow") or {}
    equip = next((n for n in flow.get("nodeTypes") or [] if n.get("type") == "Equipment"), None)
    assert equip is not None
    assert len(equip.get("aspects") or []) >= 5
