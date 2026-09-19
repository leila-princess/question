"""
ZNWD5 执行器：ZNWD4 执行 + Zvec 文档/实体元操作；chroma_* 可按环境变量映射为 zvec_*。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Tuple

from blueprints.GLCX.ZNWD4 import executor as _base

from .neo_vector_hybrid import neo_vector_hybrid_query
from .zvec_doc_retriever import zvec_doc_filter, zvec_doc_hybrid_search, zvec_doc_rerank
from .zvec_entity_probe import zvec_semantic_probe

logger = logging.getLogger(__name__)

_orig_meta_step_events = _base._meta_step_events

# 复用 ZNWD4 工具导出
ZNWD_CHROMA_UI_MAX = _base.ZNWD_CHROMA_UI_MAX
format_neo_preview_lines = _base.format_neo_preview_lines
format_chroma_preview_lines = _base.format_chroma_preview_lines
normalize_chroma_rows_for_client = _base.normalize_chroma_rows_for_client
chroma_rows_nonempty = _base.chroma_rows_nonempty
run_chroma_vector_search = _base.run_chroma_vector_search


def _doc_engine() -> str:
    return os.environ.get("ZNWD5_DOC_ENGINE", "hybrid").strip().lower()


def _use_zvec_docs() -> bool:
    return _doc_engine() in ("zvec", "hybrid")


def _remap_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not _use_zvec_docs():
        return list(steps or [])
    mapping = {
        "chroma_vector_search": "zvec_doc_hybrid_search",
        "chroma_score_filter": "zvec_doc_filter",
        "chroma_rerank": "zvec_doc_rerank",
    }
    out: List[Dict[str, Any]] = []
    for st in steps or []:
        st2 = dict(st)
        op = st2.get("op_code") or ""
        params = dict(st2.get("params") or {})
        if op in mapping:
            st2["op_code"] = mapping[op]
            if op == "chroma_rerank" and "query" not in params:
                params["query"] = params.get("query") or ""
        st2["params"] = params
        out.append(st2)
    return out


def _zvec_entity_rows(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    q = str(params.get("query") or "")
    top_k = int(params.get("top_k") or 12)
    node_types = params.get("node_types")
    rows = zvec_semantic_probe(q, label_hints=node_types, question=q, topk=top_k)
    out: List[Dict[str, Any]] = []
    for r in rows:
        props = r.get("properties") if isinstance(r.get("properties"), dict) else {}
        out.append(
            {
                "n": {
                    "labels": [r.get("node_type")],
                    "properties": {"Id": r.get("id"), "Name": r.get("name"), **props},
                },
                "zvec_score": r.get("zvec_score"),
            }
        )
    return out


def _zvec_entity_fts_rows(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    from .zvec_index_manager import get_zvec_manager

    q = str(params.get("query") or "")
    top_k = int(params.get("top_k") or 12)
    node_types = params.get("node_types")
    mgr = get_zvec_manager()
    hits = mgr.search_graph_fts(q, topk=top_k, node_types=node_types)
    out: List[Dict[str, Any]] = []
    for h in hits:
        fields = h.get("fields") or {}
        out.append(
            {
                "n": {
                    "labels": [fields.get("node_type")],
                    "properties": {"Id": fields.get("neo4j_id"), "Name": fields.get("name"), **fields},
                },
                "zvec_score": h.get("score"),
            }
        )
    return out


def _extract_field_from_node_obj(obj: Any, field: str) -> str:
    if not isinstance(obj, dict):
        return ""
    props = obj.get("properties") if isinstance(obj.get("properties"), dict) else obj
    if isinstance(props, dict):
        v = str(props.get(field) or "").strip()
        if v:
            return v
    return str(obj.get(field) or "").strip()


def _resolve_ref_values(ref_cfg: Dict[str, Any], step_outputs: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    sid = str(ref_cfg.get("step_id") or "").strip()
    tgt = str(ref_cfg.get("target") or "n").strip()
    fld = str(ref_cfg.get("field") or "Id").strip()
    rows = step_outputs.get(sid) or []
    vals: List[str] = []
    seen: set = set()

    def _collect(target: str) -> None:
        for r in rows:
            if not isinstance(r, dict):
                continue
            obj = r.get(target)
            v = _extract_field_from_node_obj(obj, fld)
            if v and v not in seen:
                seen.add(v)
                vals.append(v)

    _collect(tgt)
    if not vals and fld == "Id":
        alt = "m" if tgt == "n" else "n"
        if alt != tgt:
            _collect(alt)
    return vals


def _prepare_neo_step(st: Dict[str, Any], step_outputs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """单步委托 ZNWD4 执行前，用已完成的 step_outputs 解析 from_values_ref。"""
    st2 = dict(st)
    params = dict(st2.get("params") or {})
    refd = params.get("from_values_ref")
    if isinstance(refd, dict):
        refd = dict(refd)
        op_code = str(st2.get("op_code") or "")
        if op_code == "neo_relation_query" and not str(refd.get("target") or "").strip():
            ft = str(params.get("from_type") or "")
            tt = str(params.get("to_type") or "")
            if ft == "TestProject" and tt == "TestMethod":
                refd["target"] = "m"
            elif ft in ("Equipment", "Environment") and tt == "TestProject":
                refd["target"] = "m"
        params["value_list"] = _resolve_ref_values(refd, step_outputs)
        if params.get("match_mode") != "in":
            params["match_mode"] = "in"
        rfl = str(refd.get("field") or "Id").strip()
        if rfl:
            params["from_field"] = rfl
        params.pop("from_values_ref", None)
    st2["params"] = params
    return st2


def _znwd5_mixed_events(driver: Any, collection: Any, steps: List[Dict[str, Any]]):
    neo_rows: List[Dict[str, Any]] = []
    chroma_rows: List[Dict[str, Any]] = []
    step_results: List[Dict[str, Any]] = []
    step_outputs: Dict[str, List[Dict[str, Any]]] = {}

    from blueprints.GLCX.ZNWD4.znwd4_user_copy import step_start_purpose

    runnable = [s for s in steps if not s.get("skip_db")]
    total = len(runnable)
    run_idx = 0

    for st in steps:
        op_code = st.get("op_code") or ""
        params = dict(st.get("params") or {})

        if st.get("skip_db"):
            step_outputs[str(st["step_id"])] = []
            step_results.append({"step_id": st["step_id"], "op_code": op_code, "rows": []})
            continue

        run_idx += 1
        yield ("step_start", {"step": st, "purpose": step_start_purpose(st), "current": run_idx, "total": total})

        if op_code == "neo_vector_hybrid_query":
            res = neo_vector_hybrid_query(driver, params)
            neo_rows.extend(res)
            step_outputs[str(st["step_id"])] = res
        elif op_code == "zvec_entity_search":
            res = _zvec_entity_rows(params)
            neo_rows.extend(res)
            step_outputs[str(st["step_id"])] = res
        elif op_code == "zvec_entity_fts":
            res = _zvec_entity_fts_rows(params)
            neo_rows.extend(res)
            step_outputs[str(st["step_id"])] = res
        elif op_code == "zvec_doc_hybrid_search":
            res = zvec_doc_hybrid_search(
                str(params.get("query") or ""),
                top_k=int(params.get("top_k") or 12),
                doc_id=str(params.get("doc_id") or ""),
                title_keyword=str(params.get("title_keyword") or ""),
                std_code=str(params.get("std_code") or ""),
                chroma_collection=collection,
            )
            chroma_rows = res
            step_outputs[str(st["step_id"])] = res
        elif op_code == "zvec_doc_filter":
            chroma_rows = zvec_doc_filter(
                chroma_rows,
                min_score=float(params.get("min_score", 0.0)),
                max_distance=float(params.get("max_distance", 2.0)),
            )
            step_outputs[str(st["step_id"])] = chroma_rows
            res = chroma_rows
        elif op_code == "zvec_doc_rerank":
            chroma_rows = zvec_doc_rerank(chroma_rows, str(params.get("query") or ""), top_k=int(params.get("top_k") or 12))
            step_outputs[str(st["step_id"])] = chroma_rows
            res = chroma_rows
        else:
            prepared = _prepare_neo_step(st, step_outputs)
            for ev in _orig_meta_step_events(driver, collection, [prepared]):
                if ev[0] == "step_done":
                    data = ev[1]
                    step_neo = list(data.get("neo_rows") or [])
                    neo_rows.extend(step_neo)
                    chroma_rows = list(data.get("chroma_rows") or chroma_rows)
                    sr = data.get("step_result") or {
                        "step_id": st["step_id"],
                        "op_code": op_code,
                        "rows": [],
                    }
                    step_results.append(sr)
                    step_outputs[str(st["step_id"])] = sr.get("rows") or []
                    yield (
                        "step_done",
                        {
                            "step": st,
                            "neo_rows": step_neo,
                            "chroma_rows": chroma_rows,
                            "step_result": sr,
                        },
                    )
            continue

        sr = {"step_id": st["step_id"], "op_code": op_code, "rows": step_outputs.get(str(st["step_id"])) or []}
        step_results.append(sr)
        yield ("step_done", {"step": st, "neo_rows": list(neo_rows), "chroma_rows": list(chroma_rows), "step_result": sr})

    yield ("complete", {"neo_rows": neo_rows, "chroma_rows": chroma_rows, "step_results": step_results})


def execute_meta_steps(driver: Any, collection: Any, steps: List[Dict[str, Any]]):
    for ev in _znwd5_mixed_events(driver, collection, _remap_steps(steps)):
        if ev[0] == "complete":
            data = ev[1]
            return data["neo_rows"], data["chroma_rows"], data["step_results"]
    return [], [], []


def iter_meta_steps(driver: Any, collection: Any, steps: List[Dict[str, Any]]):
    runnable = [s for s in (_remap_steps(steps)) if not s.get("skip_db")]
    total = len(runnable)
    run_idx = 0
    for ev in _znwd5_mixed_events(driver, collection, _remap_steps(steps)):
        if ev[0] == "step_start":
            run_idx += 1
            st = ev[1].get("step") or {}
            from blueprints.GLCX.ZNWD4.znwd4_user_copy import step_start_purpose

            yield ("step_start", {**ev[1], "purpose": step_start_purpose(st), "current": run_idx, "total": total})
        else:
            yield ev
