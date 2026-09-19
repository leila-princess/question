"""Parse hierarchical equipment-type focus: level-1 Type + level-2 Name."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_PARENT_CHILD_UNDER = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)(?:的|下)([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)"
    r"(?:有|的)?(?:什么|哪些|包括哪些).{0,12}(?:具体)?\s*(?:的)?\s*(?:设备|装备)",
    re.I,
)
_PARENT_CHILD_EQUIP_TAIL = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)(?:的|下)([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)"
    r"(?:有|的)?(?:什么|哪些|包括哪些)",
    re.I,
)
_CHILD_ONLY = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)"
    r"(?:有|的)?(?:什么|哪些|包括哪些).{0,12}(?:具体)?\s*(?:的)?\s*(?:设备|装备)",
    re.I,
)


@dataclass(frozen=True)
class HierarchicalFocus:
    parent_type: str
    child_name: str
    raw_span: str

    @property
    def has_parent(self) -> bool:
        return bool((self.parent_type or "").strip())


def _clean_token(s: str) -> str:
    t = re.sub(r"\s+", "", (s or "").strip())
    return re.sub(r"(的|下|中|都|有|哪些|什么|包括|具体|类型|类别)$", "", t).strip()


def parse_equipment_type_hierarchy(question: str) -> Optional[HierarchicalFocus]:
    """Extract parent Type + child Name from questions like 「航空装备下无人机有什么具体装备」."""
    q = re.sub(r"\s+", "", (question or "").strip())
    if not q:
        return None

    for pat in (_PARENT_CHILD_UNDER, _PARENT_CHILD_EQUIP_TAIL):
        m = pat.search(q)
        if m:
            parent = _clean_token(m.group(1))
            child = _clean_token(m.group(2))
            if len(parent) >= 2 and len(child) >= 2 and parent != child:
                if child in ("什么", "哪些", "装备", "设备"):
                    continue
                return HierarchicalFocus(
                    parent_type=parent,
                    child_name=child,
                    raw_span=m.group(0),
                )

    m = _CHILD_ONLY.match(q)
    if m:
        child = _clean_token(m.group(1))
        if len(child) >= 2:
            return HierarchicalFocus(parent_type="", child_name=child, raw_span=child)

    return None


def apply_hierarchy_to_params(params: dict, question: str, *, overwrite: bool = True) -> dict:
    """Merge hierarchy parse result into params_draft."""
    out = dict(params or {})
    hf = parse_equipment_type_hierarchy(question)
    if not hf:
        return out

    if hf.child_name and (overwrite or not out.get("equipment_type_keywords")):
        out["equipment_type_keywords"] = [hf.child_name]
        out["focus"] = hf.child_name

    if hf.has_parent and (overwrite or not out.get("equipment_type_parent")):
        parent = hf.parent_type
        if isinstance(out.get("equipment_type_parent"), list):
            out["equipment_type_parent"] = [parent]
        else:
            out["equipment_type_parent"] = parent

    return out


__all__ = [
    "HierarchicalFocus",
    "apply_hierarchy_to_params",
    "parse_equipment_type_hierarchy",
]
