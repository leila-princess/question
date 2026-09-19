"""ParseBundle / MethodArtifact 等类型定义。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

PARSE_BUNDLE_SCHEMA_VERSION = "1.0"

ModuleType = Literal["METHOD", "CONDITION", "EQUIPMENT", "CRITERIA", "OTHER"]
MissingStatus = Literal["complete", "partial"]
EquipmentDisplayMode = Literal["inline", "separate_reference"]
ClusterMode = Literal["llm", "rule_fallback"]


class ParseBundle(TypedDict, total=False):
    schema_version: str
    parse_mode: str
    source: str
    file_name: str
    canonical_markdown: str
    plain_text: str
    html: str
    sections: List[Dict[str, Any]]
    node_slices_md: Dict[str, str]
    node_slices_html: Dict[str, str]
    section_catalogue: List[Dict[str, Any]]
    word_paragraph_map: Any
    flat_headings: List[Dict[str, Any]]
    toc_display: List[Dict[str, Any]]
    all_tables: List[Dict[str, Any]]
    category: str
    doc_id: str
    stats: Dict[str, Any]


class SemanticModule(TypedDict, total=False):
    module_id: str
    type: ModuleType
    title: str
    start_line: int
    end_line: int
    confidence: float
    inline_criteria: Optional[str]
    chunk_index: int
    context_only: bool


class MethodGroup(TypedDict, total=False):
    group_name: str
    module_ids: List[str]
    primary_section_no: str
    cluster_mode: ClusterMode


class MethodBlocks(TypedDict, total=False):
    sytj: str
    sygc: str
    jgpd: str
    equipment_hints: List[Dict[str, Any]]


class CompletenessInfo(TypedDict, total=False):
    missing_status: MissingStatus
    missing_blocks: List[str]


class MethodArtifact(TypedDict, total=False):
    name: str
    synr: str
    sytj: str
    sygc: str
    jgpd: str
    description: str
    blocks: MethodBlocks
    modules: List[SemanticModule]
    missing_status: MissingStatus
    missing_blocks: List[str]
    extract_version: str
    cluster_mode: ClusterMode
    equipment_display_mode: EquipmentDisplayMode
    recommended_nodes: List[Dict[str, Any]]
    recommended_relations: List[Dict[str, Any]]
    equipment_recommendations: List[Dict[str, Any]]
    extract_source: str
