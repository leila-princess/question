"""
ZNWD1 思考区 SSE：里程碑文案、路由流式 chunk 清洗、工具结果人话摘要。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .business_catalog import get_business
from .enum_inference import node_type_zh
from .user_biz_catalog import USER_BIZ_TITLES, get_scenario_spec

_TOOL_STREAM_TAG_HOLD = 36

_THINK_BLOCK = re.compile(r"<\s*THINK\s*>([\s\S]*?)<\s*/\s*THINK\s*>", re.I)
_CONTENT_BLOCK = re.compile(r"<\s*CONTENT\s*>([\s\S]*?)<\s*/\s*CONTENT\s*>", re.I)

_TOOL_LABELS = {
    "znwd1_history_recent_qa": "读取最近对话",
    "znwd1_history_similar_questions": "比对历史相似问题",
    "znwd1_probe_keyword": "在图谱中验证关键词",
}


def instant_thinking_enabled() -> bool:
    import os

    raw = (os.environ.get("ZNWD1_INSTANT_THINKING") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def route_stream_thinking_enabled() -> bool:
    """是否将路由 LLM 的原始推理流式写入思考区（默认关，避免英文/JSON 泄露）。"""
    import os

    raw = (os.environ.get("ZNWD1_ROUTE_STREAM_THINKING") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def emit_milestone(text: str, *, kind: str = "system") -> Tuple[str, Dict[str, Any]]:
    line = (text or "").strip()
    if not line:
        return ("thinking", {"content": "", "kind": kind})
    if not line.endswith("\n"):
        line += "\n"
    return ("thinking", {"content": line, "kind": kind})


def business_title(business_id: str) -> str:
    cfg = get_business(business_id)
    return str((cfg or {}).get("title") or business_id or "知识库检索")


def humanize_tool_milestone(name: str, args: Optional[Dict[str, Any]] = None) -> str:
    args = args or {}
    if name == "znwd1_probe_keyword":
        kw = str(args.get("keyword") or "").strip()
        return f"正在图谱中验证「{kw}」…" if kw else "正在图谱中验证关键词…"
    if name == "znwd1_history_recent_qa":
        return "正在结合上文理解您的问题…"
    if name == "znwd1_history_similar_questions":
        return "正在参考历史相似问题…"
    label = _TOOL_LABELS.get(name, name)
    return f"正在{label}…"


def summarize_tool_result(name: str, result_raw: str) -> Optional[str]:
    try:
        data = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if name == "znwd1_probe_keyword":
        best = data.get("best") if isinstance(data.get("best"), dict) else None
        if best:
            nt = str(best.get("node_type") or "")
            nm = str(best.get("name") or "")
            nid = str(best.get("id") or "")
            zh = str(data.get("node_type_zh") or nt)
            if nm:
                id_part = f"（Id {nid}）" if nid else ""
                return f"在图谱中定位到：{zh}「{nm}」{id_part}"
        if int(data.get("match_count") or 0) == 0:
            return "图谱中暂未命中同名节点，将按问法继续检索。"
    if name == "znwd1_history_recent_qa":
        rounds = data.get("rounds") if isinstance(data.get("rounds"), list) else []
        if rounds:
            last = rounds[-1]
            u = str((last or {}).get("user") or "")[:120]
            if u:
                return f"已结合上文，当前关注：{u}"
    return None


def _kw_list_to_zh(draft: Dict[str, Any], field: str, label: str) -> Optional[str]:
    val = draft.get(field)
    items: List[str] = []
    if isinstance(val, list):
        items = [str(x).strip() for x in val if str(x).strip()]
    elif isinstance(val, str) and val.strip():
        items = [val.strip()]
    if not items:
        return None
    return f"{label}「{'、'.join(items)}」"


def milestone_route_plan(plan: Dict[str, Any]) -> Optional[str]:
    """将 RoutePlan 转为用户可读的中文思考摘要（业务包装，无专业字段名）。"""
    if not isinstance(plan, dict):
        return None
    if str(plan.get("answer_strategy") or "").strip() == "direct_llm":
        return "这是一般性问答，无需检索知识图谱。"

    draft = plan.get("params_draft") if isinstance(plan.get("params_draft"), dict) else {}
    slot_parts: List[str] = []
    for field, label in (
        ("equipment_keywords", "装备"),
        ("equipment_type_keywords", "装备类型"),
        ("environment_keywords", "试验环境"),
        ("project_keywords", "试验项目"),
        ("indicator_keywords", "考核指标"),
        ("standard_keywords", "标准/文件"),
    ):
        seg = _kw_list_to_zh(draft, field, label)
        if seg:
            slot_parts.append(seg)
    focus = str(draft.get("focus") or "").strip()
    if focus and not any(focus in p for p in slot_parts):
        slot_parts.insert(0, f"关键词「{focus}」")

    user_biz = str(plan.get("user_biz") or "").strip()
    scenario = str(plan.get("scenario") or "").strip()
    biz_zh = USER_BIZ_TITLES.get(user_biz, "")
    spec = get_scenario_spec(user_biz, scenario) if user_biz else None
    scene_zh = (spec.title if spec else "").strip()

    if biz_zh:
        if slot_parts and scene_zh:
            return f"理解为您要查：{'，'.join(slot_parts)}。将按「{biz_zh}—{scene_zh}」在清单库中检索。"
        if slot_parts:
            return f"理解为您要查：{'，'.join(slot_parts)}。将按「{biz_zh}」在清单库中检索。"
        if scene_zh:
            return f"将按「{biz_zh}—{scene_zh}」在清单库中检索。"
        return f"将按「{biz_zh}」在清单库中检索。"

    purpose = str(plan.get("purpose_node") or "").strip()
    if purpose:
        if slot_parts:
            return f"理解为您要查：{'，'.join(slot_parts)}。目标类型：{node_type_zh(purpose)}。"
        return f"将查询{node_type_zh(purpose)}相关信息。"
    if slot_parts:
        return f"理解为您要查：{'，'.join(slot_parts)}。"
    return None


def milestone_reconcile(rec: Dict[str, Any]) -> Optional[str]:
    ub_title = str(rec.get("user_biz_title") or "").strip()
    if rec.get("playbook") and ub_title:
        sc = str(rec.get("scenario") or "").strip()
        spec = get_scenario_spec(str(rec.get("user_biz") or ""), sc)
        sc_zh = (spec.title if spec else "").strip()
        if sc_zh and sc not in ("", "default"):
            return f"已识别业务：{ub_title}（{sc_zh}），正在查清单库…"
        return f"已识别业务：{ub_title}，正在查清单库…"
    bid = str(rec.get("business_id") or "").strip()
    if not bid:
        if str(rec.get("answer_strategy") or "") == "direct_llm":
            return "无需查库，将直接回答。"
        return None
    title = business_title(bid)
    params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
    if bid == "BIZ_ENUM_NODES":
        nt = str(params.get("node_type") or "").strip()
        if nt:
            return f"将列举{node_type_zh(nt)}。"
    if bid == "BIZ_REL_ONE_HOP":
        fr = str(params.get("from_type") or "Equipment").strip()
        to = str(params.get("to_type") or "TestProject").strip()
        focus = str(params.get("focus") or params.get("focus_id") or "").strip()
        if fr and to and focus:
            return f"将查询{node_type_zh(fr)}「{focus}」关联的{node_type_zh(to)}。"
    if bid == "BIZ_REL_REVERSE_ONE_HOP":
        result = str(params.get("result_type") or params.get("from_type") or "Equipment").strip()
        anchor = str(params.get("anchor_type") or params.get("to_type") or "TestProject").strip()
        focus = str(params.get("focus") or params.get("focus_id") or "").strip()
        if result and anchor and focus:
            return f"将查询具备{node_type_zh(anchor)}「{focus}」的{node_type_zh(result)}。"
    if bid == "BIZ_EQUIP_PROJECT_BY_METHOD":
        mk = str(params.get("method_keyword") or params.get("keyword") or "").strip()
        mid = str(params.get("method_id") or "").strip()
        if mk or mid:
            if mid:
                return f"将分两步反查：① 项目→方法（Id {mid}）② 设备→项目。"
            return f"将按试验方法「{mk}」反查关联的设备与测试项目。"
    if bid == "BIZ_REL_TWO_HOP":
        mk = str(params.get("method_keyword") or params.get("keyword") or "").strip()
        if mk:
            return f"将执行两步关系查询（项目→方法，设备→项目），锚定方法「{mk}」。"
    if bid == "BIZ_METHOD_CONTENT":
        mn = str(params.get("method_name") or "").strip()
        pn = str(params.get("project_name") or "").strip()
        if mn:
            return f"将读取试验方法「{mn}」的图库正文（SYNR 优先）。"
        if pn:
            return f"将读取项目「{pn}」关联方法的图库正文（SYNR 优先）。"
        return "将读取试验方法图库正文（SYNR 优先）。"
    best = rec.get("probe_best") if isinstance(rec.get("probe_best"), dict) else None
    if best and best.get("name"):
        return f"将按「{title}」检索，主体：{best.get('name')}"
    return f"将按「{title}」检索清单库。"


def milestone_retrieval_done(neo_hits: int, chroma_hits: int) -> str:
    """思考区仅提示检索完成条数，不展开图谱/文档行级明细。"""
    parts: List[str] = []
    if neo_hits:
        parts.append(f"图谱 {neo_hits} 条")
    if chroma_hits:
        parts.append(f"文档 {chroma_hits} 条")
    detail = "、".join(parts) if parts else "暂无命中"
    return f"检索完成（{detail}）。"


def stream_retrieval_preview(
    *,
    business_id: str,
    neo_rows: List[Any],
    chroma_rows: List[Any],
    max_lines: int = 12,
) -> List[str]:
    """程序化检索结果摘要（写入思考区，非 LLM）。"""
    from .executor import format_neo_preview_lines, format_chroma_preview_lines

    lines: List[str] = [f"【检索结果】{business_title(business_id)}"]
    neo_lines = format_neo_preview_lines(neo_rows, max_lines=max_lines)
    if neo_lines:
        lines.append("图谱：")
        lines.extend(neo_lines)
    chroma_lines = format_chroma_preview_lines(chroma_rows, max_lines=min(6, max_lines))
    if chroma_lines:
        lines.append("文档：")
        lines.extend(chroma_lines)
    if len(lines) <= 1:
        lines.append("（暂无命中记录）")
    return lines


def _hold_back_incomplete_tag_suffix(t: str) -> Tuple[str, str]:
    if not t:
        return "", ""
    upper = t.upper()
    for marker in ("<THINK", "<CONTENT", "{"):
        i = upper.rfind(marker)
        if i < 0:
            continue
        tail = t[i:]
        if marker == "{" and "}" in tail:
            continue
        if ">" not in tail and marker != "{":
            return t[:i], tail
    return t, ""


def _strip_fenced_json(t: str) -> str:
    if not t:
        return t
    return re.sub(r"```[\w]*\s*[\s\S]*?```", "", t, flags=re.I)


_ROUTE_PLAN_JSON_KEYS = frozenset({
    "answer_strategy",
    "retrieval_question",
    "intended_business_id",
    "probe_snapshot",
    "params_draft",
    "guessed_node_types",
    "user_goal",
    "answer_mode",
    "use_history",
    "keyword",
})

_ROUTING_NOISE_LINE = re.compile(
    r"(probe_snapshot|params_draft|answer_strategy|intended_business_id|guessed_node_types|"
    r"user_goal|answer_mode|BIZ_[A-Z0-9_]+|database_direct|db_direct|db_synthesize|"
    r"relation_type|source_id|HAS_TEST_PROJECT|RoutePlan|retrieval_question\s*补全|"
    r"单跳关系|业务类型为|应使用\s*BIZ|对应多个.*节点|为空.*需先探测|Equipment\s*→|"
    r"match_confidence|use_history|by_equip|UBIZ_|scenario\s*[`'\"]|Calling\s|Probe\s|"
    r"detected\s*multiple|Equipment nodes|Environment node|No history|multi-equipment|"
    r"retrieval_question\s*=|Callingprobe|and environment keyword)",
    re.I,
)

_ENGLISH_DOMINANT_LINE = re.compile(r"^[A-Za-z0-9\s\[\]{}\":,\.`\-_/=]+$")


def _strip_english_route_lines(text: str) -> str:
    """去掉以英文为主的路由推理行。"""
    if not text:
        return text
    kept: List[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if _ROUTING_NOISE_LINE.search(raw):
            continue
        # 整行几乎全是 ASCII（路由模型英文推理）
        ascii_letters = sum(1 for c in raw if c.isascii() and c.isalpha())
        cjk = sum(1 for c in raw if "\u4e00" <= c <= "\u9fff")
        if ascii_letters >= 12 and cjk <= 2:
            continue
        if _ENGLISH_DOMINANT_LINE.match(raw) and len(raw) > 20:
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _is_route_plan_json_obj(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    if len(keys & _ROUTE_PLAN_JSON_KEYS) >= 2:
        return True
    if "answer_strategy" in keys or "intended_business_id" in keys:
        return True
    return False


def _strip_balanced_json_blobs(text: str, predicate) -> str:
    """移除满足 predicate 的 {...} 块（含流式拼接的不完整尾部）。"""
    if not text:
        return text
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            out.append(text[i])
            i += 1
            continue
        depth = 0
        j = i
        while j < n:
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blob = text[i : j + 1]
                    drop = False
                    try:
                        drop = predicate(json.loads(blob))
                    except (json.JSONDecodeError, TypeError, ValueError):
                        drop = _ROUTING_NOISE_LINE.search(blob) is not None
                    if not drop:
                        out.append(blob)
                    i = j + 1
                    break
            j += 1
        else:
            # 未闭合 JSON：整段丢弃（RoutePlan 流式污染）
            i = n
    return "".join(out)


def _strip_routing_noise_lines(text: str) -> str:
    if not text:
        return text
    kept: List[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if _ROUTING_NOISE_LINE.search(raw):
            continue
        if raw.startswith("{") or raw.startswith('"answer_strategy"'):
            continue
        if re.search(r'"\s*:\s*"(db_direct|db_synthesize|direct_llm)"', raw):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def sanitize_thinking_for_sse(raw: str) -> str:
    """思考区最终展示/下发前的总清洗。"""
    if not raw:
        return ""
    s = str(raw)
    s = _CONTENT_BLOCK.sub("", s)
    s = _THINK_BLOCK.sub(lambda m: (m.group(1) or "") + "\n", s)
    s = re.sub(r"<\s*/?\s*(?:THINK|CONTENT)\s*>", "", s, flags=re.I)
    s = _strip_fenced_json(s)
    s = _strip_balanced_json_blobs(s, _is_route_plan_json_obj)
    s = _strip_tool_json_paragraphs(s)
    s = _strip_routing_noise_lines(s)
    s = _strip_english_route_lines(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _strip_tool_json_paragraphs(t: str) -> str:
    if not t:
        return t
    parts = re.split(r"\n{2,}", t)
    kept: List[str] = []
    for p in parts:
        s = p.strip()
        if not s.startswith("{"):
            kept.append(p)
            continue
        try:
            o = json.loads(s)
            if isinstance(o, dict) and (
                isinstance(o.get("steps"), list)
                or o.get("op_code")
                or _is_route_plan_json_obj(o)
            ):
                continue
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        kept.append(p)
    return "\n\n".join(kept).strip()


def sanitize_route_stream_chunk(text: str, *, emitted_len: int = 0) -> Tuple[str, int]:
    if not text:
        return "", emitted_len
    s = sanitize_thinking_for_sse(text)
    if not s or len(s) <= emitted_len:
        return "", emitted_len
    delta = s[emitted_len:]
    if not delta.strip():
        return "", emitted_len
    return delta, len(s)


def sanitize_route_stream_buffer(raw: str, emitted_len: int) -> Tuple[str, int]:
    if not raw:
        return "", emitted_len
    hold = raw
    # 一旦出现 RoutePlan JSON 起始，后续 chunk 不再下发
    if re.search(r'\{\s*"(?:answer_strategy|intended_business_id|probe_snapshot)"', hold):
        brace = hold.find("{")
        if brace >= 0:
            hold = hold[:brace]
    if len(hold) > _TOOL_STREAM_TAG_HOLD:
        pref = hold[: len(hold) - _TOOL_STREAM_TAG_HOLD]
        safe, _ = _hold_back_incomplete_tag_suffix(pref)
        return sanitize_route_stream_chunk(safe, emitted_len=emitted_len)
    safe, _ = _hold_back_incomplete_tag_suffix(hold)
    return sanitize_route_stream_chunk(safe, emitted_len=emitted_len)
