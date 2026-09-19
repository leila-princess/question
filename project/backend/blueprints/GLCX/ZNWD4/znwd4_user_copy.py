"""User-facing copy for ZNWD4 SSE events (no Neo4j/Chroma/internal jargon)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_TECH_PATTERN = re.compile(
    r"Neo4j|Chroma|Cypher|probe_|BIZ_[A-Z0-9_]+|UBIZ_|neo_[a-z_]+|focus_id|"
    r"match_confidence|db_direct|db_synthesize|routing|retrieval_question",
    re.I,
)

_STEP_LABELS = {
    "routing": "理解问题",
    "retrieval": "检索清单与资料",
    "guard": "核对检索结果",
    "generating": "整理回答",
    "intent_judge": "理解问题",
    "neo4j_search": "清单检索",
    "chroma_search": "文档检索",
    "fusion": "合并结果",
    "synthesis": "整理回答",
}

_KIND_LABELS = {
    "system": "",
    "route": "理解问题",
    "retrieval": "检索",
    "synthesis": "整理回答",
    "guard": "核对",
}


def sanitize_user_text(text: str, *, fallback: str = "正在处理…") -> str:
    """Strip or replace technical tokens in user-visible strings."""
    s = (text or "").strip()
    if not s:
        return fallback
    if _TECH_PATTERN.search(s):
        return fallback
    s = s.replace("图谱", "清单").replace("清单库", "清单与标准库")
    s = re.sub(r"\bSYNR\b", "方法正文", s)
    return s


def step_label(step_type: str) -> str:
    return _STEP_LABELS.get(str(step_type or "").strip(), "处理中")


def thinking_kind_label(kind: str) -> str:
    return _KIND_LABELS.get(str(kind or "system").strip().lower(), "")


def step_message(
    step_type: str,
    status: str,
    *,
    purpose: str = "",
    neo_hits: int = 0,
    chroma_hits: int = 0,
    user_biz_title: str = "",
    progress_current: int = 0,
    progress_total: int = 0,
) -> str:
    st = str(step_type or "").strip()
    stat = str(status or "").strip().lower()
    if st == "routing":
        if stat == "loading":
            return "正在理解您的问题…"
        if user_biz_title:
            return f"已理解：{sanitize_user_text(user_biz_title, fallback='将为您检索清单数据')}"
        return "已理解您的问题，准备检索"
    if st == "retrieval":
        if stat == "loading":
            if purpose:
                return f"正在检索：{sanitize_user_text(purpose, fallback='相关清单数据')}…"
            if progress_total > 1:
                return f"正在检索（{progress_current}/{progress_total}）…"
            return "正在清单与标准库中检索…"
        parts: List[str] = []
        if neo_hits:
            parts.append(f"清单 {neo_hits} 条")
        if chroma_hits:
            parts.append(f"文档 {chroma_hits} 份")
        detail = "、".join(parts) if parts else "暂无命中"
        return f"检索完成（{detail}）"
    if st == "guard":
        if stat == "loading":
            return "正在核对检索结果是否与您的问题一致…"
        return "检索结果已核对"
    if st == "generating":
        if stat == "loading":
            return "正在整理回答…"
        return "回答已生成"
    return sanitize_user_text(purpose) or step_label(st)


def retrieval_done_message(neo_hits: int, chroma_hits: int) -> str:
    parts: List[str] = []
    if neo_hits:
        parts.append(f"清单数据 {neo_hits} 条")
    if chroma_hits:
        parts.append(f"相关文档 {chroma_hits} 份")
    detail = "、".join(parts) if parts else "暂无命中"
    return f"检索完成（{detail}）。"


def step_start_purpose(st: Dict[str, Any]) -> str:
    purpose = str(st.get("purpose") or "").strip()
    if purpose:
        return sanitize_user_text(purpose, fallback="相关数据")
    op = str(st.get("op_code") or "")
    if op == "neo_equipment_under_type":
        return "设备类型下的具体装备"
    if op == "chroma_vector_search":
        return "相关标准文档"
    if op.startswith("neo_"):
        return "清单数据"
    if op.startswith("chroma_"):
        return "标准文档"
    return "检索数据"


def humanize_tool_milestone_user(name: str, args: Optional[Dict[str, Any]] = None) -> str:
    args = args or {}
    if name == "znwd4_probe_keyword":
        kw = str(args.get("keyword") or "").strip()
        return f"正在清单里核对「{kw}」…" if kw else "正在清单里核对关键词…"
    if name == "znwd4_history_recent_qa":
        return "正在结合上文理解您的问题…"
    if name == "znwd4_history_similar_questions":
        return "正在参考历史相似问题…"
    return "正在处理…"


def milestone_ecs_fast_path(route_id: str = "", score: float = 0.0) -> str:
    """User-facing ECS fast-path milestone (no route IDs or scores)."""
    return "已理解您的问题，准备按装备类型检索…"


def humanize_probe_result_user(data: Dict[str, Any]) -> Optional[str]:
    best = data.get("best") if isinstance(data.get("best"), dict) else None
    if best:
        nm = str(best.get("name") or "").strip()
        type_val = str(best.get("type_value") or "").strip()
        if nm and type_val and type_val not in nm:
            return f"在清单中定位到：{type_val} · {nm}"
        if nm:
            return f"在清单中定位到：{nm}"
    if int(data.get("match_count") or 0) == 0:
        return "清单中暂未命中同名项，将按问法继续检索。"
    return None


def stream_preview_lines_user(
    *,
    business_title: str,
    neo_lines: List[str],
    chroma_lines: List[str],
) -> List[str]:
    out: List[str] = [f"【检索结果】{business_title}"]
    if neo_lines:
        out.append("清单：")
        out.extend(neo_lines)
    if chroma_lines:
        out.append("文档：")
        out.extend(chroma_lines)
    if len(out) <= 1:
        out.append("（暂无命中记录）")
    return out


def pack_step_payload(
    step_type: str,
    status: str,
    *,
    debug_message: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    msg = step_message(
        step_type,
        status,
        purpose=str(extra.get("purpose") or ""),
        neo_hits=int(extra.get("neoHits") or extra.get("neo_hits") or 0),
        chroma_hits=int(extra.get("chromaHits") or extra.get("chroma_hits") or 0),
        user_biz_title=str(extra.get("userBizTitle") or extra.get("user_biz_title") or ""),
        progress_current=int(extra.get("progress_current") or 0),
        progress_total=int(extra.get("progress_total") or 0),
    )
    payload: Dict[str, Any] = {
        "type": step_type,
        "status": status,
        "message": msg,
        **{k: v for k, v in extra.items() if k not in ("purpose", "neoHits", "neo_hits", "chromaHits", "chroma_hits")},
    }
    if debug_message:
        payload["debugMessage"] = debug_message
    return payload


__all__ = [
    "humanize_probe_result_user",
    "humanize_tool_milestone_user",
    "pack_step_payload",
    "retrieval_done_message",
    "sanitize_user_text",
    "step_label",
    "step_message",
    "step_start_purpose",
    "stream_preview_lines_user",
    "thinking_kind_label",
]
