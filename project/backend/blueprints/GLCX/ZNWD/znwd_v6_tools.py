"""
ZNWD V6 工具层：与 services_v6 解耦。
- **显式检索**：`TOOL_*` 中 Neo 查询使用**固定 Cypher 骨架**，**起点/终点标签、关系类型、匹配侧等**
  均由 LLM 以参数传入；服务端用 **NODE_LABELS / RELATION_SCHEMA** 校验后写入 `$from_label`、`$rel_type` 等
  绑定参数执行（**不在查询串里拼接未校验的标签名**）。Chroma 仍走 `run_chroma_vector_search`。
- **元操作检索（兼容）**：`znwd_meta_ops_retrieve` 仍可由旧客户端调用；`execute_meta_ops_retrieve`
  会对 LLM 常见错键（from_value、label、only_enabled 等）做归一化后再校验执行。
- 历史：最近 n 轮问答、基于用户句的相似度 Top-K（SequenceMatcher，无向量库依赖）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional

from .memory_store import get_memory_store
from .retrieval_meta_ops import (
    META_OPS,
    NODE_ENTITY_FIELDS,
    NODE_LABELS,
    RELATION_EXPLANATIONS,
    RELATION_SCHEMA,
    clamp_meta_op_params,
    validate_meta_op,
)
from utils.neo4j_flgl_status import flgl_status_cypher_param

from .znwd_v6_executor import (
    ZNWD_CHROMA_UI_MAX,
    execute_meta_steps,
    normalize_chroma_rows_for_client,
    normalize_section_prefix_param,
    rerank_neo_by_interest,
    run_chroma_vector_search,
    run_neo_cypher,
)

logger = logging.getLogger(__name__)

_LOG_JSON_MAX = 4000


def resolve_znwd_v6_tool_call_parser() -> Optional[str]:
    """
    与 vLLM OpenAI 兼容网关的 --tool-call-parser 对齐，经 LangChain ChatOpenAI 的 extra_body 透传。

    环境变量 ZNWD_V6_TOOL_CALL_PARSER：
    - hermes（默认）：extra_body.tool_call_parser=hermes
    - qwen3：extra_body.tool_call_parser=qwen3
    - none / off / disabled：不传 extra_body（由推理服务单独配置 parser）
    """
    raw = (os.environ.get("ZNWD_V6_TOOL_CALL_PARSER") or "hermes").strip().lower()
    if raw in ("", "none", "off", "disabled", "false", "0", "no"):
        return None
    if raw in ("hermes", "qwen3"):
        return raw
    logger.warning(
        "[znwd_v6_tools] ZNWD_V6_TOOL_CALL_PARSER=%r 无效，回退 hermes",
        os.environ.get("ZNWD_V6_TOOL_CALL_PARSER"),
    )
    return "hermes"


def _safe_json(obj: Any, limit: int = _LOG_JSON_MAX) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…"


def _canonical_meta_step_id(raw: Optional[str], fallback_index: int) -> str:
    t = (raw or "").strip()
    m = re.match(r"^step_(\d+)$", t, re.I)
    if m:
        return f"s{int(m.group(1))}"
    if t:
        return t
    return f"s{fallback_index + 1}"


def _repair_from_values_ref_param(params: Dict[str, Any]) -> None:
    ref = params.get("from_values_ref")
    if isinstance(ref, dict):
        sid = str(ref.get("step_id") or "").strip()
        m = re.match(r"^step_(\d+)$", sid, re.I)
        if m:
            ref["step_id"] = f"s{int(m.group(1))}"
        return
    if not isinstance(ref, str) or not ref.strip():
        return
    rs = ref.strip()
    m = re.match(r"^(?:step_)?(\d+)$", rs, re.I)
    sid = f"s{int(m.group(1))}" if m else rs
    from_t = str(params.get("from_type") or "")
    to_t = str(params.get("to_type") or "")
    tgt = "n"
    if from_t == "Equipment" and to_t == "TestProject":
        tgt = "m"
    elif from_t == "Environment" and to_t == "TestProject":
        tgt = "m"
    params["from_values_ref"] = {"step_id": sid, "target": tgt, "field": "Id"}


def coerce_meta_tool_args_steps(args: Dict[str, Any]) -> Dict[str, Any]:
    a = dict(args or {})
    steps = a.get("steps")
    if isinstance(steps, list) and len(steps) > 0:
        return a
    op_code = str(a.get("op_code") or "").strip()
    if op_code and isinstance(a.get("params"), dict):
        slim = {k: a[k] for k in ("step_id", "op_code", "params", "purpose") if k in a}
        return {"steps": [slim]}
    return a


def normalize_llm_mistake_meta_params(op_code: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """将常见错键（如 from_value、label、only_enabled）归一成 META_OPS 校验所需字段。"""
    p = dict(params or {})
    if op_code == "neo_relation_query":
        if not str(p.get("value") or "").strip():
            for key in ("from_value", "equipment_name", "equipment", "keyword", "kw", "name", "q"):
                v = str(p.get(key) or "").strip()
                if v:
                    p["value"] = v
                    break
    if op_code == "neo_node_query":
        if not str(p.get("value") or "").strip():
            for key in ("keyword", "kw", "name", "query", "q", "text"):
                v = str(p.get(key) or "").strip()
                if v:
                    p["value"] = v
                    break
    if op_code == "neo_list_nodes":
        if not str(p.get("node_type") or "").strip():
            for key in ("label", "type", "kind"):
                v = str(p.get(key) or "").strip()
                if v:
                    p["node_type"] = v
                    break
        if p.get("status_filter") is None and "only_enabled" in p:
            if p.get("only_enabled") is True:
                p["status_filter"] = 1
            elif p.get("only_enabled") is False:
                p["status_filter"] = 0
    return p


def canonical_langchain_tool_name(raw: str) -> str:
    """统一 Qwen3 / 用户习惯中的 @TOOL 前缀与 OpenAPI function.name（TOOL_…）。"""
    n = (raw or "").strip()
    if not n:
        return n
    low = n.lower()
    if low.startswith("@tool_"):
        tail = n[6:]
        if tail.lower().startswith("tool_"):
            return tail
        return f"TOOL_{tail}" if tail else n
    if low.startswith("@tool"):
        rest = n[5:].lstrip("_")
        if rest.lower().startswith("tool_"):
            return rest
        return f"TOOL_{rest}" if rest else n
    return n


def execute_meta_ops_retrieve(
    args: Dict[str, Any],
    driver: Any,
    collection: Any,
    user_interest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """仅执行 LLM 给出的 META_OPS steps（无服务端程序化兜底）。"""
    args = coerce_meta_tool_args_steps(args)
    raw_in = args.get("steps")
    if not isinstance(raw_in, list) or not raw_in:
        logger.warning("[znwd_v6_tools] meta_ops_retrieve 拒绝：steps 缺失或为空 keys=%s", list(args.keys()))
        return {
            "error": "steps 必须为非空数组",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
            "answer_guidance": (
                "本轮未执行有效检索（steps 为空）。请在回答中说明未能从清单/文件库取数，"
                "并基于用户问题做一般性说明（勿仅以无数据收尾）。"
            ),
        }

    normalized_steps: List[Dict[str, Any]] = []
    validation_notes: List[Dict[str, Any]] = []
    for i, s in enumerate(raw_in[:16]):
        if not isinstance(s, dict):
            validation_notes.append({"index": i, "error": "skip_non_dict"})
            continue
        op_code = str(s.get("op_code") or "").strip()
        params = dict(s.get("params") or {}) if isinstance(s.get("params"), dict) else {}
        params = normalize_llm_mistake_meta_params(op_code, params)
        if op_code == "neo_relation_query":
            _repair_from_values_ref_param(params)
        if op_code in ("neo_node_query", "neo_property_filter", "neo_relation_query"):
            mm = str(params.get("match_mode") or "").strip().lower()
            if mm == "exact":
                params["match_mode"] = "fuzzy"
        params = clamp_meta_op_params(op_code, params)
        if op_code == "chroma_rerank" and not str(params.get("query") or "").strip():
            for j in range(i - 1, -1, -1):
                prev = raw_in[j] if isinstance(raw_in[j], dict) else None
                if not prev or str(prev.get("op_code") or "") != "chroma_vector_search":
                    continue
                pp = prev.get("params") if isinstance(prev.get("params"), dict) else {}
                pq = str(pp.get("query") or "").strip()
                if pq:
                    params["query"] = pq
                break
        if op_code == "chroma_section_expand":
            raw_sp = str(params.get("section_prefix") or "")
            nsp = normalize_section_prefix_param(raw_sp)
            if raw_sp.strip() and not nsp:
                validation_notes.append(
                    {"step_id": s.get("step_id"), "op_code": op_code, "validation_errors": ["invalid section_prefix"]}
                )
                continue
            params["section_prefix"] = nsp if raw_sp.strip() else nsp
        errs = validate_meta_op(op_code, params)
        if errs:
            validation_notes.append(
                {"step_id": s.get("step_id"), "op_code": op_code, "validation_errors": errs}
            )
            continue
        sid = _canonical_meta_step_id(str(s.get("step_id") or ""), len(normalized_steps))
        normalized_steps.append(
            {
                "step_id": sid,
                "op_code": op_code,
                "params": params,
                "purpose": str(s.get("purpose") or ""),
            }
        )

    if not normalized_steps:
        logger.warning(
            "[znwd_v6_tools] meta_ops_retrieve 无合法步骤 notes=%s",
            _safe_json(validation_notes, 2000),
        )
        return {
            "error": "没有通过校验的步骤",
            "validation_notes": validation_notes,
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
            "answer_guidance": (
                "本轮步骤未通过校验。请说明未能取数并基于问题补充说明（勿仅以无数据收尾）。"
            ),
        }

    op_chain = " -> ".join(str(s.get("op_code") or "") for s in normalized_steps)
    neo_like = [
        s
        for s in normalized_steps
        if str(s.get("op_code") or "").startswith("neo_") and str(s.get("op_code")) != "neo_property_filter"
    ]
    chroma_like = [s for s in normalized_steps if str(s.get("op_code") or "").startswith("chroma_")]
    logger.warning(
        "[ZNWD][RETRIEVE_EXEC] op_chain=%s neo_steps=%d chroma_steps=%d driver_ok=%s collection_ok=%s",
        op_chain,
        len(neo_like),
        len(chroma_like),
        bool(driver),
        bool(collection),
    )
    if neo_like and not driver:
        logger.error(
            "[ZNWD][RETRIEVE_EXEC] 步骤含 Neo 元操作但 NEO4J_DRIVER 未注入，无法执行 Cypher，将仅返回 error 行"
        )
    if chroma_like and not collection:
        logger.error(
            "[ZNWD][RETRIEVE_EXEC] 步骤含 Chroma 元操作但 collection 未注入，向量检索将失败"
        )

    logger.warning(
        "[ZNWD][TOOL_RETRIEVE] znwd_meta_ops_retrieve op_chain=%s",
        op_chain,
    )
    neo_rows, chroma_rows, step_results = execute_meta_steps(driver, collection, normalized_steps)
    neo_rows = rerank_neo_by_interest(neo_rows, user_interest)
    chroma_rows = normalize_chroma_rows_for_client(chroma_rows)
    neo_cap = 120
    out_neo = list(neo_rows[:neo_cap])
    out_chr = list((chroma_rows or [])[:ZNWD_CHROMA_UI_MAX])
    logger.info(
        "[znwd_v6_tools] meta_ops_retrieve 完成 ops=%s neo_out=%d chroma_out=%d",
        op_chain,
        len(out_neo),
        len(out_chr),
    )

    def _has_usable_hits(rows: List[Any]) -> bool:
        for r in rows or []:
            if isinstance(r, dict) and not r.get("error"):
                return True
        return False

    out: Dict[str, Any] = {
        "neo4jResults": out_neo,
        "chromaResults": out_chr,
        "stepResults": step_results,
        "validation_notes": validation_notes,
        "truncated": {"neo": len(neo_rows) > neo_cap, "chroma": len(chroma_rows or []) > len(out_chr)},
        "retrievalSummary": {
            "driver_attached": bool(driver),
            "collection_attached": bool(collection),
            "neo_meta_step_count": len(neo_like),
            "chroma_meta_step_count": len(chroma_like),
            "neo_cypher_runs": sum(
                1
                for sr in step_results or []
                if isinstance(sr, dict) and sr.get("cypher")
            ),
            "op_chain": op_chain,
        },
    }
    neo_cypher_lines: List[str] = []
    for sr in step_results or []:
        if not isinstance(sr, dict):
            continue
        cy = sr.get("cypher")
        if cy:
            pr = sr.get("cypher_params") or {}
            neo_cypher_lines.append(
                f"--- step={sr.get('step_id')} op={sr.get('op_code')} ---\n{cy}\nparams: "
                + json.dumps(pr, ensure_ascii=False, default=str)[:4000]
            )
        elif sr.get("cypher_note"):
            neo_cypher_lines.append(
                f"--- step={sr.get('step_id')} op={sr.get('op_code')} ---\n{sr.get('cypher_note')}"
            )
    out["neoCypherTrace"] = neo_cypher_lines
    if neo_cypher_lines:
        logger.info(
            "[znwd_v6_tools][CYPHER_TRACE]\n%s",
            "\n\n".join(neo_cypher_lines)[:15000],
        )
    if not _has_usable_hits(out_neo) and not _has_usable_hits(out_chr):
        out["answer_guidance"] = (
            "本轮清单与文件库均无可用命中。请先在正文中简要说明未命中，再展开库外补充说明。"
        )
    return out


def _clamp_v6_limit(v: Any, default: int, lo: int, hi: int) -> int:
    try:
        x = int(v)
    except (TypeError, ValueError):
        x = default
    return max(lo, min(hi, x))


def _pack_v6_explicit_tool(
    *,
    tool_name: str,
    neo_rows: Optional[List[Dict[str, Any]]] = None,
    chroma_rows: Optional[List[Dict[str, Any]]] = None,
    cypher: Optional[str] = None,
    cypher_params: Optional[Dict[str, Any]] = None,
    user_interest: Optional[Dict[str, Any]],
    driver_attached: bool,
    collection_attached: bool,
) -> Dict[str, Any]:
    neo_in = rerank_neo_by_interest(list(neo_rows or []), user_interest)
    neo_cap = 120
    out_neo = neo_in[:neo_cap]
    chr_raw = normalize_chroma_rows_for_client(list(chroma_rows or []))
    out_chr = chr_raw[:ZNWD_CHROMA_UI_MAX]
    step_results: List[Dict[str, Any]] = []
    if cypher:
        step_results.append(
            {
                "step_id": "t1",
                "op_code": tool_name,
                "rows": neo_in,
                "cypher": cypher,
                "cypher_params": dict(cypher_params or {}),
            }
        )
    elif chroma_rows is not None:
        step_results.append(
            {
                "step_id": "t1",
                "op_code": tool_name,
                "rows": chr_raw,
                "cypher": None,
                "cypher_note": "Chroma 向量检索（无 Cypher）",
            }
        )
    trace: List[str] = []
    if cypher:
        trace.append(
            f"--- tool={tool_name} ---\n{cypher}\nparams: "
            + json.dumps(cypher_params or {}, ensure_ascii=False, default=str)[:4000]
        )
    out: Dict[str, Any] = {
        "neo4jResults": out_neo,
        "chromaResults": out_chr,
        "stepResults": step_results,
        "neoCypherTrace": trace,
        "retrievalSummary": {
            "driver_attached": driver_attached,
            "collection_attached": collection_attached,
            "neo_cypher_runs": 1 if cypher else 0,
            "explicit_tool": tool_name,
        },
    }
    if trace:
        logger.info("[znwd_v6_tools][CYPHER_TRACE]\n%s", "\n".join(trace)[:15000])

    def _hits(xs: List[Any]) -> bool:
        for r in xs or []:
            if isinstance(r, dict) and not r.get("error"):
                return True
        return False

    if not _hits(out_neo) and not _hits(out_chr):
        out["answer_guidance"] = (
            "本轮清单与文件库均无可用命中。请先在正文中简要说明未命中，再展开库外补充说明。"
        )
    return out


def _allowed_rel_triple(from_label: str, rel_type: str, to_label: str) -> bool:
    pairs = RELATION_SCHEMA.get(rel_type) or []
    return (from_label, to_label) in pairs


def _norm_name_match_side(raw: Any) -> str:
    s = str(raw or "from").strip().lower()
    return s if s in ("from", "to") else "from"


def _norm_id_match_side(raw: Any) -> str:
    s = str(raw or "from").strip().lower()
    return s if s in ("from", "to") else "from"


# 标签与关系类型均以 Neo4j 参数传入；仅 MATCH 骨架固定，避免拼接未校验标识符。
_CYPHER_RELATION_NAME_SEARCH = """
MATCH (n)-[r]->(m)
WHERE $from_label IN labels(n)
  AND type(r) = $rel_type
  AND $to_label IN labels(m)
  AND (
    ($name_match_side = 'from' AND toLower(coalesce(n.Name,'')) CONTAINS toLower($kw))
    OR
    ($name_match_side = 'to' AND toLower(coalesce(m.Name,'')) CONTAINS toLower($kw))
  )
RETURN n, r, m
LIMIT $limit
""".strip()


_CYPHER_RELATION_BY_IDS = """
MATCH (n)-[r]->(m)
WHERE $from_label IN labels(n)
  AND type(r) = $rel_type
  AND $to_label IN labels(m)
  AND (
    ($id_match_side = 'from' AND toString(coalesce(n.Id,'')) IN $ids)
    OR
    ($id_match_side = 'to' AND toString(coalesce(m.Id,'')) IN $ids)
  )
RETURN n, r, m
LIMIT $limit
""".strip()


_CYPHER_NODE_NAME_SEARCH = """
MATCH (n)
WHERE $node_label IN labels(n)
  AND toLower(coalesce(n.Name,'')) CONTAINS toLower($value)
RETURN n
LIMIT $limit
""".strip()


_CYPHER_LIST_NODES_ENABLED = """
MATCH (n)
WHERE $node_label IN labels(n)
  AND (n.Status IS NULL OR n.Status IN $flgl_enabled_status)
RETURN n
ORDER BY coalesce(toString(n.Name), toString(n.Label), toString(n.Id), '')
LIMIT $limit
""".strip()


_CYPHER_LIST_NODES_ALL = """
MATCH (n)
WHERE $node_label IN labels(n)
RETURN n
ORDER BY coalesce(toString(n.Name), toString(n.Label), toString(n.Id), '')
LIMIT $limit
""".strip()


def explicit_tool_neo_relation_name_search(
    driver: Any,
    args: Dict[str, Any],
    user_interest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """单跳关系 + 起点或终点 Name 子串匹配；标签与关系类型均为查询参数。"""
    from_l = str(
        args.get("from_node_label") or args.get("from_type") or args.get("from_label") or ""
    ).strip()
    to_l = str(args.get("to_node_label") or args.get("to_type") or args.get("to_label") or "").strip()
    rel_t = str(args.get("relationship_type") or args.get("rel_type") or "CON").strip()
    side = _norm_name_match_side(args.get("name_match_side"))
    kw = str(args.get("name_keyword") or args.get("keyword") or args.get("value") or "").strip()
    limit = _clamp_v6_limit(args.get("limit"), 80, 1, 300)
    if from_l not in NODE_LABELS or to_l not in NODE_LABELS:
        return {
            "error": f"invalid node labels: from_node_label={from_l!r} to_node_label={to_l!r}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if rel_t not in RELATION_SCHEMA:
        return {
            "error": f"invalid relationship_type: {rel_t!r}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if not _allowed_rel_triple(from_l, rel_t, to_l):
        return {
            "error": f"relation not allowed by schema: {from_l}-[{rel_t}]->{to_l}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if not kw:
        return {
            "error": "missing name_keyword",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    prm = {
        "from_label": from_l,
        "rel_type": rel_t,
        "to_label": to_l,
        "name_match_side": side,
        "kw": kw,
        "limit": limit,
    }
    rows = (
        run_neo_cypher(driver, _CYPHER_RELATION_NAME_SEARCH, prm)
        if driver
        else [{"error": "Neo4j driver not initialized"}]
    )
    return _pack_v6_explicit_tool(
        tool_name="TOOL_neo_relation_name_search",
        neo_rows=rows,
        cypher=_CYPHER_RELATION_NAME_SEARCH,
        cypher_params=prm,
        user_interest=user_interest,
        driver_attached=bool(driver),
        collection_attached=False,
    )


def explicit_tool_neo_relation_by_ids(
    driver: Any,
    args: Dict[str, Any],
    user_interest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """单跳关系 + 起点或终点 Id IN 列表；标签与关系类型均为查询参数。"""
    from_l = str(
        args.get("from_node_label") or args.get("from_type") or args.get("from_label") or ""
    ).strip()
    to_l = str(args.get("to_node_label") or args.get("to_type") or args.get("to_label") or "").strip()
    rel_t = str(args.get("relationship_type") or args.get("rel_type") or "CON").strip()
    id_side = _norm_id_match_side(args.get("id_match_side"))
    raw = args.get("ids") if args.get("ids") is not None else args.get("project_ids")
    ids: List[str] = []
    if isinstance(raw, list):
        ids = [str(x).strip() for x in raw if str(x).strip()]
    limit = _clamp_v6_limit(args.get("limit"), 200, 1, 500)
    if from_l not in NODE_LABELS or to_l not in NODE_LABELS:
        return {
            "error": f"invalid node labels: from_node_label={from_l!r} to_node_label={to_l!r}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if rel_t not in RELATION_SCHEMA:
        return {
            "error": f"invalid relationship_type: {rel_t!r}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if not _allowed_rel_triple(from_l, rel_t, to_l):
        return {
            "error": f"relation not allowed by schema: {from_l}-[{rel_t}]->{to_l}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if not ids:
        return {
            "error": "missing ids",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
            "answer_guidance": "缺少 ids（上一跳节点 Id 字符串数组）。",
        }
    prm = {
        "from_label": from_l,
        "rel_type": rel_t,
        "to_label": to_l,
        "id_match_side": id_side,
        "ids": ids,
        "limit": limit,
    }
    rows = (
        run_neo_cypher(driver, _CYPHER_RELATION_BY_IDS, prm)
        if driver
        else [{"error": "Neo4j driver not initialized"}]
    )
    return _pack_v6_explicit_tool(
        tool_name="TOOL_neo_relation_by_ids",
        neo_rows=rows,
        cypher=_CYPHER_RELATION_BY_IDS,
        cypher_params=prm,
        user_interest=user_interest,
        driver_attached=bool(driver),
        collection_attached=False,
    )


def explicit_tool_neo_node_name_search(
    driver: Any,
    args: Dict[str, Any],
    user_interest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    node_label = str(
        args.get("node_label") or args.get("node_type") or args.get("label") or ""
    ).strip()
    value = str(args.get("name_keyword") or args.get("value") or args.get("keyword") or "").strip()
    limit = _clamp_v6_limit(args.get("limit"), 80, 1, 300)
    if node_label not in NODE_LABELS:
        return {
            "error": f"invalid node_label: {node_label!r}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if not value:
        return {
            "error": "missing name_keyword",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    prm = {"node_label": node_label, "value": value, "limit": limit}
    rows = (
        run_neo_cypher(driver, _CYPHER_NODE_NAME_SEARCH, prm)
        if driver
        else [{"error": "Neo4j driver not initialized"}]
    )
    return _pack_v6_explicit_tool(
        tool_name="TOOL_neo_node_name_search",
        neo_rows=rows,
        cypher=_CYPHER_NODE_NAME_SEARCH,
        cypher_params=prm,
        user_interest=user_interest,
        driver_attached=bool(driver),
        collection_attached=False,
    )


def explicit_tool_neo_list_nodes(
    driver: Any,
    args: Dict[str, Any],
    user_interest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    node_label = str(args.get("node_label") or args.get("node_type") or args.get("label") or "").strip()
    limit = _clamp_v6_limit(args.get("limit"), 200, 1, 500)
    only = args.get("only_enabled")
    if only is None:
        only = True
    if node_label not in NODE_LABELS:
        return {
            "error": f"invalid node_label: {node_label!r}",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    if only:
        stp = flgl_status_cypher_param()
        prm = {**stp, "node_label": node_label, "limit": limit}
        cy = _CYPHER_LIST_NODES_ENABLED
    else:
        prm = {"node_label": node_label, "limit": limit}
        cy = _CYPHER_LIST_NODES_ALL
    rows = run_neo_cypher(driver, cy, prm) if driver else [{"error": "Neo4j driver not initialized"}]
    return _pack_v6_explicit_tool(
        tool_name="TOOL_neo_list_nodes",
        neo_rows=rows,
        cypher=cy,
        cypher_params=prm,
        user_interest=user_interest,
        driver_attached=bool(driver),
        collection_attached=False,
    )


def explicit_tool_chroma_vector_search(
    collection: Any,
    args: Dict[str, Any],
    user_interest: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    _ = user_interest
    q = str(args.get("query") or "").strip()
    top_k = _clamp_v6_limit(args.get("top_k"), 12, 1, 200)
    doc_id = str(args.get("doc_id") or "").strip()
    title_keyword = str(args.get("title_keyword") or "").strip()
    if not q:
        return {
            "error": "missing query",
            "neo4jResults": [],
            "chromaResults": [],
            "stepResults": [],
        }
    rows = (
        run_chroma_vector_search(collection, q, top_k, doc_id, title_keyword)
        if collection
        else [{"error": "Chroma collection not initialized"}]
    )
    return _pack_v6_explicit_tool(
        tool_name="TOOL_chroma_vector_search",
        chroma_rows=rows,
        cypher=None,
        cypher_params=None,
        user_interest=None,
        driver_attached=False,
        collection_attached=bool(collection),
    )


def _pair_messages_to_rounds(msgs: List[dict]) -> List[Dict[str, str]]:
    """将 Redis 消息列表转为 [{user, assistant}, ...] 时间顺序。"""
    rounds: List[Dict[str, str]] = []
    pending_u: Optional[str] = None
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        if role == "user":
            pending_u = content
        elif role == "assistant" and pending_u is not None:
            rounds.append({"user": pending_u[:8000], "assistant": content[:8000]})
            pending_u = None
    return rounds


def history_recent_qa(user_id: Optional[str], session_id: Optional[str], n: int = 3) -> Dict[str, Any]:
    """返回最近 n 轮（每轮 user+assistant）。"""
    store = get_memory_store()
    if not store or not user_id or not session_id:
        return {"error": "memory_unavailable", "rounds": []}
    try:
        n = max(1, min(int(n), 10))
    except (TypeError, ValueError):
        n = 3
    msgs = store.get_messages(user_id, session_id)
    rounds = _pair_messages_to_rounds(msgs)
    tail = rounds[-n:] if rounds else []
    return {"rounds": tail, "total_rounds_in_store": len(rounds)}


def history_similar_questions(
    user_id: Optional[str],
    session_id: Optional[str],
    query: str,
    top_k: int = 3,
) -> Dict[str, Any]:
    """在历史用户句中与 query 相似度最高的 top_k 条，附对应助手答摘要。"""
    store = get_memory_store()
    if not store or not user_id or not session_id:
        return {"error": "memory_unavailable", "matches": []}
    q = (query or "").strip()
    if not q:
        return {"matches": []}
    try:
        top_k = max(1, min(int(top_k), 10))
    except (TypeError, ValueError):
        top_k = 3
    msgs = store.get_messages(user_id, session_id)
    rounds = _pair_messages_to_rounds(msgs)
    scored: List[tuple] = []
    for r in rounds:
        u = (r.get("user") or "").strip()
        if not u:
            continue
        score = SequenceMatcher(None, q, u).ratio()
        scored.append((score, u, (r.get("assistant") or "")[:600]))
    scored.sort(key=lambda x: x[0], reverse=True)
    matches = [
        {"score": round(s, 4), "user_question": u[:2000], "assistant_excerpt": a}
        for s, u, a in scored[:top_k]
    ]
    return {"matches": matches, "query": q}


def build_znwd_v6_vocab_dict(*, compact: bool = False) -> Dict[str, Any]:
    """图谱词汇表。compact=True 时省略大块字段，供 V6 Agent 注入 system 以控制 token。"""
    rel_keys = list(RELATION_SCHEMA.keys())
    tools_desc_full = [
        {
            "api_name": "TOOL_neo_relation_name_search",
            "params": {
                "from_node_label": f"enum {NODE_LABELS}",
                "relationship_type": f"enum {rel_keys}",
                "to_node_label": f"enum {NODE_LABELS}",
                "name_match_side": "from|to（from 匹配起点 Name，to 匹配终点 Name）",
                "name_keyword": "string",
                "limit": "int optional",
            },
            "when": "单跳关系 + Name 子串；例：无人机项目 from=Equipment,rel=CON,to=TestProject,side=from,keyword=无人机",
        },
        {
            "api_name": "TOOL_neo_relation_by_ids",
            "params": {
                "from_node_label": f"enum {NODE_LABELS}",
                "relationship_type": f"enum {rel_keys}",
                "to_node_label": f"enum {NODE_LABELS}",
                "id_match_side": "from|to（Id 列表对应 n 或 m）",
                "ids": "string[]",
                "limit": "int optional",
            },
            "when": "单跳关系 + Id IN；例：项目→方法 from=TestProject,rel=CON,to=TestMethod,id_match_side=from,ids=[...]",
        },
        {
            "api_name": "TOOL_neo_node_name_search",
            "params": {"node_label": f"enum {NODE_LABELS}", "name_keyword": "string", "limit": "int optional"},
            "when": "单类节点按 Name 模糊查",
        },
        {
            "api_name": "TOOL_neo_list_nodes",
            "params": {
                "node_label": f"enum {NODE_LABELS}",
                "limit": "int optional",
                "only_enabled": "bool default true",
            },
            "when": "枚举某类节点",
        },
        {
            "api_name": "TOOL_chroma_vector_search",
            "params": {
                "query": "string (required)",
                "top_k": "int optional",
                "doc_id": "string optional",
                "title_keyword": "string optional",
            },
            "when": "标准/文档向量检索",
        },
    ]
    if compact:
        tools_desc = [
            {"api_name": x["api_name"], "when": x["when"]}
            for x in tools_desc_full
        ]
    else:
        tools_desc = tools_desc_full

    base: Dict[str, Any] = {
        "schema_source_file": "backend/blueprints/GLCX/ZNWD/retrieval_meta_ops.py",
        "node_labels": NODE_LABELS,
        "relation_meanings": RELATION_EXPLANATIONS,
        "relation_schema": RELATION_SCHEMA,
        "v6_retrieval_tools": tools_desc,
        "tool_call_convention": (
            "OpenAPI function.name 均以 TOOL_ 开头（书写习惯可记作 @TOOL + 同名）。"
            "环境变量 **ZNWD_V6_TOOL_CALL_PARSER**：**hermes**（默认）→ 请求体 "
            "`extra_body.tool_call_parser=hermes`，推理服务请使用 `--tool-call-parser hermes`；"
            "**qwen3** → `extra_body.tool_call_parser=qwen3`，与 `--tool-call-parser qwen3` 对齐；"
            "**none** / off / disabled → 不传 extra_body，由网关或 vLLM 启动参数单独指定 parser。"
        ),
        "v6_tool_call_parser": resolve_znwd_v6_tool_call_parser(),
    }
    if not compact:
        base["node_entity_fields"] = NODE_ENTITY_FIELDS
        base["meta_op_codes"] = list(META_OPS.keys())
    else:
        base["node_entity_fields_note"] = (
            "完整 NODE_ENTITY_FIELDS 见后端 retrieval_meta_ops.py；本轮为省 token 已从 system JSON 省略。"
        )
    return base


HISTORY_RECENT_TOOL_OPENAI: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "znwd_history_recent_qa",
        "description": (
            "读取本会话在 Redis 中的最近若干轮「用户提问 + 助手回答」。"
            "当当前用户问题指代不明（如「它有哪些项目」「上文那个设备」）且需要承接对话时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "返回最近 n 轮完整问答对，默认 3，最大 10",
                    "default": 3,
                },
            },
        },
    },
}

HISTORY_SIMILAR_TOOL_OPENAI: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "znwd_history_similar_questions",
        "description": (
            "在本会话历史用户问题中，按字符串相似度找出与给定 query 最接近的若干条，"
            "并返回对应助手回答摘要。用于判断是否与历史问题重复或可参考历史结论。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用于比对的历史检索句（可与当前用户原话相同或略改写）"},
                "top_k": {"type": "integer", "description": "返回条数，默认 3，最大 10", "default": 3},
            },
            "required": ["query"],
        },
    },
}


_EXPLICIT_RETRIEVAL_TOOLS_OPENAI: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "TOOL_neo_relation_name_search",
            "description": (
                "【@TOOL】Neo4j：单跳关系 + Name 子串。须先判断关键词落在**起点 n** 还是**终点 m** 的 Name 上，再选 name_match_side。"
                "**Equipment→TestProject**：问「某装备有哪些测试项目」（关键词是装备名，如无人机、坦克）→ **name_match_side=from**，"
                "name_keyword=装备短词（匹配起点 Equipment.n.Name）。"
                "**Equipment→Indicator**：问「某装备有哪些考核/性能指标」或同一问句里既要项目又要指标时→ **from_node_label=Equipment,relationship_type=CON,to_node_label=Indicator,name_match_side=from**，name_keyword=装备短词。"
                "仅当问「哪些设备具备某类/某个测试项目」且关键词是**项目侧**词（如高温、淋雨打在 TestProject.Name）→ **name_match_side=to**。"
                "Environment→TestProject 且关键词是环境词 → 通常 **from**。TestProject→TestMethod 且上一步已锁定项目 Id → 常用 **by_ids**。"
                "name_keyword 用短词。反例：「无人机有哪些项目」却用 to+无人机 会错（会把无人机当成项目名）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_node_label": {"type": "string", "enum": list(NODE_LABELS)},
                    "relationship_type": {"type": "string", "enum": list(RELATION_SCHEMA.keys())},
                    "to_node_label": {"type": "string", "enum": list(NODE_LABELS)},
                    "name_match_side": {
                        "type": "string",
                        "enum": ["from", "to"],
                        "description": (
                            "from=关键词匹配起点 n.Name；to=匹配终点 m.Name。"
                            "Equipment→TestProject 且问「某装备有哪些项目」→必须用 from。"
                            "Equipment→TestProject 且问「哪些设备有某项目」、关键词打在项目名上→用 to。"
                        ),
                    },
                    "name_keyword": {
                        "type": "string",
                        "description": "短关键词，勿填整句；与 name_match_side 搭配打在正确的 n.Name 或 m.Name 上",
                    },
                    "limit": {"type": "integer", "default": 80},
                },
                "required": [
                    "from_node_label",
                    "relationship_type",
                    "to_node_label",
                    "name_match_side",
                    "name_keyword",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TOOL_neo_relation_by_ids",
            "description": (
                "【@TOOL】Neo4j：单跳关系 + **图节点 Id 列表**（属性 Id，不是 Name）。"
                "ids 中每一项必须是数据库里的 Id 字符串；禁止把中文项目名、整句用户问题、方法标题当 Id 填入。"
                "若只有名称没有 Id，请先用 TOOL_neo_relation_name_search 或 TOOL_neo_node_name_search 取得 Id。"
                "须传 from_node_label、relationship_type、to_node_label、id_match_side（from=过滤 n.Id，to=过滤 m.Id）、ids。"
                "例 TestProject→TestMethod：TestProject,CON,TestMethod,id_match_side=from,ids=[上一步返回的项目Id]。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_node_label": {"type": "string", "enum": list(NODE_LABELS)},
                    "relationship_type": {"type": "string", "enum": list(RELATION_SCHEMA.keys())},
                    "to_node_label": {"type": "string", "enum": list(NODE_LABELS)},
                    "id_match_side": {
                        "type": "string",
                        "enum": ["from", "to"],
                        "description": "ids 对应起点 n 还是终点 m 的 Id",
                    },
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "节点 Id 列表（图属性 Id）；禁止填显示名或自然语言句子",
                    },
                    "limit": {"type": "integer", "default": 200},
                },
                "required": [
                    "from_node_label",
                    "relationship_type",
                    "to_node_label",
                    "id_match_side",
                    "ids",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TOOL_neo_node_name_search",
            "description": (
                "【@TOOL】Neo4j：单类节点按 Name 模糊查。node_label 为图节点标签；返回含完整 properties。"
                "用户说的整句像**试验/测试项目名称**（如「高温工作性能测试」「淋雨试验」）时，优先 **TestProject** + "
                "name_keyword 用核心短词（可含「性能」「测试」等词根，勿把整个问句塞进去）。"
                "不要把此类问句默认当成 Environment 只查「高温」。"
                "node_label=**Environment** 且用户问该环境的**分类/分级/典型组合/约束/要求**时：name_keyword=环境短词（如高温），"
                "作答须引用命中节点 properties 的 **Type、Class、Combination、Limit、Request**（与库字段名一致）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_label": {"type": "string", "enum": list(NODE_LABELS)},
                    "name_keyword": {"type": "string"},
                    "limit": {"type": "integer", "default": 80},
                },
                "required": ["node_label", "name_keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TOOL_neo_list_nodes",
            "description": (
                "【@TOOL】Neo4j：按 node_label 列举节点（含 properties）；only_enabled 默认 true。"
                "问全库「环境分类/分级/典型组合/约束/要求」或环境体系时，用 node_label=**Environment**，"
                "在返回结果中归纳各节点的 **Type、Class、Combination、Limit、Request**，勿用 TestProject 或仅 Chroma。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_label": {"type": "string", "enum": list(NODE_LABELS)},
                    "limit": {"type": "integer", "default": 200},
                    "only_enabled": {"type": "boolean", "default": True},
                },
                "required": ["node_label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TOOL_chroma_vector_search",
            "description": "【@TOOL】Chroma 向量检索文档片段。方法类问题在 Neo 之后再调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 12},
                    "doc_id": {"type": "string"},
                    "title_keyword": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
]


def build_znwd_v6_openai_tool_definitions() -> List[Dict[str, Any]]:
    """供 OpenAI / LangChain bind_tools：历史 + Neo/Chroma 工具（Cypher 骨架固定，标签/关系类型经参数传入并由服务端校验）。"""
    return [
        HISTORY_RECENT_TOOL_OPENAI,
        HISTORY_SIMILAR_TOOL_OPENAI,
        *_EXPLICIT_RETRIEVAL_TOOLS_OPENAI,
    ]


def build_znwd_v6_tool_dispatch(
    driver: Any,
    collection: Any,
    user_interest: Optional[Dict[str, Any]],
    user_id: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    """name -> (args_dict) -> result_dict（与现有 SSE tool_result 形态兼容）。"""

    def _recent(args: Dict[str, Any]) -> Dict[str, Any]:
        n = args.get("n", 3)
        return history_recent_qa(user_id, session_id, int(n) if n is not None else 3)

    def _similar(args: Dict[str, Any]) -> Dict[str, Any]:
        q = str(args.get("query") or "")
        tk = args.get("top_k", 3)
        return history_similar_questions(user_id, session_id, q, int(tk) if tk is not None else 3)

    def _meta(args: Dict[str, Any]) -> Dict[str, Any]:
        return execute_meta_ops_retrieve(args, driver, collection, user_interest)

    return {
        "znwd_history_recent_qa": _recent,
        "znwd_history_similar_questions": _similar,
        "znwd_meta_ops_retrieve": _meta,
        "znwd_execute_meta_steps": _meta,
        "TOOL_neo_relation_name_search": lambda a: explicit_tool_neo_relation_name_search(driver, a, user_interest),
        "TOOL_neo_relation_by_ids": lambda a: explicit_tool_neo_relation_by_ids(driver, a, user_interest),
        "TOOL_neo_node_name_search": lambda a: explicit_tool_neo_node_name_search(driver, a, user_interest),
        "TOOL_neo_list_nodes": lambda a: explicit_tool_neo_list_nodes(driver, a, user_interest),
        "TOOL_chroma_vector_search": lambda a: explicit_tool_chroma_vector_search(collection, a, user_interest),
    }
