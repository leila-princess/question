"""多轮追问问句消解单测。"""

from blueprints.GLCX.ZNWD.session_context import (
    QUERY_TARGET_INDICATOR,
    _compose_equip_target_question,
    _detect_target_only_followup,
    resolve_question_context,
)


def test_detect_target_only_indicator():
    hit = _detect_target_only_followup("考核指标呢")
    assert hit is not None
    target, phrase = hit
    assert target == QUERY_TARGET_INDICATOR
    assert "指标" in phrase


def test_compose_equip_indicator_question():
    q = _compose_equip_target_question("无人机", "有哪些考核指标")
    assert q == "无人机有哪些考核指标"


def test_resolve_target_only_with_session_ctx():
    session_ctx = {
        "focus_entity": {"type": "Equipment", "id": "ZB_0001", "name": "无人机"},
        "last_template_id": "equip_to_projects",
        "last_query_target": "project",
        "slots": {"equipment": ["无人机"]},
        "last_user_question": "无人机有什么试验项目",
        "last_resolved_question": "无人机有哪些试验项目",
    }
    resolved = resolve_question_context(
        "考核指标呢",
        session_ctx=session_ctx,
    )
    assert resolved.is_follow_up is True
    assert "无人机" in resolved.resolved_question
    assert "指标" in resolved.resolved_question
    assert resolved.template_hint == "equip_to_indicators"


def test_resolve_znwd4_rules_without_session_ctx():
    """无 session_context 时，Redis 历史槽位规则仍可补全「考核指标呢」。"""
    from unittest.mock import MagicMock, patch

    from blueprints.GLCX.ZNWD.session_context import resolve_question_context

    fake_store = MagicMock()
    fake_store.get_session_context.return_value = {}
    fake_store.get_messages.return_value = [
        {"role": "user", "content": "无人机有什么试验项目"},
        {"role": "assistant", "content": "无人机相关试验项目如下…"},
    ]

    with patch("blueprints.GLCX.ZNWD.session_context._get_memory_store", return_value=fake_store):
        with patch("blueprints.GLCX.ZNWD4.znwd4_history_context.get_memory_store", return_value=fake_store):
            resolved = resolve_question_context(
                "考核指标呢",
                user_id="u1",
                session_id="s1",
            )
    assert resolved.is_follow_up is True
    assert "无人机" in resolved.resolved_question
    assert "指标" in resolved.resolved_question
    assert resolved.template_hint == "equip_to_indicators"


def test_resolve_full_question_unchanged():
    q = "无人机有哪些考核指标"
    resolved = resolve_question_context(q, session_ctx={})
    assert resolved.resolved_question == q
    assert resolved.is_follow_up is False
    assert resolved.template_hint == "equip_to_indicators"

