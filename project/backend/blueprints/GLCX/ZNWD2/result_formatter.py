"""检索结果格式化为自然语言。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .types import ExecutionResult, IntentJson

from .schema import NODE_LABEL_ZH

_LABEL_ZH = NODE_LABEL_ZH


def format_answer(
    user_query: str,
    intent_json: IntentJson,
    result: ExecutionResult,
) -> str:
    """将执行结果格式化为用户可读答案。"""
    if result.error:
        return f"查询执行失败：{result.error}"

    rows = result.rows or []
    intent_type = str(intent_json.get("intent_type") or "")
    target_zh = _LABEL_ZH.get(result.target_label, result.target_label or "结果")

    if not rows:
        filters = intent_json.get("start_filters") or {}
        kw = ", ".join(f"{k}={v}" for k, v in filters.items()) or "（未指定）"
        return f"未在知识图谱中找到与「{user_query}」匹配的{target_zh}（检索条件：{kw}）。"

    lines = [f"根据您的问题「{user_query}」，共找到 **{len(rows)}** 条{target_zh}：", ""]

    for i, row in enumerate(rows[:50], 1):
        props = row.get("properties") or {}
        name = str(props.get("Name") or props.get("Label") or props.get("Id") or "未命名")
        typ = props.get("Type")
        extra_parts: List[str] = []
        if typ:
            extra_parts.append(f"类型={typ}")
        if row.get("project_name"):
            extra_parts.append(f"项目={row['project_name']}")
        if row.get("parent_name"):
            extra_parts.append(f"父项目={row['parent_name']}")
        if row.get("method_name"):
            extra_parts.append(f"方法={row['method_name']}")
        if row.get("equipment_type_name"):
            extra_parts.append(f"类型={row['equipment_type_name']}")
        if intent_type == "查方法" and props.get("SYNR"):
            synr = str(props["SYNR"]).strip()
            if synr:
                extra_parts.append(f"内容摘要={synr[:80]}…" if len(synr) > 80 else f"内容={synr}")
        extra = f"（{', '.join(extra_parts)}）" if extra_parts else ""
        lines.append(f"{i}. {name}{extra}")

    if len(rows) > 50:
        lines.append("")
        lines.append(f"（仅展示前 50 条，共 {len(rows)} 条）")

    if result.macro_name:
        lines.append("")
        lines.append(f"_执行路径：宏「{result.macro_name}」_")

    return "\n".join(lines)


def summarize_intent(intent_json: IntentJson) -> str:
    """意图 JSON 一行摘要（思考区用）。"""
    parts = [
        f"意图={intent_json.get('intent_type')}",
        f"起点={','.join(intent_json.get('start_entities') or [])}",
        f"目标={','.join(intent_json.get('target_entities') or [])}",
    ]
    sf = intent_json.get("start_filters")
    if sf:
        parts.append(f"过滤={sf}")
    env = intent_json.get("environment_constraint")
    if env:
        parts.append(f"环境={env}")
    return "；".join(parts)
