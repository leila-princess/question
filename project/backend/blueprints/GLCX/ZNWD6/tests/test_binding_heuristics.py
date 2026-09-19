from blueprints.GLCX.ZNWD6.binding_heuristics import try_rule_binding


def test_rule_bind_equipment_exact():
    tau = {
        "flat_candidates": [
            {"entity_id": "a1", "node_type": "Equipment", "name": "通信设备A", "score": 0.9},
            {"entity_id": "a2", "node_type": "Equipment", "name": "通信设备A型", "score": 0.85},
        ]
    }
    b = try_rule_binding("通信设备A有哪些试验项目", tau, "equip_to_projects")
    assert b is not None
    names = [x["name"] for x in b["bindings"]]
    assert "通信设备A" in names


def test_rule_bind_equip_and_env():
    tau = {
        "flat_candidates": [
            {"entity_id": "e1", "node_type": "Equipment", "name": "汽车2", "score": 0.9},
            {"entity_id": "v1", "node_type": "Environment", "name": "test", "score": 0.8},
        ]
    }
    b = try_rule_binding("汽车2在test环境下有哪些试验项目", tau, "equip_and_env_to_projects")
    assert b is not None
    by_type = {x["node_type"]: x["name"] for x in b["bindings"]}
    assert by_type.get("Equipment") == "汽车2"
    assert by_type.get("Environment") == "test"


def test_rule_bind_equip_and_env_with_query_prefix():
    tau = {
        "flat_candidates": [
            {"entity_id": "e1", "node_type": "Equipment", "name": "汽车2", "score": 0.9},
            {"entity_id": "v1", "node_type": "Environment", "name": "test", "score": 0.8},
        ]
    }
    b = try_rule_binding("查询汽车2在test环境下的试验项目清单", tau, "equip_and_env_to_projects")
    assert b is not None
    by_type = {x["node_type"]: x["name"] for x in b["bindings"]}
    assert by_type.get("Equipment") == "汽车2"
    assert by_type.get("Environment") == "test"
