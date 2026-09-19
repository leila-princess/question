"""法规标准：按章节写入 Chroma（与 ZNWD4 chroma_section_expand 对齐）。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .schema import SECTION_CHROMA_MAX_CHARS, SECTION_CHROMA_OVERLAP

logger = logging.getLogger(__name__)


def _sanitize_section_no(raw: str) -> str:
    s = (raw or "").strip().lstrip(".")
    return s


def _split_section_text(text: str, max_chars: int, overlap: int) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_chars:
        return [t]
    parts: List[str] = []
    start = 0
    while start < len(t):
        end = min(len(t), start + max_chars)
        if end < len(t):
            cut = t.rfind("\n", start, end)
            if cut <= start:
                cut = end
        else:
            cut = end
        chunk = t[start:cut].strip()
        if chunk:
            parts.append(chunk)
        if cut >= len(t):
            break
        start = max(cut - overlap, start + 1)
    return parts


def _infer_slot_type(section_title: str, section_no: str) -> str:
    title = section_title or ""
    from blueprints.FLGL.GetTestMethod.section_extract_config import title_keywords

    for slot in ("sytj", "sygc", "jgpd"):
        for kw in title_keywords(slot):
            if kw in title:
                return slot
    return ""


def delete_doc_chunks(collection: Any, doc_id: str) -> int:
    if collection is None or not doc_id:
        return 0
    try:
        results = collection.get(where={"doc_id": doc_id})
        ids = results.get("ids") or []
        if ids:
            collection.delete(ids=ids)
        return len(ids)
    except Exception as e:
        logger.warning("[SectionChroma] delete doc %s failed: %s", doc_id, e)
        return 0


def index_sections_to_chroma(
    collection: Any,
    *,
    doc_id: str,
    file_id: str,
    title: str,
    category: str,
    std_code: str,
    sections: List[Dict[str, Any]],
    minio_source: str = "",
    replace_existing: bool = True,
    node_slices: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "success": False,
        "doc_id": doc_id,
        "chunks": 0,
        "chunk_ids": [],
        "deleted": 0,
        "error": None,
    }
    if collection is None:
        result["error"] = "Chroma collection not initialized"
        return result
    if not sections:
        result["error"] = "No sections to index"
        return result

    if replace_existing:
        result["deleted"] = delete_doc_chunks(collection, doc_id)

    node_slices = node_slices or {}
    chunk_ids: List[str] = []
    indexed = 0

    for sec in sections:
        section_no = _sanitize_section_no(str(sec.get("section_no") or ""))
        section_title = str(sec.get("title") or "").strip()
        sid = str(sec.get("section_id") or "")
        text = str(sec.get("text") or "").strip()
        if not text and sid and sid in node_slices:
            text = re.sub(r"<[^>]+>", " ", str(node_slices[sid]))
            text = re.sub(r"\s+", " ", text).strip()
        if not text and sid and category and doc_id:
            try:
                from blueprints.ZSGL.WJGL1.document_parse_service import load_section_html_from_minio

                html = load_section_html_from_minio(category, doc_id, sid)
                if html:
                    text = re.sub(r"<[^>]+>", " ", html)
                    text = re.sub(r"\s+", " ", text).strip()
            except Exception:
                pass
        if not text and sec.get("html"):
            text = re.sub(r"<[^>]+>", " ", str(sec.get("html")))
            text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue

        slot_type = _infer_slot_type(section_title, section_no)
        sub_chunks = _split_section_text(text, SECTION_CHROMA_MAX_CHARS, SECTION_CHROMA_OVERLAP)

        for sub_idx, chunk_text in enumerate(sub_chunks):
            sec_key = (section_no or sec.get("section_id") or str(indexed)).replace(".", "_").replace(" ", "_")
            chunk_id = f"{doc_id}_sec_{sec_key}_{sub_idx}"
            meta = {
                "doc_id": str(doc_id or ""),
                "file_id": str(file_id or ""),
                "title": str(title or "")[:200],
                "std_code": str(std_code or "")[:80],
                "section_id": str(sid or ""),
                "section_no": str(section_no or ""),
                "section": str(section_no or ""),
                "section_title": str(section_title or "")[:120],
                "slot_type": str(slot_type or ""),
                "chunk_index": int(indexed),
                "chunk_in_section": int(sub_idx),
                "type": str(category or "")[:40],
                "source_file": str(minio_source or "")[:200],
                "content_html_key": str(sec.get("content_html_key") or "")[:200],
            }
            try:
                collection.add(ids=[chunk_id], documents=[chunk_text], metadatas=[meta])
                chunk_ids.append(chunk_id)
                indexed += 1
            except Exception as e:
                logger.error("[SectionChroma] add %s failed: %s", chunk_id, e)
                result["error"] = str(e)
                result["chunks"] = len(chunk_ids)
                result["chunk_ids"] = chunk_ids
                return result

    result["success"] = indexed > 0
    result["chunks"] = len(chunk_ids)
    result["chunk_ids"] = chunk_ids
    logger.info(
        "[SectionChroma] doc_id=%s indexed=%d deleted=%d",
        doc_id, len(chunk_ids), result["deleted"],
    )
    return result
