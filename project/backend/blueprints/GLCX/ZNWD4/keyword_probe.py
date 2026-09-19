"""
图谱关键词探测：keyword → hits[] / best（供 znwd4_probe_keyword 工具与 plan_reconcile 共用）。
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SEARCH_ORDER = ("TestProject", "Environment", "Equipment", "Indicator", "TestMethod", "EquipmentType")

_LABEL_ZH = {
    "Equipment": "设备",
    "EquipmentType": "设备类型",
    "Environment": "试验环境",
    "TestProject": "测试项目",
    "Indicator": "指标",
    "TestMethod": "测试方法",
}


def plain_intent_slice(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.search(r"【当前问题】\s*(.+)$", s, re.S)
    if m:
        cur = (m.group(1) or "").strip()
        if len(cur) >= 2:
            return cur
    return s


def extract_focus_candidates(question: str, retrieval_question: str = "") -> List[str]:
    seen: set = set()
    out: List[str] = []

    def add(p: str) -> None:
        p = re.sub(r"\s+", "", (p or "").strip())
        if len(p) < 2 or p in seen:
            return
        if p in ("什么", "哪些", "如何", "怎样", "请问", "查询"):
            return
        seen.add(p)
        out.append(p)

    for raw in (retrieval_question or "", question or ""):
        s = plain_intent_slice(raw) or raw
        m = re.search(r"【承前主体】\s*([^。；;\n]+)", s)
        if m:
            add(m.group(1).strip())

    try:
        from .question_route_lock import extract_project_keywords
        from .rel_inference import looks_like_equipment_by_project

        q_all = f"{retrieval_question}\n{question}"
        from .question_route_lock import _looks_like_project_methods

        if _looks_like_project_methods(q_all) or looks_like_equipment_by_project(q_all):
            for proj in extract_project_keywords(q_all):
                add(proj)
        from .rel_inference import looks_like_equipment_by_indicator, looks_like_projects_by_method
        from .question_route_lock import extract_indicator_keywords, extract_method_keywords

        if looks_like_equipment_by_indicator(q_all):
            for ind in extract_indicator_keywords(q_all):
                add(ind)
        if looks_like_projects_by_method(q_all):
            for meth in extract_method_keywords(q_all):
                add(meth)
    except ImportError:
        pass

    for raw in (retrieval_question or "", question or ""):
        s = plain_intent_slice(raw) or raw
        for pat in (
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,36}(?:测试|试验))\s*(?:有什么|有哪些|包括哪些|的)?\s*方法",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*(?:有什么|有哪些|包括哪些|都有哪些)\s*方法",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*(?:有什么|有哪些|包括哪些|都有哪些)",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*的\s*(?:测试|试验)?\s*(?:项目|指标|方法)",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*(?:有什么|有哪些).{0,16}(?:试验项目|测试项目|项目|指标|方法)",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*测试\s*(?:的)?\s*(?:方法|步骤)",
        ):
            mm = re.search(pat, s)
            if mm:
                add(mm.group(1))

    q = plain_intent_slice(question) or (question or "").strip()
    q2 = re.sub(
        r"(有什么|有哪些|包括哪些|是什么|的方法|的指标|的项目|试验项目|测试项目|试验方法|测试方法).*$",
        "",
        q,
    ).strip()
    if len(q2) >= 2:
        add(q2)
    out.sort(key=len, reverse=True)
    return out


def _node_name(props: Dict[str, Any]) -> str:
    if not isinstance(props, dict):
        return ""
    return str(props.get("Name") or props.get("name") or "").strip()


def _node_id(props: Dict[str, Any]) -> str:
    if not isinstance(props, dict):
        return ""
    return str(props.get("Id") or props.get("id") or "").strip()


def _fast_label_order() -> Tuple[str, ...]:
    raw = (os.environ.get("ZNWD4_KEYWORD_LABELS_FAST") or "TestProject,Environment,Equipment").strip()
    labels = tuple(x.strip() for x in raw.split(",") if x.strip())
    return labels or ("TestProject", "Environment", "Equipment")


def _batch_query_labels(
    driver: Any,
    keyword: str,
    labels: Tuple[str, ...],
    *,
    limit_per_label: int = 6,
) -> List[Dict[str, Any]]:
    kw = re.sub(r"\s+", "", (keyword or "").strip())
    if not driver or not kw or not labels:
        return []
    parts: List[str] = []
    for lbl in labels:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", lbl):
            continue
        parts.append(
            f"MATCH (n:{lbl}) WHERE toLower(coalesce(n.Name,'')) CONTAINS toLower($kw) "
            f"RETURN '{lbl}' AS node_type, n AS n LIMIT $lim"
        )
    if not parts:
        return []
    cypher = " UNION ALL ".join(parts)
    rows: List[Dict[str, Any]] = []
    try:
        t0 = time.perf_counter()
        with driver.session() as session:
            for rec in session.run(cypher, kw=kw, lim=limit_per_label):
                n = rec.get("n")
                nt = str(rec.get("node_type") or "")
                if hasattr(n, "items"):
                    props = dict(n.items())
                    rows.append(
                        {
                            "node_type": nt,
                            "id": _node_id(props),
                            "name": _node_name(props),
                            "properties": props,
                        }
                    )
        logger.debug(
            "[ZNWD4][probe] batch labels=%s kw=%r hits=%d ms=%.1f",
            labels,
            kw[:20],
            len(rows),
            (time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        logger.warning("[ZNWD4][probe] batch 查询失败: %s", e)
    return rows


def _batch_query_testmethod(driver: Any, keyword: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    """TestMethod 标准号常写在 Name 或 Source_name，需单独查。"""
    from .method_inference import expand_method_standard_keywords

    kw = re.sub(r"\s+", "", (keyword or "").strip())
    if not driver or not kw:
        return []
    kws = expand_method_standard_keywords(kw) or [kw]
    cypher = """
    MATCH (n:TestMethod)
    WHERE ANY(k IN $kws WHERE
      toLower(coalesce(n.Name,'')) CONTAINS toLower(k)
      OR toLower(coalesce(n.Source_name,'')) CONTAINS toLower(k)
    )
    RETURN 'TestMethod' AS node_type, n AS n
    LIMIT $lim
    """
    rows: List[Dict[str, Any]] = []
    try:
        with driver.session() as session:
            for rec in session.run(cypher, kws=kws, lim=limit):
                n = rec.get("n")
                if hasattr(n, "items"):
                    props = dict(n.items())
                    rows.append(
                        {
                            "node_type": "TestMethod",
                            "id": _node_id(props),
                            "name": _node_name(props) or str(props.get("Source_name") or "").strip(),
                            "properties": props,
                        }
                    )
    except Exception as e:
        logger.warning("[ZNWD4][probe] TestMethod 查询失败: %s", e)
    return rows


def _looks_like_standard_token(kw: str) -> bool:
    return bool(re.search(r"G\s*B\s*/?\s*T|^\d+\.\d+", kw or "", re.I))


def _batch_query_equipment_type(driver: Any, keyword: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    """EquipmentType 常把类别名写在 Type 字段，单独补查。"""
    kw = re.sub(r"\s+", "", (keyword or "").strip())
    if not driver or not kw:
        return []
    cypher = (
        "MATCH (n:EquipmentType) WHERE "
        "toLower(coalesce(n.Name,'')) CONTAINS toLower($kw) OR "
        "toLower(coalesce(n.Type,'')) CONTAINS toLower($kw) "
        "RETURN 'EquipmentType' AS node_type, n AS n LIMIT $lim"
    )
    rows: List[Dict[str, Any]] = []
    try:
        with driver.session() as session:
            for rec in session.run(cypher, kw=kw, lim=limit):
                n = rec.get("n")
                if hasattr(n, "items"):
                    props = dict(n.items())
                    rows.append(
                        {
                            "node_type": "EquipmentType",
                            "id": _node_id(props),
                            "name": _node_name(props) or str(props.get("Type") or "").strip(),
                            "properties": props,
                        }
                    )
    except Exception as e:
        logger.warning("[ZNWD4][probe] EquipmentType 查询失败: %s", e)
    return rows


def _levenshtein_leq(a: str, b: str, max_dist: int = 2) -> bool:
    """True if edit distance <= max_dist (early exit)."""
    a, b = a.strip(), b.strip()
    if not a or not b:
        return False
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_min = cur[0]
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            row_min = min(row_min, cur[j])
        if row_min > max_dist:
            return False
        prev = cur
    return prev[lb] <= max_dist


def _prefix_variants(keyword: str, *, min_len: int = 2) -> List[str]:
    """Shorter prefixes help under_tail truncated queries."""
    kw = re.sub(r"\s+", "", (keyword or "").strip())
    if len(kw) < min_len:
        return []
    out: List[str] = []
    seen: set = set()
    for n in range(len(kw), min_len - 1, -1):
        p = kw[:n]
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _score_match(candidate: str, name: str, *, enhanced: bool = False, question: str = "") -> float:
    from .keyword_refine import probe_enhanced_enabled, score_entity_match

    use_enhanced = enhanced or probe_enhanced_enabled()
    return score_entity_match(candidate, name, question, enhanced=use_enhanced)


def probe_keyword(
    driver: Any,
    keyword: str,
    *,
    label_hints: Optional[List[str]] = None,
    enhanced: bool = False,
    question: str = "",
    parent_hint: str = "",
    child_hint: str = "",
) -> Dict[str, Any]:
    """返回 {keyword, hits, best, match_count}，best 含 node_type/id/name/score。"""
    kw = re.sub(r"\s+", "", (keyword or "").strip())
    if not kw or not driver:
        return {"keyword": kw, "hits": [], "best": None, "match_count": 0}

    hints = [x for x in (label_hints or []) if x in _SEARCH_ORDER]
    order: Tuple[str, ...] = tuple(hints) if hints else _fast_label_order()
    raw_rows: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def _merge_rows(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            key = (row.get("node_type"), row.get("id"), row.get("name"))
            if key in seen_ids:
                continue
            seen_ids.add(key)
            raw_rows.append(row)

    _merge_rows(_batch_query_equipment_type(driver, kw) if "EquipmentType" in hints else [])
    if not raw_rows:
        _merge_rows(_batch_query_labels(driver, kw, order))
    if not raw_rows and hints:
        _merge_rows(_batch_query_labels(driver, kw, _fast_label_order()))
    if not raw_rows:
        _merge_rows(_batch_query_equipment_type(driver, kw))
    if not raw_rows and (_looks_like_standard_token(kw) or "TestMethod" in (hints or [])):
        _merge_rows(_batch_query_testmethod(driver, kw))

    from .keyword_refine import probe_enhanced_enabled

    use_enhanced = enhanced or probe_enhanced_enabled()
    if use_enhanced and (not raw_rows or len(kw) >= 4):
        for variant in _prefix_variants(kw)[1:4]:
            _merge_rows(_batch_query_labels(driver, variant, order))
            if "TestMethod" in (hints or []):
                _merge_rows(_batch_query_testmethod(driver, variant))

    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    hits: List[Dict[str, Any]] = []
    for row in raw_rows:
        name = row.get("name") or ""
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        type_val = str(props.get("Type") or "").strip()
        sc = _score_match(kw, name, enhanced=use_enhanced)
        if type_val and sc < _score_match(kw, type_val, enhanced=use_enhanced):
            sc = _score_match(kw, type_val, enhanced=use_enhanced)
        hit = {
            "node_type": row.get("node_type"),
            "id": row.get("id"),
            "name": name,
            "type_value": type_val,
            "score": round(sc, 4),
        }
        hits.append(hit)
        if sc > best_score:
            best_score = sc
            best = hit

    hits.sort(key=lambda h: h.get("score", 0), reverse=True)
    result: Dict[str, Any] = {
        "keyword": kw,
        "hits": hits[:12],
        "best": best,
        "match_count": len(hits),
        "node_type_zh": _LABEL_ZH.get(str((best or {}).get("node_type") or ""), ""),
    }
    q_ctx = (question or "").strip()
    if q_ctx and hits:
        from .entity_disambiguation import disambiguate_probe_snapshot
        from .hierarchy_focus import parse_equipment_type_hierarchy

        hf = parse_equipment_type_hierarchy(q_ctx)
        result = disambiguate_probe_snapshot(
            result,
            question=q_ctx,
            parent_hint=parent_hint or (hf.parent_type if hf else ""),
            child_hint=child_hint or (hf.child_name if hf else ""),
        )
    return result


def compress_probe_for_tool(result: Dict[str, Any]) -> Dict[str, Any]:
    """工具回写 LLM 时压缩 hits，避免整段进上下文。"""
    best = result.get("best")
    hits = result.get("hits") if isinstance(result.get("hits"), list) else []
    slim_hits = [
        {
            "node_type": h.get("node_type"),
            "id": h.get("id"),
            "name": (str(h.get("name") or ""))[:80],
            "score": h.get("score"),
        }
        for h in hits[:5]
        if isinstance(h, dict)
    ]
    return {
        "keyword": result.get("keyword"),
        "match_count": result.get("match_count"),
        "best": best,
        "hits": slim_hits,
        "node_type_zh": result.get("node_type_zh"),
    }
