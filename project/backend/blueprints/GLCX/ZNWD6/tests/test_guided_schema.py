from blueprints.GLCX.ZNWD6.entity_search import load_guided_schema
from blueprints.GLCX.ZNWD6.ontology_loader import load_guided_intents, load_ontology


def test_load_guided_intents():
    guided = load_guided_intents()
    assert len(guided.get("intents") or []) >= 7
    assert len(guided.get("composites") or []) >= 3


def test_load_guided_schema():
    schema = load_guided_schema()
    assert schema.get("nodeTypes")
    assert schema.get("intents")
    assert schema.get("quickExamples")


def test_ontology_has_seven_labels():
    ont = load_ontology()
    assert len(ont.get("node_labels") or []) == 7
