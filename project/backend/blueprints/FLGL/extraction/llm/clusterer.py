"""LLM 轻量聚类 + 规则降级。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from ..shared.schemas import ClusterMode, MethodGroup, SemanticModule
from .anatomist import _call_llm_json, AnatomistError
from .prompts import CLUSTER_SYSTEM_PROMPT, CLUSTER_USER_TEMPLATE

logger = logging.getLogger(__name__)


def _modules_for_cluster_prompt(modules: List[SemanticModule]) -> List[Dict[str, Any]]:
    sorted_mods = sorted(modules, key=lambda m: (m.get("start_line") or 0))
    out = []
    for i, m in enumerate(sorted_mods):
        neighbor_before = sorted_mods[i - 1]["module_id"] if i > 0 else None
        neighbor_after = sorted_mods[i + 1]["module_id"] if i < len(sorted_mods) - 1 else None
        out.append({
            "module_id": m.get("module_id"),
            "type": m.get("type"),
            "title": m.get("title"),
            "start_line": m.get("start_line"),
            "end_line": m.get("end_line"),
            "neighbor_before": neighbor_before,
            "neighbor_after": neighbor_after,
        })
    return out


def cluster_modules_llm(modules: List[SemanticModule]) -> tuple[List[MethodGroup], ClusterMode]:
    if not modules:
        return [], "llm"
    payload = _modules_for_cluster_prompt(modules)
    user = CLUSTER_USER_TEMPLATE.format(modules_json=json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        data = _call_llm_json(CLUSTER_SYSTEM_PROMPT, user, temperature=0.05)
        groups_raw = data.get("groups") or []
        if not isinstance(groups_raw, list) or not groups_raw:
            raise AnatomistError("empty groups")
        groups: List[MethodGroup] = []
        for g in groups_raw:
            if not isinstance(g, dict):
                continue
            name = str(g.get("group_name") or "").strip()
            mids = list(g.get("module_ids") or [])
            if not name or not mids:
                continue
            groups.append({
                "group_name": name,
                "module_ids": [str(x) for x in mids],
                "primary_section_no": str(g.get("primary_section_no") or ""),
                "cluster_mode": "llm",
            })
        if groups:
            return groups, "llm"
    except Exception as e:
        logger.warning("[clusterer] LLM 聚类失败，规则降级: %s", e)
    return cluster_modules_rule_fallback(modules)


def cluster_modules_rule_fallback(modules: List[SemanticModule]) -> tuple[List[MethodGroup], ClusterMode]:
    """按章节号前缀与行号邻接分组。"""
    if not modules:
        return [], "rule_fallback"
    sec_pat = re.compile(r"(\d+(?:\.\d+){0,2})")
    buckets: Dict[str, List[SemanticModule]] = {}
    for m in sorted(modules, key=lambda x: x.get("start_line") or 0):
        title = str(m.get("title") or "")
        msec = sec_pat.search(title)
        key = msec.group(1).rsplit(".", 1)[0] if msec and "." in msec.group(1) else (msec.group(1) if msec else f"block_{m.get('start_line')}")
        buckets.setdefault(key, []).append(m)

    groups: List[MethodGroup] = []
    for key, mods in buckets.items():
        name_base = mods[0].get("title") or key
        name = f"{name_base}-试验方法" if "试验方法" not in name_base else name_base
        groups.append({
            "group_name": name[:80],
            "module_ids": [str(m["module_id"]) for m in mods],
            "primary_section_no": key if re.match(r"^\d", key) else "",
            "cluster_mode": "rule_fallback",
        })
    return groups, "rule_fallback"
