"""装备主体 vs 全局枚举 模板路由单测。"""

from blueprints.GLCX.ZNWD6.intent_router import (
    _resolve_effective_template_hint,
    enforce_binding_template,
    infer_template_hint_from_question,
    suggest_template,
)


def test_equip_indicator_not_enum():
    q = "无人机有哪些考核指标"
    tau = {"flat_candidates": [{"node_type": "Equipment", "name": "无人机", "entity_id": "ZB_1"}]}
    tid, reason = suggest_template(q, tau)
    assert tid == "equip_to_indicators"
    assert reason in ("equip_indicator", "session_context_hint")


def test_global_indicator_enum():
    q = "有哪些考核指标"
    tid, _ = suggest_template(q, {"flat_candidates": []})
    assert tid == "enum_indicator"


def test_stale_session_hint_overridden():
    """上一轮 project 的 session hint 不应覆盖本轮 indicator 问句。"""
    q = "无人机有哪些考核指标"
    tau = {"flat_candidates": []}
    tid, reason = suggest_template(q, tau, template_hint="equip_to_projects")
    assert tid == "equip_to_indicators"
    assert reason == "session_context_hint"


def test_enforce_binding_blocks_llm_enum():
    q = "无人机的考核指标有哪些"
    tau = {"flat_candidates": []}
    binding = {
        "bindings": [{"node_type": "Equipment", "name": "无人机", "entity_id": "ZB_1"}],
        "template_id": "enum_indicator",
        "intent": "llm",
    }
    fixed = enforce_binding_template(
        binding,
        suggested_tid="equip_to_indicators",
        suggest_reason="equip_indicator",
        template_hint="equip_to_indicators",
        question=q,
        tau=tau,
    )
    assert fixed["template_id"] == "equip_to_indicators"


def test_infer_variants():
    tau = {"flat_candidates": []}
    assert infer_template_hint_from_question("汽车2的考核指标有哪些", tau) == "equip_to_indicators"
    assert infer_template_hint_from_question("有哪些考核指标", tau) == "enum_indicator"


def test_znwd4_enriched_short_indicator_phrase():
    q = "无人机有哪些指标"
    assert infer_template_hint_from_question(q, {"flat_candidates": []}) == "equip_to_indicators"
    tid, _ = suggest_template(q, {"flat_candidates": []})
    assert tid == "equip_to_indicators"

    q = "无人机有哪些考核指标"
    tau = {"flat_candidates": []}
    assert _resolve_effective_template_hint(q, tau, "equip_to_projects") == "equip_to_indicators"
    assert _resolve_effective_template_hint("有哪些考核指标", tau, "equip_to_indicators") == "enum_indicator"


def test_refine_blocks_enum_with_equipment_binding():
    from blueprints.GLCX.ZNWD6.intent_router import refine_template_by_binding

    binding = {
        "bindings": [{"node_type": "Equipment", "name": "无人机"}],
        "template_id": "enum_indicator",
        "intent": "global_enum_indicator",
    }
    tid = refine_template_by_binding(
        "enum_indicator",
        binding,
        template_hint=None,
        question="无人机有哪些考核指标",
        tau={"flat_candidates": []},
    )
    assert tid == "equip_to_indicators"


def test_enforce_binding_without_hint_but_equipment_bound():
    binding = {
        "bindings": [{"node_type": "Equipment", "name": "无人机", "entity_id": "x"}],
        "template_id": "enum_indicator",
        "intent": "llm",
    }
    fixed = enforce_binding_template(
        binding,
        suggested_tid="enum_indicator",
        suggest_reason="global_enum_indicator",
        template_hint=None,
        question="无人机有哪些考核指标",
        tau={"flat_candidates": []},
    )
    assert fixed["template_id"] == "equip_to_indicators"
