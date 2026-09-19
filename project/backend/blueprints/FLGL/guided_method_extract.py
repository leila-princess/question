"""引导式 LLM 方法提取：文档记忆 + 用户检阅 + 锚点程序化取文。"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from flask import Response, current_app, jsonify, request

from blueprints.FLGL.guided_prompts import (
    GUIDED_CHUNK_MEMORY_PROMPT,
    GUIDED_CRITIQUE_METHODS_PROMPT,
    GUIDED_LIST_METHODS_PROMPT,
    GUIDED_MERGE_MEMORY_PROMPT,
    GUIDED_MERGE_METHODS_PROMPT,
    GUIDED_REFINE_ANCHOR_PROMPT,
    GUIDED_UNDERSTAND_PROMPT,
)

logger = logging.getLogger(__name__)


def _backend_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def guided_session_root(session_id: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", str(session_id or "").strip())[:80]
    return os.path.join(_backend_root(), "output_json", "flgl_guided_extract", safe)


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_json(path: str, default: Any = None) -> Any:
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _append_feedback_log(session_dir: str, entry: dict) -> None:
    path = os.path.join(session_dir, "feedback_log.jsonl")
    os.makedirs(session_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _inject_llm_runtime(app=None) -> None:
    try:
        from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import inject_flask_runtime_into_word_llm

        flask_app = app or current_app._get_current_object()
        inject_flask_runtime_into_word_llm(flask_app)
    except Exception as exc:
        logger.warning("[guided] 注入 Flask runtime 失败: %s", exc)


def _ensure_llm_client(app=None) -> None:
    _inject_llm_runtime(app)
    from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import _get_main_config

    if getattr(_get_main_config(), "OPENAI_CLIENT", None) is None:
        raise RuntimeError(
            "LLM 客户端未初始化。请确认 OPENAI_API_KEY / OPENAI_BASE_URL 已配置，"
            "且后端已成功创建 OPENAI_CLIENT。"
        )


def _catalogue_from_sections(sections: List[dict], limit: int = 100) -> List[dict]:
    out = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        text = str(sec.get("text") or "").replace("\n", " ")[:120]
        out.append({
            "section_id": sec.get("section_id"),
            "section_no": sec.get("section_no") or "",
            "title": str(sec.get("title") or "")[:80],
            "level": sec.get("level"),
            "snippet": text,
        })
        if len(out) >= limit:
            break
    return out


def _compact_catalogue_json(catalogue: List[dict], limit: int = 5000) -> str:
    return json.dumps(catalogue, ensure_ascii=False, separators=(",", ":"))[:limit]


def _parent_section_no_for_catalogue(section_no: str) -> str:
    from blueprints.FLGL.method_routes import _normalize_section_no

    parts = [p for p in _normalize_section_no(section_no).split(".") if p]
    if len(parts) <= 1:
        return ""
    return ".".join(parts[:-1])


def _section_no_sort_key(section_no: str) -> tuple:
    from blueprints.FLGL.method_routes import _normalize_section_no

    parts = [p for p in _normalize_section_no(section_no).split(".") if p]
    key: List[Any] = []
    for p in parts:
        try:
            key.append(int(p))
        except ValueError:
            key.append(p)
    return tuple(key)


def _direct_child_section_nos(parent_sno: str, all_nos: List[str]) -> List[str]:
    from blueprints.FLGL.method_routes import _normalize_section_no

    parent = _normalize_section_no(parent_sno)
    parent_depth = _section_no_depth(parent) if parent else 0
    target_depth = parent_depth + 1 if parent else 1
    children: List[str] = []
    for sno in all_nos:
        norm = _normalize_section_no(sno)
        if not norm or _section_no_depth(norm) != target_depth:
            continue
        if _parent_section_no_for_catalogue(norm) == parent:
            children.append(norm)
    return sorted(children, key=_section_no_sort_key)


def _catalogue_for_methods(sections: List[dict], limit: int = 200) -> List[dict]:
    """完整章节目录 + 层级元数据，供 LLM 自行推断方法粒度。"""
    all_nos = [
        str(sec.get("section_no") or "").strip()
        for sec in (sections or [])
        if str(sec.get("section_no") or "").strip()
    ]
    out: List[dict] = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or "")
        sno = str(sec.get("section_no") or "").strip()
        if title.startswith("|") or title.startswith("｜"):
            continue
        if not sno and not title:
            continue
        if re.match(r"^[（(]\d+[）)]", title.strip()):
            continue
        text = str(sec.get("text") or "").replace("\n", " ")[:120]
        out.append({
            "section_id": sec.get("section_id"),
            "section_no": sno,
            "title": title[:80],
            "level": sec.get("level"),
            "snippet": text,
            "depth": _section_no_depth(sno) if sno else int(sec.get("level") or 1),
            "parent_section_no": _parent_section_no_for_catalogue(sno) if sno else "",
            "child_section_nos": _direct_child_section_nos(sno, all_nos)[:12] if sno else [],
        })
        if len(out) >= limit:
            break
    return out


def _is_paren_only_title(title: str) -> bool:
    t = str(title or "").strip()
    return bool(re.match(r"^[（(]\d+[）)]", t))


def _has_valid_method_anchor(raw: dict) -> bool:
    b_start = raw.get("boundary_start") if isinstance(raw.get("boundary_start"), dict) else {}
    nos = [str(n).strip() for n in (b_start.get("section_nos") or []) if str(n).strip()]
    if nos:
        return True
    anchor_nos = [str(n).strip() for n in (raw.get("anchor_section_nos") or []) if str(n).strip()]
    if anchor_nos:
        return True
    anchor_ids = [str(x).strip() for x in (raw.get("anchor_section_ids") or []) if str(x).strip()]
    if anchor_ids:
        return True
    included = [str(x).strip() for x in (raw.get("included_section_ids") or []) if str(x).strip()]
    return bool(included)


def _is_granular_method_candidate(raw: dict) -> bool:
    """
    卫生层过滤：表格碎片、纯列表项标题、无锚点且 excerpt 过短。
    返回 True 表示应丢弃。不按节号深度或文档类型做业务判断。
    """
    if not isinstance(raw, dict):
        return True

    display = str(raw.get("display_name") or "").strip()
    short = str(raw.get("method_short_name") or "").strip()
    if _is_paren_only_title(display) or _is_paren_only_title(short):
        return True

    excerpt = str(raw.get("memory_excerpt") or "").strip()
    if excerpt.startswith("|") or excerpt.startswith("｜"):
        return True

    if not _has_valid_method_anchor(raw):
        if len(excerpt) < 40 and not str(raw.get("project_name") or "").strip():
            return True
    return False


def _filter_granular_method_candidates(methods: List[dict]) -> List[dict]:
    out: List[dict] = []
    dropped = 0
    for m in methods or []:
        if not isinstance(m, dict):
            continue
        if _is_granular_method_candidate(m):
            dropped += 1
            continue
        out.append(m)
    if dropped:
        logger.info("[guided] 过滤过小粒度方法候选 %d 条，保留 %d 条", dropped, len(out))
    return out


def _normalize_title_for_match(title: str) -> str:
    return re.sub(r"\s+", "", str(title or "")).lower()


def _section_no_depth(section_no: str) -> int:
    sno = str(section_no or "").strip().lstrip(".")
    if not sno:
        return 0
    return len([p for p in sno.split(".") if p])


def _shallowest_section_no(nos: List[str]) -> str:
    valid = [str(n).strip() for n in nos if str(n).strip()]
    if not valid:
        return ""
    return min(valid, key=lambda n: (_section_no_depth(n) or 99, n))


def _parent_section_no(section_no: str) -> str:
    from blueprints.FLGL.method_routes import _normalize_section_no

    parts = [p for p in _normalize_section_no(section_no).split(".") if p]
    if len(parts) <= 1:
        return _normalize_section_no(section_no)
    return ".".join(parts[:-1])


def _is_indicator_scope(method_scope: str) -> bool:
    return str(method_scope or "").strip() in ("indicator_method", "sub_project")


def _section_no_under_root(section_no: str, root_no: str) -> bool:
    from blueprints.FLGL.method_routes import _normalize_section_no

    child = _normalize_section_no(section_no)
    root = _normalize_section_no(root_no)
    if not child or not root:
        return False
    if child == root:
        return True
    return child.startswith(root + ".")


def _boundary_root_no(candidate: dict) -> str:
    method_scope = str(candidate.get("method_scope") or "").strip()
    b_start = candidate.get("boundary_start") if isinstance(candidate.get("boundary_start"), dict) else {}
    nos = list(b_start.get("section_nos") or [])
    if nos:
        root = _shallowest_section_no(nos)
        if method_scope == "full_project" and _section_no_depth(root) >= 3:
            root = _parent_section_no(root)
        return root
    anchor_nos = list(candidate.get("anchor_section_nos") or [])
    if anchor_nos:
        root = _shallowest_section_no(anchor_nos)
        if method_scope == "full_project" and _section_no_depth(root) >= 3:
            root = _parent_section_no(root)
        return root
    return ""


def _filter_included_section_ids(
    included_ids: List[str],
    sections: List[dict],
    root_no: str,
) -> List[str]:
    if not included_ids:
        return []
    if not root_no:
        return list(included_ids)
    sec_map = {str(s.get("section_id")): s for s in sections if s.get("section_id")}
    out: List[str] = []
    for sid in included_ids:
        sec = sec_map.get(str(sid))
        if not sec:
            continue
        sno = str(sec.get("section_no") or "")
        if _section_no_under_root(sno, root_no):
            out.append(str(sid))
    return out


def _fix_anchor_sections(
    candidate: dict,
    sections: List[dict],
) -> tuple:
    root_no = _boundary_root_no(candidate)
    hint = " ".join(
        x for x in (
            str(candidate.get("project_name") or ""),
            str(candidate.get("display_name") or ""),
        )
        if x
    )
    anchor_nos = list(candidate.get("anchor_section_nos") or [])
    method_scope = str(candidate.get("method_scope") or "").strip()

    if root_no:
        anchor_nos = [root_no] + [n for n in anchor_nos if n != root_no]
    elif method_scope == "full_project" and anchor_nos:
        shallow = _shallowest_section_no(anchor_nos)
        if _section_no_depth(shallow) >= 3:
            parent = _parent_section_no(shallow)
            if parent:
                anchor_nos = [parent] + [n for n in anchor_nos if n != parent]

    sec_map = {str(s.get("section_id")): s for s in sections if s.get("section_id")}
    anchor_ids: List[str] = []
    for sid in candidate.get("anchor_section_ids") or []:
        sec = sec_map.get(str(sid))
        if not sec:
            continue
        sno = str(sec.get("section_no") or "")
        if root_no and not _section_no_under_root(sno, root_no):
            continue
        anchor_ids.append(str(sid))

    if root_no and not anchor_ids:
        sec = _pick_best_section_for_no(sections, root_no, hint=hint)
        if sec:
            anchor_ids = [str(sec.get("section_id") or "")]

    return anchor_ids, anchor_nos


def _method_dedupe_key(m: dict) -> str:
    b_start = m.get("boundary_start") if isinstance(m.get("boundary_start"), dict) else {}
    nos = list(b_start.get("section_nos") or [])
    if nos:
        return f"anchor:{_shallowest_section_no(nos)}"
    anchor_nos = [str(n).strip() for n in (m.get("anchor_section_nos") or []) if str(n).strip()]
    if anchor_nos:
        return f"anchor:{_shallowest_section_no(anchor_nos)}"
    included = [str(x).strip() for x in (m.get("included_section_ids") or []) if str(x).strip()]
    if included:
        return f"inc:{included[0]}"
    scope = str(m.get("method_scope") or "").strip()
    if _is_indicator_scope(scope):
        short = re.sub(r"\s+", "", str(m.get("method_short_name") or ""))
        if short:
            return f"ind:{short}"
    key = re.sub(r"\s+", "", str(m.get("project_name") or m.get("display_name") or ""))
    if not key:
        return "__anon_"
    return key


def _guided_chunk_shell_len(
    heading_outline: str,
    catalogue_json: str,
    chunk_index: int,
    chunk_total: int,
) -> int:
    return len(
        GUIDED_CHUNK_MEMORY_PROMPT.format(
            heading_outline=heading_outline,
            section_catalogue_json=catalogue_json,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            chunk_text="",
        )
    )


def _guided_understand_shell_len(
    heading_outline: str,
    catalogue_json: str,
    chunk_index: int,
    chunk_total: int,
) -> int:
    return _guided_chunk_shell_len(heading_outline, catalogue_json, chunk_index, chunk_total)


def _parse_guided_understand_data(data: Any) -> tuple:
    if not isinstance(data, dict):
        return "", []
    memory = str(
        data.get("document_memory_markdown")
        or data.get("document_memory")
        or data.get("chunk_memory")
        or data.get("memory")
        or ""
    ).strip()
    methods_raw = list(
        data.get("methods")
        or data.get("chunk_methods")
        or data.get("method_candidates")
        or []
    )
    return memory, [m for m in methods_raw if isinstance(m, dict)]


def _call_guided_llm_json(prompt: str) -> tuple:
    from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import _call_llm, _safe_json_loads

    raw = _call_llm(prompt)
    data = _safe_json_loads(raw or "", {})
    if not isinstance(data, dict):
        data = {}
    return data, str(raw or "")


def _normalize_candidate(raw: dict, idx: int, sections: Optional[List[dict]] = None) -> dict:
    cid = str(raw.get("candidate_id") or f"C{idx:03d}")
    project = str(raw.get("project_name") or "").strip()
    short = str(raw.get("method_short_name") or "").strip()
    method_scope = str(raw.get("method_scope") or "").strip()
    display = str(raw.get("display_name") or "").strip()
    if method_scope == "full_project" and project:
        display = project
        short = ""
    elif not display and project and short:
        display = f"{project}_{short}"
    elif not display:
        display = short or project or f"方法{idx}"

    b_start = raw.get("boundary_start") if isinstance(raw.get("boundary_start"), dict) else {}
    b_end = raw.get("boundary_end") if isinstance(raw.get("boundary_end"), dict) else {}

    pre = {
        **raw,
        "project_name": project,
        "method_short_name": short,
        "method_scope": method_scope,
        "display_name": display,
        "boundary_start": b_start,
        "boundary_end": b_end,
    }
    root_no = _boundary_root_no(pre)
    included = list(raw.get("included_section_ids") or [])
    anchor_ids = list(raw.get("anchor_section_ids") or [])
    anchor_nos = list(raw.get("anchor_section_nos") or [])
    section_mapping = raw.get("section_mapping") if isinstance(raw.get("section_mapping"), dict) else {}
    semantic_slots = raw.get("semantic_slots") if isinstance(raw.get("semantic_slots"), dict) else {}

    if sections:
        included = _filter_included_section_ids(included, sections, root_no)
        anchor_ids, anchor_nos = _fix_anchor_sections(pre, sections)
        if root_no and section_mapping:
            filtered_map: dict = {}
            for role, ids in section_mapping.items():
                if isinstance(ids, list):
                    filtered_map[role] = _filter_included_section_ids(
                        [str(x) for x in ids], sections, root_no,
                    )
                else:
                    filtered_map[role] = ids
            section_mapping = filtered_map

    return {
        "candidate_id": cid,
        "status": str(raw.get("status") or "pending"),
        "display_name": display,
        "project_name": project,
        "method_short_name": short,
        "method_scope": method_scope,
        "organization_hint": str(raw.get("organization_hint") or ""),
        "boundary_start": b_start,
        "boundary_end": b_end,
        "included_section_ids": included,
        "section_mapping": section_mapping,
        "anchor_section_ids": anchor_ids,
        "anchor_section_nos": anchor_nos,
        "span_hint": str(raw.get("span_hint") or ""),
        "memory_excerpt": str(raw.get("memory_excerpt") or ""),
        "user_feedback": str(raw.get("user_feedback") or ""),
        "confidence": float(raw.get("confidence") or 0.5),
        "reasoning": str(raw.get("reasoning") or ""),
        "granularity_rationale": str(raw.get("granularity_rationale") or ""),
        "structure_profile_ref": str(raw.get("structure_profile_ref") or ""),
        "semantic_slots": semantic_slots,
        "materialized": raw.get("materialized") or {},
    }


def _load_session(session_id: str) -> Optional[dict]:
    root = guided_session_root(session_id)
    meta = _read_json(os.path.join(root, "session.json"))
    if not meta:
        return None
    meta["session_id"] = session_id
    meta["session_dir"] = root
    meta["parse_cache"] = _read_json(os.path.join(root, "parse_cache.json"), {})
    meta["candidates"] = _read_json(os.path.join(root, "method_candidates.json"), [])
    memory_path = os.path.join(root, "document_memory.md")
    if os.path.isfile(memory_path):
        with open(memory_path, encoding="utf-8") as f:
            meta["document_memory"] = f.read()
    else:
        meta["document_memory"] = ""
    profile_path = os.path.join(root, "structure_profile.json")
    meta["structure_profile"] = _read_json(profile_path, {})
    meta["review_notes"] = str(meta.get("review_notes") or "")
    return meta


def _save_session_meta(session_dir: str, meta: dict) -> None:
    _write_json(os.path.join(session_dir, "session.json"), meta)


def _save_candidates(session_dir: str, candidates: List[dict]) -> None:
    _write_json(os.path.join(session_dir, "method_candidates.json"), candidates)


_METHOD_TABLE_TO_BODY_ALIASES: Dict[str, List[str]] = {
    "低温工作试验": ["（1）低温试验（非工作状态）", "低温试验（非工作状态）"],
    "高温工作试验": ["（3）高温试验（非工作状态）", "高温试验（非工作状态）"],
    "低温贮存试验": ["低温贮存试验"],
    "高温贮存试验": ["高温贮存试验"],
    "低气压试验": ["（2）低温低气压工作测试", "低温低气压工作测试"],
}


def _is_table_fragment_section(sec: dict) -> bool:
    title = str(sec.get("title") or "").strip()
    return title.startswith("|") or title.startswith("｜")


def _is_table_fragment_candidate(candidate: dict, sections: List[dict]) -> bool:
    excerpt = str(candidate.get("memory_excerpt") or "").strip()
    if excerpt.startswith("|") or excerpt.startswith("｜"):
        return True
    sec_map = {str(s.get("section_id")): s for s in sections if s.get("section_id")}
    for sid in candidate.get("anchor_section_ids") or []:
        sec = sec_map.get(str(sid))
        if sec and _is_table_fragment_section(sec):
            return True
    return False


def _chapter_start_offset(sections: List[dict], chapter: str) -> int:
    from blueprints.FLGL.method_routes import _normalize_section_no

    target = _normalize_section_no(chapter)
    starts: List[int] = []
    for sec in sections:
        if _normalize_section_no(str(sec.get("section_no") or "")) != target:
            continue
        try:
            starts.append(int(sec.get("start_offset") or 0))
        except (TypeError, ValueError):
            continue
    return min(starts) if starts else 0


def _find_paren_list_item_span(
    full_text: str,
    keyword: str,
    min_offset: int = 0,
) -> Optional[tuple]:
    pos = full_text.find(keyword, min_offset)
    if pos < 0:
        return None
    line_start = full_text.rfind("\n", 0, pos)
    line_start = 0 if line_start < 0 else line_start + 1
    prefix = full_text[line_start:pos]
    m = re.search(r"（\d+）", prefix)
    if m:
        line_start = line_start + m.start()
    rest = full_text[pos + len(keyword):]
    end_rel = len(rest)
    m_next = re.search(r"\n（\d+）", rest)
    if m_next:
        end_rel = m_next.start()
    else:
        m_dec = re.search(r"\n\d+\.\d+(?:\.\d+)?\s", rest)
        if m_dec:
            end_rel = m_dec.start()
    end = min(pos + len(keyword) + end_rel, len(full_text))
    return line_start, end


def _method_body_min_offset(sections: List[dict], full_text: str) -> int:
    """试验方法正文搜索起点：取章节 offset 与正文中 8.x 标题位置的较早者。"""
    off = _chapter_start_offset(sections, "8")
    text_markers: List[int] = []
    for pat in (r"\n8\.2\.3\s", r"\n8\.\d+\s", r"\n8\s"):
        m = re.search(pat, full_text)
        if m:
            text_markers.append(m.start() + 1)
    text_off = min(text_markers) if text_markers else 0
    if off > 0 and text_off > 0:
        return min(off, text_off)
    return off or text_off


def _remap_table_fragment_candidate(
    candidate: dict,
    sections: List[dict],
    full_text: str,
) -> dict:
    if not _is_table_fragment_candidate(candidate, sections):
        return candidate
    short = str(candidate.get("method_short_name") or "").strip()
    aliases = list(_METHOD_TABLE_TO_BODY_ALIASES.get(short, []))
    if short and short not in aliases:
        aliases.insert(0, short)
    min_off = _method_body_min_offset(sections, full_text)
    for alias in aliases:
        if not alias:
            continue
        span = _find_paren_list_item_span(full_text, alias, min_offset=min_off)
        if span:
            start, end = span
            return {
                **candidate,
                "_keyword_span": {"start": start, "end": end, "keyword": alias},
            }
    for needle in (f"| {short} |", short):
        if not needle.strip("| ").strip():
            continue
        pos = full_text.find(needle)
        if pos < 0:
            continue
        line_start = full_text.rfind("\n", 0, pos) + 1
        line_end = full_text.find("\n", pos + len(needle))
        if line_end < 0:
            line_end = len(full_text)
        return {
            **candidate,
            "_keyword_span": {"start": line_start, "end": line_end, "keyword": needle},
        }
    return candidate


def _section_item_from_sec(sec: dict) -> dict:
    return {
        "section_id": str(sec.get("section_id") or ""),
        "sectionNo": str(sec.get("section_no") or ""),
        "title": str(sec.get("title") or ""),
        "level": int(sec.get("level") or 1),
    }


from blueprints.FLGL.method_routes import (
    _pick_best_section_for_no,
    _text_offset_for_section_heading,
)


def _resolve_sections_for_anchors(
    sections: List[dict],
    anchor_ids: List[str],
    anchor_nos: List[str],
    *,
    hint: str = "",
    candidate: Optional[dict] = None,
) -> List[dict]:
    sec_map = {str(s.get("section_id")): s for s in sections if s.get("section_id")}
    by_id: List[dict] = []
    for sid in anchor_ids or []:
        sec = sec_map.get(str(sid))
        if not sec or _is_table_fragment_section(sec):
            continue
        by_id.append(_section_item_from_sec(sec))

    nos = list(anchor_nos or [])
    if candidate and _is_table_fragment_candidate(candidate, sections):
        nos = [n for n in nos if not str(n).strip().startswith("7.")]

    by_no: List[dict] = []
    seen_ids: set = set()
    for sno in nos:
        sec = _pick_best_section_for_no(sections, str(sno or ""), hint=hint)
        if not sec:
            continue
        sid = str(sec.get("section_id") or "")
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        by_no.append(_section_item_from_sec(sec))

    id_depth = max((_section_no_depth(x["sectionNo"]) for x in by_id), default=0)
    no_depth = max((_section_no_depth(n) for n in nos if str(n).strip()), default=0)

    if by_no and (not by_id or no_depth > id_depth):
        return by_no
    if by_id:
        return by_id
    return by_no


def _offset_from_boundary_side(
    side: dict,
    sections: List[dict],
    full_text: str,
    *,
    hint: str = "",
    min_offset: int = 0,
    pick: str = "start",
) -> Optional[int]:
    """从 boundary_start/end 解析 offset。"""
    if not isinstance(side, dict):
        return None
    from blueprints.FLGL.method_routes import (
        _compute_section_span_end_offset,
        _span_start_for_decimal_section,
    )

    offsets: List[int] = []
    sec_map = {str(s.get("section_id")): s for s in sections if s.get("section_id")}

    for sid in side.get("section_ids") or []:
        sec = sec_map.get(str(sid))
        if not sec:
            continue
        try:
            off = int(sec.get("start_offset") or 0)
            if off >= min_offset:
                offsets.append(off)
        except (TypeError, ValueError):
            continue

    for sno in side.get("section_nos") or []:
        sec = _pick_best_section_for_no(sections, str(sno or ""), hint=hint)
        if not sec:
            continue
        if pick == "start":
            off = _span_start_for_decimal_section(sec, sections)
        else:
            try:
                off = int(sec.get("start_offset") or 0)
            except (TypeError, ValueError):
                off = 0
        if off >= min_offset:
            offsets.append(off)

    title_hint = str(side.get("title_hint") or "").strip()
    if title_hint:
        hint_n = _normalize_title_for_match(title_hint)
        for sec in sections:
            try:
                off = int(sec.get("start_offset") or 0)
            except (TypeError, ValueError):
                continue
            if off < min_offset:
                continue
            title_n = _normalize_title_for_match(str(sec.get("title") or ""))
            if hint_n and (hint_n in title_n or title_n.startswith(hint_n[:8])):
                offsets.append(off)

    text_anchor = str(side.get("text_anchor") or "").strip()
    if text_anchor:
        pos = full_text.find(text_anchor, min_offset)
        if pos >= 0:
            line_start = full_text.rfind("\n", 0, pos)
            offsets.append(0 if line_start < 0 else line_start + 1)

    if not offsets:
        return None
    return min(offsets) if pick == "start" else min(offsets)


def _resolve_indicator_boundary_span(
    candidate: dict,
    sections: List[dict],
    full_text: str,
    *,
    hint: str = "",
) -> Optional[dict]:
    """指标/子项目方法：按 boundary 节号在 full_text 上取首次标题到下一同级标题。"""
    method_scope = str(candidate.get("method_scope") or "").strip()
    if not _is_indicator_scope(method_scope):
        return None

    b_start = candidate.get("boundary_start") if isinstance(candidate.get("boundary_start"), dict) else {}
    b_end = candidate.get("boundary_end") if isinstance(candidate.get("boundary_end"), dict) else {}
    anchor_nos = list(candidate.get("anchor_section_nos") or [])

    start_no = _shallowest_section_no(list(b_start.get("section_nos") or []))
    if not start_no:
        start_no = _boundary_root_no(candidate)
    if not start_no and anchor_nos:
        start_no = _shallowest_section_no(anchor_nos)
    if not start_no:
        return None

    start = _text_offset_for_section_heading(
        full_text,
        start_no,
        title_hint=str(b_start.get("title_hint") or ""),
    )
    if start is None:
        start = _offset_from_boundary_side(
            b_start, sections, full_text, hint=hint, pick="start",
        )
    if start is None:
        return None

    ends: List[int] = []
    end_nos = list(b_end.get("section_nos") or [])
    if end_nos:
        end_no = _shallowest_section_no(end_nos)
        end_off = _text_offset_for_section_heading(
            full_text,
            end_no,
            title_hint=str(b_end.get("title_hint") or ""),
            min_offset=start + 1,
        )
        if end_off is not None and end_off > start:
            ends.append(end_off)

    if not ends:
        end_off = _offset_from_boundary_side(
            b_end, sections, full_text, hint=hint, min_offset=start + 1, pick="end",
        )
        if end_off is not None and end_off > start:
            ends.append(end_off)

    if not ends:
        from blueprints.FLGL.method_routes import _compute_section_span_end_offset

        sec = _pick_best_section_for_no(sections, start_no, hint=hint)
        if sec:
            end_i = _compute_section_span_end_offset(
                sec, sections, full_text_len=len(full_text),
            )
            if end_i > start:
                ends.append(end_i)

    end = min(ends) if ends else len(full_text)
    if end <= start:
        return None

    return {
        "start": start,
        "end": end,
        "fetch_path": "indicator_heading_span",
        "chunk_len": end - start,
    }


def _resolve_method_boundary_span(
    candidate: dict,
    sections: List[dict],
    full_text: str,
) -> Optional[dict]:
    """按 LLM boundary 在 full_text 上取连续 span。"""
    if not full_text.strip():
        return None

    hint = " ".join(
        x for x in (
            str(candidate.get("project_name") or ""),
            str(candidate.get("display_name") or ""),
            str(candidate.get("memory_excerpt") or "")[:80],
        )
        if x
    )
    method_scope = str(candidate.get("method_scope") or "").strip()
    b_start = candidate.get("boundary_start") if isinstance(candidate.get("boundary_start"), dict) else {}
    b_end = candidate.get("boundary_end") if isinstance(candidate.get("boundary_end"), dict) else {}
    root_no = _boundary_root_no(candidate)

    indicator_span = _resolve_indicator_boundary_span(
        candidate, sections, full_text, hint=hint,
    )
    if indicator_span:
        return indicator_span

    starts: List[int] = []
    included_ids = _filter_included_section_ids(
        list(candidate.get("included_section_ids") or []),
        sections,
        root_no,
    )
    for sid in included_ids:
        for sec in sections:
            if str(sec.get("section_id")) != str(sid):
                continue
            try:
                starts.append(int(sec.get("start_offset") or 0))
            except (TypeError, ValueError):
                pass

    start_off = _offset_from_boundary_side(
        b_start, sections, full_text, hint=hint, pick="start",
    )
    if start_off is not None:
        starts.append(start_off)

    anchor_nos = list(candidate.get("anchor_section_nos") or [])
    if method_scope == "full_project" and anchor_nos:
        root_no = _shallowest_section_no(anchor_nos)
        if _section_no_depth(root_no) >= 3:
            root_no = _parent_section_no(root_no)
        sec = _pick_best_section_for_no(sections, root_no, hint=hint)
        if sec:
            from blueprints.FLGL.method_routes import _span_start_for_decimal_section

            starts.append(_span_start_for_decimal_section(sec, sections))

    if not starts and anchor_nos:
        sec = _pick_best_section_for_no(
            sections, _shallowest_section_no(anchor_nos), hint=hint,
        )
        if sec:
            from blueprints.FLGL.method_routes import _span_start_for_decimal_section

            starts.append(_span_start_for_decimal_section(sec, sections))

    if not starts:
        return None

    start = min(starts)
    ends: List[int] = []

    end_off = _offset_from_boundary_side(
        b_end, sections, full_text, hint=hint, min_offset=start + 1, pick="end",
    )
    if end_off is not None and end_off > start:
        ends.append(end_off)

    for sno in list(b_end.get("section_nos") or []):
        sec = _pick_best_section_for_no(sections, str(sno or ""), hint=hint)
        if not sec:
            continue
        title = str(sec.get("title") or "").strip()
        for needle in (title, str(sno or "")):
            if not needle:
                continue
            pos = full_text.find(needle, start + 1)
            if pos >= 0:
                ends.append(pos)
                break

    if not ends:
        root_no = ""
        if b_start.get("section_nos"):
            root_no = _shallowest_section_no(list(b_start.get("section_nos") or []))
        elif anchor_nos:
            root_no = _shallowest_section_no(anchor_nos)
            if method_scope == "full_project" and _section_no_depth(root_no) >= 3:
                root_no = _parent_section_no(root_no)
        if root_no:
            sec = _pick_best_section_for_no(sections, root_no, hint=hint)
            if sec:
                from blueprints.FLGL.method_routes import _compute_section_span_end_offset

                end_i = _compute_section_span_end_offset(
                    sec, sections, full_text_len=len(full_text),
                )
                if end_i > start:
                    ends.append(end_i)

    end = min(ends) if ends else len(full_text)
    if end <= start:
        return None

    return {
        "start": start,
        "end": end,
        "fetch_path": "boundary_span",
        "chunk_len": end - start,
    }


def _should_use_keyword_span(candidate: dict) -> bool:
    scope = str(candidate.get("method_scope") or "").strip()
    org = str(candidate.get("organization_hint") or "").strip()
    if scope == "full_project" or org == "project_block":
        return False
    if candidate.get("boundary_start") or candidate.get("boundary_end"):
        return False
    return True


def _dedupe_methods_by_project(methods: List[dict]) -> List[dict]:
    """程序化去重：按锚点节号区分，禁止仅因 project_name 相同而合并。"""
    buckets: Dict[str, List[dict]] = {}
    anon_idx = 0
    for m in methods:
        if not isinstance(m, dict):
            continue
        key = _method_dedupe_key(m)
        if key.startswith("__anon_"):
            key = f"__anon_{anon_idx}"
            anon_idx += 1
        buckets.setdefault(key, []).append(m)

    def _score(m: dict) -> tuple:
        excerpt = str(m.get("memory_excerpt") or "").strip()
        conf = float(m.get("confidence") or 0)
        b_start = m.get("boundary_start") if isinstance(m.get("boundary_start"), dict) else {}
        score = conf
        if b_start.get("section_nos"):
            score += 3
        if str(m.get("reasoning") or "").strip():
            score += 2
        if excerpt.startswith("|") or excerpt.startswith("｜"):
            score -= 20
        return (score, len(str(m.get("span_hint") or "")))

    out: List[dict] = []
    for _key, group in buckets.items():
        if len(group) == 1:
            out.append(group[0])
        else:
            out.append(max(group, key=_score))
    return out


def _candidate_to_group(candidate: dict, sections: List[dict]) -> dict:
    hint = " ".join(
        x for x in (
            str(candidate.get("method_short_name") or ""),
            str(candidate.get("memory_excerpt") or ""),
            str(candidate.get("display_name") or ""),
        )
        if x
    )
    anchors = _resolve_sections_for_anchors(
        sections,
        candidate.get("anchor_section_ids") or [],
        candidate.get("anchor_section_nos") or [],
        hint=hint,
        candidate=candidate,
    )
    if not anchors:
        return {}
    parent = anchors[0]
    children = anchors[1:] if len(anchors) > 1 else []
    project = str(candidate.get("project_name") or "").strip()
    if project:
        return {
            "parent": parent,
            "children": children,
            "project": {"section_id": "", "title": project},
        }
    return {"parent": parent, "children": children}


def _build_structure_profile(parsed_doc: dict, session: dict, app=None) -> dict:
    """Phase0：复用 word_llm 结构分析与组织形态判断。"""
    from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
        analyze_document_structure,
        llm_judge_method_organization,
    )

    profile = analyze_document_structure(parsed_doc)
    org = llm_judge_method_organization(parsed_doc)
    merged = {
        **(profile if isinstance(profile, dict) else {}),
        "organization_mode": org.get("organization_mode"),
        "organization_confidence": org.get("confidence"),
        "organization_reason": org.get("reason"),
        "organization_warnings": list(org.get("warnings") or []),
        "source_name": str(session.get("source_name") or parsed_doc.get("file_name") or ""),
    }
    return merged


def _fallback_single_method_candidate(sections: List[dict], source_name: str) -> List[dict]:
    """LLM 无输出时的最小兜底。"""
    title = str(source_name or "整文档方法").rsplit(".", 1)[0] or "整文档方法"
    anchor_sec = None
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sno = str(sec.get("section_no") or "").strip()
        stitle = str(sec.get("title") or "")
        if sno or stitle:
            anchor_sec = sec
            break
    anchor_no = str(anchor_sec.get("section_no") or "").strip() if anchor_sec else ""
    anchor_id = str(anchor_sec.get("section_id") or "") if anchor_sec else ""
    return [{
        "display_name": title,
        "project_name": title,
        "method_short_name": "",
        "method_scope": "standalone",
        "reasoning": "LLM 未输出方法候选，使用整文档最小兜底，请用户检阅后调整边界",
        "granularity_rationale": "无 LLM 推测结果时的程序兜底",
        "structure_profile_ref": "fallback",
        "semantic_slots": {},
        "boundary_start": {"section_nos": [anchor_no] if anchor_no else []},
        "boundary_end": {"section_nos": []},
        "anchor_section_ids": [anchor_id] if anchor_id else [],
        "anchor_section_nos": [anchor_no] if anchor_no else [],
        "span_hint": "整文档兜底",
        "confidence": 0.25,
    }]


def _run_understand(
    session: dict,
    on_event: Optional[Callable[[str, dict], None]] = None,
    app=None,
) -> dict:
    _ensure_llm_client(app)
    from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
        _build_heading_outline,
        _llm_chunk_text_char_budget,
        _split_body_into_llm_chunks,
    )

    parse_cache = session.get("parse_cache") or {}
    parsed_doc = dict(parse_cache.get("parsed_document") or {})
    parsed_doc["sections"] = list(parse_cache.get("sections") or [])
    parsed_doc["section_catalogue"] = list(parse_cache.get("section_catalogue") or [])
    full_text = str(
        parsed_doc.get("raw_full_text") or session.get("full_text") or ""
    ).strip()
    parsed_doc["full_text"] = full_text
    parsed_doc["raw_full_text"] = full_text
    parsed_doc["file_name"] = str(session.get("source_name") or "document.docx")
    sections = list(parse_cache.get("sections") or [])
    catalogue = _catalogue_from_sections(sections)
    catalogue_json = _compact_catalogue_json(catalogue)
    methods_catalogue = _catalogue_for_methods(sections)
    methods_catalogue_json = _compact_catalogue_json(methods_catalogue, limit=12000)
    heading_outline = _build_heading_outline(parsed_doc, limit=80)[:3000]

    shell_len = _guided_chunk_shell_len(heading_outline, catalogue_json, 1, 1)
    chunk_budget = _llm_chunk_text_char_budget(prompt_shell_chars=shell_len)
    chunks = _split_body_into_llm_chunks(full_text, sections=sections, chunk_chars=chunk_budget)
    if not chunks:
        chunks = [full_text[:chunk_budget]] if full_text else [""]

    chunk_memories: List[str] = []
    total = len(chunks)
    memory = ""
    document_structure: dict = {}
    methods_raw: List[dict] = []
    review_notes = ""
    understand_trace: List[dict] = []
    session_dir = session["session_dir"]
    debug_dir = os.path.join(session_dir, "understand_debug")
    os.makedirs(debug_dir, exist_ok=True)

    # --- Phase0：结构画像 ---
    if on_event:
        on_event("understanding_progress", {"stage": "structure_profile"})
    structure_profile = _build_structure_profile(parsed_doc, session, app=app)
    _write_json(os.path.join(session_dir, "structure_profile.json"), structure_profile)
    structure_profile_json = json.dumps(structure_profile, ensure_ascii=False)[:4000]
    org_mode = str(structure_profile.get("organization_mode") or "unknown")
    org_reason = str(structure_profile.get("organization_reason") or "")
    understand_trace.append({
        "stage": "structure_profile",
        "organization_mode": org_mode,
        "document_style": structure_profile.get("document_style"),
        "method_unit": structure_profile.get("method_unit"),
    })

    # --- 阶段 A：分片记忆 ---
    for i, chunk in enumerate(chunks, start=1):
        if on_event:
            on_event("understanding_progress", {"chunk_index": i, "chunk_total": total, "stage": "memory"})
        prompt = GUIDED_CHUNK_MEMORY_PROMPT.format(
            heading_outline=heading_outline,
            section_catalogue_json=catalogue_json,
            chunk_index=i,
            chunk_total=total,
            chunk_text=chunk[:chunk_budget],
        )
        data, raw = _call_guided_llm_json(prompt)
        raw_path = os.path.join(debug_dir, f"chunk_{i:02d}_memory_raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw)
        memory_part, _ = _parse_guided_understand_data(data)
        understand_trace.append({
            "stage": "chunk_memory",
            "chunk_index": i,
            "chunk_total": total,
            "prompt_chars": len(prompt),
            "raw_len": len(raw),
            "parsed_keys": list(data.keys()) if isinstance(data, dict) else [],
            "memory_len": len(memory_part),
            "raw_path": raw_path,
        })
        if memory_part:
            chunk_memories.append(memory_part)

    # --- 阶段 B：合并记忆 + 文档结构 ---
    if on_event:
        on_event("understanding_progress", {"stage": "merge_memory"})
    merge_prompt = GUIDED_MERGE_MEMORY_PROMPT.format(
        heading_outline=heading_outline,
        chunk_summaries="\n\n---\n\n".join(chunk_memories)[:12000],
        structure_profile_json=structure_profile_json,
    )
    merged_data, merge_raw = _call_guided_llm_json(merge_prompt)
    with open(os.path.join(debug_dir, "merge_memory_raw.txt"), "w", encoding="utf-8") as f:
        f.write(merge_raw)
    merge_memory, _ = _parse_guided_understand_data(merged_data)
    memory = merge_memory or "\n\n".join(chunk_memories)
    if isinstance(merged_data, dict):
        ds = merged_data.get("document_structure")
        if isinstance(ds, dict):
            document_structure = ds
    understand_trace.append({
        "stage": "merge_memory",
        "prompt_chars": len(merge_prompt),
        "raw_len": len(merge_raw),
        "memory_len": len(memory),
        "document_structure_keys": list(document_structure.keys()),
    })

    # --- 阶段 C：Methods 专调用 ---
    if on_event:
        on_event("understanding_progress", {"stage": "list_methods"})
    methods_prompt = GUIDED_LIST_METHODS_PROMPT.format(
        structure_profile_json=structure_profile_json,
        organization_mode=org_mode,
        organization_reason=org_reason[:500],
        document_memory=memory[:10000],
        document_structure_json=json.dumps(document_structure, ensure_ascii=False)[:3000],
        heading_outline=heading_outline,
        section_catalogue_json=methods_catalogue_json,
    )
    methods_data, methods_raw_text = _call_guided_llm_json(methods_prompt)
    with open(os.path.join(debug_dir, "list_methods_raw.txt"), "w", encoding="utf-8") as f:
        f.write(methods_raw_text)
    if isinstance(methods_data, dict):
        methods_raw = list(methods_data.get("methods") or [])
    understand_trace.append({
        "stage": "list_methods",
        "prompt_chars": len(methods_prompt),
        "raw_len": len(methods_raw_text),
        "methods_count": len(methods_raw),
    })

    # --- 阶段 D：自洽复核 ---
    if methods_raw:
        if on_event:
            on_event("understanding_progress", {"stage": "critique_methods"})
        critique_prompt = GUIDED_CRITIQUE_METHODS_PROMPT.format(
            structure_profile_json=structure_profile_json,
            document_memory=memory[:6000],
            methods_json=json.dumps(methods_raw, ensure_ascii=False)[:12000],
        )
        critique_data, critique_raw = _call_guided_llm_json(critique_prompt)
        with open(os.path.join(debug_dir, "critique_methods_raw.txt"), "w", encoding="utf-8") as f:
            f.write(critique_raw)
        if isinstance(critique_data, dict) and critique_data.get("methods"):
            methods_raw = list(critique_data.get("methods") or [])
            review_notes = str(critique_data.get("review_notes") or "")
        understand_trace.append({
            "stage": "critique_methods",
            "methods_count": len(methods_raw),
            "review_notes": review_notes[:500],
        })

    # --- 阶段 E：LLM merge 去重（候选较多时）---
    if len(methods_raw) > 8:
        merge_m_prompt = GUIDED_MERGE_METHODS_PROMPT.format(
            structure_profile_json=structure_profile_json[:2000],
            document_memory=memory[:4000],
            methods_json=json.dumps(methods_raw, ensure_ascii=False)[:12000],
        )
        merged_m, merged_m_raw = _call_guided_llm_json(merge_m_prompt)
        with open(os.path.join(debug_dir, "merge_methods_raw.txt"), "w", encoding="utf-8") as f:
            f.write(merged_m_raw)
        if isinstance(merged_m, dict) and merged_m.get("methods"):
            methods_raw = list(merged_m.get("methods") or [])
        understand_trace.append({
            "stage": "merge_methods",
            "methods_count": len(methods_raw),
        })

    if not methods_raw:
        methods_raw = _fallback_single_method_candidate(
            sections, str(session.get("source_name") or ""),
        )
        understand_trace.append({
            "stage": "fallback_methods",
            "methods_count": len(methods_raw),
        })

    methods_raw = _filter_granular_method_candidates(methods_raw)
    methods_raw = _dedupe_methods_by_project(methods_raw)

    _write_json(os.path.join(session_dir, "understand_trace.json"), understand_trace)
    _write_json(os.path.join(session_dir, "document_structure.json"), document_structure)

    if not memory.strip() and not methods_raw:
        raise RuntimeError(
            "LLM 返回内容无法解析为文档记忆或方法候选（可能输出被截断或格式不符）。"
            f"请查看 {debug_dir} 下原始响应。"
        )

    with open(os.path.join(session_dir, "document_memory.md"), "w", encoding="utf-8") as f:
        f.write(memory or "")

    candidates = [
        _normalize_candidate(m, idx, sections=sections)
        for idx, m in enumerate(methods_raw or [], start=1)
        if isinstance(m, dict)
    ]
    _save_candidates(session_dir, candidates)

    meta = _read_json(os.path.join(session_dir, "session.json"), {})
    meta["understood_at"] = datetime.now(timezone.utc).isoformat()
    meta["candidate_count"] = len(candidates)
    meta["review_notes"] = review_notes
    _save_session_meta(session_dir, meta)

    result = {
        "document_memory": memory,
        "document_structure": document_structure,
        "structure_profile": structure_profile,
        "review_notes": review_notes,
        "candidates": candidates,
    }
    if on_event:
        on_event("understanding_done", {"candidate_count": len(candidates)})
    return result


def _run_refine_candidate(session: dict, candidate_id: str, app=None) -> dict:
    _ensure_llm_client(app)

    candidates = list(session.get("candidates") or [])
    target = next((c for c in candidates if c.get("candidate_id") == candidate_id), None)
    if not target:
        raise ValueError(f"候选不存在: {candidate_id}")

    parse_cache = session.get("parse_cache") or {}
    sections = list(parse_cache.get("sections") or [])
    catalogue = _catalogue_for_methods(sections)
    catalogue_json = _compact_catalogue_json(catalogue, limit=8000)
    structure_profile = session.get("structure_profile") or _read_json(
        os.path.join(session["session_dir"], "structure_profile.json"), {},
    )
    structure_profile_json = json.dumps(structure_profile, ensure_ascii=False)[:4000]

    prompt = GUIDED_REFINE_ANCHOR_PROMPT.format(
        structure_profile_json=structure_profile_json,
        document_memory=str(session.get("document_memory") or "")[:8000],
        section_catalogue_json=catalogue_json,
        current_candidate_json=json.dumps(target, ensure_ascii=False)[:4000],
        user_feedback=str(target.get("user_feedback") or "请根据文档记忆修正锚点"),
    )
    data, _raw = _call_guided_llm_json(prompt)

    updated = dict(target)
    for key in (
        "anchor_section_ids", "anchor_section_nos", "span_hint", "memory_excerpt",
        "display_name", "project_name", "method_short_name", "confidence",
        "method_scope", "boundary_start", "boundary_end", "included_section_ids",
        "section_mapping", "organization_hint",
        "reasoning", "granularity_rationale", "semantic_slots", "structure_profile_ref",
    ):
        if key in data and data[key] is not None:
            updated[key] = data[key]
    updated["status"] = "pending"
    updated["refine_note"] = str(data.get("refine_note") or "")

    new_candidates = [
        updated if c.get("candidate_id") == candidate_id else c for c in candidates
    ]
    _save_candidates(session["session_dir"], new_candidates)
    _append_feedback_log(session["session_dir"], {
        "type": "refine",
        "candidate_id": candidate_id,
        "feedback": target.get("user_feedback"),
        "result": updated,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    return updated


def _get_neo4j_driver_for_guided(app=None):
    try:
        from blueprints.FLGL.method_routes import _get_neo4j_driver_for_recall

        return _get_neo4j_driver_for_recall()
    except Exception:
        try:
            from flask import current_app

            flask_app = app or current_app._get_current_object()
            return (
                flask_app.config.get("FLGL_TESTMETHOD_NEO4J_DRIVER")
                or flask_app.config.get("NEO4J_DRIVER")
            )
        except Exception:
            return None


def _merged_html_for_boundary_candidate(
    candidate: dict,
    sections: List[dict],
    parse_cache: dict,
    full_text: str,
    boundary: dict,
) -> str:
    """指标方法优先用 boundary 整段 plain→HTML；项目方法尝试 parse_cache 章节 HTML。"""
    from blueprints.FLGL.method_routes import _section_html_from_parse_cache
    from blueprints.FLGL.method_section_enrich import _plain_text_to_method_html

    start = int(boundary.get("start") or 0)
    end = int(boundary.get("end") or len(full_text))
    method_scope = str(candidate.get("method_scope") or "").strip()

    if _is_indicator_scope(method_scope):
        return _plain_text_to_method_html(full_text[start:end])

    hint = " ".join(
        x for x in (
            str(candidate.get("project_name") or ""),
            str(candidate.get("display_name") or ""),
            str(candidate.get("memory_excerpt") or "")[:80],
        )
        if x
    )
    root_no = _boundary_root_no(candidate)
    if root_no:
        sec = _pick_best_section_for_no(sections, root_no, hint=hint)
        if sec:
            html = _section_html_from_parse_cache(
                parse_cache,
                section_id=str(sec.get("section_id") or ""),
                resolved=sec,
                full_text=full_text,
            )
            if html.strip():
                return html

    chunk = full_text[start:end]
    return _plain_text_to_method_html(chunk)


def _run_materialize(session: dict, candidate_ids: Optional[List[str]] = None, app=None) -> List[dict]:
    from blueprints.FLGL.method_routes import _merge_explicit_selected_section_content
    from blueprints.FLGL.method_section_enrich import enrich_section_method, label_from_source_filename

    parse_cache = session.get("parse_cache") or {}
    sections = list(parse_cache.get("sections") or [])
    full_text = str(
        session.get("full_text")
        or (parse_cache.get("parsed_document") or {}).get("raw_full_text")
        or parse_cache.get("full_text")
        or ""
    )
    temp_path = str(session.get("temp_path") or "")
    ext = str(session.get("ext") or "")
    session_dir = session["session_dir"]
    material_dir = os.path.join(session_dir, "materialized")
    os.makedirs(material_dir, exist_ok=True)

    candidates = list(session.get("candidates") or [])
    targets = [
        c for c in candidates
        if c.get("status") == "approved"
        and (not candidate_ids or c.get("candidate_id") in candidate_ids)
    ]

    methods_out: List[dict] = []
    traces: List[dict] = []

    for cand in targets:
        cid = str(cand.get("candidate_id") or "")
        trace_entry: dict = {"candidate_id": cid}
        merged = ""

        boundary = _resolve_method_boundary_span(cand, sections, full_text)
        if boundary:
            merged = _merged_html_for_boundary_candidate(
                cand, sections, parse_cache, full_text, boundary,
            )
            trace_entry.update(boundary)
            trace_entry["span_start"] = boundary["start"]
            trace_entry["span_end"] = boundary["end"]
            trace_entry["fetch_path"] = trace_entry.get("fetch_path") or "boundary_span_html"
        elif _should_use_keyword_span(cand):
            cand_kw = _remap_table_fragment_candidate(cand, sections, full_text)
            keyword_span = cand_kw.get("_keyword_span")
            if keyword_span:
                from blueprints.FLGL.method_section_enrich import _plain_text_to_method_html

                chunk = full_text[int(keyword_span["start"]):int(keyword_span["end"])]
                merged = _plain_text_to_method_html(chunk)
                trace_entry.update({
                    "fetch_path": "keyword_span",
                    "keyword": keyword_span.get("keyword"),
                    "span_start": keyword_span.get("start"),
                    "span_end": keyword_span.get("end"),
                    "chunk_len": len(chunk),
                })

        if not merged.strip():
            group = _candidate_to_group(cand, sections)
            if not group:
                traces.append({**trace_entry, "error": "no_anchor"})
                continue

            merged = _merge_explicit_selected_section_content(
                group,
                parse_cache=parse_cache,
                category=str(session.get("category") or "_preview"),
                doc_id=str(session.get("preview_cache_key") or session.get("session_id") or ""),
                temp_path=temp_path,
                ext=ext,
                all_tables=list(parse_cache.get("all_tables") or session.get("all_tables") or []),
                all_figures=[],
                tmp_dir=session_dir,
                pages=list(session.get("pages") or []),
                full_text=full_text,
                debug_session_id=str(session.get("preview_cache_key") or session.get("session_id") or ""),
            )
            trace_entry["fetch_path"] = trace_entry.get("fetch_path") or "section_merge"

        if not merged.strip():
            traces.append({**trace_entry, "error": "empty_content"})
            continue

        project = str(cand.get("project_name") or "").strip()
        short = str(cand.get("method_short_name") or "").strip()
        scope = str(cand.get("method_scope") or "").strip()
        if scope == "full_project" and project:
            method_name = f"{project}-试验方法"
        elif project and short:
            method_name = f"{project}_{short}-试验方法"
        else:
            method_name = f"{cand.get('display_name') or short or project}-试验方法"

        enriched = enrich_section_method(
            merged,
            method_name,
            section_extract_mode=True,
            full_text=full_text,
            all_tables=list(parse_cache.get("all_tables") or session.get("all_tables") or []),
            all_figures=list(parse_cache.get("all_figures") or session.get("all_figures") or []),
            std_name=str(session.get("source_name") or ""),
            std_code="",
            neo4j_driver=_get_neo4j_driver_for_guided(app),
            category=str(session.get("category") or "_preview"),
            doc_id=str(session.get("preview_cache_key") or session.get("session_id") or ""),
        )
        synr = str(enriched.get("formatted_single") or "")
        if not synr:
            from blueprints.FLGL.method_section_enrich import format_selected_content_rule_based

            synr = format_selected_content_rule_based(str(enriched.get("content_html") or merged))
        from blueprints.FLGL.method_section_enrich import apply_section_extract_editor_synr

        synr = apply_section_extract_editor_synr(
            synr,
            full_text=full_text,
            all_tables=list(parse_cache.get("all_tables") or session.get("all_tables") or []),
            all_figures=list(parse_cache.get("all_figures") or session.get("all_figures") or []),
            category=str(session.get("category") or ""),
            doc_id=str(session.get("doc_id") or ""),
        )

        source_name = str(session.get("source_name") or "")
        method_label = label_from_source_filename(source_name)
        rec_nodes = enriched.get("recommended_nodes") or []
        rec_rels = enriched.get("recommended_relations") or []
        eq_recs = enriched.get("equipment_recommendations") or []

        safe_cid = re.sub(r"[^\w\-]", "_", cid)
        with open(os.path.join(material_dir, f"{safe_cid}_chunk.html"), "w", encoding="utf-8") as f:
            f.write(merged)
        with open(os.path.join(material_dir, f"{safe_cid}_synr.html"), "w", encoding="utf-8") as f:
            f.write(synr)

        entry = {
            "candidate_id": cid,
            "name": method_name,
            "synr": synr,
            "synr_len": len(synr),
            "merged_len": len(merged),
            "label": method_label,
            "source_name": source_name,
            "recommended_nodes": rec_nodes,
            "recommended_relations": rec_rels,
            "equipment_recommendations": eq_recs,
            "method_meta": {
                "project_id": "",
                "method_id": cid,
                "project_name": project,
                "anchor_section_ids": cand.get("anchor_section_ids") or [],
                "anchor_section_nos": cand.get("anchor_section_nos") or [],
                "method_scope": scope,
            },
        }
        methods_out.append(entry)
        cand["materialized"] = {"synr_len": len(synr), "merged_len": len(merged)}
        trace_entry["merged_len"] = len(merged)
        trace_entry["synr_len"] = len(synr)
        trace_entry["equipment_count"] = len(eq_recs)
        traces.append(trace_entry)

    _save_candidates(session_dir, candidates)
    _write_json(os.path.join(session_dir, "materialize_trace.json"), traces)
    _write_json(os.path.join(session_dir, "materialized_methods.json"), methods_out)
    return methods_out


def _session_public_view(session: dict) -> dict:
    return {
        "session_id": session.get("session_id"),
        "source_name": session.get("source_name"),
        "preview_cache_key": session.get("preview_cache_key"),
        "document_memory": session.get("document_memory") or "",
        "structure_profile": session.get("structure_profile") or {},
        "review_notes": session.get("review_notes") or "",
        "candidates": session.get("candidates") or [],
        "sections_count": len((session.get("parse_cache") or {}).get("sections") or []),
        "understood_at": (_read_json(os.path.join(session["session_dir"], "session.json")) or {}).get("understood_at"),
    }


def register_guided_extract_routes(bp) -> None:
    """注册引导式提取路由到 FLGL_method 蓝图。"""

    @bp.route("/method-extract-guided/session", methods=["POST"])
    def guided_create_session():
        if "file" not in request.files:
            return jsonify({"success": False, "error": "没有文件"}), 400
        file = request.files["file"]
        if not file.filename:
            return jsonify({"success": False, "error": "文件名为空"}), 400

        from blueprints.FLGL.method_routes import (
            _ensure_filepath_extension,
            _parse_word_like_llm_extract,
            _safe_filename,
        )

        session_id = uuid.uuid4().hex
        session_dir = guided_session_root(session_id)
        os.makedirs(session_dir, exist_ok=True)

        tmp_dir = tempfile.mkdtemp()
        filename = _safe_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        filepath = os.path.join(session_dir, "source" + ext)
        file.save(filepath)
        filepath = _ensure_filepath_extension(filepath, ext)

        preview_cache_key = session_id
        ctx = _parse_word_like_llm_extract(
            filepath, ext, tmp_dir, preview_cache_key=preview_cache_key,
        )
        if not ctx:
            try:
                from blueprints.ZSGL.WJGL1.document_parse_service import parse_local_file

                parsed = parse_local_file(filepath)
                parse_cache = {
                    "sections": parsed.get("sections") or [],
                    "node_slices": parsed.get("node_slices") or {},
                    "parsed_document": parsed.get("parsed_document") or {},
                    "section_catalogue": parsed.get("section_catalogue") or [],
                }
                ctx = {
                    "full_text": parsed.get("full_text") or "",
                    "pages": parsed.get("pages") or [],
                    "all_tables": [],
                    "parse_cache": parse_cache,
                    "category": "_preview",
                    "doc_id": preview_cache_key,
                }
            except Exception as exc:
                return jsonify({"success": False, "error": str(exc)}), 500

        parse_cache = ctx.get("parse_cache") or {}
        _write_json(os.path.join(session_dir, "parse_cache.json"), parse_cache)

        sections = list(parse_cache.get("sections") or [])
        snapshot = {
            "session_id": session_id,
            "source_name": file.filename,
            "preview_cache_key": preview_cache_key,
            "text_len": len(str(ctx.get("full_text") or "")),
            "sections_count": len(sections),
            "section_catalogue": _catalogue_from_sections(sections),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        meta = {
            **snapshot,
            "temp_path": filepath,
            "ext": ext,
            "full_text": ctx.get("full_text") or "",
            "category": ctx.get("category") or "_preview",
            "pages": ctx.get("pages") or [],
            "all_tables": ctx.get("all_tables") or [],
        }
        _save_session_meta(session_dir, meta)
        _write_json(os.path.join(session_dir, "parsed_snapshot.json"), snapshot)
        _save_candidates(session_dir, [])

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id,
                "preview_cache_key": preview_cache_key,
                "debug_dir": os.path.join(
                    _backend_root(), "output_json", "flgl_extract_debug", preview_cache_key,
                ),
                **snapshot,
            },
        })

    @bp.route("/method-extract-guided/<session_id>", methods=["GET"])
    def guided_get_session(session_id: str):
        session = _load_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        return jsonify({"success": True, "data": _session_public_view(session)})

    @bp.route("/method-extract-guided/<session_id>/understand", methods=["POST", "OPTIONS"])
    def guided_understand(session_id: str):
        if request.method == "OPTIONS":
            return "", 200
        session = _load_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404

        meta = _read_json(os.path.join(session["session_dir"], "session.json"), {})
        session["temp_path"] = meta.get("temp_path")
        session["ext"] = meta.get("ext")
        session["full_text"] = meta.get("full_text")
        session["category"] = meta.get("category")
        session["preview_cache_key"] = meta.get("preview_cache_key")
        session["pages"] = meta.get("pages")
        session["all_tables"] = meta.get("all_tables")
        session["source_name"] = meta.get("source_name")

        event_queue: queue.Queue = queue.Queue()
        app = current_app._get_current_object()

        def worker():
            with app.app_context():
                try:
                    event_queue.put(("session_ready", {"session_id": session_id}))
                    _run_understand(session, on_event=lambda t, d: event_queue.put((t, d)), app=app)
                    event_queue.put(("done", {"session_id": session_id}))
                except Exception as exc:
                    logger.exception("[guided] understand 失败: %s", exc)
                    event_queue.put(("error", {"message": str(exc)}))
                finally:
                    event_queue.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def generate():
            yield ": guided-extract\n\n"
            while True:
                item = event_queue.get()
                if item is None:
                    break
                evt, data = item
                payload = json.dumps(data, ensure_ascii=False)
                yield f"event: {evt}\ndata: {payload}\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @bp.route("/method-extract-guided/<session_id>/candidates/<candidate_id>", methods=["PATCH"])
    def guided_patch_candidate(session_id: str, candidate_id: str):
        session = _load_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        body = request.json or {}
        candidates = list(session.get("candidates") or [])
        found = False
        for c in candidates:
            if c.get("candidate_id") != candidate_id:
                continue
            found = True
            if "status" in body:
                c["status"] = str(body["status"])
            if "user_feedback" in body:
                c["user_feedback"] = str(body["user_feedback"] or "")
            if "anchor_section_ids" in body:
                c["anchor_section_ids"] = list(body["anchor_section_ids"] or [])
            if "anchor_section_nos" in body:
                c["anchor_section_nos"] = list(body["anchor_section_nos"] or [])
            if body.get("status") == "needs_refine":
                c["status"] = "needs_refine"
        if not found:
            return jsonify({"success": False, "error": "候选不存在"}), 404
        _save_candidates(session["session_dir"], candidates)
        _append_feedback_log(session["session_dir"], {
            "type": "patch",
            "candidate_id": candidate_id,
            "body": body,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        updated = next(c for c in candidates if c.get("candidate_id") == candidate_id)
        return jsonify({"success": True, "data": updated})

    @bp.route("/method-extract-guided/<session_id>/candidates/<candidate_id>/refine", methods=["POST"])
    def guided_refine_candidate(session_id: str, candidate_id: str):
        session = _load_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        meta = _read_json(os.path.join(session["session_dir"], "session.json"), {})
        for k in ("temp_path", "ext", "full_text", "category", "preview_cache_key", "pages", "all_tables"):
            session[k] = meta.get(k)
        try:
            updated = _run_refine_candidate(session, candidate_id)
            return jsonify({"success": True, "data": updated})
        except Exception as exc:
            logger.exception("[guided] refine 失败: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500

    @bp.route("/method-extract-guided/<session_id>/materialize", methods=["POST"])
    def guided_materialize(session_id: str):
        session = _load_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        meta = _read_json(os.path.join(session["session_dir"], "session.json"), {})
        for k in ("temp_path", "ext", "full_text", "category", "preview_cache_key", "pages", "all_tables", "source_name"):
            session[k] = meta.get(k)
        body = request.json or {}
        candidate_ids = body.get("candidate_ids")
        try:
            methods = _run_materialize(session, candidate_ids=candidate_ids, app=current_app._get_current_object())
            return jsonify({"success": True, "data": {"methods": methods, "count": len(methods)}})
        except Exception as exc:
            logger.exception("[guided] materialize 失败: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500

    @bp.route("/method-extract-guided/<session_id>/finalize", methods=["POST"])
    def guided_finalize(session_id: str):
        session = _load_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"}), 404
        path = os.path.join(session["session_dir"], "materialized_methods.json")
        methods = _read_json(path, [])
        if not methods:
            meta = _read_json(os.path.join(session["session_dir"], "session.json"), {})
            session.update({k: meta.get(k) for k in meta})
            methods = _run_materialize(session, app=current_app._get_current_object())
        entries = []
        for i, m in enumerate(methods):
            entries.append({
                "id": "" if i else (request.json or {}).get("draft_method_id") or "",
                "name": m.get("name"),
                "type": "分则",
                "class": "分则",
                "label": m.get("label") or "",
                "synr": m.get("synr"),
                "sytj": "",
                "sygc": "",
                "jgpd": "",
                "source_name": m.get("source_name") or session.get("source_name") or "",
                "extract_source": "guided_llm",
                "recommended_nodes": m.get("recommended_nodes") or [],
                "recommended_relations": m.get("recommended_relations") or [],
                "equipment_recommendations": m.get("equipment_recommendations") or [],
                "method_meta": m.get("method_meta") or {},
            })
        return jsonify({"success": True, "data": {"methods": entries}})
