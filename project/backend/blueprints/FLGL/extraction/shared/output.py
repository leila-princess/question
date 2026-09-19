"""MethodPanel 归一化、描述、召回 — 薄封装。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def generate_method_description(method_name: str, *, content_html: str = "", content_plain: str = "") -> str:
    from blueprints.FLGL.method_section_enrich import generate_method_description_llm

    return generate_method_description_llm(
        method_name,
        content_html=content_html,
        content_plain=content_plain,
    )


def recall_equipment_for_method(
    method_name: str,
    text: str,
    *,
    std_name: str = "",
    std_code: str = "",
    neo4j_driver=None,
) -> tuple:
    from blueprints.FLGL.method_section_enrich import build_equipment_scoped_recommendations

    return build_equipment_scoped_recommendations(
        f"{method_name}\n{text[:3000]}",
        neo4j_driver=neo4j_driver,
        std_name=std_name,
        std_code=std_code,
    )


def normalize_method_artifact(
    artifact: Dict[str, Any],
    *,
    draft_method_id: str = "",
    source_name: str = "",
    method_index: int = 1,
) -> Dict[str, Any]:
    """转为 MethodPanel 兼容 dict（调用现有 _normalize_method_for_panel 逻辑）。"""
    from blueprints.FLGL.method_routes import _normalize_method_for_panel

    return _normalize_method_for_panel(
        artifact,
        draft_method_id=draft_method_id,
        source_name=source_name,
        method_index=method_index,
        phase="full",
        document_dir=artifact.get("document_dir") or "",
    )
