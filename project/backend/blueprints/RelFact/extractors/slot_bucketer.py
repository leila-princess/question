"""将 sections 分桶到 sytj / sygc / jgpd（复用 FLGL 标题关键词配置）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from blueprints.FLGL.GetTestMethod.section_extract_config import (
    exclude_title_keywords,
    title_keywords,
)

DECIMAL_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s*(.*)$")


def _title_matches(title: str, keywords: tuple) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    for kw in keywords:
        if kw and kw in t:
            return True
    return False


def _is_excluded(title: str) -> bool:
    t = (title or "").strip()
    for kw in exclude_title_keywords():
        if kw and kw in t:
            return True
    return False


def bucket_sections_for_slots(sections: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {"sytj": [], "sygc": [], "jgpd": []}
    if not sections:
        return buckets

    current_slot: Optional[str] = None
    anchor_slot: Optional[str] = None

    for sec in sections:
        title = str(sec.get("title") or "").strip()
        if _is_excluded(title):
            current_slot = None
            continue

        matched: Optional[str] = None
        for slot in ("sytj", "sygc", "jgpd"):
            if _title_matches(title, title_keywords(slot)):
                matched = slot
                anchor_slot = slot
                break

        if matched:
            current_slot = matched
            buckets[current_slot].append(sec)
            continue

        if current_slot or anchor_slot:
            slot = current_slot or anchor_slot
            if slot:
                buckets[slot].append(sec)

    return buckets


def pick_primary_section(bucket: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not bucket:
        return None
    for sec in bucket:
        if str(sec.get("section_no") or "").strip():
            return sec
    return bucket[0]


def section_clause_no(sec: Dict[str, Any]) -> str:
    raw = str(sec.get("section_no") or "").strip()
    if raw:
        return raw.lstrip(".")
    title = str(sec.get("title") or "")
    m = DECIMAL_HEADING_RE.match(title)
    return m.group(1) if m else ""


def excerpt_from_section(sec: Dict[str, Any], max_len: int = 200) -> str:
    text = re.sub(r"\s+", " ", str(sec.get("text") or "")).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
