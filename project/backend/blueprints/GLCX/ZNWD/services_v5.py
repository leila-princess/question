import json
import logging
import re
import time
from difflib import SequenceMatcher, get_close_matches
from typing import Any, Dict, Generator, List, Optional, Tuple

from utils.neo4j_flgl_status import flgl_status_cypher_param

from .memory_store import get_memory_store

from .prompts_v5 import META_PLAN_PROMPT, META_SYNTHESIS_PROMPT
from .intent_classifier import plain_intent_slice
from .znwd_llm_intent_classifier import Intent, IntentResult, classify_intent, relation_target_kind
from .retrieval_meta_ops import (
    META_OPS,
    NODE_LABELS,
    clamp_meta_op_params,
    meta_ops_for_prompt,
    validate_meta_op,
)
from .services_v4 import (
    _enrich_retrieval_question_with_focus,
    _extract_entity_from_user_turn,
    _extract_ssyq_keyword_from_question,
    _infer_focus_entity_from_messages,
    collect_v4_retrieval_results,
    llm_stream_single as llm_stream_single_v4,
    _stream_single_llm as _stream_single_llm_v4,
    search_knowledge_base,
    ZNWD_CHROMA_UI_MAX,
)
from .prompts_v4 import DIRECT_ANSWER_PROMPT
from .source_table import (
    build_rows_and_selection_from_neo4j_hits,
    collect_neo_hit_selection_ids,
    flatten_neo4j_hits_to_nodes,
)

logger = logging.getLogger(__name__)

_V5_LOG_JSON_MAX = 8000

# 实体 Name 列表缓存（按节点类型），用于模糊纠错
_ENTITY_NAMES_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_ENTITY_CORRECTION_TYPES: Tuple[str, ...] = ("TestProject", "Indicator", "Environment", "Equipment")
_ENV_SUBJECT_KEYWORDS: Tuple[str, ...] = (
    "高温",
    "低温",
    "湿热",
    "盐雾",
    "淋雨",
    "振动",
    "沙尘",
    "砂尘",
    "老化",
    "霉菌",
    "高海拔",
    "复杂气象",
    "太阳辐射",
)


def _get_entity_names(driver: Any, node_type: str, field: str = "Name") -> List[str]:
    """从 Neo4j 拉取启用节点的 Name 列表（带 TTL 缓存）。"""
    cache_key = f"{node_type}:{field}"
    now = time.time()
    hit = _ENTITY_NAMES_CACHE.get(cache_key)
    if hit and now - hit[0] < 300:
        return hit[1]
    names: List[str] = []
    if not driver:
        _ENTITY_NAMES_CACHE[cache_key] = (now, names)
        return names
    params = flgl_status_cypher_param()
    cypher = (
        f"MATCH (n:{node_type}) WHERE n.Status IN $flgl_enabled_status "
        f"AND coalesce(n.{field}, n.name) IS NOT NULL "
        f"RETURN DISTINCT trim(toString(coalesce(n.{field}, n.name))) AS name LIMIT 8000"
    )
    try:
        with driver.session() as session:
            for rec in session.run(cypher, params):
                nm = str(rec.get("name") or "").strip()
                if nm and nm not in names:
                    names.append(nm)
    except Exception as e:
        logger.warning("[V5][entity] 拉取 %s.%s 失败: %s", node_type, field, e)
    _ENTITY_NAMES_CACHE[cache_key] = (now, names)
    return names


def warm_v5_entity_name_caches(driver: Any) -> None:
    """应用启动或首次请求时预热实体名称缓存。"""
    if not driver:
        return
    for nt in _ENTITY_CORRECTION_TYPES:
        _get_entity_names(driver, nt)
    logger.info("[V5][entity_cache] 已预热 %s 的 Name 列表", ",".join(_ENTITY_CORRECTION_TYPES))


def _fuzzy_correct_token(cand: str, driver: Any, cutoff: float = 0.65, min_ratio: float = 0.68) -> Optional[str]:
    """对单个候选词在各类实体 Name 中找最近似项；无合适匹配返回 None。"""
    c = (cand or "").strip()
    if len(c) < 2 or len(c) > 36:
        return None
    for nt in _ENTITY_CORRECTION_TYPES:
        names = _get_entity_names(driver, nt)
        if not names:
            continue
        if c in names:
            return None
        ms = get_close_matches(c, names, n=1, cutoff=cutoff)
        if not ms:
            continue
        m = ms[0]
        if m == c:
            continue
        ratio = SequenceMatcher(None, c, m).ratio()
        if ratio >= min_ratio:
            return m
    return None


def _entity_correction_candidates(question: str) -> List[str]:
    """从问题中切出可能实体短语（启发式，不依赖 jieba）。"""
    if not question:
        return []
    q = re.sub(r"【[^】]*】", " ", question)
    parts = re.split(r"[，,。.!！？?、；;\s]+", q)
    cands: List[str] = []
    for p in parts:
        p = p.strip()
        p = re.sub(r"(有哪些|都有什么|是什么|包括哪些|列出|列举|的测试方法|的试验方法|测试方法|试验方法|方法|项目|指标)$", "", p).strip()
        if 2 <= len(p) <= 40:
            cands.append(p)
        for sub in re.split(r"[的之与和在对从以]", p):
            sub = sub.strip()
            if 2 <= len(sub) <= 24:
                cands.append(sub)
    seen: set = set()
    out: List[str] = []
    for c in cands:
        if c in seen:
            continue
        if not re.match(r"^[\u4e00-\u9fffA-Za-z0-9·\-]+$", c):
            continue
        seen.add(c)
        out.append(c)
    out.sort(key=len, reverse=True)
    return out[:48]


def _replace_entities_with_corrections(question: str, driver: Any) -> Tuple[str, Dict[str, str]]:
    """
    对用户问题中的潜在实体词做近似纠错。
    返回 (纠正后全文, {原始子串: 标准名称})。
    """
    if not driver or not question:
        return question, {}
    corrections: Dict[str, str] = {}
    for cand in _entity_correction_candidates(question):
        if any(cand.startswith(k) or cand.endswith(k) for k in ("请问", "查询", "了解")):
            continue
        fixed = _fuzzy_correct_token(cand, driver)
        if fixed and fixed != cand:
            corrections[cand] = fixed
    if not corrections:
        return question, {}
    corrected = question
    for orig in sorted(corrections.keys(), key=len, reverse=True):
        corrected = corrected.replace(orig, corrections[orig])
    return corrected, corrections


def _validate_relation_path(steps: List[Dict[str, Any]], question: str) -> List[Dict[str, Any]]:
    """
    后处理：根据问题中的环境/方法类关键词，修正明显错误的第一步（如把环境词当作 Equipment 的 value）。
    """
    if not steps:
        return steps
    q = question or ""
    # 已明确「测试项目/试验项目」且问方法/内容：主体是项目名（如「沙尘防护测试项目」），禁止把沙尘等环境词强行改成 Environment 起点
    if re.search(r"(测试项目|试验项目)", q) and re.search(
        r"(方法|测试方法|试验方法|具体内容|内容是什么|怎么测|步骤)",
        q,
    ):
        return steps
    if not re.search(r"(方法|测试方法|试验方法|项目|试验项目|测试项目)", q):
        return steps
    if re.search(r"(哪些|哪一些|什么|都有哪些).{0,24}(设备|装备)", q) and re.search(
        r"(有|具备|关联).{0,16}项目", q,
    ):
        return steps
    has_env_word = any(k in q for k in _ENV_SUBJECT_KEYWORDS)

    def _has_environment_step(sts: List[Dict[str, Any]]) -> bool:
        for s in sts:
            if not isinstance(s, dict):
                continue
            op = str(s.get("op_code") or "")
            pr = s.get("params") if isinstance(s.get("params"), dict) else {}
            if op == "neo_node_query" and str(pr.get("node_type")) == "Environment":
                return True
            if op == "neo_relation_query" and str(pr.get("from_type")) == "Environment":
                return True
        return False

    if not has_env_word or _has_environment_step(steps):
        return steps

    st0 = steps[0]
    if not isinstance(st0, dict) or str(st0.get("op_code")) != "neo_relation_query":
        return steps
    pr = dict(st0.get("params") or {})
    val = str(pr.get("value") or "").strip()
    if str(pr.get("where_side") or "from").lower() == "to":
        return steps
    if pr.get("from_type") != "Equipment" or str(pr.get("match_mode") or "").lower() != "fuzzy":
        return steps
    if val not in _ENV_SUBJECT_KEYWORDS:
        return steps

    pr2 = dict(pr)
    pr2["from_type"] = "Environment"
    pr2["rel_type"] = "CON"
    pr2["to_type"] = "TestProject"
    pr2["from_field"] = str(pr2.get("from_field") or "Name")
    pr2["match_mode"] = "fuzzy"
    pr2["value"] = val
    errs = validate_meta_op("neo_relation_query", clamp_meta_op_params("neo_relation_query", pr2))
    if errs:
        logger.warning("[V5][PATH] 环境起点修正未通过校验: %s", errs)
        return steps
    out_steps: List[Dict[str, Any]] = []
    for i, s in enumerate(steps):
        if i == 0 and isinstance(s, dict):
            ns = dict(s)
            ns["params"] = pr2
            ns["purpose"] = str(ns.get("purpose") or "") + "（后处理：环境词误作设备起点已改为 Environment→TestProject）"
            out_steps.append(ns)
        else:
            out_steps.append(s)
    logger.info("[V5][PATH] 已将首步 Equipment 纠正为 Environment→TestProject，value=%r", val)
    return out_steps


def _safe_json(obj: Any, limit: int = _V5_LOG_JSON_MAX) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) > limit:
        return s[:limit] + f"...(truncated, total_len={len(s)})"
    return s


def _neo_row_one_line(row: Dict[str, Any]) -> str:
    if not isinstance(row, dict):
        return str(row)[:200]
    if row.get("error"):
        return f"error={row.get('error')}"
    parts: List[str] = []
    for key in ("n", "m", "r"):
        if key not in row:
            continue
        v = row[key]
        if isinstance(v, dict):
            if "labels" in v:
                lab = (v.get("labels") or ["?"])[0]
                p = v.get("properties") or {}
                nm = str(p.get("Name") or p.get("Label") or p.get("Id") or "")[:60]
                parts.append(f"{key}:{lab}({nm})")
            elif v.get("type"):
                parts.append(f"{key}:REL[{v.get('type')}]")
        else:
            parts.append(f"{key}:{str(v)[:40]}")
    return " | ".join(parts) if parts else str(row)[:200]


def _summarize_neo_rows(rows: List[Dict[str, Any]], max_lines: int = 25) -> str:
    if not rows:
        return "  (无)"
    lines = []
    for i, r in enumerate(rows):
        if i >= max_lines:
            lines.append(f"  ... 共 {len(rows)} 行，仅展示前 {max_lines} 行")
            break
        lines.append(f"  [{i}] {_neo_row_one_line(r)}")
    return "\n".join(lines)


def _summarize_chroma_rows(rows: List[Dict[str, Any]], max_lines: int = 15) -> str:
    if not rows:
        return "  (无)"
    lines = []
    for i, c in enumerate(rows):
        if i >= max_lines:
            lines.append(f"  ... 共 {len(rows)} 条，仅展示前 {max_lines} 条")
            break
        if not isinstance(c, dict):
            lines.append(f"  [{i}] <非dict>")
            continue
        if c.get("error"):
            lines.append(f"  [{i}] error={c.get('error')}")
            continue
        did = str(c.get("doc_id") or "")[:36]
        tit = str(c.get("title") or "")[:50]
        sec = str(c.get("section_no") or "")
        clen = len(str(c.get("content") or ""))
        dist = c.get("distance")
        sc = c.get("score")
        lines.append(f"  [{i}] doc_id={did!r} title={tit!r} section={sec!r} len={clen} dist={dist} score={sc}")
    return "\n".join(lines)


def _last_user_question(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "").strip()
    return ""


def _send_step(
    msg: str,
    tp: str = "retrieve",
    status: str = "loading",
    *,
    phase: Optional[str] = None,
) -> Tuple[str, dict]:
    payload: Dict[str, Any] = {"type": tp, "status": status, "message": msg}
    if phase:
        payload["phase"] = phase
    return ("step", payload)


def _extract_section_prefix(text: str) -> str:
    m = re.search(r"\b(\d+(?:\.\d+){0,6})\b", text or "")
    return m.group(1) if m else ""


def _normalize_section_prefix_param(raw: str) -> str:
    """
    将 chroma_section_expand.section_prefix 规整为「4.2」式阿拉伯数字层级。
    「第1章」等与切片 section 元数据通常对不齐，返回空串以便归一化阶段丢弃该步；
    「第4.2节」等提取为 4.2。
    """
    s = (raw or "").strip().strip(".")
    if not s:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)*", s):
        return s
    if re.fullmatch(r"第\s*\d+\s*章", s):
        return ""
    m = re.search(r"第\s*(\d+(?:\.\d+)*)", s)
    if m:
        return m.group(1)
    return ""


def _looks_like_standard_process_question(text: str) -> bool:
    t = text or ""
    return bool(re.search(r"(标准|GB/T|GJB).*(试验过程|试验步骤|章节|第\d+(?:\.\d+)*)", t))


def _looks_like_node_type_enumeration_question(text: str) -> bool:
    """如「有哪些设备类型」「列出全部环境」等，应使用 neo_list_nodes。"""
    t = (plain_intent_slice(text) or (text or "")).strip()
    if not t:
        return False
    # 「XX 的测试/试验项目/指标/方法有哪些」是特定对象下的关系查询，不是全库枚举
    if re.search(
        r"(?:查询|请问|想了解一下|想了解)?\s*[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:测试|试验)?\s*"
        r"(?:项目|指标|方法|测试方法|试验方法)\s*(?:有哪些|都有什么|包括哪些|是什么)",
        t,
    ):
        return False
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些)\s*(?:测试项目|试验项目|指标|方法|测试方法|试验方法)",
        t,
    ) and not re.search(r"(全部|所有|整个库|整个系统|一共)", t):
        return False
    if re.search(r"(有哪些|都有什么|列出|列举|显示|查看|全部|所有)", t) and re.search(
        r"(设备类型|装备类型|设备类别|环境类型|试验项目|测试项目|指标|测试方法|试验方法|文件)",
        t,
    ):
        return True
    if re.search(r"(设备类型|装备类型|环境类型)\s*(有哪些|是什么|都有什么|包括哪些)", t):
        return True
    return False


def _default_label_for_enumeration_question(text: str) -> str:
    t = (text or "").strip()
    if "设备类型" in t or "装备类型" in t or "设备类别" in t:
        return "EquipmentType"
    if ("设备" in t or "装备" in t) and "类型" not in t and re.search(r"(有哪些|都有什么|列出|列举)", t):
        if "项目" not in t and "指标" not in t and "方法" not in t:
            return "Equipment"
    if "环境" in t:
        return "Environment"
    if "测试项目" in t or "试验项目" in t or ("项目" in t and ("测试" in t or "试验" in t)):
        return "TestProject"
    if "指标" in t:
        return "Indicator"
    if "方法" in t or "测试方法" in t or "试验方法" in t:
        return "TestMethod"
    if "文件" in t:
        return "File"
    return "EquipmentType"


def _v5_rows_non_empty(neo_rows: List[Dict[str, Any]], chroma_rows: List[Dict[str, Any]]) -> bool:
    def ok_block(xs: List[Dict[str, Any]]) -> bool:
        for x in xs or []:
            if not isinstance(x, dict):
                continue
            if x.get("error"):
                continue
            return True
        return False

    return ok_block(neo_rows) or ok_block(chroma_rows)


def _v5_chroma_nonempty(chroma_rows: List[Dict[str, Any]]) -> bool:
    return _v5_rows_non_empty([], chroma_rows)


def _extract_minio_path_from_metadata(md: Dict[str, Any]) -> str:
    if not isinstance(md, dict):
        return ""
    for k in ("source_file", "minio_path", "minIo_id", "minio_id", "original_file", "object_path"):
        v = str(md.get(k) or "").strip()
        if v and v.lower() not in ("none", "null"):
            return v
    return ""


def _infer_file_format_from_minio_path(path: str) -> str:
    p = (path or "").lower().split("?", 1)[0]
    if p.endswith(".pdf"):
        return "pdf"
    if p.endswith(".docx"):
        return "docx"
    if p.endswith(".doc"):
        return "doc"
    return "other"


def _normalize_chroma_rows_for_client(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in rows or []:
        if not isinstance(c, dict):
            continue
        n = dict(c)
        md = n.get("metadata") if isinstance(n.get("metadata"), dict) else {}
        path = _extract_minio_path_from_metadata(md)
        if path:
            n["minio_path"] = path
            if not str(n.get("source_file") or "").strip():
                n["source_file"] = path
        n["file_format"] = _infer_file_format_from_minio_path(path or str(n.get("source_file") or ""))
        out.append(n)
    return out


_SOURCE_PICK_START = "<<<SOURCE_PICK>>>"
_SOURCE_PICK_END = "<<<END_SOURCE_PICK>>>"


def _strip_and_parse_source_pick(text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    if not text:
        return text, None
    s = text
    start = s.find(_SOURCE_PICK_START)
    if start < 0:
        return s, None
    end = s.find(_SOURCE_PICK_END, start)
    if end < 0:
        return s[:start].strip(), None
    blob = s[start + len(_SOURCE_PICK_START) : end].strip()
    try:
        data = json.loads(blob)
    except Exception:
        logger.warning("[V5] SOURCE_PICK JSON 解析失败: %s", blob[:500])
        merged = (s[:start].strip() + s[end + len(_SOURCE_PICK_END) :].strip()).strip()
        return merged, None
    pick = data if isinstance(data, dict) else None
    merged = (s[:start].strip() + s[end + len(_SOURCE_PICK_END) :].strip()).strip()
    return merged, pick


def _single_chroma_source_view(c: Dict[str, Any], reason: str) -> Dict[str, Any]:
    md = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
    path = str(c.get("minio_path") or "").strip() or _extract_minio_path_from_metadata(md)
    fmt = str(c.get("file_format") or "").strip() or _infer_file_format_from_minio_path(path)
    return {
        "doc_id": str(c.get("doc_id") or ""),
        "title": str(c.get("title") or ""),
        "minio_path": path,
        "file_format": fmt,
        "reason": reason,
    }


def _merge_primary_source_with_chroma(
    pick: Optional[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not pick:
        return None
    doc_id = str(pick.get("doc_id") or "").strip()
    title = str(pick.get("title") or "").strip()
    reason = str(pick.get("reason") or "").strip()
    for c in chroma_rows or []:
        if not isinstance(c, dict) or c.get("error"):
            continue
        cid = str(c.get("doc_id") or "").strip()
        cti = str(c.get("title") or "").strip()
        if doc_id and cid == doc_id:
            return _single_chroma_source_view(c, reason)
        if title and (title == cti or title in cti or cti in title):
            return _single_chroma_source_view(c, reason)
    path = str(pick.get("minio_path") or "").strip()
    fmt = str(pick.get("file_format") or "").strip() or _infer_file_format_from_minio_path(path)
    return {
        "doc_id": doc_id,
        "title": title or "（未匹配到检索片段）",
        "minio_path": path,
        "file_format": fmt,
        "reason": reason,
    }


def _looks_like_followup_without_subject(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 48:
        return False
    if re.search(r"(无人机|坦克|雷达|通信设备|设备类型|环境)", t):
        return False
    return bool(re.search(r"(有什么|有哪些|哪些|如何).*(项目|指标|方法|试验|测试)", t))


def _extract_focus_from_summary(messages: List[Dict[str, Any]]) -> str:
    """
    在仅有「历史摘要」而缺少逐轮 user/assistant 消息时，从摘要中提取最近主体实体。
    """
    for m in reversed(messages[:-1]):
        if m.get("role") != "system":
            continue
        content = str(m.get("content") or "")
        if "历史对话摘要" not in content and "摘要" not in content:
            continue
        # 优先抓“某某...测试项目/指标/方法”前的对象词
        mt = re.search(
            r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,12})(?:的)?(?:测试项目|试验项目|指标|方法)",
            content,
        )
        if mt:
            return mt.group(1).strip()
        # 兜底：抓常见实体词
        for kw in ("无人机", "坦克", "雷达", "通信设备", "装备"):
            if kw in content:
                return kw
    return ""


def _second_last_user_text(messages: List[Dict[str, Any]]) -> str:
    us = [str(m.get("content") or "").strip() for m in messages if m.get("role") == "user"]
    if len(us) >= 2:
        return us[-2]
    return ""


def _extract_short_followup_entity(q: str) -> str:
    """识别「坦克呢」「那雷达呢」等短句中的新主体。"""
    t = (q or "").strip()
    if not t:
        return ""
    m = re.match(r"^(?:那|那么|还有|再|请问|想问)[，,。\s]*([\u4e00-\u9fffA-Za-z0-9·\-]{2,12})呢[？?！!。.\s]*$", t)
    if m:
        w = m.group(1).strip()
        if w not in ("什么", "哪些", "如何", "怎样", "这个", "那个"):
            return w
    ent = _extract_entity_from_user_turn(t)
    if ent:
        return ent
    return ""


def _query_tail_after_leading_entity(prev_user: str) -> str:
    """去掉上一轮句首实体/关键词，保留问法尾巴（如「有哪些测试项目」）。"""
    t = re.sub(r"^(?:那|那么|还有|请问|想问)[，,。\s]*", "", (prev_user or "").strip()).strip()
    if not t:
        return ""
    kw = _extract_ssyq_keyword_from_question(t)
    if len(kw) >= 2 and t.startswith(kw):
        rest = t[len(kw) :].strip().lstrip("，,的").strip()
        if rest:
            return rest
    m = re.match(r"^[\u4e00-\u9fffA-Za-z0-9·\-]{2,16}(?:的)?\s*", t)
    if m:
        rest = t[m.end() :].strip()
        if rest:
            return rest
    return ""


def _compose_followup_retrieval_question(ent: str, tail: str) -> str:
    tail = (tail or "").strip()
    if not tail:
        return f"【承前主体】{ent}。{ent}"
    if tail[0] in "的有哪包" or tail.startswith("的"):
        composed = f"{ent}{tail}"
    else:
        composed = f"{ent}的{tail}"
    return f"【承前主体】{ent}。{composed}"


def _apply_primary_focus_to_equipment_relation_steps(program: Dict[str, Any], retrieval_question: str) -> None:
    """若检索用语含【承前主体】，强制 Equipment 模糊 neo_relation 的 value 为该主体，避免 LLM 仍查上一轮设备。"""
    m = re.search(r"【承前主体】\s*([^。；;\n]+)", retrieval_question or "")
    if not m:
        return
    raw = m.group(1).strip()
    focus = re.split(r"[，,、\s]+", raw)[0].strip()
    if not focus or len(focus) > 24:
        return
    steps = program.get("steps")
    if not isinstance(steps, list):
        return
    for s in steps:
        if not isinstance(s, dict):
            continue
        if str(s.get("op_code") or "") != "neo_relation_query":
            continue
        pr = s.get("params") if isinstance(s.get("params"), dict) else {}
        pr = dict(pr)
        if str(pr.get("from_type") or "") != "Equipment":
            continue
        if str(pr.get("match_mode") or "fuzzy").lower() == "in":
            continue
        pr["value"] = focus
        s["params"] = pr


def _build_v5_retrieval_question(messages: List[Dict[str, Any]], question: str) -> str:
    q = (question or "").strip()
    ent_cur = _extract_short_followup_entity(q)
    prev_u = _second_last_user_text(messages)
    prev_clean = re.sub(r"^(?:那|那么|还有|请问|想问)[，,。\s]*", "", prev_u).strip()
    tail = _query_tail_after_leading_entity(prev_clean) if prev_clean else ""

    if ent_cur and prev_clean and tail and len(tail) >= 2:
        rq = _compose_followup_retrieval_question(ent_cur, tail)
        logger.info("[V5] retrieval_question built from short follow-up: %r", rq[:300])
        return rq

    rq = _enrich_retrieval_question_with_focus(messages, question)
    if "【承前主体】" in rq:
        return rq
    if not _looks_like_followup_without_subject(question):
        return rq
    focus = _extract_focus_from_summary(messages)
    if not focus:
        return rq
    if focus in rq:
        return rq
    return f"【承前主体】{focus}。{rq}"


def _natural_section_key(sec: str) -> Tuple:
    nums = [int(x) for x in re.findall(r"\d+", sec or "")]
    return tuple(nums) if nums else (10**9,)


def _parse_meta_program(content: str) -> Optional[Dict[str, Any]]:
    if not content:
        return None
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.replace("json", "", 1).strip()
    for cand in _meta_json_candidates(content):
        try:
            data = json.loads(cand)
            return data if isinstance(data, dict) else None
        except Exception:
            continue
    return None


def _meta_json_candidates(content: str) -> List[str]:
    """尝试多种切片，兼容定界符后仍有前后缀或围栏。"""
    s = (content or "").strip()
    out: List[str] = []
    seen: set = set()

    def push(x: str) -> None:
        x = x.strip()
        if len(x) < 2 or x in seen:
            return
        seen.add(x)
        out.append(x)

    push(s)
    if s.startswith("```"):
        t = s.strip("`").replace("json", "", 1).strip()
        push(t)
    i0, i1 = s.find("{"), s.rfind("}")
    if i0 >= 0 and i1 > i0:
        push(s[i0 : i1 + 1])
    return out


_META_JSON_MARKER = "<<<META_JSON>>>"


def _iter_meta_plan_stream(
    client,
    model: str,
    retrieval_question: str,
    meta_out: Dict[str, Any],
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, dict], None, None]:
    """
    首个规划 LLM：先流式输出定界符前的中文思考，再解析定界符后的 JSON。
    结束时写入 meta_out['program']、meta_out['raw']。
    """
    meta_out["program"] = None
    meta_out["raw"] = ""
    meta_out["think_emitted"] = False
    if not client:
        yield ("thinking", {"content": "（规划器不可用，将使用内置兜底检索步骤。）\n"})
        meta_out["think_emitted"] = True
        return

    ops = meta_ops_for_prompt()
    user_msg = {
        "role": "user",
        "content": json.dumps(
            {
                "question": retrieval_question,
                "meta_ops": ops,
                "instruction": (
                    "严格按系统说明：先写四行中文检索规划（1.～4.），再单独一行输出定界符 <<<META_JSON>>>，"
                    "最后输出 JSON；定界符前不要出现 JSON 花括号。"
                ),
            },
            ensure_ascii=False,
        ),
    }
    mo = int((llm_limits or {}).get("meta_plan_max_tokens") or 0) or 2400
    full = ""
    emitted_think = 0
    in_json = False
    marker = _META_JSON_MARKER
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": META_PLAN_PROMPT}, user_msg],
            temperature=0.22,
            max_tokens=min(mo, 4096),
            stream=True,
        )
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if not delta or not delta.content:
                continue
            piece = delta.content
            full += piece
            meta_out["raw"] = full
            if not in_json:
                if marker in full:
                    idx = full.index(marker)
                    emit = full[emitted_think:idx]
                    emitted_think = idx + len(marker)
                    in_json = True
                    if emit:
                        meta_out["think_emitted"] = True
                        yield ("thinking", {"content": emit})
                else:
                    safe = max(0, len(full) - len(marker) + 1)
                    if safe > emitted_think:
                        emit = full[emitted_think:safe]
                        emitted_think = safe
                        if emit:
                            meta_out["think_emitted"] = True
                            yield ("thinking", {"content": emit})
    except Exception as e:
        logger.warning("[V5] meta plan stream failed: %s", e)
        yield ("thinking", {"content": f"\n（规划流式生成异常：{e}，将尝试非流式兜底。）\n"})
        meta_out["think_emitted"] = True
        sync_prog = _build_meta_program_sync(client, model, retrieval_question)
        meta_out["program"] = sync_prog
        meta_out["raw"] = meta_out.get("raw") or ""
        return

    if not in_json:
        tail = full[emitted_think:]
        cand_parse = _parse_meta_program(full.strip())
        if tail.strip() and cand_parse is None:
            meta_out["think_emitted"] = True
            yield ("thinking", {"content": tail})
        meta_out["program"] = cand_parse
    else:
        json_part = full[emitted_think:]
        meta_out["program"] = _parse_meta_program(json_part)

    if meta_out["program"] is None and full.strip():
        logger.warning("[V5][META] 流式输出 JSON 解析失败，尝试非流式兜底")
        meta_out["program"] = _build_meta_program_sync(client, model, retrieval_question)

    logger.info("[V5][META] LLM 规划完整输出（截断）:\n%s", full[:12000])
    if meta_out.get("program"):
        logger.info("[V5][META] 解析后的 JSON:\n%s", _safe_json(meta_out["program"], 12000))


def _build_meta_program_sync(client, model: str, retrieval_question: str) -> Optional[Dict[str, Any]]:
    if not client:
        return None
    ops = meta_ops_for_prompt()
    user_msg = {
        "role": "user",
        "content": json.dumps(
            {
                "question": retrieval_question,
                "meta_ops": ops,
                "instruction": (
                    "若无法分两段输出，则仅输出一个 JSON 对象（不要 markdown），"
                    "且尽量包含 intent_summary、focus_entity、answer_mode、steps。"
                ),
            },
            ensure_ascii=False,
        ),
    }
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": META_PLAN_PROMPT}, user_msg],
            temperature=0.22,
            max_tokens=1200,
            stream=False,
        )
        txt = (resp.choices[0].message.content or "").strip()
        logger.info("[V5][META] 非流式兜底原始输出:\n%s", txt[:12000])
        parsed = _parse_meta_program(txt)
        if _META_JSON_MARKER in txt:
            _, _, after = txt.partition(_META_JSON_MARKER)
            parsed = _parse_meta_program(after) or parsed
        return parsed
    except Exception as e:
        logger.warning("[V5] meta plan sync generation failed: %s", e)
        return None


def _build_meta_program(client, model: str, retrieval_question: str) -> Optional[Dict[str, Any]]:
    """非流式单次规划（兼容旧调用；主流程使用 _iter_meta_plan_stream）。"""
    return _build_meta_program_sync(client, model, retrieval_question)


_PROJECT_OR_ENV_IN_EQUIP_VALUE = re.compile(
    r"(测试|试验|鉴定|环境|高温|低温|湿热|盐雾|淋雨|振动|沙尘|老化|可靠性|力学|emc|海拔|霉菌)",
    re.I,
)


def _looks_like_project_or_env_in_equipment_value(val: str) -> bool:
    """Equipment 模糊匹配 value 若含项目/环境类词，则几乎肯定不是装备名（图里 CON 起点是装备 n）。"""
    return bool(_PROJECT_OR_ENV_IN_EQUIP_VALUE.search((val or "").strip()))


def _last_equipment_neo_query_value_before(steps: List[Dict[str, Any]], before_index: int) -> str:
    for j in range(before_index - 1, -1, -1):
        if j < 0:
            break
        st = steps[j]
        if not isinstance(st, dict):
            continue
        if str(st.get("op_code")) != "neo_node_query":
            continue
        pr = st.get("params") if isinstance(st.get("params"), dict) else {}
        if str(pr.get("node_type") or "") != "Equipment":
            continue
        v = str(pr.get("value") or "").strip()
        if v:
            return v
    return ""


def _anchor_from_retrieval_question(rq: str) -> str:
    m = re.search(r"【承前主体】\s*([^。；;\n]+)", rq or "")
    if not m:
        return ""
    seg = (m.group(1) or "").strip()
    if not seg:
        return ""
    return re.split(r"[，,、\s]+", seg)[0].strip()


def _repair_equipment_to_testproject_fuzzy_values(
    steps: List[Dict[str, Any]],
    program: Dict[str, Any],
    retrieval_question: str,
) -> None:
    """
    LLM 常错把「高温测试」写进 Equipment-[CON]->TestProject 的 fuzzy value；
    该关系的 WHERE 子句匹配的是**装备 n 的 Name**，value 必须是装备短名。
    """
    if not steps:
        return
    focus = str(program.get("focus_entity") or "").strip()
    anchor = _anchor_from_retrieval_question(retrieval_question)
    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            continue
        if str(st.get("op_code")) != "neo_relation_query":
            continue
        pr = st.get("params") if isinstance(st.get("params"), dict) else {}
        if str(pr.get("from_type")) != "Equipment" or str(pr.get("to_type")) != "TestProject":
            continue
        if str(pr.get("where_side") or "from").lower() == "to":
            continue
        if str(pr.get("match_mode") or "fuzzy").lower() != "fuzzy":
            continue
        val = str(pr.get("value") or "").strip()
        if not val or not _looks_like_project_or_env_in_equipment_value(val):
            continue
        equip = _last_equipment_neo_query_value_before(steps, i)
        replacement = equip
        if not replacement and focus and not _looks_like_project_or_env_in_equipment_value(focus):
            replacement = focus
        if not replacement and anchor and not _looks_like_project_or_env_in_equipment_value(anchor):
            replacement = anchor
        if not replacement or replacement == val:
            continue
        pr2 = dict(pr)
        pr2["value"] = replacement
        st["params"] = pr2
        logger.warning(
            "[V5][META] 已修正 Equipment→TestProject 模糊 value：%r → %r（装备侧只能匹配装备名；"
            "项目/环境词应放在 neo_node_query(TestProject) 或 neo_property_filter target=m）",
            val,
            replacement,
        )


def _repair_testproject_method_ref_target(steps: List[Dict[str, Any]]) -> None:
    """上一步为 Equipment-[CON]->TestProject 时，项目节点在 m；后续 TestProject→TestMethod 的 ref 误写 target=n 应改为 m。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for st in steps:
        if isinstance(st, dict) and st.get("step_id"):
            by_id[str(st["step_id"])] = st
    for st in steps:
        if not isinstance(st, dict):
            continue
        if str(st.get("op_code")) != "neo_relation_query":
            continue
        pr = st.get("params") if isinstance(st.get("params"), dict) else {}
        if str(pr.get("from_type")) != "TestProject" or str(pr.get("to_type")) != "TestMethod":
            continue
        ref = pr.get("from_values_ref")
        if not isinstance(ref, dict):
            continue
        if str(ref.get("target") or "").strip().lower() != "n":
            continue
        sid = str(ref.get("step_id") or "").strip()
        prev = by_id.get(sid)
        if not prev:
            continue
        p0 = prev.get("params") if isinstance(prev.get("params"), dict) else {}
        if str(p0.get("from_type")) != "Equipment" or str(p0.get("to_type")) != "TestProject":
            continue
        ref2 = dict(ref)
        ref2["target"] = "m"
        pr2 = dict(pr)
        pr2["from_values_ref"] = ref2
        st["params"] = pr2
        logger.warning(
            "[V5][META] 已修正 TestProject→TestMethod 的 from_values_ref.target：n → m（引用步骤 %s 为 Equipment→TestProject，项目 Id 在返回的 m）",
            sid,
        )


def _coerce_llm_misplan_to_equipment_discovery(
    steps: List[Dict[str, Any]],
    retrieval_question: str,
    user_question: str,
) -> None:
    """规划器只写了 neo_node_query(TestProject) 时，纠正为「按项目关键词列设备」。"""
    ctx = f"{(user_question or '').strip()}\n{plain_intent_slice(retrieval_question) or retrieval_question or ''}".strip()
    if relation_target_kind(ctx) != "equipment":
        return
    pk = _extract_project_keyword_for_equipment_discovery(user_question, retrieval_question)
    if len(pk) < 1:
        return
    for st in steps:
        if not isinstance(st, dict):
            continue
        if str(st.get("op_code")) != "neo_relation_query":
            continue
        pr = st.get("params") if isinstance(st.get("params"), dict) else {}
        if (
            str(pr.get("from_type")) == "Equipment"
            and str(pr.get("to_type")) == "TestProject"
            and str(pr.get("where_side") or "from").lower() == "to"
        ):
            return
    if len(steps) != 1:
        return
    st0 = steps[0]
    if str(st0.get("op_code")) != "neo_node_query":
        return
    pr0 = st0.get("params") if isinstance(st0.get("params"), dict) else {}
    if str(pr0.get("node_type")) != "TestProject":
        return
    steps[:] = [
        {
            "step_id": "s1",
            "op_code": "neo_relation_query",
            "params": {
                "from_type": "Equipment",
                "rel_type": "CON",
                "to_type": "TestProject",
                "from_field": "Name",
                "match_mode": "fuzzy",
                "where_side": "to",
                "value": pk,
                "limit": 200,
            },
            "purpose": "后处理：纠错为列出具备该关键词测试项目的设备",
        }
    ]
    logger.warning("[V5][DISCOVERY] LLM 仅查 TestProject，已改为 Equipment→TestProject（where_side=to）")


def _repair_equipment_list_steps_for_question(
    steps: List[Dict[str, Any]],
    retrieval_question: str,
    user_question: str,
) -> None:
    """用户明确问装备下「指标」列表时，将误规划为 Equipment→TestProject 的一步改为 Indicator。"""
    ctx = f"{(user_question or '').strip()}\n{plain_intent_slice(retrieval_question) or retrieval_question or ''}".strip()
    if relation_target_kind(ctx) != "indicator":
        return
    for st in steps:
        if not isinstance(st, dict):
            continue
        if str(st.get("op_code")) != "neo_relation_query":
            continue
        pr = st.get("params") if isinstance(st.get("params"), dict) else {}
        if str(pr.get("from_type")) != "Equipment" or str(pr.get("rel_type")) != "CON":
            continue
        if str(pr.get("to_type")) != "TestProject":
            continue
        if str(pr.get("where_side") or "from").lower() == "to":
            continue
        pr2 = dict(pr)
        pr2["to_type"] = "Indicator"
        pr2 = clamp_meta_op_params("neo_relation_query", pr2)
        errs = validate_meta_op("neo_relation_query", pr2)
        if errs:
            logger.warning("[V5][LIST] 指标目标修正未通过校验: %s", errs)
            continue
        st["params"] = pr2
        st["purpose"] = str(st.get("purpose") or "") + "（后处理：问句为指标列表，to_type 已改为 Indicator）"
        logger.info("[V5][LIST] 已将 Equipment→TestProject 修正为 Equipment→Indicator")


def _normalize_program(
    program: Optional[Dict[str, Any]], retrieval_question: str, user_question: str = ""
) -> Dict[str, Any]:
    if not isinstance(program, dict):
        program = {}
    steps = program.get("steps") if isinstance(program.get("steps"), list) else []
    fallback_v4 = bool(program.get("fallback_v4", False))
    answer_mode = str(program.get("answer_mode") or "llm_synthesize").strip().lower()
    if answer_mode not in ("database_direct", "llm_synthesize"):
        answer_mode = "llm_synthesize"

    normalized_steps: List[Dict[str, Any]] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        op_code = str(s.get("op_code") or "").strip()
        params = s.get("params") if isinstance(s.get("params"), dict) else {}
        params = dict(params)
        if op_code in ("neo_node_query", "neo_property_filter", "neo_relation_query"):
            mm = str(params.get("match_mode") or "").strip().lower()
            if mm == "exact":
                params["match_mode"] = "fuzzy"
        params = clamp_meta_op_params(op_code, params)
        if op_code == "chroma_section_expand":
            raw_sp = str(params.get("section_prefix") or "")
            nsp = _normalize_section_prefix_param(raw_sp)
            if raw_sp.strip() and not nsp:
                logger.warning(
                    "[V5] chroma_section_expand.section_prefix 无效（将省略该步）: raw=%r",
                    raw_sp,
                )
            elif raw_sp.strip() and raw_sp.strip() != nsp:
                logger.info("[V5] chroma_section_expand.section_prefix %r -> %r", raw_sp, nsp)
            params["section_prefix"] = nsp
        errs = validate_meta_op(op_code, params)
        if errs:
            logger.warning("[V5] invalid step op=%s errs=%s", op_code, errs)
            continue
        normalized_steps.append(
            {
                "step_id": str(s.get("step_id") or f"s{i+1}"),
                "op_code": op_code,
                "params": params,
                "purpose": str(s.get("purpose") or ""),
            }
        )

    if not normalized_steps:
        rq_plain = plain_intent_slice(retrieval_question) or retrieval_question
        kw = _extract_ssyq_keyword_from_question(rq_plain) or rq_plain[:18]
        list_ctx = f"{(user_question or '').strip()}\n{rq_plain}".strip()
        rk = relation_target_kind(list_ctx)
        if _looks_like_node_type_enumeration_question(retrieval_question):
            label = _default_label_for_enumeration_question(rq_plain)
            normalized_steps = [
                {
                    "step_id": "s1",
                    "op_code": "neo_list_nodes",
                    "params": {"node_type": label, "limit": 300, "status_filter": 1},
                    "purpose": f"列举 {label} 类型节点",
                }
            ]
            answer_mode = "database_direct"
        elif _looks_like_standard_process_question(retrieval_question):
            sec = _extract_section_prefix(retrieval_question)
            normalized_steps = [
                {"step_id": "s1", "op_code": "chroma_vector_search", "params": {"query": retrieval_question, "top_k": 30}},
                {"step_id": "s2", "op_code": "chroma_rerank", "params": {"query": retrieval_question, "top_k": 30}},
            ]
            if sec:
                normalized_steps.append(
                    {
                        "step_id": "s3",
                        "op_code": "chroma_section_expand",
                        "params": {"section_prefix": sec, "max_chunks": 500},
                    }
                )
            answer_mode = "database_direct"
        elif rk == "indicator":
            normalized_steps = [
                {
                    "step_id": "s1",
                    "op_code": "neo_relation_query",
                    "params": {
                        "from_type": "Equipment",
                        "rel_type": "CON",
                        "to_type": "Indicator",
                        "from_field": "Name",
                        "match_mode": "fuzzy",
                        "value": kw,
                        "limit": 80,
                    },
                    "purpose": "兜底：装备关联指标",
                }
            ]
        elif rk == "project":
            normalized_steps = [
                {
                    "step_id": "s1",
                    "op_code": "neo_relation_query",
                    "params": {
                        "from_type": "Equipment",
                        "rel_type": "CON",
                        "to_type": "TestProject",
                        "from_field": "Name",
                        "match_mode": "fuzzy",
                        "value": kw,
                        "limit": 80,
                    },
                    "purpose": "兜底：装备关联测试项目",
                }
            ]
        elif rk == "equipment":
            pk = _extract_project_keyword_for_equipment_discovery(user_question, rq_plain)
            if len(pk) >= 1:
                normalized_steps = [
                    {
                        "step_id": "s1",
                        "op_code": "neo_relation_query",
                        "params": {
                            "from_type": "Equipment",
                            "rel_type": "CON",
                            "to_type": "TestProject",
                            "from_field": "Name",
                            "match_mode": "fuzzy",
                            "where_side": "to",
                            "value": pk,
                            "limit": 200,
                        },
                        "purpose": "兜底：按项目名称过滤，列出关联设备",
                    }
                ]
            else:
                normalized_steps = [
                    {
                        "step_id": "s1",
                        "op_code": "neo_relation_query",
                        "params": {
                            "from_type": "Equipment",
                            "rel_type": "CON",
                            "to_type": "TestProject",
                            "from_field": "Name",
                            "match_mode": "fuzzy",
                            "value": kw,
                            "limit": 80,
                        },
                    },
                    {
                        "step_id": "s2",
                        "op_code": "neo_relation_query",
                        "params": {
                            "from_type": "Equipment",
                            "rel_type": "CON",
                            "to_type": "Indicator",
                            "from_field": "Name",
                            "match_mode": "fuzzy",
                            "value": kw,
                            "limit": 80,
                        },
                    },
                ]
        else:
            normalized_steps = [
                {
                    "step_id": "s1",
                    "op_code": "neo_relation_query",
                    "params": {
                        "from_type": "Equipment",
                        "rel_type": "CON",
                        "to_type": "TestProject",
                        "from_field": "Name",
                        "match_mode": "fuzzy",
                        "value": kw,
                        "limit": 80,
                    },
                },
                {
                    "step_id": "s2",
                    "op_code": "neo_relation_query",
                    "params": {
                        "from_type": "Equipment",
                        "rel_type": "CON",
                        "to_type": "Indicator",
                        "from_field": "Name",
                        "match_mode": "fuzzy",
                        "value": kw,
                        "limit": 80,
                    },
                },
            ]
    _repair_equipment_to_testproject_fuzzy_values(
        normalized_steps,
        program if isinstance(program, dict) else {},
        retrieval_question,
    )
    _repair_testproject_method_ref_target(normalized_steps)
    normalized_steps = _validate_relation_path(normalized_steps, retrieval_question)
    _repair_equipment_list_steps_for_question(normalized_steps, retrieval_question, user_question)
    _coerce_llm_misplan_to_equipment_discovery(normalized_steps, retrieval_question, user_question)
    inferred_intent = str(program.get("_v5_intent") or "").strip()
    if not inferred_intent and normalized_steps:
        op0 = str(normalized_steps[0].get("op_code") or "")
        if op0 == "neo_list_nodes":
            inferred_intent = Intent.ENUMERATION.value
        elif op0 in ("chroma_vector_search",):
            inferred_intent = Intent.STANDARD_CHAPTER.value
        elif op0 == "neo_relation_query":
            inferred_intent = Intent.RELATION_QUERY.value
    out = {
        "intent_summary": str(program.get("intent_summary") or ""),
        "focus_entity": str(program.get("focus_entity") or ""),
        "answer_mode": answer_mode,
        "fallback_v4": fallback_v4,
        "missing_capability": str(program.get("missing_capability") or ""),
        "steps": normalized_steps,
        "plan_inferred_question": str(program.get("plan_inferred_question") or "").strip(),
        "plan_what_to_retrieve_nl": str(program.get("plan_what_to_retrieve_nl") or "").strip(),
        "plan_retrieve_order_nl": str(program.get("plan_retrieve_order_nl") or "").strip(),
        "_v5_fast_route": bool(program.get("_v5_fast_route")),
        "_v5_intent": inferred_intent,
    }
    if out.get("steps"):
        try:
            from .znwd_thinking_narrative import enrich_program_display

            enrich_program_display(out)
        except Exception:
            pass
    logger.info("[V5][META] 归一化后的检索程序（将执行）:\n%s", _safe_json(out, 12000))
    return out


def _plan_retrieve_order_nl_fallback(steps: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            continue
        # 优先使用 display_text（语义化），其次使用 purpose
        display_text = s.get("display_text", "")
        purpose = str(s.get("purpose") or "").strip()
        text = display_text or purpose
        if text:
            lines.append(f"第{i + 1} 步：{text}")
    return " → ".join(lines) if lines else ""


def _plan_what_to_retrieve_nl_fallback(program: Dict[str, Any]) -> str:
    steps = program.get("steps") if isinstance(program.get("steps"), list) else []
    parts: List[str] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        # 优先使用 display_text（语义化），其次使用 purpose
        display_text = s.get("display_text", "")
        pur = display_text or str(s.get("purpose") or "").strip()
        if pur:
            parts.append(pur)
    return "；".join(parts) if parts else ""


def _plan_thinking_display_text(program: Dict[str, Any], retrieval_question: str) -> str:
    """
    生成检索规划的显示文本（面向用户的中文业务语义，无模板代号/函数名）。
    """
    from .znwd_thinking_narrative import build_retrieval_planning_text

    if program.get("_business_template") or program.get("_v5_fast_route"):
        return build_retrieval_planning_text(program, retrieval_question)

    # [未启用] business_templates.json + znwd_template_config 展示模板（无配置文件时跳过）
    # from .znwd_template_config import get_template_config
    # ... display_template.planning 替换逻辑 ...

    try:
        from .znwd_thinking_narrative import enrich_program_display

        program = enrich_program_display(dict(program))
        return build_retrieval_planning_text(program, retrieval_question)
    except Exception:
        pass

    iq = str(program.get("plan_inferred_question") or program.get("intent_summary_user") or "").strip()
    if not iq:
        iq = str(program.get("intent_summary") or "").strip()
    if not iq:
        iq = (retrieval_question or "").strip()[:200]

    what = str(program.get("plan_what_to_retrieve_nl") or "").strip()
    if not what:
        what = _plan_what_to_retrieve_nl_fallback(program)

    order = str(program.get("plan_retrieve_order_nl") or "").strip()
    if not order:
        order = _plan_retrieve_order_nl_fallback(program.get("steps") or [])
    if not order:
        order = "按已生成步骤依次在清单库与文档库中检索。"

    lines = [
        "【检索规划】",
        f"1. 结合对话历史对用户意图的理解：{iq or '将根据您的问题检索相关信息。'}",
        f"2. 拟在图谱/文档侧了解的内容：{what or '在清单库与文档库中查找相关条目。'}",
        f"3. 检索顺序：{order}",
        "4. 以下开始按规划在后台执行检索。",
    ]
    return "\n".join(lines)


def _build_ssyq_table_keyword_v5(
    messages: List[Dict[str, Any]],
    retrieval_question: str,
    program: Optional[Dict[str, Any]],
    last_question: str,
) -> str:
    """
    表格检索词：优先承前设备/规划器 focus_entity，再拼本轮问句短词（顿号分隔），
    便于 /source/table 多段匹配（如「坦克、命中率」）。
    """
    program = program or {}
    base = _extract_ssyq_keyword_from_question(retrieval_question).strip()
    focus = str(program.get("focus_entity") or "").strip()
    m = re.search(r"【承前主体】\s*([^。；;\n]+)", retrieval_question or "")
    if m:
        anchor = m.group(1).strip()
        if anchor:
            focus = focus or anchor
    if not focus:
        focus = _infer_focus_entity_from_messages(messages, last_question).strip()
    chunks: List[str] = []
    seen: set = set()
    for p in (focus, base):
        p = (p or "").strip()
        if len(p) < 2 or p in ("什么", "哪些", "如何", "怎样"):
            continue
        if p in seen:
            continue
        if any(p != x and (p in x or x in p) for x in chunks):
            continue
        chunks.append(p)
        seen.add(p)
    if not chunks:
        wq = _extract_ssyq_keyword_from_question(last_question).strip()
        wr = _extract_ssyq_keyword_from_question(retrieval_question).strip()
        tail = (wq or wr or (last_question or "").strip()[:24] or (retrieval_question or "").strip()[:24]).strip()
        merged = (base or focus or tail).strip()
        return merged[:80] if merged else ""
    if len(chunks) == 1:
        return chunks[0][:60]
    return "、".join(c[:24] for c in chunks)[:80]


_NEO_LABEL_ZH = {
    "Equipment": "设备",
    "EquipmentType": "设备类型",
    "Environment": "试验环境",
    "TestProject": "测试项目",
    "Indicator": "指标",
    "TestMethod": "测试方法",
    "File": "文件",
    "Node": "关联信息",
    "Other": "关联信息",
}


def _neo_label_zh(lab: str) -> str:
    k = str(lab or "").strip()
    if not k:
        return "关联信息"
    return _NEO_LABEL_ZH.get(k, k)


_KIND_ORDER = (
    "TestMethod",
    "Indicator",
    "TestProject",
    "Equipment",
    "Environment",
    "EquipmentType",
    "File",
    "Other",
)


def _kind_sort_key(k: str) -> int:
    try:
        return _KIND_ORDER.index(k)
    except ValueError:
        return 99


def _infer_neo_node_kind(node: Dict[str, Any]) -> str:
    """当 Neo4j 返回缺少 labels 时，根据属性推断图节点类型（用于面向用户的分组标题）。"""
    if not isinstance(node, dict):
        return "Other"
    labs = node.get("labels") if isinstance(node.get("labels"), list) else []
    if labs:
        return str(labs[0])
    p = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    if any(str(p.get(k) or "").strip() for k in ("SYGC", "SYTJ", "JGPD")) or str(p.get("Source_name") or "").strip():
        return "TestMethod"
    if str(p.get("Type") or "").strip().lower() in ("test", "category"):
        return "TestProject"
    if any(k in p for k in ("Combination", "Request", "Limit", "Class")) and p.get("Name"):
        return "Environment"
    if p.get("EquipmentTypeId"):
        return "Equipment"
    return "Other"


def _friendly_neo_display_name(node: Dict[str, Any]) -> str:
    """优先用业务名、标准名等展示，避免「未命名对象」类技术占位。"""
    if not isinstance(node, dict):
        return ""
    p = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    for key in ("Name", "Label", "Source_name", "Project_name", "Indicator_name", "title"):
        v = str(p.get(key) or "").strip()
        if v:
            return v[:400]
    return ""


def _run_neo_cypher(driver, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not driver:
        return [{"error": "Neo4j driver not initialized"}]
    out: List[Dict[str, Any]] = []
    try:
        logger.warning(
            "[ZNWD][CYPHER_EXEC] %s",
            (cypher or "").strip().replace("\n", " "),
        )
        logger.warning(
            "[ZNWD][CYPHER_EXEC][PARAMS] %s",
            json.dumps(params, ensure_ascii=False, default=str)[:12000],
        )
        with driver.session() as session:
            result = session.run(cypher, **params)
            for rec in result:
                row: Dict[str, Any] = {}
                for k in rec.keys():
                    v = rec.get(k)
                    # 顺序很重要：Relationship 也有 items()，但没有 labels，先按关系分支处理
                    if hasattr(v, "type") and not hasattr(v, "labels"):
                        row[k] = {
                            "type": getattr(v, "type", ""),
                            "properties": dict(v.items()) if hasattr(v, "items") else {},
                        }
                    elif hasattr(v, "labels") and hasattr(v, "items"):
                        row[k] = {
                            "labels": list(v.labels),
                            "properties": {x: y for x, y in v.items() if y is not None},
                            "__elementId": getattr(v, "element_id", None),
                        }
                    else:
                        row[k] = v
                out.append(row)
    except Exception as e:
        logger.error("[V5] neo cypher failed: %s", e)
        return [{"error": f"neo4j query failed: {e}"}]
    return out


def _execute_neo_step(driver, op_code: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    cfg = META_OPS.get(op_code) or {}
    template = cfg.get("cypher_template", "")
    cypher_for_log = ""
    # 通用节点查询
    if op_code == "neo_node_query":
        node_type = str(params.get("node_type") or "Equipment")
        field = str(params.get("field") or "Name")
        value = str(params.get("value") or "")
        limit = int(params.get("limit") or 50)
        where_clause = f"toLower(coalesce(n.{field},'')) CONTAINS toLower($value)"
        cypher = f"MATCH (n:{node_type}) WHERE {where_clause} RETURN n LIMIT $limit"
        cypher_for_log = cypher
        logger.info(
            "[V5][NEO] op=%s cypher=\n%s\nparams=%s",
            op_code,
            cypher_for_log,
            _safe_json({"value": value, "limit": limit}, 2000),
        )
        return _run_neo_cypher(driver, cypher, {"value": value, "limit": limit})
    if op_code == "neo_list_nodes":
        node_type = str(params.get("node_type") or "Equipment")
        if node_type not in NODE_LABELS:
            return [{"error": f"invalid node_type: {node_type}"}]
        limit = int(params.get("limit") or 200)
        limit = max(1, min(limit, 500))
        status_filter = int(params.get("status_filter", 1))
        if status_filter:
            stp = flgl_status_cypher_param()
            cypher = (
                f"MATCH (n:{node_type}) "
                "WHERE n.Status IS NULL OR n.Status IN $flgl_enabled_status "
                "RETURN n "
                "ORDER BY coalesce(toString(n.Name), toString(n.Label), toString(n.Id), '') "
                "LIMIT $limit"
            )
            run_p = {**stp, "limit": limit}
        else:
            cypher = (
                f"MATCH (n:{node_type}) RETURN n "
                "ORDER BY coalesce(toString(n.Name), toString(n.Label), toString(n.Id), '') "
                "LIMIT $limit"
            )
            run_p = {"limit": limit}
        logger.info("[V5][NEO] op=%s node_type=%s limit=%s status_filter=%s", op_code, node_type, limit, status_filter)
        return _run_neo_cypher(driver, cypher, run_p)
    # 通用关系查询
    if op_code == "neo_relation_query":
        from_type = str(params.get("from_type") or "")
        rel_type = str(params.get("rel_type") or "")
        to_type = str(params.get("to_type") or "")
        from_field = str(params.get("from_field") or "Name")
        mode = str(params.get("match_mode") or "fuzzy").lower()
        if mode != "in":
            mode = "fuzzy"
        value = str(params.get("value") or "")
        value_list = params.get("value_list") if isinstance(params.get("value_list"), list) else []
        limit = int(params.get("limit") or 30)
        limit = max(1, min(limit, 500))
        where_side = str(params.get("where_side") or "from").lower()
        if where_side not in ("from", "to"):
            where_side = "from"
        wn = "n" if where_side == "from" else "m"
        if mode == "in":
            where_clause = f"toString(coalesce({wn}.{from_field},'')) IN $value_list"
        else:
            where_clause = f"toLower(coalesce({wn}.{from_field},'')) CONTAINS toLower($value)"
        cypher = template.format(node1=from_type, rel=rel_type, node2=to_type, where_clause=where_clause)
        cypher_for_log = cypher
        run_params: Dict[str, Any] = {"limit": limit}
        if mode == "in":
            run_params["value_list"] = [str(x) for x in value_list if str(x).strip()]
            if not run_params["value_list"]:
                logger.warning("[V5][NEO] op=%s match_mode=in 但 value_list 为空，跳过", op_code)
                return []
        else:
            run_params["value"] = value
        logger.info(
            "[V5][NEO] op=%s cypher=\n%s\nparams=%s",
            op_code,
            cypher_for_log,
            _safe_json(run_params, 4000),
        )
        return _run_neo_cypher(driver, cypher, run_params)
    # 兼容旧 op
    if op_code == "neo_relation_lookup":
        cypher = template.format(node1=params["node1"], rel=params["rel"], node2=params["node2"], where_clause="toLower(coalesce(n.Name,'')) CONTAINS toLower($value)")
        logger.info("[V5][NEO] op=%s cypher=\n%s", op_code, cypher)
        return _run_neo_cypher(driver, cypher, {"value": params.get("keyword", ""), "limit": params.get("limit", 30)})
    if template:
        logger.info("[V5][NEO] op=%s template 直跑 params=%s", op_code, _safe_json(params, 2000))
        return _run_neo_cypher(driver, template, params)
    return [{"error": f"unsupported neo op: {op_code}"}]


def _chroma_vector_query(collection, query: str, top_k: int = 12, doc_id: str = "", title_keyword: str = "") -> List[Dict[str, Any]]:
    if not collection:
        return [{"error": "Chroma collection not initialized"}]
    chunks: List[Dict[str, Any]] = []
    where = {"doc_id": doc_id} if doc_id else None
    try:
        res = collection.query(query_texts=[query], n_results=max(1, min(top_k, 200)), where=where)
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            md = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
            if title_keyword and title_keyword not in str(md.get("title") or ""):
                continue
            chunks.append(
                {
                    "doc_id": md.get("doc_id", ""),
                    "title": md.get("title", ""),
                    "content": doc or "",
                    "distance": dists[i] if i < len(dists) else None,
                    "metadata": md,
                }
            )
        return chunks
    except Exception:
        # 回退 v4 的通用检索（不带 distance）
        basic = search_knowledge_base(collection, query, top_k=top_k)
        for b in basic:
            if not isinstance(b, dict) or b.get("error"):
                continue
            if doc_id and str(b.get("doc_id") or "") != doc_id:
                continue
            if title_keyword and title_keyword not in str(b.get("title") or ""):
                continue
            chunks.append({**b, "distance": None, "metadata": {}})
        return chunks


def _score_filter(chunks: List[Dict[str, Any]], min_score: float = 0.0, max_distance: float = 2.0) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in chunks:
        d = c.get("distance")
        s = c.get("score")
        if s is not None and isinstance(s, (int, float)) and float(s) < float(min_score):
            continue
        if d is not None and isinstance(d, (int, float)) and float(d) > float(max_distance):
            continue
        out.append(c)
    return out


def _simple_rerank(chunks: List[Dict[str, Any]], query: str, top_k: int = 12) -> List[Dict[str, Any]]:
    terms = [x for x in re.split(r"[\s,，。；;]+", query or "") if x]
    scored = []
    for c in chunks:
        text = f"{c.get('title','')} {c.get('content','')}"
        hit = sum(text.count(t) for t in terms)
        dist = c.get("distance")
        dist_penalty = float(dist) if isinstance(dist, (int, float)) else 0.0
        score = hit * 2.0 - dist_penalty
        n = dict(c)
        n["score"] = score
        scored.append(n)
    scored.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return scored[: max(1, min(top_k, 200))]


def _extract_section_no_from_chunk(c: Dict[str, Any]) -> str:
    md = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
    for k in ("section", "section_no", "chapter", "heading_no"):
        v = str(md.get(k) or "").strip()
        if v:
            return v
    text = f"{c.get('title','')} {c.get('content','')}"
    m = re.search(r"\b(\d+(?:\.\d+){0,6})\b", text)
    return m.group(1) if m else ""


def _section_expand(chunks: List[Dict[str, Any]], section_prefix: str, doc_id: str = "", title_keyword: str = "") -> List[Dict[str, Any]]:
    prefix = _normalize_section_prefix_param(section_prefix)
    if not prefix:
        return list(chunks) if chunks else []
    out: List[Dict[str, Any]] = []
    for c in chunks:
        if doc_id and str(c.get("doc_id") or "") != doc_id:
            continue
        if title_keyword and title_keyword not in str(c.get("title") or ""):
            continue
        sec = _extract_section_no_from_chunk(c)
        if not sec:
            continue
        if sec == prefix or sec.startswith(prefix + "."):
            n = dict(c)
            n["section_no"] = sec
            out.append(n)
    out.sort(key=lambda x: _natural_section_key(str(x.get("section_no") or "")))
    if not out and chunks:
        logger.warning(
            "[V5][CHROMA] chroma_section_expand: 前缀 %r 未命中任何带章节号的片段，保留扩展前 %d 条",
            prefix,
            len(chunks),
        )
        return list(chunks)
    return out


def _format_section_direct_answer(question: str, chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return "未在文档库中命中对应章节内容。"
    lines = [f"针对问题「{question}」，检索到以下章节内容：", ""]
    for c in chunks:
        sec = str(c.get("section_no") or "")
        title = str(c.get("title") or "")
        content = str(c.get("content") or "").strip()
        head = f"{sec} {title}".strip()
        lines.append(f"### {head}" if head else "### 章节")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def _format_neo_direct_answer(question: str, neo_rows: List[Dict[str, Any]]) -> str:
    """面向前端的数据库直出，不暴露内部 evidence JSON；用语贴近业务人员，避免 Node/未命名对象 等说法。"""
    if not neo_rows:
        return "未在图数据库命中相关内容。"

    def _row_is_n_only(row: Dict[str, Any]) -> bool:
        n = row.get("n") if isinstance(row.get("n"), dict) else {}
        m = row.get("m") if isinstance(row.get("m"), dict) else {}
        np = n.get("properties") if isinstance(n.get("properties"), dict) else {}
        mp = m.get("properties") if isinstance(m.get("properties"), dict) else {}
        return bool(np) and not mp

    if all(_row_is_n_only(r) if isinstance(r, dict) else False for r in neo_rows):
        lines: List[str] = [f"针对问题「{question}」，检索到以下内容：", ""]
        for row in neo_rows:
            if not isinstance(row, dict):
                continue
            n = row.get("n") if isinstance(row.get("n"), dict) else {}
            np = n.get("properties") if isinstance(n.get("properties"), dict) else {}
            nm = _friendly_neo_display_name(n) or "（本条暂未填写名称，可在系统中维护）"
            kind = _infer_neo_node_kind(n)
            zh = _neo_label_zh(kind)
            lines.append(f"- **{zh}**：{nm}")
        return "\n".join(lines).strip()

    lines = [f"针对问题「{question}」，检索结果如下：", ""]
    grouped: Dict[str, Dict[str, set]] = {}
    head_kind: Dict[str, str] = {}
    for row in neo_rows:
        if not isinstance(row, dict):
            continue
        n = row.get("n") if isinstance(row.get("n"), dict) else {}
        m = row.get("m") if isinstance(row.get("m"), dict) else {}
        n_title = _friendly_neo_display_name(n) or "（本条暂未填写名称，可在系统中维护）"
        m_disp = _friendly_neo_display_name(m)
        if not m_disp:
            continue
        m_kind = _infer_neo_node_kind(m)
        grouped.setdefault(n_title, {}).setdefault(m_kind, set()).add(m_disp)
        head_kind.setdefault(n_title, _infer_neo_node_kind(n))
    if not grouped:
        return (
            f"针对问题「{question}」，图数据库有命中关系，但关联条目缺少「名称/标准名」等可展示字段，"
            f"暂无法列出易读清单。请在库中补全测试方法的 Name 或 Source_name 等字段。"
        )
    for src, by_type in grouped.items():
        nk = head_kind.get(src) or "TestProject"
        head_zh = _neo_label_zh(nk)
        lines.append(f"**{head_zh}：{src}**")
        for t, names in sorted(by_type.items(), key=lambda kv: (_kind_sort_key(kv[0]), kv[0])):
            zh = _neo_label_zh(t)
            lines.append(f"- **{zh}**")
            for nm in sorted(names):
                lines.append(f"  - {nm}")
        lines.append("")
    return "\n".join(lines).strip()


def _execute_meta_steps(driver, collection, steps: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    neo_rows: List[Dict[str, Any]] = []
    neo_current: List[Dict[str, Any]] = []
    chroma_rows: List[Dict[str, Any]] = []
    step_results: List[Dict[str, Any]] = []
    step_outputs: Dict[str, List[Dict[str, Any]]] = {}

    def _resolve_ref_values(ref_cfg: Dict[str, Any]) -> List[str]:
        sid = str(ref_cfg.get("step_id") or "").strip()
        tgt = str(ref_cfg.get("target") or "n").strip()
        fld = str(ref_cfg.get("field") or "Id").strip()
        rows = step_outputs.get(sid) or []
        vals: List[str] = []
        seen = set()
        for r in rows:
            if not isinstance(r, dict):
                continue
            obj = r.get(tgt)
            if not isinstance(obj, dict):
                continue
            props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
            v = str(props.get(fld) or "").strip()
            if v and v not in seen:
                seen.add(v)
                vals.append(v)
        return vals
    for st in steps:
        op_code = st["op_code"]
        params = dict(st["params"])
        if isinstance(params.get("from_values_ref"), dict):
            refd = params["from_values_ref"]
            if op_code == "neo_relation_query" and isinstance(refd, dict) and not str(refd.get("target") or "").strip():
                ft = str(params.get("from_type") or "")
                tt = str(params.get("to_type") or "")
                if ft == "TestProject" and tt == "TestMethod":
                    refd["target"] = "n"
                elif ft in ("Equipment", "Environment") and tt == "TestProject":
                    refd["target"] = "m"
            params["value_list"] = _resolve_ref_values(params["from_values_ref"])
            if params.get("match_mode") != "in":
                params["match_mode"] = "in"
            if op_code == "neo_relation_query" and isinstance(refd, dict):
                rfl = str(refd.get("field") or "Id").strip()
                if rfl:
                    params["from_field"] = rfl
        logger.info(
            "[V5][STEP] id=%s purpose=%r op=%s params=%s",
            st.get("step_id"),
            st.get("purpose", ""),
            op_code,
            _safe_json(params, 4000),
        )
        if op_code.startswith("neo_") and op_code != "neo_property_filter":
            res = _execute_neo_step(driver, op_code, params)
            if isinstance(res, list):
                neo_current = res
                neo_rows.extend(res)
                step_outputs[str(st["step_id"])] = res
            err_ct = sum(1 for x in (res or []) if isinstance(x, dict) and x.get("error"))
            logger.info(
                "[V5][STEP] id=%s op=%s 完成 neo_rows=%d errors=%d\n摘要:\n%s",
                st.get("step_id"),
                op_code,
                len(res) if isinstance(res, list) else 0,
                err_ct,
                _summarize_neo_rows(res if isinstance(res, list) else [], max_lines=20),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": res})
            continue
        if op_code == "neo_property_filter":
            target = str(params.get("target") or "m")
            field = str(params.get("field") or "Name")
            value = str(params.get("value") or "")
            filtered: List[Dict[str, Any]] = []
            for row in neo_current:
                if not isinstance(row, dict):
                    continue
                obj = row.get(target)
                if not isinstance(obj, dict):
                    continue
                props = obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
                fv = str(props.get(field) or "")
                if value.lower() in fv.lower():
                    filtered.append(row)
            neo_current = filtered
            neo_rows.extend(filtered)
            step_outputs[str(st["step_id"])] = filtered
            logger.info(
                "[V5][STEP] id=%s op=neo_property_filter 完成 过滤后行数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(filtered),
                _summarize_neo_rows(filtered, max_lines=20),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": filtered})
            continue
        if op_code == "chroma_vector_search":
            res = _chroma_vector_query(
                collection,
                query=str(params.get("query") or ""),
                top_k=int(params.get("top_k", 12)),
                doc_id=str(params.get("doc_id") or ""),
                title_keyword=str(params.get("title_keyword") or ""),
            )
            chroma_rows = res
            step_outputs[str(st["step_id"])] = res
            logger.info(
                "[V5][CHROMA] id=%s op=chroma_vector_search 命中=%d\n摘要:\n%s",
                st.get("step_id"),
                len(res) if isinstance(res, list) else 0,
                _summarize_chroma_rows(res if isinstance(res, list) else [], max_lines=12),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": res})
            continue
        if op_code == "chroma_score_filter":
            chroma_rows = _score_filter(
                chroma_rows,
                min_score=float(params.get("min_score", 0.0)),
                max_distance=float(params.get("max_distance", 2.0)),
            )
            step_outputs[str(st["step_id"])] = chroma_rows
            logger.info(
                "[V5][CHROMA] id=%s op=chroma_score_filter 后条数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(chroma_rows),
                _summarize_chroma_rows(chroma_rows, max_lines=12),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": chroma_rows})
            continue
        if op_code == "chroma_rerank":
            chroma_rows = _simple_rerank(
                chroma_rows,
                query=str(params.get("query") or ""),
                top_k=int(params.get("top_k", 12)),
            )
            step_outputs[str(st["step_id"])] = chroma_rows
            logger.info(
                "[V5][CHROMA] id=%s op=chroma_rerank 后条数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(chroma_rows),
                _summarize_chroma_rows(chroma_rows, max_lines=12),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": chroma_rows})
            continue
        if op_code == "chroma_section_expand":
            chroma_rows = _section_expand(
                chroma_rows,
                section_prefix=str(params.get("section_prefix") or ""),
                doc_id=str(params.get("doc_id") or ""),
                title_keyword=str(params.get("title_keyword") or ""),
            )
            step_outputs[str(st["step_id"])] = chroma_rows
            logger.info(
                "[V5][CHROMA] id=%s op=chroma_section_expand 后条数=%d\n摘要:\n%s",
                st.get("step_id"),
                len(chroma_rows),
                _summarize_chroma_rows(chroma_rows, max_lines=20),
            )
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": chroma_rows})
            continue
        step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": [{"error": "unsupported op"}]})
    logger.info(
        "[V5][SUMMARY] 全步执行完毕 neo_total_accum=%d chroma_final=%d\nneo 末段摘要:\n%s\nchroma 末段摘要:\n%s",
        len(neo_rows),
        len(chroma_rows),
        _summarize_neo_rows(neo_rows[-30:] if len(neo_rows) > 30 else neo_rows, max_lines=15),
        _summarize_chroma_rows(chroma_rows, max_lines=15),
    )
    return neo_rows, chroma_rows, step_results


def _extract_project_keyword_for_equipment_discovery(question: str, retrieval_question: str) -> str:
    """「哪些设备有高温项目」类：抽取项目名称侧过滤词（如 高温、淋雨）。"""
    for src in (question or "", plain_intent_slice(retrieval_question) or retrieval_question or ""):
        s = (src or "").strip()
        if not s:
            continue
        for kw in _ENV_SUBJECT_KEYWORDS:
            if kw in s:
                return kw
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,14})(?:测试)?项目", s)
        if m:
            core = (m.group(1) or "").strip()
            core = re.sub(r"(的|与|和|及)$", "", core).strip()
            if len(core) >= 2 and core not in ("测试", "试验", "设备", "装备", "哪些", "什么"):
                return core[:14]
    return ""


def _extract_test_project_keyword_for_method_question(question: str, retrieval_question: str) -> str:
    from .znwd_business_templates import _extract_test_project_keyword_for_method_question as _ext

    return _ext(question, retrieval_question)


def _fast_path_focus_entity(messages: List[Dict[str, Any]], question: str, retrieval_question: str) -> str:
    fe = _infer_focus_entity_from_messages(messages, question).strip()
    if fe:
        return fe
    anchor = _anchor_from_retrieval_question(retrieval_question)
    if anchor:
        return anchor
    return _extract_ssyq_keyword_from_question(retrieval_question).strip()


def _fast_program_normalize_ok(fast_in: Dict[str, Any], normalized: Dict[str, Any]) -> bool:
    if not fast_in.get("_v5_fast_route") and not fast_in.get("_business_template"):
        return False
    ins = fast_in.get("steps") if isinstance(fast_in.get("steps"), list) else []
    outs = normalized.get("steps") if isinstance(normalized.get("steps"), list) else []
    if len(ins) != len(outs):
        logger.warning("[V5][FAST] 快速 program 归一化后步数变化 %d→%d，放弃快速路径", len(ins), len(outs))
        return False
    for a, b in zip(ins, outs):
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        if str(a.get("op_code") or "") != str(b.get("op_code") or ""):
            logger.warning("[V5][FAST] 快速 program 步 op 不一致 %r vs %r", a.get("op_code"), b.get("op_code"))
            return False
    return True


def _try_build_fast_path_program(
    intent_res: IntentResult,
    question: str,
    retrieval_question: str,
    messages: List[Dict[str, Any]],
    driver: Any = None,
    routing: Any = None,
) -> Optional[Dict[str, Any]]:
    """构造跳过 LLM 规划器的简化 program（业务模板驱动）。"""
    from .znwd_business_templates import build_fast_program_from_template

    return build_fast_program_from_template(
        intent_res,
        question,
        retrieval_question,
        messages,
        driver=driver,
        routing=routing,
    )


def is_neo_sufficient(neo_rows: List[Dict[str, Any]], question: str, intent: Intent) -> bool:
    """判断 Neo 命中是否足以支撑数据库直出（启发式）。"""
    _ = question
    if intent == Intent.ENUMERATION:
        for r in neo_rows or []:
            if not isinstance(r, dict) or r.get("error"):
                continue
            n = r.get("n")
            if isinstance(n, dict) and (n.get("properties") or n.get("labels")):
                return True
        return False
    if intent == Intent.RELATION_QUERY:
        for r in neo_rows or []:
            if not isinstance(r, dict) or r.get("error"):
                continue
            n, m = r.get("n"), r.get("m")
            if not isinstance(n, dict) or not isinstance(m, dict):
                continue
            np = n.get("properties") if isinstance(n.get("properties"), dict) else {}
            mp = m.get("properties") if isinstance(m.get("properties"), dict) else {}
            if (str(np.get("Name") or np.get("Id") or "").strip() and str(mp.get("Name") or mp.get("Id") or "").strip()):
                return True
        return False
    return False


def is_retrieval_sufficient(
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    question: str,
    intent: Intent,
) -> bool:
    if intent == Intent.STANDARD_CHAPTER:
        ok = _v5_chroma_nonempty(chroma_rows)
        logger.info("[V5][SUFFICIENCY] STANDARD_CHAPTER chroma_ok=%s", ok)
        return ok
    if intent in (Intent.ENUMERATION, Intent.RELATION_QUERY):
        ok = is_neo_sufficient(neo_rows, question, intent)
        logger.info("[V5][SUFFICIENCY] intent=%s neo_ok=%s", intent.value, ok)
        return ok
    return False


def _infer_last_neo_relation_labels(program: Dict[str, Any]) -> Tuple[str, str]:
    for st in reversed(program.get("steps") or []):
        if not isinstance(st, dict):
            continue
        if str(st.get("op_code")) != "neo_relation_query":
            continue
        pr = st.get("params") if isinstance(st.get("params"), dict) else {}
        return str(pr.get("from_type") or "Equipment"), str(pr.get("to_type") or "TestProject")
    return "Equipment", "TestProject"


def _format_relation_direct_answer(
    question: str,
    neo_rows: List[Dict[str, Any]],
    from_label: str,
    to_label: str,
) -> str:
    """关系类数据库直出：自然语言列表，不暴露内部 Id / 属性键名。"""
    if not neo_rows:
        return "未在图数据库命中相关关系。"
    from_zh = _neo_label_zh(from_label)
    to_zh = _neo_label_zh(to_label)
    lines: List[str] = [f"针对问题「{question}」，整理如下「{from_zh}」与「{to_zh}」的关联：", ""]
    grouped: Dict[str, List[str]] = {}
    for row in neo_rows:
        if not isinstance(row, dict) or row.get("error"):
            continue
        n = row.get("n") if isinstance(row.get("n"), dict) else {}
        m = row.get("m") if isinstance(row.get("m"), dict) else {}
        left = _friendly_neo_display_name(n)
        right = _friendly_neo_display_name(m)
        if not left or not right:
            continue
        grouped.setdefault(left, [])
        if right not in grouped[left]:
            grouped[left].append(right)
    if not grouped:
        return (
            f"针对问题「{question}」，图数据库有命中关系，但缺少可展示的业务名称字段，"
            f"暂无法生成易读列表。"
        )
    for left, rights in sorted(grouped.items(), key=lambda kv: kv[0]):
        lines.append(f"- **{from_zh}：{left}**")
        for rnm in rights:
            lines.append(f"  - **{to_zh}**：{rnm}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_fast_direct_body(
    intent: Intent,
    question: str,
    retrieval_question: str,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    program: Dict[str, Any],
) -> str:
    if intent == Intent.STANDARD_CHAPTER:
        expanded = list(chroma_rows or [])
        sec = _extract_section_prefix(question)
        if sec:
            expanded = _section_expand(expanded, sec)
        return _format_section_direct_answer(question, expanded)
    if intent == Intent.RELATION_QUERY:
        fl, tl = _infer_last_neo_relation_labels(program)
        return _format_relation_direct_answer(question, neo_rows, fl, tl)
    return _format_neo_direct_answer(question, neo_rows)


def _node_props_summary(props: Dict[str, Any], max_keys: int = 8) -> Dict[str, Any]:
    """仅保留常见业务可读字段。"""
    if not isinstance(props, dict):
        return {}
    keys = (
        "Name",
        "name",
        "Label",
        "label",
        "Description",
        "description",
        "Type",
        "type",
        "Class",
        "Source_name",
        "SYGC",
        "SYTJ",
        "JGPD",
    )
    out: Dict[str, Any] = {}
    for k in keys:
        if k in props and str(props.get(k) or "").strip():
            out[k] = props.get(k)
        if len(out) >= max_keys:
            break
    return out


def _extract_structured_entities_from_neo(neo_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """从 neo 命中提取业务向的结构化实体，供合成阶段 keyEntities 使用。"""
    empty = {"equipments": [], "projects": [], "indicators": [], "methods": []}
    if not neo_rows:
        return empty
    try:
        rows, sel = build_rows_and_selection_from_neo4j_hits(neo_rows)
        hints = collect_neo_hit_selection_ids(neo_rows)
        from blueprints.GLCX.SSYQ.routes import (
            _get_equipment_data,
            _get_project_data_with_traceback,
            _get_indicator_data_with_traceback,
        )

        equipments: List[Dict[str, Any]] = []
        seen_eq: set = set()
        for eid in (hints.get("equipmentIds") or [])[:25]:
            eid = str(eid).strip()
            if not eid or eid in seen_eq:
                continue
            seen_eq.add(eid)
            try:
                row = _get_equipment_data(eid)
            except Exception as ex:
                logger.warning("[V5][keyEntities] equipment %s: %s", eid, ex)
                continue
            if not row:
                continue
            eq = row.get("equipment") or {}
            equipments.append(
                {
                    "name": str(eq.get("name") or eq.get("Name") or "").strip() or eid,
                    "summary": "含该设备下的测试项目与环境分组信息（已结构化）",
                }
            )

        projects: List[Dict[str, Any]] = []
        seen_p: set = set()
        for pid in (hints.get("projectIds") or [])[:40]:
            pid = str(pid).strip()
            if not pid or pid in seen_p:
                continue
            seen_p.add(pid)
            try:
                prow = _get_project_data_with_traceback(pid)
            except Exception as ex:
                logger.warning("[V5][keyEntities] project %s: %s", pid, ex)
                continue
            props: Dict[str, Any] = {}
            env_nm = ""
            if isinstance(prow, dict):
                proj = prow.get("project") or {}
                props = proj if isinstance(proj, dict) else {}
                env_nm = str(prow.get("envName") or "").strip()
            if not props:
                for node in flatten_neo4j_hits_to_nodes(neo_rows):
                    labs = node.get("labels") or []
                    if isinstance(labs, str):
                        labs = [labs]
                    if "TestProject" not in labs:
                        continue
                    p2 = node.get("properties") if isinstance(node.get("properties"), dict) else {}
                    if str(p2.get("Id") or p2.get("id") or "").strip() != pid:
                        continue
                    props = p2
                    break
            nm = str(props.get("Name") or props.get("name") or "").strip() or pid
            projects.append({"name": nm, "environment": env_nm, "detail": _node_props_summary(props)})

        indicators: List[Dict[str, Any]] = []
        seen_i: set = set()
        for iid in (hints.get("indicatorIds") or [])[:40]:
            iid = str(iid).strip()
            if not iid or iid in seen_i:
                continue
            seen_i.add(iid)
            try:
                irow = _get_indicator_data_with_traceback(iid)
            except Exception as ex:
                logger.warning("[V5][keyEntities] indicator %s: %s", iid, ex)
                continue
            if not irow:
                continue
            ind = irow.get("indicator") or {}
            props = ind if isinstance(ind, dict) else {}
            nm = str(props.get("Name") or props.get("name") or "").strip() or iid
            indicators.append({"name": nm, "detail": _node_props_summary(props)})

        methods: List[Dict[str, Any]] = []
        seen_m: set = set()
        for node in flatten_neo4j_hits_to_nodes(neo_rows):
            if not isinstance(node, dict):
                continue
            labs = node.get("labels") or []
            if isinstance(labs, str):
                labs = [labs]
            if "TestMethod" not in labs:
                continue
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            mid = str(props.get("Id") or props.get("id") or "").strip()
            if mid and mid in seen_m:
                continue
            if mid:
                seen_m.add(mid)
            nm = str(props.get("Name") or props.get("Source_name") or "").strip() or mid or "（未命名方法）"
            methods.append({"name": nm, "detail": _node_props_summary(props)})

        out = {
            "equipments": equipments[:20],
            "projects": projects[:30],
            "indicators": indicators[:30],
            "methods": methods[:30],
            "tableRowCount": len(rows or []),
            "selectionSummary": {k: len(v or []) if isinstance(v, list) else 0 for k, v in (sel or {}).items()},
        }
        logger.info(
            "[V5][keyEntities] equipments=%d projects=%d indicators=%d methods=%d",
            len(out["equipments"]),
            len(out["projects"]),
            len(out["indicators"]),
            len(out["methods"]),
        )
        return out
    except Exception as e:
        logger.warning("[V5][keyEntities] 提取失败: %s", e, exc_info=True)
        return empty


def _needs_v4_fallback(program: Dict[str, Any], step_results: List[Dict[str, Any]]) -> bool:
    if program.get("fallback_v4"):
        return True
    if not program.get("steps"):
        return True
    for s in step_results:
        rows = s.get("rows")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and rows[0].get("error") == "unsupported op":
            return True
    return False


def _intent_for_sufficiency(program: Dict[str, Any], retrieval_question: str) -> Intent:
    raw = program.get("_v5_intent")
    if isinstance(raw, str) and raw.strip():
        try:
            return Intent(raw.strip())
        except ValueError:
            pass
    return classify_intent(retrieval_question).intent


def _neo_node_labels(node: Any) -> List[str]:
    if not isinstance(node, dict):
        return []
    labs = node.get("labels") or []
    if isinstance(labs, str):
        return [labs]
    return list(labs)


def _neo_prop_id(props: Dict[str, Any]) -> str:
    return str(props.get("Id") or props.get("id") or "").strip()


def _rerank_neo_by_interest(
    neo_rows: List[Dict[str, Any]],
    user_interest: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    软加权：为命中行附加 interest_score 并按分数降序；不丢弃任何行。
    user_interest: environment_ids；一级设备类型展开后的 equipment_type_node_ids（ZBLX_*）；
    equipment_instance_ids（ZB_*，可选）；primary_equipment_type_labels（一级 Type 文本）。
    兼容旧字段 equipment_ids：若含 ZBLX_/ZB_ 则按类型/实例解析。
    """
    if not neo_rows:
        return neo_rows
    if not user_interest:
        return neo_rows
    legacy = [str(x).strip() for x in (user_interest.get("equipment_ids") or []) if str(x).strip()]
    et_node_ids = {str(x).strip() for x in (user_interest.get("equipment_type_node_ids") or []) if str(x).strip()}
    equip_inst_ids = {str(x).strip() for x in (user_interest.get("equipment_instance_ids") or []) if str(x).strip()}
    primary_labels = {str(x).strip() for x in (user_interest.get("primary_equipment_type_labels") or []) if str(x).strip()}
    for x in legacy:
        if x.startswith("ZB_"):
            equip_inst_ids.add(x)
        elif x.startswith("ZBLX_"):
            et_node_ids.add(x)
        else:
            primary_labels.add(x)
    env_ids = {str(x).strip() for x in (user_interest.get("environment_ids") or []) if str(x).strip()}
    if not et_node_ids and not equip_inst_ids and not primary_labels and not env_ids:
        return neo_rows
    out: List[Dict[str, Any]] = []
    for row in neo_rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        r = dict(row)
        score = 0
        for key in ("n", "m"):
            node = r.get(key)
            if not isinstance(node, dict):
                continue
            labs = _neo_node_labels(node)
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            nid = _neo_prop_id(props)
            et_ref = str(props.get("EquipmentTypeId") or props.get("equipmentTypeId") or "").strip()
            type_prop = str(props.get("Type") or props.get("type") or "").strip()
            if "Equipment" in labs:
                if nid and nid in equip_inst_ids:
                    score += 12
                if et_ref and et_ref in et_node_ids:
                    score += 10
            if "EquipmentType" in labs:
                if nid and nid in et_node_ids:
                    score += 10
                if type_prop and type_prop in primary_labels:
                    score += 8
            if "Environment" in labs and nid in env_ids:
                score += 5
        r["interest_score"] = score
        out.append(r)
    out.sort(key=lambda x: x.get("interest_score", 0) if isinstance(x, dict) else 0, reverse=True)
    logger.info(
        "[V5][interest] reranked neo_rows=%d top_score=%s",
        len(out),
        out[0].get("interest_score") if out and isinstance(out[0], dict) else 0,
    )
    return out


def _v5_finalize_memory(
    user_id: Optional[str],
    session_id: Optional[str],
    question: str,
    final_answer: str,
    client: Any,
    model: str,
) -> None:
    if not user_id or not session_id:
        return
    text = (final_answer or "").strip()
    if not text:
        return
    store = get_memory_store()
    if not store:
        return
    try:
        store.add_message(user_id, session_id, "user", question)
        store.add_message(user_id, session_id, "assistant", text)
        store.maybe_compress_async(user_id, session_id, client, model)
        logger.info("[V5][memory] 已写入 Redis 消息队列 session=%s", session_id[:16])
    except Exception as e:
        logger.warning("[V5][memory] finalize 失败: %s", e)


def llm_stream_single_v5(
    client,
    model,
    messages,
    driver,
    collection,
    llm_limits: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_interest: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, dict], None, None]:
    work_messages: List[Dict[str, Any]] = [dict(m) if isinstance(m, dict) else m for m in messages]
    messages = work_messages
    question = _last_user_question(messages)
    if driver:
        cq, corrections = _replace_entities_with_corrections(question, driver)
        if corrections:
            logger.info("[V5][ENTITY_FIX] 实体模糊纠正: %s", corrections)
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                if isinstance(m, dict) and m.get("role") == "user":
                    m["content"] = cq
                    break
            question = _last_user_question(messages)
    retrieval_question = _build_v5_retrieval_question(messages, question)
    store = get_memory_store()
    if store and user_id and session_id:
        sm = store.get_summary(user_id, session_id)
        if sm:
            retrieval_question = f"【历史摘要】{sm[:1500]}\n【当前问题】{retrieval_question}"
            logger.info("[V5][memory] 已在检索问题中注入历史摘要 len=%d", len(sm))
    logger.info("[V5] retrieval_question=%r", retrieval_question[:400])

    yield ("thinking_start", {})
    from .znwd_intent_mode import get_intent_mode
    from .znwd_llm_router import route_question
    from .znwd_fast_route import (
        fast_program_normalize_ok,
        normalize_program,
        try_build_fast_path_program,
    )

    routing = route_question(
        question,
        retrieval_question,
        client=client,
        model=model,
        driver=driver,
    )
    intent_res = routing.to_intent_result()
    logger.info(
        "[V5][INTENT] mode=%s %s (%s) focus=%r",
        get_intent_mode(),
        intent_res.intent.value,
        intent_res.reason,
        getattr(routing, "focus", ""),
    )

    if getattr(routing, "skip_retrieval", False):
        yield _send_step("判定为无需查库，直接回答", tp="intent_judge", status="done")
        if getattr(routing, "plan_user_nl", ""):
            yield ("thinking", {"content": routing.plan_user_nl + "\n\n"})
        direct_messages = [
            {"role": "system", "content": DIRECT_ANSWER_PROMPT},
            {"role": "user", "content": question},
        ]
        full_answer = ""
        full_thinking = ""
        for et, data in _stream_single_llm_v4(
            client,
            model,
            direct_messages,
            driver,
            collection,
            tools_disabled=True,
            llm_limits=llm_limits,
        ):
            if et == "done":
                continue
            yield (et, data)
            if et == "answer":
                full_answer += str(data.get("content") or "")
            elif et == "thinking":
                full_thinking += str(data.get("content") or "")
        _v5_finalize_memory(user_id, session_id, question, full_answer, client, model)
        yield (
            "done",
            {"fullAnswer": full_answer, "fullThinking": full_thinking, "primaryChromaSource": None},
        )
        return

    meta_out: Dict[str, Any] = {}
    used_fast_route = False
    fast_program_raw: Optional[Dict[str, Any]] = try_build_fast_path_program(
        intent_res,
        question,
        retrieval_question,
        messages,
        driver=driver,
        routing=routing,
    )
    program: Dict[str, Any] = {}

    if fast_program_raw:
        program = normalize_program(fast_program_raw, retrieval_question, question)
        if fast_program_normalize_ok(fast_program_raw, program) and not program.get("fallback_v4"):
            used_fast_route = True
            fb = _plan_thinking_display_text(program, retrieval_question)
            if fb.strip():
                yield ("thinking", {"content": fb + "\n\n"})
            logger.info("[V5][FAST] 跳过 LLM 规划器，使用快速 program（intent=%s）", intent_res.intent.value)
        else:
            program = {}
            fast_program_raw = None

    if not used_fast_route:
        for et, data in _iter_meta_plan_stream(client, model, retrieval_question, meta_out, llm_limits):
            yield (et, data)
        raw_program = meta_out.get("program")
        program = _normalize_program(raw_program, retrieval_question, question)
        if not meta_out.get("think_emitted"):
            fb = _plan_thinking_display_text(program, retrieval_question)
            if fb.strip():
                yield ("thinking", {"content": fb + "\n\n"})
    _apply_primary_focus_to_equipment_relation_steps(program, retrieval_question)
    table_kw = _build_ssyq_table_keyword_v5(messages, retrieval_question, program, question)
    yield ("search_keywords", {"ssyqTableKeyword": table_kw})
    yield _send_step("V5 检索程序生成完成。", tp="intent_judge", status="done")

    yield _send_step("正在执行程序化检索步骤...", tp="retrieve", status="loading")
    neo_rows, chroma_rows, step_results = _execute_meta_steps(driver, collection, program["steps"])
    neo_rows = _rerank_neo_by_interest(neo_rows, user_interest)
    chroma_rows = _normalize_chroma_rows_for_client(chroma_rows)
    chroma_display = (chroma_rows or [])[:ZNWD_CHROMA_UI_MAX]
    yield ("source_data", {"neo4jResults": neo_rows, "chromaResults": chroma_display})
    yield _send_step(f"程序化检索完成：Neo={len(neo_rows)}，Chroma={len(chroma_rows)}", tp="retrieve", status="done")
    logger.info(
        "[V5][RESULT] stepResults JSON（截断）:\n%s",
        _safe_json(step_results, _V5_LOG_JSON_MAX),
    )

    if _needs_v4_fallback(program, step_results):
        logger.warning(
            "[V5_META_GAP][OG] fallback to v4 | missing_capability=%r | question=%r",
            program.get("missing_capability", ""),
            question[:200],
        )
        yield _send_step(
            "当前元操作无法完整覆盖该问题，已回退至 V4 流程；请在日志中查看建议新增元操作。",
            tp="retrieve",
            status="done",
        )
        mem_final = ""
        for event in llm_stream_single_v4(client, model, messages, driver, collection, llm_limits=llm_limits):
            yield event
            if not isinstance(event, (tuple, list)) or len(event) < 2:
                continue
            et, dt = event[0], event[1]
            if et == "answer" and isinstance(dt, dict):
                mem_final += str(dt.get("content") or "")
            elif et == "done" and isinstance(dt, dict):
                fa = str(dt.get("fullAnswer") or "").strip()
                if fa:
                    mem_final = fa
        _v5_finalize_memory(user_id, session_id, question, mem_final, client, model)
        return

    if not _v5_rows_non_empty(neo_rows, chroma_rows):
        yield _send_step("V5 未命中有效图/文档数据，正在使用 V4 检索策略补充…", tp="retrieve", status="loading")
        try:
            pack = collect_v4_retrieval_results(client, model, messages, driver, collection, llm_limits=llm_limits)
            v4_neo = list(pack.get("neo_res") or [])
            v4_chroma = list(pack.get("chroma_res") or [])
            # 保留 V5 程序化检索返回的整行（含 n/m/r），仅追加 V4 结果，避免覆盖为「仅 n」或 rowKind 单形态
            neo_rows = list(neo_rows) + v4_neo
            chroma_rows = list(chroma_rows) + v4_chroma
            chroma_rows = _normalize_chroma_rows_for_client(chroma_rows)
            chroma_display = (chroma_rows or [])[:ZNWD_CHROMA_UI_MAX]
            ssyq = str(pack.get("ssyq_kw") or "").strip()
            if ssyq:
                yield ("search_keywords", {"ssyqTableKeyword": ssyq})
            yield ("source_data", {"neo4jResults": neo_rows, "chromaResults": chroma_display})
            yield _send_step(
                f"V4 补充检索完成：Neo={len(neo_rows)}，Chroma={len(chroma_rows)}",
                tp="retrieve",
                status="done",
            )
            logger.info(
                "[V5→V4] supplement | neo_nonempty=%s chroma_nonempty=%s",
                _v5_rows_non_empty(neo_rows, []),
                _v5_rows_non_empty([], chroma_rows),
            )
            step_results.append(
                {
                    "step_id": "v4_supplement",
                    "op_code": "v4_retrieval",
                    "rows": [
                        {
                            "info": "V5 无有效命中后执行 V4 规划检索",
                            "neo_count": len(v4_neo),
                            "chroma_count": len(v4_chroma),
                        }
                    ],
                }
            )
            neo_rows = _rerank_neo_by_interest(neo_rows, user_interest)
        except Exception as ex:
            logger.warning("[V5→V4] collect_v4_retrieval_results failed: %s", ex)
            yield _send_step("V4 补充检索失败，将按无命中处理。", tp="retrieve", status="done")

    if not _v5_rows_non_empty(neo_rows, chroma_rows):
        yield ("thinking_start", {})
        yield ("thinking", {"content": "图谱与清单检索未命中，已尝试备用检索；将结合常识与问题本身作答。"})
        direct_messages = [
            {"role": "system", "content": DIRECT_ANSWER_PROMPT},
            {"role": "user", "content": question},
        ]
        full_answer = ""
        full_thinking = ""
        for et, data in _stream_single_llm_v4(
            client,
            model,
            direct_messages,
            driver,
            collection,
            tools_disabled=True,
            llm_limits=llm_limits,
        ):
            if et in ("done",):
                continue
            yield (et, data)
            if et == "answer":
                full_answer += str(data.get("content") or "")
            elif et == "thinking":
                full_thinking += str(data.get("content") or "")
        _v5_finalize_memory(user_id, session_id, question, full_answer, client, model)
        yield ("done", {"fullAnswer": full_answer, "fullThinking": full_thinking, "primaryChromaSource": None})
        return

    chroma_nonempty = _v5_chroma_nonempty(chroma_rows)
    # 任一向量库命中：必须由 LLM 归纳文档（禁止仅模板直出）；与 answer_mode 原值无关
    if chroma_nonempty:
        program["answer_mode"] = "llm_synthesize"

    if program.get("answer_mode") == "database_direct":
        from .znwd_fast_route import (
            format_fast_direct_body,
            intent_for_sufficiency,
            is_retrieval_sufficient_for_intent,
        )

        intent_eff = intent_for_sufficiency(program, retrieval_question)
        sufficient = is_retrieval_sufficient_for_intent(neo_rows, chroma_rows, question, intent_eff)
        if sufficient:
            yield ("thinking_start", {})
            if intent_eff == Intent.STANDARD_CHAPTER:
                yield ("thinking", {"content": "检索结果已覆盖标准/章节类问题，直接整理文档要点作答，不再调用模型合成。"})
                full_th = "标准章节直出"
            else:
                yield ("thinking", {"content": "图检索结果已满足直出条件，使用模板整理为可读列表，不再调用模型合成。"})
                full_th = "数据库直出"
            body = format_fast_direct_body(
                intent_eff, question, retrieval_question, neo_rows, chroma_rows, program
            )
            for i in range(0, len(body), 600):
                yield ("answer", {"content": body[i : i + 600]})
            _v5_finalize_memory(user_id, session_id, question, body, client, model)
            yield ("done", {"fullAnswer": body, "fullThinking": full_th, "primaryChromaSource": None})
            return
        logger.info(
            "[V5][SUFFICIENCY] database_direct 但命中不足，回退 LLM 合成 | intent=%s chroma_nonempty=%s",
            intent_eff.value,
            chroma_nonempty,
        )
        program["answer_mode"] = "llm_synthesize"

    structured_entities: Dict[str, Any] = {}
    if neo_rows and program.get("answer_mode") != "database_direct":
        try:
            structured_entities = _extract_structured_entities_from_neo(neo_rows)
            logger.info(
                "[V5][keyEntities] 已注入 evidence | equipments=%d projects=%d indicators=%d methods=%d",
                len(structured_entities.get("equipments") or []),
                len(structured_entities.get("projects") or []),
                len(structured_entities.get("indicators") or []),
                len(structured_entities.get("methods") or []),
            )
        except Exception as e:
            logger.warning("[V5][keyEntities] 提取失败: %s", e, exc_info=True)

    _synth_chroma_top = 3
    evidence = {
        "question": question,
        "retrievalQuestion": retrieval_question,
        "metaProgram": program,
        "stepResults": step_results,
        "neo4jResults": neo_rows,
        "chromaResults": (chroma_rows or [])[:_synth_chroma_top],
        "keyEntities": structured_entities,
    }
    phase2_messages = [
        {"role": "system", "content": META_SYNTHESIS_PROMPT},
        {"role": "user", "content": json.dumps(evidence, ensure_ascii=False, default=str)[:16000]},
    ]
    yield _send_step("正在基于程序化检索结果生成回答...", tp="synthesis", status="loading")
    final_answer = ""
    full_thinking = ""
    pick_raw = ""
    for et, data in _stream_single_llm_v4(
        client,
        model,
        phase2_messages,
        driver,
        collection,
        tools_disabled=True,
        llm_limits=llm_limits,
    ):
        if et == "internal_source_pick":
            pick_raw = str((data or {}).get("raw") or "")
            continue
        if et in ("done",):
            continue
        yield (et, data)
        if et == "answer":
            final_answer += str(data.get("content") or "")
        elif et == "thinking":
            full_thinking += str(data.get("content") or "")
    yield _send_step("V5 回答生成完成。", tp="synthesis", status="done")
    merged_tail = (final_answer + ("\n" + pick_raw if pick_raw else "")).strip()
    clean_answer, raw_pick = _strip_and_parse_source_pick(merged_tail)
    primary = _merge_primary_source_with_chroma(raw_pick, (chroma_rows or [])[:ZNWD_CHROMA_UI_MAX]) if chroma_nonempty else None
    if not clean_answer.strip():
        clean_answer = final_answer.strip()
    _v5_finalize_memory(user_id, session_id, question, clean_answer, client, model)
    yield ("done", {"fullAnswer": clean_answer, "fullThinking": full_thinking, "primaryChromaSource": primary})
