from blueprints.GLCX.ZNWD6.intent_router import suggest_template
from blueprints.GLCX.ZNWD6.ontology_loader import load_ontology
from blueprints.GLCX.ZNWD6.plan_builder import build_route_plan


def test_document_lookup_route_id():
    load_ontology.cache_clear()
    plan = build_route_plan(
        "GBT 标准内容",
        {"flat_candidates": [], "spans": [], "zvec_used": False},
        {"bindings": [], "template_id": "document_lookup", "doc_hints": []},
        template_id="document_lookup",
    )
    assert plan["scenario"] == "keyword_doc"
    assert plan["route_id"] == "R-KNOWLEDGE-keyword_doc"
    assert plan.get("znwd6_deferred_doc") is True


def test_equip_route_id():
    load_ontology.cache_clear()
    binding = {
        "bindings": [
            {"span": "无人机", "entity_id": "e1", "node_type": "Equipment", "name": "多旋翼无人机", "slot_key": "equipment_keywords"}
        ],
        "template_id": "equip_to_projects",
    }
    tau = {
        "flat_candidates": [{"entity_id": "e1", "node_type": "Equipment", "name": "多旋翼无人机"}],
        "spans": [],
        "zvec_used": True,
    }
    plan = build_route_plan("多旋翼无人机有哪些试验项目", tau, binding, template_id="equip_to_projects")
    assert plan["route_id"] == "R-TEST_PROJECT-by_equipment"
    assert plan["params_draft"].get("equipment_keywords") == ["多旋翼无人机"]


def test_chitchat_route():
    load_ontology.cache_clear()
    plan = build_route_plan(
        "你好",
        {"flat_candidates": [], "spans": []},
        {"bindings": [], "template_id": "chitchat"},
        template_id="chitchat",
    )
    assert plan["route_id"] == "R-CHAT-direct"


def test_enum_environment_intent():
    load_ontology.cache_clear()
    tid, reason = suggest_template("有哪些试验环境", {"flat_candidates": []})
    assert tid == "enum_environment"
    assert "intent=" in reason


def test_equip_env_plan_injects_environment_focus_id():
    load_ontology.cache_clear()
    binding = {
        "bindings": [
            {"span": "汽车2", "entity_id": "e1", "node_type": "Equipment", "name": "汽车2", "slot_key": "equipment_keywords"},
            {"span": "test", "entity_id": "v1", "node_type": "Environment", "name": "test", "slot_key": "environment_keywords"},
        ],
        "template_id": "equip_and_env_to_projects",
    }
    tau = {
        "flat_candidates": [
            {"entity_id": "e1", "node_type": "Equipment", "name": "汽车2"},
            {"entity_id": "v1", "node_type": "Environment", "name": "test"},
        ],
        "spans": [],
    }
    plan = build_route_plan("汽车2在test环境下有哪些试验项目", tau, binding, template_id="equip_and_env_to_projects")
    draft = plan["params_draft"]
    assert draft.get("environment_focus_id") == "v1"
    assert draft.get("environment_label") == "test"
    hits = plan["probe_snapshot"]["hits"]
    assert hits[0].get("id") == "e1"
    assert hits[1].get("id") == "v1"
