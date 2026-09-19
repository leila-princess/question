from blueprints.GLCX.ZNWD6.ontology_loader import get_template, load_ontology, slot_key_for_node_type


def test_load_ontology():
    ont = load_ontology()
    assert "Equipment" in ont["node_labels"]
    assert "equip_to_projects" in ont["templates"]


def test_slot_key():
    assert slot_key_for_node_type("Equipment") == "equipment_keywords"


def test_get_template():
    tpl = get_template("equip_to_projects")
    assert tpl.get("user_biz") == "UBIZ_TEST_PROJECT"
