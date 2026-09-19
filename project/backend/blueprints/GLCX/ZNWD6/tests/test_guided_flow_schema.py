from blueprints.GLCX.ZNWD6.entity_search import load_guided_schema, _VALID_TYPES
from blueprints.GLCX.ZNWD6.ontology_loader import load_guided_intents


def test_guided_flow_present():
    schema = load_guided_schema()
    flow = schema.get("guidedFlow") or {}
    assert flow.get("greetingZh")
    assert len(flow.get("nodeTypes") or []) >= 7


def test_guided_flow_intent_refs_valid():
    schema = load_guided_schema()
    guided = load_guided_intents()
    intent_ids = {i["id"] for i in (guided.get("intents") or [])}
    composite_ids = {c["id"] for c in (guided.get("composites") or [])}
    all_ids = intent_ids | composite_ids

    flow = schema.get("guidedFlow") or {}
    for nt in flow.get("nodeTypes") or []:
        for aspect in nt.get("aspects") or []:
            iid = aspect.get("intentId")
            assert iid in all_ids, f"aspect {aspect.get('id')} refs unknown intent {iid}"


def test_guided_flow_slot_node_types_valid():
    schema = load_guided_schema()
    guided = load_guided_intents()
    intent_map = {i["id"]: i for i in (guided.get("intents") or [])}
    composite_map = {c["id"]: c for c in (guided.get("composites") or [])}

    flow = schema.get("guidedFlow") or {}
    for nt in flow.get("nodeTypes") or []:
        for aspect in nt.get("aspects") or []:
            iid = aspect.get("intentId")
            item = intent_map.get(iid) or composite_map.get(iid)
            assert item is not None
            for slot in item.get("slots") or []:
                nt_slot = slot.get("node_type")
                if nt_slot and not slot.get("free_text"):
                    assert nt_slot in _VALID_TYPES
