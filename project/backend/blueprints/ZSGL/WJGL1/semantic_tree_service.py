"""LLM 语义树抽取 — WJGL 文件审核（国军标/国标试验标准）。"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import Config

from .graph_link_helper import build_graph_link_candidates
from .semantic_tree_schema import (
    GJB_GBT_SEMANTIC_TEMPLATE,
    build_rule_based_semantic_tree,
    infer_equipment_name,
)

logger = logging.getLogger(__name__)

_SEMANTIC_TREE_PROMPT = f"""你是国军标/国标试验标准文档结构分析专家。根据元数据、章节目录与正文摘要，构建**语义树**（按试验对象与试验主题组织，禁止逐条复制目录）。

{GJB_GBT_SEMANTIC_TEMPLATE}

## 抽取要求
1. **根节点 ST_ROOT** = 试验对象/装备（如无人机、汽车），title 用装备名，不是标准全称
2. **graph_link_hint** = 根节点对应的装备中文名（用于图库 Equipment 关联）
3. 一级子节点：通用要求(ST_GENERAL)、试验主题(ST_TEST，名从标准 part/标题提取)、附录(ST_APPENDIX)
4. ST_TEST 下必须有：试验条件(ST_TEST_COND)、试验过程(ST_TEST_PROC)、结果评定(ST_TEST_EVAL)（无对应章节可省略该子节点）
5. section_ids 必须从给定目录选取，可跨节合并；每个 section_id 最多出现一次
6. document_class：法规标准 / 试验方法标准 / 试验方案 / 试验报告

返回纯 JSON（不要 markdown）：
{{
  "document_class": "法规标准",
  "root": {{
    "node_id": "ST_ROOT",
    "title": "装备名",
    "summary": "一句话",
    "section_ids": [],
    "graph_link_hint": "装备名",
    "children": [
      {{
        "node_id": "ST_GENERAL",
        "title": "通用要求",
        "summary": "",
        "section_ids": [],
        "children": []
      }},
      {{
        "node_id": "ST_TEST",
        "title": "低温试验",
        "summary": "",
        "section_ids": [],
        "children": [
          {{"node_id": "ST_TEST_COND", "title": "试验条件", "section_ids": [], "children": []}},
          {{"node_id": "ST_TEST_PROC", "title": "试验过程", "section_ids": [], "children": []}},
          {{"node_id": "ST_TEST_EVAL", "title": "结果评定", "section_ids": [], "children": []}}
        ]
      }}
    ]
  }}
}}
"""


def _extract_json(text: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except Exception:
        return {}


def _normalize_tree_node(raw: Dict[str, Any], fallback_id: str) -> Dict[str, Any]:
    children_raw = raw.get("children") or []
    children = [
        _normalize_tree_node(c, f"{fallback_id}_{i + 1:02d}")
        for i, c in enumerate(children_raw)
        if isinstance(c, dict)
    ]
    return {
        "node_id": str(raw.get("node_id") or fallback_id),
        "title": str(raw.get("title") or "").strip() or "未命名",
        "summary": str(raw.get("summary") or "").strip(),
        "section_ids": [str(x) for x in (raw.get("section_ids") or []) if x],
        "graph_link_hint": str(raw.get("graph_link_hint") or "").strip(),
        "children": children,
    }


def _catalogue_text(catalogue: List[Any], limit: int = 60) -> str:
    lines: List[str] = []
    for item in (catalogue or [])[:limit]:
        if isinstance(item, dict):
            sid = item.get("section_id") or item.get("id") or ""
            title = item.get("title") or item.get("heading") or ""
            lines.append(f"- {sid}: {title}")
        else:
            lines.append(str(item))
    return "\n".join(lines)


def _resolve_graph_link(root: Dict[str, Any], meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    hint = (
        root.get("graph_link_hint")
        or root.get("title")
        or infer_equipment_name(meta.get("std_name") or meta.get("title") or "", meta.get("abstract") or "")
    )
    doc = {
        "std_name": meta.get("std_name") or meta.get("title") or hint,
        "std_code": meta.get("std_code") or "",
        "abstract": meta.get("abstract") or root.get("summary") or "",
        "keywords": meta.get("keywords") or [],
        "title": meta.get("title") or "",
    }
    try:
        result = build_graph_link_candidates(doc, Config.NEO4J_DRIVER)
        cands = result.get("candidates") or []
        for c in cands:
            if c.get("node_type") in ("Equipment", "EquipmentType") and c.get("status") == "recommended":
                return {
                    "node_type": c.get("node_type"),
                    "node_id": c.get("node_id"),
                    "node_name": c.get("node_name"),
                    "confidence": c.get("score", 0),
                    "reason": (c.get("evidence") or {}).get("rule_detail") or "规则/向量匹配",
                }
        for c in cands:
            if c.get("node_type") == "Equipment" and c.get("node_id"):
                return {
                    "node_type": c.get("node_type"),
                    "node_id": c.get("node_id"),
                    "node_name": c.get("node_name"),
                    "confidence": c.get("score", 0),
                    "reason": "装备相关度匹配",
                }
        if cands:
            c = cands[0]
            return {
                "node_type": c.get("node_type"),
                "node_id": c.get("node_id"),
                "node_name": c.get("node_name"),
                "confidence": c.get("score", 0),
                "reason": "综合相关度最高",
            }
    except Exception as e:
        logger.warning("resolve graph link failed: %s", e)
    if hint:
        return {"node_type": "Equipment", "node_id": "", "node_name": hint, "confidence": 0.3, "reason": "标题推断"}
    return None


def build_fallback_semantic_tree(
    parsed: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """规则语义树 — 即时展示，符合国军标/国标结构。"""
    meta = meta or {}
    sections = parsed.get("sections") or []
    root = build_rule_based_semantic_tree(sections, meta)
    gl = _resolve_graph_link(root, meta)
    if gl:
        root["graph_link"] = gl
    return {
        "document_class": meta.get("category") or "法规标准",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tree_source": "fallback",
        "root": root,
    }


def build_semantic_tree(
    parsed: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
    *,
    use_llm: bool = True,
) -> Dict[str, Any]:
    meta = meta or {}
    if not use_llm:
        return build_fallback_semantic_tree(parsed, meta)

    catalogue = parsed.get("section_catalogue") or []
    if not catalogue and parsed.get("sections"):
        catalogue = [
            {"section_id": s.get("section_id"), "title": s.get("title")}
            for s in parsed.get("sections") or []
        ]

    full_text = (parsed.get("full_text") or "")[:6000]
    cat_text = _catalogue_text(catalogue)
    meta_block = json.dumps({
        "std_name": meta.get("std_name") or meta.get("title"),
        "std_code": meta.get("std_code"),
        "abstract": meta.get("abstract"),
        "keywords": meta.get("keywords"),
    }, ensure_ascii=False)

    fallback = build_fallback_semantic_tree(parsed, meta)

    if not Config.OPENAI_CLIENT:
        return fallback

    user_msg = (
        f"元数据：\n{meta_block}\n\n"
        f"章节目录（section_id: 标题）：\n{cat_text}\n\n"
        f"正文摘要：\n{full_text[:5000]}\n\n"
        f"请按模板构建语义树，根节点必须是装备/试验对象名。"
    )
    try:
        resp = Config.OPENAI_CLIENT.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SEMANTIC_TREE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=2500,
        )
        raw = _extract_json(resp.choices[0].message.content or "")
        root_raw = raw.get("root") or {}
        root = _normalize_tree_node(root_raw, "ST_ROOT")
        if not root.get("children"):
            return fallback
        gl = _resolve_graph_link(root, meta)
        if gl:
            root["graph_link"] = gl
        return {
            "document_class": raw.get("document_class") or meta.get("category") or "法规标准",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tree_source": "llm",
            "root": root,
        }
    except Exception as e:
        logger.error("build_semantic_tree LLM failed: %s", e)
        return fallback


def find_tree_node(root: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    if not root:
        return None
    if root.get("node_id") == node_id:
        return root
    for ch in root.get("children") or []:
        found = find_tree_node(ch, node_id)
        if found:
            return found
    return None


def collect_tree_node_ids(root: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    if root.get("node_id"):
        ids.append(root["node_id"])
    for ch in root.get("children") or []:
        ids.extend(collect_tree_node_ids(ch))
    return ids


def collect_node_section_ids(node: Dict[str, Any]) -> List[str]:
    """节点及子树的 section_ids（用于 ST_TEST 等父节点内容聚合）。"""
    ids: List[str] = []
    for sid in node.get("section_ids") or []:
        if sid and sid not in ids:
            ids.append(str(sid))
    for ch in node.get("children") or []:
        for sid in collect_node_section_ids(ch):
            if sid not in ids:
                ids.append(sid)
    return ids
