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
        "战车", "自行火炮", "装备", "设备", "武器", "平台", "系统",
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
) -> Tuple[str, Dict[str, Any]]:
    """
    基于槽位数据结构的承上省略自动补全。

    不依赖固定问法模式，而是：
    1. 将当前问题解析为槽位结构 {equipment, environment, project, indicator, standard}
    2. 将历史问题解析为槽位结构
    3. 检测当前问题缺少哪些槽位
    4. 用历史槽位补全缺失
    5. 重组为自然语言问题

    示例：
    - 上问：「无人机有哪些试验项目」→ slots={equipment:["无人机"], project:["试验项目"]}
    - 当前：「坦克呢」→ slots={equipment:["坦克"]}
    - 缺失：project
    - 补全：「坦克有哪些试验项目」

    - 上问：「无人机在复杂电磁环境下有什么项目」→ slots={equipment:["无人机"], environment:["复杂电磁环境"], project:["项目"]}
    - 当前：「复杂地理环境呢」→ slots={environment:["复杂地理环境"]}
    - 缺失：equipment
    - 补全：「无人机在复杂地理环境下有什么项目」
    """
    q = (question or "").strip()
    if len(q) > 60:
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
