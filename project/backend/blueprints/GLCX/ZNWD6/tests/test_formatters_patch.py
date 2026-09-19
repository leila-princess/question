from blueprints.GLCX.ZNWD4.formatters import (
    _equipment_group_keys_for_focus,
    _format_equip_projects_methods_composite,
    format_intent_plan_answer,
    format_test_projects_hierarchical,
)


def test_merge_equipment_variant_groups():
    groups = {"通信设备A": [{}], "通信设备A型": [{}], "通信设备B": [{}]}
    keys = _equipment_group_keys_for_focus(groups, "通信设备A")
    assert "通信设备A" in keys
    assert "通信设备A型" in keys
    assert "通信设备B" not in keys


def test_equip_project_flat_summary():
    rows = [
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "通信设备A"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "机动试验", "Id": "p1"}},
        },
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "通信设备A型"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "振动环境测试项目", "Id": "p2"}},
        },
    ]
    text = format_test_projects_hierarchical(rows, focus="通信设备A", from_type="Equipment")
    assert "通信设备A相关的试验项目有：" in text
    assert "机动试验" in text
    assert "振动环境测试项目" in text


def test_intent_plan_compact_format():
    raw = {
        "question": "通信设备A有哪些试验项目和考核指标？",
        "binding_mode": "shared",
        "source": "rule",
        "steps": [
            {
                "goal": "test_projects",
                "purpose": "",
                "route_id": "",
                "span": "",
                "slots": {"equipment_keywords": ["通信设备A"], "focus": "通信设备A"},
                "anchor_slots": {},
                "filter_hints": {},
                "chain": "",
                "step_id": "intent1",
            },
            {
                "goal": "indicators",
                "purpose": "",
                "route_id": "",
                "span": "",
                "slots": {"equipment_keywords": ["通信设备A"], "focus": "通信设备A"},
                "anchor_slots": {},
                "filter_hints": {},
                "chain": "",
                "step_id": "intent2",
            },
        ],
    }
    step_results = [
        {
            "step_id": "s1",
            "rows": [
                {
                    "n": {"labels": ["Equipment"], "properties": {"Name": "通信设备A"}},
                    "m": {"labels": ["TestProject"], "properties": {"Name": "机动试验", "Id": "p1"}},
                }
            ],
        },
        {
            "step_id": "s2",
            "rows": [
                {
                    "n": {"labels": ["Equipment"], "properties": {"Name": "通信设备A"}},
                    "m": {"properties": {"Name": "高温适应性", "hierarchy_type": "test"}},
                }
            ],
        },
    ]
    text = format_intent_plan_answer(
        neo_rows=[],
        params={"intent_plan": raw, "equipment_keywords": ["通信设备A"], "focus": "通信设备A"},
        step_results=step_results,
    )
    assert "试验项目" in text
    assert "考核指标" in text
    assert "机动试验" in text
    assert "高温适应性" in text


def test_equip_env_compact_format():
    rows = [
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目B", "Id": "p1"}},
        },
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目A", "Id": "p2"}},
        },
    ]
    text = format_test_projects_hierarchical(
        rows, focus="汽车2", from_type="Equipment", environment_label="test"
    )
    assert text == "汽车2在test环境下的试验项目有：项目B、项目A。"


def test_composite_project_method_no_miss_markers():
    s1 = [
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目B", "Id": "p1"}},
        },
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目A", "Id": "p2"}},
        },
    ]
    s2 = [
        {
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目A"}},
            "n": {"labels": ["TestMethod"], "properties": {"Name": "汽车最高车速试验方法"}},
        }
    ]
    text = _format_equip_projects_methods_composite(
        "汽车2", s1, s2, retrieval_question="汽车2有哪些试验项目和方法"
    )
    assert "未在清单库" not in text
    assert "汽车2的试验项目包括：" in text
    assert "对应试验方法包括：汽车最高车速试验方法" in text


def test_composite_env_project_method_format():
    s2 = [
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目B", "Id": "p1"}},
        },
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目A", "Id": "p2"}},
        },
    ]
    s3 = [
        {
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目A"}},
            "n": {"labels": ["TestMethod"], "properties": {"Name": "汽车最高车速试验方法"}},
        }
    ]
    text = _format_equip_projects_methods_composite(
        "汽车2",
        s2,
        s3,
        retrieval_question="汽车2在test环境下有哪些试验项目，这些项目用什么方法？",
        environment_label="test",
    )
    assert "汽车2在test环境下需做 项目B、项目A；对应方法：汽车最高车速试验方法" == text


def test_multi_step_project_method_format():
    s1 = [
        {
            "n": {"labels": ["Equipment"], "properties": {"Name": "汽车2"}},
            "m": {"labels": ["TestProject"], "properties": {"Name": "项目B", "Id": "p1"}},
        },
    ]
    s2 = [
        {
            "n": {"labels": ["TestMethod"], "properties": {"Name": "汽车最高车速试验方法"}},
        }
    ]
    text = _format_equip_projects_methods_composite(
        "汽车2",
        s1,
        s2,
        retrieval_question="请先告诉我汽车2有哪些试验项目，再告诉我这些项目对应的试验方法",
    )
    assert text.startswith("第一步：汽车2的试验项目包括")
    assert "第二步：对应试验方法包括" in text
    assert "未在清单库" not in text
