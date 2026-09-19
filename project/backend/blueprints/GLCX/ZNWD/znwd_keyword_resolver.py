"""
图谱关键词解析：判断关键词更像哪类节点，并尽量还原完整名称（如 无人机A型 而非 无人机）。
"""

from __future__ import annotations

import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from .intent_classifier import plain_intent_slice

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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _fast_label_order() -> Tuple[str, ...]:
    raw = (os.environ.get("ZNWD_KEYWORD_LABELS_FAST") or "TestProject,Environment,Equipment").strip()
    labels = tuple(x.strip() for x in raw.split(",") if x.strip())
    return labels or ("TestProject", "Environment", "Equipment")


def _node_name(props: Dict[str, Any]) -> str:
    if not isinstance(props, dict):
        return ""
    return str(props.get("Name") or props.get("name") or "").strip()


def extract_focus_candidates(question: str, retrieval_question: str = "") -> List[str]:
    """从问句中提取若干候选焦点（长串优先）。"""
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
        for pat in (
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,36}(?:测试|试验))\s*(?:有什么|有哪些|包括哪些|的)?\s*方法",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*(?:有什么|有哪些|包括哪些|都有哪些)\s*方法",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*(?:有什么|有哪些|包括哪些|都有哪些)",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*的\s*(?:测试|试验)?\s*(?:项目|指标|方法)",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*(?:有什么|有哪些).{0,16}(?:试验项目|测试项目|项目|指标|方法)",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)\s*测试\s*(?:的)?\s*(?:方法|步骤)",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*测试项目",
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*试验项目",
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


def _query_nodes_containing(driver: Any, node_type: str, value: str, limit: int = 8) -> List[Dict[str, Any]]:
    if not driver or not value:
        return []
    cypher = (
        f"MATCH (n:{node_type}) WHERE toLower(coalesce(n.Name,'')) "
        f"CONTAINS toLower($value) RETURN n LIMIT $limit"
    )
    rows: List[Dict[str, Any]] = []
    try:
        with driver.session() as session:
            for rec in session.run(cypher, value=value, limit=limit):
                n = rec.get("n")
                if hasattr(n, "items"):
                    rows.append(
                        {
                            "node_type": node_type,
                            "name": _node_name(dict(n.items())),
                            "properties": dict(n.items()),
                        }
                    )
    except Exception as e:
        logger.warning("[ZNWD][KeywordResolver] %s 查询失败: %s", node_type, e)
    return rows


def _batch_query_labels(
    driver: Any,
    keyword: str,
    labels: Tuple[str, ...],
    *,
    limit_per_label: int = 6,
) -> List[Dict[str, Any]]:
    """单条 Cypher UNION ALL，减少 round-trip。"""
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
                    rows.append(
                        {
                            "node_type": nt,
                            "name": _node_name(dict(n.items())),
                            "properties": dict(n.items()),
                        }
                    )
        logger.debug(
            "[ZNWD][KeywordResolver] batch labels=%s kw=%r hits=%d ms=%.1f",
            labels,
            kw[:20],
            len(rows),
            (time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        logger.warning("[ZNWD][KeywordResolver] batch 查询失败: %s", e)
    return rows


def _score_match(candidate: str, name: str) -> float:
    c, n = candidate.strip(), name.strip()
    if not c or not n:
        return 0.0
    if c == n:
        return 1.0
    if c in n or n in c:
        return 0.85 + 0.1 * (len(c) / max(len(n), 1))
    return SequenceMatcher(None, c.lower(), n.lower()).ratio()


def resolve_keyword_in_graph(
    driver: Any,
    keyword: str,
    *,
    preferred_types: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Any]:
    """
    在 Neo4j 中解析关键词所属节点类型及最佳匹配名称。
    """
    kw = re.sub(r"\s+", "", (keyword or "").strip())
    if not kw or not driver:
        return {
            "keyword": kw,
            "node_type": "",
            "node_type_zh": "",
            "matched_name": "",
            "match_count": 0,
            "hits": [],
            "score": 0.0,
        }

    order = preferred_types or _SEARCH_ORDER
    if len(order) <= 3:
        raw_rows = _batch_query_labels(driver, kw, order)
    else:
        raw_rows = []
        for node_type in order:
            raw_rows.extend(_query_nodes_containing(driver, node_type, kw))

    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    all_hits: List[Dict[str, Any]] = []

    for row in raw_rows:
        name = row.get("name") or ""
        sc = _score_match(kw, name)
        all_hits.append({**row, "score": sc})
        if sc > best_score:
            best_score = sc
            best = row

    node_type = str((best or {}).get("node_type") or "")
    matched = str((best or {}).get("name") or "")
    return {
        "keyword": kw,
        "node_type": node_type,
        "node_type_zh": _LABEL_ZH.get(node_type, node_type),
        "matched_name": matched or kw,
        "match_count": len([h for h in all_hits if h.get("node_type") == node_type]),
        "hits": all_hits[:12],
        "score": best_score,
    }


def resolve_focus_fast(
    driver: Any,
    question: str,
    retrieval_question: str = "",
) -> Dict[str, Any]:
    """
    快速焦点解析：限制候选数与标签，批量 Cypher；未命中时扩全标签再查一次。
    """
    candidates = extract_focus_candidates(question, retrieval_question)
    max_cand = _env_int("ZNWD_KEYWORD_CANDIDATE_MAX", 2)
    candidates = candidates[: max(1, max_cand)]
    if not candidates:
        return {"focus": "", "node_type": "", "matched_name": "", "resolution": None}

    if not driver:
        return {
            "focus": candidates[0],
            "node_type": "",
            "matched_name": candidates[0],
            "candidates_checked": candidates,
        }

    fast_labels = _fast_label_order()
    t0 = time.perf_counter()
    best_agg: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for cand in candidates:
        res = resolve_keyword_in_graph(driver, cand, preferred_types=fast_labels)
        sc = float(res.get("score") or 0)
        if sc > best_score or (
            sc == best_score and len(cand) > len(str((best_agg or {}).get("keyword") or ""))
        ):
            best_score = sc
            best_agg = {**res, "candidates_checked": candidates}

    if (not best_agg or not best_agg.get("node_type")) and best_score < 0.45:
        for cand in candidates:
            res = resolve_keyword_in_graph(driver, cand, preferred_types=_SEARCH_ORDER)
            sc = float(res.get("score") or 0)
            if sc > best_score or (
                sc == best_score and len(cand) > len(str((best_agg or {}).get("keyword") or ""))
            ):
                best_score = sc
                best_agg = {**res, "candidates_checked": candidates}

    neo_ms = (time.perf_counter() - t0) * 1000
    if not best_agg or not best_agg.get("node_type"):
        logger.info(
            "[ZNWD][KeywordResolver] resolve_focus_fast 无图谱命中 neo_ms=%.1f cands=%s",
            neo_ms,
            candidates[:2],
        )
        return {
            "focus": candidates[0],
            "node_type": "",
            "matched_name": candidates[0],
            "resolution": best_agg,
            "candidates_checked": candidates,
            "neo_ms": neo_ms,
            "score": best_score,
        }

    focus = best_agg.get("matched_name") or best_agg.get("keyword") or candidates[0]
    logger.info(
        "[ZNWD][KeywordResolver] resolve_focus_fast focus=%r type=%s score=%.2f neo_ms=%.1f",
        focus[:40],
        best_agg.get("node_type"),
        best_score,
        neo_ms,
    )
    return {
        "focus": focus,
        "node_type": best_agg.get("node_type"),
        "node_type_zh": best_agg.get("node_type_zh"),
        "matched_name": best_agg.get("matched_name"),
        "resolution": best_agg,
        "candidates_checked": candidates,
        "neo_ms": neo_ms,
        "score": best_score,
    }


def resolve_best_focus(
    driver: Any,
    question: str,
    retrieval_question: str = "",
) -> Dict[str, Any]:
    """对问句生成多个焦点候选，选图谱上匹配得分最高的一项（默认走快速路径）。"""
    return resolve_focus_fast(driver, question, retrieval_question)


__all__ = (
    "extract_focus_candidates",
    "resolve_best_focus",
    "resolve_focus_fast",
    "resolve_keyword_in_graph",
)
