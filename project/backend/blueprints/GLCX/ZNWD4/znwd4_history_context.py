"""
对话历史格式化：供 direct_llm / 合成阶段注入上下文。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .memory_store import get_memory_store

_META_PREV_QUESTION = re.compile(
    r"(上(一|个|轮)|刚才|之前|前面).{0,8}(问|问题|说的|提到)"
    r"|(问|说)的?.{0,6}是什么"
    r"|重复.{0,6}一遍",
    re.I,
)

# 标准槽位定义：与路由 LLM 的 params_draft 一致
SLOT_KEYS = ["equipment", "environment", "project", "indicator", "standard", "equipment_type"]

# 各槽位的关键词词典（可扩展）
_SLOT_KEYWORDS: Dict[str, List[str]] = {
    "equipment": [
        "无人机", "坦克", "装甲", "雷达", "导弹", "舰船", "飞机", "直升机",
        "战车", "自行火炮", "武器", "平台", "系统",
        "汽车", "火炮", "火箭", "卫星",
    ],
    "environment": [
        "复杂电磁", "复杂地理", "盐雾", "高温", "低温", "湿热", "干热",
        "寒冷", "振动", "冲击", "淋雨", "沙尘", "高原", "高海拔", "深海",
        "极寒", "酷热", "环境", "气候", "条件",
    ],
    "project": [
        "试验项目", "测试项目", "项目", "试验", "测试", "鉴定", "定型",
        "考核", "验证",
    ],
    "indicator": [
        "指标", "考核指标", "性能指标", "试验指标", "测试指标",
    ],
    "standard": [
        "标准", "GB", "GJB", "规范", "条文", "章节",
    ],
    "equipment_type": [
        "装备类型", "设备类型", "地面装备", "空中装备", "海上装备",
        "陆地", "航空", "舰载", "车载",
    ],
}


# 「X有哪些Y」中 Y 为查询目标（装备/项目/指标等），不是槽位实体名
_EXPLICIT_QUERY_TARGET = re.compile(
    r"(有哪些|有什么|包括哪些|都有哪些)"
    r"\s*(?:具体)?(?:的)?\s*"
    r"(装备|设备|试验项目|测试项目|考核指标|性能指标|试验指标|测试指标|指标|"
    r"试验方法|测试方法|方法|环境|类型|类别|二级(?:装备|设备)?(?:类型|类别)?)",
    re.I,
)


def _question_has_explicit_query_target(text: str) -> bool:
    """问句已明确「查什么」（如 有哪些装备 / 有哪些试验项目），不应被历史槽位重组。"""
    t = (text or "").strip()
    if not t:
        return False
    if _EXPLICIT_QUERY_TARGET.search(t):
        return True
    if re.search(r"具体\s*(?:设备|装备)", t) and re.search(r"(有哪些|有什么|包括哪些)", t):
        return True
    return False


def _looks_like_contextual_follow_up(text: str) -> bool:
    """承上省略的短追问（如「坦克呢」），才应用历史槽位补全。"""
    t = (text or "").strip()
    if not t:
        return False
    if _question_has_explicit_query_target(t):
        return False
    if re.match(r"^(?:考核|性能|试验)?指标呢[？?！!。.\s]*$", t, re.I):
        return True
    if re.match(r"^(?:试验|测试|实验)?项目呢[？?！!。.\s]*$", t, re.I):
        return True
    if re.match(r"^(?:试验|测试)?方法呢[？?！!。.\s]*$", t, re.I):
        return True
    if re.search(r"(呢|吗|啊|吧)$", t):
        return True
    if re.search(r"^(?:那|换|改|也|同样|还是|接着)", t):
        return True
    if len(t) <= 8 and not re.search(r"(有哪些|有什么|是什么|包括哪些|都有哪些)", t):
        return True
    return False


def _extract_list_tail(original: str) -> str:
    """从「X有哪些Y」提取 Y（查询目标），供槽位重组时保留。"""
    t = (original or "").strip()
    m = re.search(
        r"(有哪些|有什么|包括哪些|都有哪些)\s*(?:具体)?(?:的)?\s*(.+)$",
        t,
    )
    if not m:
        return ""
    tail = (m.group(2) or "").strip()
    if tail in ("呢", "啊", "吗", "吧"):
        return ""
    return tail


def looks_like_meta_history_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if len(t) > 80:
        return False
    return bool(_META_PREV_QUESTION.search(t))


def _pair_messages_to_rounds(msgs: List[dict]) -> List[Dict[str, str]]:
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


def _detect_slots(text: str) -> Dict[str, List[str]]:
    """
    从问题文本中检测填充了哪些槽位。
    返回 {slot_key: [匹配到的关键词列表]}
    """
    t = (text or "").strip().lower()
    slots: Dict[str, List[str]] = {k: [] for k in SLOT_KEYS}

    for slot_key, keywords in _SLOT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in t:
                if kw == "考核" and re.search(r"考核\s*指标", t, re.I):
                    continue
                slots[slot_key].append(kw)

    # 特殊处理：提取装备名（"X有哪些..." → X 是装备）
    equip_match = re.search(r"^(.+?)(在|的|有|进行|做|开展|呢|啊|吗)", t)
    if equip_match:
        candidate = equip_match.group(1).strip()
        candidate = re.sub(r"^(请问|问下|帮我查|查询|那|换)", "", candidate).strip()
        # 如果候选词不是槽位关键词，且长度合理，认为是装备名
        if candidate and len(candidate) <= 20:
            is_keyword = any(candidate in kw for kws in _SLOT_KEYWORDS.values() for kw in kws)
            if not is_keyword:
                if "equipment" not in slots or not slots["equipment"]:
                    slots["equipment"] = [candidate]

    return slots


def _extract_slot_value(slots: Dict[str, List[str]], slot_key: str) -> str:
    """从槽位字典中提取指定槽位的值（取第一个）。"""
    vals = slots.get(slot_key, [])
    return vals[0] if vals else ""


def _has_slot(slots: Dict[str, List[str]], slot_key: str) -> bool:
    return bool(slots.get(slot_key))


def _slots_are_complete(slots: Dict[str, List[str]], required: Set[str]) -> bool:
    """检查是否包含所有必需槽位。"""
    return all(_has_slot(slots, k) for k in required)


def _merge_slots(current: Dict[str, List[str]], prev: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """用历史槽位补全当前缺失的槽位。"""
    merged = {k: list(v) for k, v in current.items()}
    for k in SLOT_KEYS:
        if not merged.get(k) and prev.get(k):
            merged[k] = list(prev[k])
    return merged


def _slots_to_question(slots: Dict[str, List[str]], original: str) -> str:
    """
    将槽位合并为自然语言问题。
    策略：按 [装备] + [环境] + [项目/指标] 的顺序重组。
    """
    equip = _extract_slot_value(slots, "equipment")
    env = _extract_slot_value(slots, "environment")
    proj = _extract_slot_value(slots, "project")
    ind = _extract_slot_value(slots, "indicator")

    # 当前追问仅换查询目标时，不沿用上一轮 project/indicator 槽（如「考核指标呢」）
    current_only = _detect_slots(original)
    if _has_slot(current_only, "indicator") and not _has_slot(current_only, "project"):
        proj = ""
    if _has_slot(current_only, "project") and not _has_slot(current_only, "indicator"):
        ind = ""

    # 如果原始问题只是换了某个维度（如只换了环境），保留原始问题的意图
    # 检测原始问题的"意图词"（有哪些/是什么/呢）
    intent = _extract_intent(original)

    parts: List[str] = []
    if equip:
        parts.append(equip)
    if env:
        # 清理环境词中的"环境"后缀（重组时会加）
        env_clean = env.replace("环境", "").replace("条件", "").strip()
        if env_clean:
            parts.append(f"在{env_clean}环境下")
        elif env:
            parts.append(f"在{env}下")
    if proj:
        parts.append(f"有哪些{proj}")
    elif ind:
        parts.append(f"有哪些{ind}")
    else:
        list_tail = _extract_list_tail(original)
        if list_tail:
            parts.append(f"有哪些{list_tail}")
        elif intent:
            if parts:
                parts.append(intent)
            else:
                return original

    if parts:
        return "".join(parts)
    return original


def _extract_intent(text: str) -> str:
    """提取问题的意图词（有哪些/是什么/呢等）。"""
    t = (text or "").strip()
    m = re.search(r"(有哪些|是什么|有什么|怎么做|呢|啊|吗|吧)", t)
    if m:
        return m.group(0)
    return "有哪些项目"


def _pair_messages_to_rounds(msgs: List[dict]) -> List[Dict[str, str]]:
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


def format_history_rounds(rounds: List[Dict[str, str]], *, max_rounds: int = 5) -> str:
    if not rounds:
        return ""
    lines: List[str] = []
    tail = rounds[-max_rounds:] if len(rounds) > max_rounds else rounds
    for i, r in enumerate(tail, 1):
        u = str((r or {}).get("user") or "").strip()
        a = str((r or {}).get("assistant") or "").strip()
        if u:
            lines.append(f"第{i}轮 用户：{u}")
        if a:
            lines.append(f"第{i}轮 助手：{a[:600]}")
    return "\n".join(lines)


def snapshot_from_tool_payload(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        return data
    return {}


def load_history_from_store(
    user_id: Optional[str],
    session_id: Optional[str],
    *,
    n: int = 5,
) -> Dict[str, Any]:
    store = get_memory_store()
    if not store or not user_id or not session_id:
        return {"rounds": []}
    msgs = store.get_messages(user_id, session_id)
    rounds = _pair_messages_to_rounds(msgs)
    if n > 0 and len(rounds) > n:
        rounds = rounds[-n:]
    return {"rounds": rounds, "total_rounds_in_store": len(_pair_messages_to_rounds(msgs))}


def build_history_context_block(
    snapshot: Optional[Dict[str, Any]],
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    question: str = "",
    min_rounds: int = 0,
) -> str:
    """
    返回可拼进 Human 消息的历史块；无历史则返回空串。
    对「上一个问题」类 meta 问句，若 snapshot 为空则从 Redis 再拉一次。
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    rounds = snap.get("rounds") if isinstance(snap.get("rounds"), list) else []
    if not rounds and (
        looks_like_meta_history_question(question)
        or str(snap.get("force_load") or "")
    ):
        snap = load_history_from_store(user_id, session_id, n=5)
        rounds = snap.get("rounds") or []

    text = format_history_rounds(rounds)
    if not text and min_rounds > 0:
        return ""
    if not text:
        return ""
    return f"【对话历史】\n{text}"


def build_direct_llm_user_message(
    question: str,
    *,
    history_snapshot: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    q = (question or "").strip()
    hist = build_history_context_block(
        history_snapshot,
        user_id=user_id,
        session_id=session_id,
        question=q,
    )
    if hist:
        return f"{hist}\n\n【当前问题】\n{q}"
    return q


def enrich_short_follow_up(
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    client: Any = None,
    model: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    承上省略补全：默认用 LLM + 滚动摘要 + 最近 3 轮对话（见 znwd4_llm_context）。
    无 client/model 或关闭开关时原样返回。
    """
    from .znwd4_llm_context import enrich_question_with_history

    return enrich_question_with_history(
        question,
        user_id=user_id,
        session_id=session_id,
        client=client,
        model=model,
    )


def _enrich_short_follow_up_rules(
    question: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """旧版规则补全（仅测试/回退用）。"""
    q = (question or "").strip()
    if len(q) > 60:
        return q, {}

    # 完整问句（已含查询目标）不做承上重组，避免吞掉「装备/项目」等关键词
    if _question_has_explicit_query_target(q):
        return q, {}

    store = get_memory_store()
    if not store or not user_id or not session_id:
        return q, {}

    msgs = store.get_messages(user_id, session_id)
    rounds = _pair_messages_to_rounds(msgs)
    if not rounds:
        return q, {}

    last_user = (rounds[-1].get("user") or "").strip()
    if not last_user:
        return q, {}

    if not _looks_like_contextual_follow_up(q):
        return q, {}

    # 解析当前问题和历史问题的槽位
    current_slots = _detect_slots(q)
    prev_slots = _detect_slots(last_user)

    # 检测当前问题缺少历史的哪些槽位
    missing_keys = [k for k in SLOT_KEYS if _has_slot(prev_slots, k) and not _has_slot(current_slots, k)]

    # 如果没有缺失槽位，不需要补全
    if not missing_keys:
        # 特殊情况：当前问题只是换了装备名（如"坦克呢"），但历史有其他槽位
        # 检测当前问题是否极短（可能是省略句）
        if len(q) <= 10 and _has_slot(prev_slots, "project"):
            # 当前问题很短且历史有 project 槽位，可能是"XX呢"省略句
            # 补全历史的 project 槽位
            merged_slots = _merge_slots(current_slots, prev_slots)
            enriched = _slots_to_question(merged_slots, q)
            hist_snap = {"rounds": rounds[-1:], "total_rounds_in_store": len(rounds)}
            return enriched, hist_snap
        return q, {}

    # 有缺失槽位，用历史补全
    merged_slots = _merge_slots(current_slots, prev_slots)

    # 重组问题
    enriched = _slots_to_question(merged_slots, q)

    hist_snap = {"rounds": rounds[-1:], "total_rounds_in_store": len(rounds)}
    return enriched, hist_snap
