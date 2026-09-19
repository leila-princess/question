"""RelFact schema：File 分类、关系类型与属性约定。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# 扩展关系类型（在 HAS / CON / BELONGS_TO 之外）
REL_DERIVED_FROM = "DERIVED_FROM"
REL_GOVERNED_BY = "GOVERNED_BY"
REL_CONSTRAINED_BY = "CONSTRAINED_BY"
REL_LIMITED_BY = "LIMITED_BY"
REL_APPLIES_TO = "APPLIES_TO"
REL_CITES = "CITES"

EXTENDED_REL_TYPES: Tuple[str, ...] = (
    REL_DERIVED_FROM,
    REL_GOVERNED_BY,
    REL_CONSTRAINED_BY,
    REL_LIMITED_BY,
    REL_APPLIES_TO,
    REL_CITES,
)

FILE_KIND_GB = "gb_standard"
FILE_KIND_GJB = "gjb_standard"
FILE_KIND_INDUSTRY = "industry_standard"
FILE_KIND_TEST_PLAN = "test_plan"
FILE_KIND_TEST_OUTLINE = "test_outline"
FILE_KIND_TEST_REPORT = "test_report"
FILE_KIND_REGULATION = "regulation"
FILE_KIND_OTHER = "other"

STANDARD_FILE_KINDS = frozenset({FILE_KIND_GB, FILE_KIND_GJB, FILE_KIND_INDUSTRY})

SLOT_SYTJ = "sytj"
SLOT_SYGC = "sygc"
SLOT_JGPD = "jgpd"
SLOTS: Tuple[str, ...] = (SLOT_SYTJ, SLOT_SYGC, SLOT_JGPD)

EXTRACT_VERSION = "relfact_v1"

# 法规标准类 WJGL Type
WJGL_TYPE_STANDARD = "法规标准"

SECTION_CHROMA_MAX_CHARS = 1500
SECTION_CHROMA_OVERLAP = 100


def infer_file_kind(category: str, std_code: str = "") -> str:
    cat = (category or "").strip()
    code = (std_code or "").upper().replace(" ", "")
    if cat != WJGL_TYPE_STANDARD and "标准" not in cat:
        if "试验方案" in cat:
            return FILE_KIND_TEST_PLAN
        if "试验大纲" in cat:
            return FILE_KIND_TEST_OUTLINE
        if "试验报告" in cat:
            return FILE_KIND_TEST_REPORT
        if "法规" in cat or "规章" in cat:
            return FILE_KIND_REGULATION
        return FILE_KIND_OTHER
    if code.startswith("GJB"):
        return FILE_KIND_GJB
    if code.startswith("GB/T") or code.startswith("GB") or "GB/T" in code:
        return FILE_KIND_GB
    return FILE_KIND_INDUSTRY


def should_section_chroma_index(file_kind: str) -> bool:
    return file_kind in STANDARD_FILE_KINDS


def should_extract_facts(file_kind: str) -> bool:
    return file_kind in STANDARD_FILE_KINDS


def rel_merge_spec(
    rel_type: str,
    from_label: str,
    from_id: str,
    to_label: str,
    to_id: str,
    props: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "rel_type": rel_type,
        "from_label": from_label,
        "from_id": from_id,
        "to_label": to_label,
        "to_id": to_id,
        "props": props or {},
    }


def serialize_json_field(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)
