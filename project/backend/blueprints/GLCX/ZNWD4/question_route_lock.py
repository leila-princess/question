"""
ZNWD4 问句意图锁：程序层强制 purpose / route_id / probe 模式，复用 rel_inference 规则。

弱 LLM（如 qwen2.5-14b-awq）场景下，命中 lock 时可直接跳过 Step1/Step2 LLM。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .rel_inference import (
    extract_equipment_focus_for_belongs_to_type,
    extract_type_focus_for_equipment,
    looks_like_equipment_belongs_to_type,
    looks_like_equipment_by_indicator,
    looks_like_equipment_by_project,
    looks_like_projects_by_method,
    looks_like_type_equipment_relation,
)
from .routing_catalog import PURPOSE_ENUM, PURPOSE_META, RouteEntry, _purpose_zh, get_route, resolve_route

logger = logging.getLogger(__name__)

_LEVEL2_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}).{0,10}二级.{0,8}(?:装备|设备|类别|类型)"
    r"|([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}).{0,8}(?:有哪些|有什么).{0,8}二级",
    re.I,
)


_PROJECT_TOKEN_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]+?(?<![试验测试]))(?:试验|测试)?项目",
    re.I,
)

_INDICATOR_TOKEN_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]+?)(?:考核|性能|试验|测试)?指标",
    re.I,
)

_INDICATOR_QUANTIFIER_STUBS = frozenset(
    {"两个", "几个", "若干", "多个", "全部", "所有", "哪些", "什么", "哪一些", "考核", "性能"}
)


@dataclass
class RouteLock:
    purpose: str
    route_id: str
    probe_labels: List[str]
    slot_key: str
    focus: str
    reason: str
    keywords: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


def skip_llm_routing_when_locked() -> bool:
    raw = (os.environ.get("ZNWD4_SKIP_LLM_WHEN_LOCKED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def lock_is_slotless(lock: RouteLock) -> bool:
    """无实体槽位的锁（meta / 全库 enum）：可 0 LLM 直出。"""
    if not lock.slot_key:
        return True
    return lock.reason in (
        "meta_info",
        "enum_equipment",
        "enum_test_project",
        "enum_environment",
        "enum_indicator",
        "enum_equipment_type",
    )


def skip_llm_for_lock(lock: RouteLock) -> bool:
    """仅对无槽 enum/meta 等锁跳过 LLM；实体类锁需 probe/PCSR 或 Hybrid。"""
    if not skip_llm_routing_when_locked():
        return False
    return lock_is_slotless(lock)


def hybrid_routing_complete_lock(lock: Optional[RouteLock]) -> bool:
    """锁已含 route+purpose，且（无槽或已有 keywords/focus）时可跳过 Step1/2。"""
    if lock is None:
        return False
    if not str(lock.route_id or "").strip() or not str(lock.purpose or "").strip():
        return False
    if skip_llm_for_lock(lock):
        return True
    if lock_is_slotless(lock):
        return True
    if lock.keywords:
        return True
    if str(lock.focus or "").strip():
        return True
    return False


def _entity_route_lock(
    *,
    purpose: str,
    route_id: str,
    probe_labels: List[str],
    slot_key: str,
    reason: str,
    extra: Optional[Dict[str, Any]] = None,
) -> RouteLock:
    """实体类 route-only 锁：槽位由 probe PCSR 填充，不写 regex focus。"""
    return RouteLock(
        purpose=purpose,
        route_id=route_id,
        probe_labels=list(probe_labels),
        slot_key=slot_key,
        focus="",
        keywords=[],
        reason=reason,
        extra=dict(extra or {}),
    )


def _extract_level1_focus(question: str) -> str:
    q = (question or "").strip()
    for pat in (
        r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)(?:都)?(?:有)?(?:什么|哪些|包括哪些)",
        r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,24}?)(?:的|下)?(?:有)?二级",
    ):
        m = re.match(pat, q)
        if m:
            focus = re.sub(r"\s+", "", (m.group(1) or "").strip())
            focus = re.sub(r"(的|下|中)$", "", focus).strip()
            if len(focus) >= 2 and "二级" not in focus:
                return focus
    m = _LEVEL2_PATTERN.search(q)
    if m:
        focus = (m.group(1) or m.group(2) or "").strip()
        focus = re.sub(r"(的|下|中|都|有|哪些|什么|包括|二级|类|型)$", "", focus).strip()
        if len(focus) >= 2 and "二级" not in focus:
            return focus
    alt = extract_type_focus_for_equipment(q)
    return alt if alt else ""


def _looks_like_level2_equipment_types(question: str) -> bool:
    q = (question or "").strip()
    if not q or looks_like_type_equipment_relation(q):
        return False
    if not re.search(r"二级", q):
        return False
    if re.search(r"具体\s*(?:设备|装备)", q):
        return False
    if re.search(r"(属于|归为|算作|算是)", q):
        return False
    return bool(
        re.search(r"二级.{0,8}(?:装备|设备)?(?:类别|类型)", q)
        or re.search(r"(?:有什么|有哪些|包括哪些).{0,12}二级", q)
        or re.search(r"二级.{0,8}(装备|设备|类别|类型)", q)
    )


def _looks_like_belongs_to_question(question: str) -> bool:
    """严格 belongs_to：排除「一级下有哪些二级类型」类列举问句。"""
    q = (question or "").strip()
    if not q or _looks_like_level2_equipment_types(q):
        return False
    if re.search(r"二级", q):
        return False
    if re.search(r"(有什么|有哪些|包括哪些).{0,16}(?:装备|设备)?\s*类型", q):
        return False
    if re.search(r"(?:类型|类别).{0,8}(?:下|中|里).{0,8}(?:有|包括|哪些|什么)", q):
        return False
    return looks_like_equipment_belongs_to_type(q)


def _looks_like_equipment_test_projects(question: str) -> bool:
    q = (question or "").strip()
    if _looks_like_project_methods(q):
        return False
    if looks_like_type_equipment_relation(q) or _looks_like_belongs_to_question(q):
        return False
    if _looks_like_level2_equipment_types(q):
        return False
    if not re.search(
        r"(有哪些|有什么|包括哪些|都有哪些|涉及|关联|需要做|要做|做).{0,16}(试验项目|测试项目|试验|测试)",
        q,
    ):
        return False
    if re.search(r"(装备类型|设备类型|二级)", q):
        return False
    if re.search(r"环境.{0,16}(下|中|里|条件|对应).{0,16}(试验|测试)?项目", q):
        return False
    from .slot_focus import extract_equipment_focus

    return bool(extract_equipment_focus(q))


def _looks_like_project_methods(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if (
        _looks_like_composite_project_method(q)
        or _looks_like_composite_project_env_method(q)
        or _looks_like_multi_step_project_method(q)
        or _looks_like_composite_indicator_method(q)
        or _looks_like_multi_step_indicator_method(q)
    ):
        return False
    proj_kws = extract_project_keywords_for_methods(q)
    if proj_kws and re.search(r"方法", q) and re.search(
        r"(有哪些|有什么|包括哪些|都有哪些|分别|对应|采用|是什么|查询)",
        q,
    ):
        return True
    from .method_inference import looks_like_equip_project_by_method, looks_like_method_content_question

    if looks_like_equip_project_by_method(q) or looks_like_method_content_question(q):
        return False
    if looks_like_type_equipment_relation(q) or _looks_like_belongs_to_question(q):
        return False
    if _looks_like_level2_equipment_types(q):
        return False
    if not re.search(r"方法", q):
        return False
    if not re.search(
        r"(有哪些|有什么|包括哪些|都有哪些|分别|对应|采用|是什么|查询)",
        q,
    ):
        return False
    if re.search(r"(试验|测试)?项目", q):
        return True
    if re.search(r"对应的测试方法|对应的试验方法|测试方法是什么|试验方法是什么", q):
        return True
    return bool(
        re.search(
            r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?(?:试验|测试)(?!项目).{0,24}"
            r"(?:有哪些|有什么|包括哪些|都有哪些|分别).{0,12}(?:试验|测试)?\s*方法",
            q,
        )
    )


_PROJECT_QUANTIFIER_STUBS = frozenset(
    {"两个", "几个", "若干", "多个", "全部", "所有", "哪些", "什么", "哪一些"}
)


def _normalize_project_token(name: str) -> str:
    name = re.sub(r"\s+", "", (name or "").strip())
    name = re.sub(r"^[和与及、]+", "", name)
    return re.sub(r"(的|与|和|及|分别)$", "", name).strip()


def extract_project_keywords(question: str) -> List[str]:
    """从问句抽取试验项目名称（支持多项目；过滤「两个项目」等量词伪名）。"""
    q = re.sub(r"\s+", "", (question or "").strip())
    if not q:
        return []
    names: List[str] = []
    seen: set = set()

    def add(full_token: str, name_part: str = "") -> None:
        token = _normalize_project_token(full_token)
        stub = _normalize_project_token(name_part or token.replace("项目", "").replace("试验", "").replace("测试", ""))
        if len(token) < 2 or token in seen:
            return
        if stub in _PROJECT_QUANTIFIER_STUBS or token in _PROJECT_QUANTIFIER_STUBS:
            return
        seen.add(token)
        names.append(token)

    for m in _PROJECT_TOKEN_RE.finditer(q):
        add(m.group(0), m.group(1) or "")

    if names:
        return names

    m = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)(试验|测试)(?!项目).{0,24}"
        r"(?:有哪些|有什么|包括哪些|都有哪些|分别).{0,12}(?:试验|测试)?\s*方法",
        q,
    )
    if m:
        add(m.group(1) + m.group(2))
    return names


def extract_project_keywords_for_methods(question: str) -> List[str]:
    return extract_project_keywords(question)


def _normalize_indicator_token(name: str) -> str:
    name = re.sub(r"\s+", "", (name or "").strip())
    name = re.sub(r"^[和与及、]+", "", name)
    name = re.sub(r"(的|与|和|及|分别)$", "", name).strip()
    if name and not name.endswith("指标"):
        name = name + "指标"
    return name


def extract_indicator_keywords(question: str) -> List[str]:
    """从问句抽取考核指标名称（支持多指标）。"""
    q = re.sub(r"\s+", "", (question or "").strip())
    if not q:
        return []
    names: List[str] = []
    seen: set = set()

    def add(full_token: str, name_part: str = "") -> None:
        token = _normalize_indicator_token(full_token)
        stub = re.sub(r"指标$", "", name_part or token).strip()
        if len(token) < 2 or token in seen:
            return
        if stub in _INDICATOR_QUANTIFIER_STUBS or token in _INDICATOR_QUANTIFIER_STUBS:
            return
        seen.add(token)
        names.append(token)

    for m in _INDICATOR_TOKEN_RE.finditer(q):
        add(m.group(0), m.group(1) or "")

    if names:
        return names

    m = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]{2,40}?)指标.{0,24}"
        r"(?:有哪些|有什么|包括哪些|都有哪些|分别).{0,12}(?:设备|装备)",
        q,
    )
    if m:
        add(m.group(1) + "指标")
    return names


def extract_method_keywords(question: str) -> List[str]:
    """从问句抽取试验方法名（用于方法→项目反查等）。"""
    from .method_inference import extract_method_standard_keyword, normalize_method_standard_keyword

    q = re.sub(r"\s+", "", (question or "").strip())
    if not q:
        return []
    names: List[str] = []
    seen: set = set()

    def add(raw: str) -> None:
        token = normalize_method_standard_keyword(raw)
        if len(token) < 2 or token in seen:
            return
        if token in ("试验方法", "测试方法", "方法"):
            return
        seen.add(token)
        names.append(token)

    std = extract_method_standard_keyword(q)
    if std:
        add(std)
        return names

    for m in re.finditer(
        r"([\u4e00-\u9fffA-Za-z0-9·\-/\.]{2,48}?)(?:的)?(?:试验|测试)方法",
        q,
    ):
        add(m.group(1))
    if names:
        return names
    m = re.search(
        r"(?:有|具备|关联|使用|采用).{0,12}([\u4e00-\u9fffA-Za-z0-9·\-/\.]{2,48}?)(?:试验|测试)?方法",
        q,
    )
    if m:
        add(m.group(1))
    return names


def _looks_like_equipment_indicators(question: str) -> bool:
    q = (question or "").strip()
    if looks_like_type_equipment_relation(q) or _looks_like_level2_equipment_types(q):
        return False
    if not re.search(r"(考核|性能|试验)?指标", q):
        return False
    if re.search(
        r"(有哪些|有什么|包括哪些|都有哪些).{0,12}(?:考核|性能|试验)?指标",
        q,
    ) and re.search(r"(装备|设备|坦克|无人机|汽车|雷达|通信|多旋翼|轻小型)", q):
        return True
    if re.search(r"^.{2,}的(考核|性能|试验)?指标有哪些", q):
        return True
    if re.search(r"^.{2,}(考核|性能|试验)指标有哪些", q):
        return True
    return bool(re.search(r"(要|需要|应).{0,8}(?:考核|满足).{0,8}(?:哪些|什么|哪).{0,8}指标", q))


def _looks_like_enum_equipment(question: str) -> bool:
    q = (question or "").strip()
    # 「X有哪些装备」= 某类型下具体装备，不是全库枚举
    if looks_like_type_equipment_relation(q):
        return False
    if not re.search(r"(有哪些|都有什么|有什么|列出|列举|清单|登记|全部)", q):
        return False
    if re.search(r"(试验项目|测试项目|指标|方法|环境|类型|类别|二级|具体)", q):
        return False
    return bool(re.search(r"(装备|设备)", q))


def _looks_like_meta_info(question: str) -> bool:
    from .meta_info_handler import looks_like_meta_info_question

    return looks_like_meta_info_question(question)


def _looks_like_project_environment(question: str) -> bool:
    """某项目在什么环境/条件下试验（非环境节点属性问答）。"""
    q = (question or "").strip()
    if not re.search(r"(什么|哪种|哪个|在哪|在哪些).{0,10}(试验|测试)?环境", q):
        return False
    if re.search(r"(分类|分级|组合|约束|要求|属性)", q):
        return False
    if re.search(r"(有哪些|有什么).{0,12}(试验|测试)?项目", q):
        return False
    return bool(re.search(r"(试验|测试)?项目", q) or extract_project_keywords_for_methods(q))


def _looks_like_environment_properties(question: str) -> bool:
    q = (question or "").strip()
    if _looks_like_project_environment(q):
        return False
    if re.search(r"(有哪些|有什么).{0,12}(试验|测试)?项目", q):
        return False
    if re.search(
        r"(介绍|说明|描述|讲讲|说说).{0,16}.{0,32}环境",
        q,
    ):
        return True
    if re.search(
        r"(介绍|说明|描述|讲讲|说说).{0,8}([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)(?:试验|测试)?环境",
        q,
    ):
        return True
    if re.search(r"(分类|分级|组合|约束|要求|属性|Type|Class|Level)", q, re.I) and re.search(
        r"环境", q
    ):
        return True
    return False


def _extract_equip_env_test_project_parts(question: str) -> Tuple[str, str]:
    """从「某装备在某环境下有哪些试验项目」类问句提取 (装备, 环境)。"""
    q = re.sub(r"\s+", "", (question or "").strip())
    patterns = (
        r"^(?:查询)?(.+?)在(.+?)环境下有哪些试验项目",
        r"^(?:查询)?(.+?)在(.+?)条件下需要做哪些试验",
        r"^查询(.+?)在(.+?)环境下的试验项目清单",
    )
    for pat in patterns:
        m = re.search(pat, q)
        if not m:
            continue
        equip = (m.group(1) or "").strip()
        env = (m.group(2) or "").strip()
        if len(equip) >= 2 and len(env) >= 1:
            return equip, env
    return "", ""


def _looks_like_equip_and_env_test_projects(question: str) -> bool:
    q = (question or "").strip()
    if _looks_like_composite_project_env_method(q):
        return False
    equip, env = _extract_equip_env_test_project_parts(q)
    return bool(equip and env)


def _looks_like_environment_test_projects(question: str) -> bool:
    q = (question or "").strip()
    if _looks_like_equip_and_env_test_projects(q):
        return False
    if _looks_like_environment_properties(q):
        return False
    if not re.search(r"环境", q):
        return False
    if not re.search(r"(有哪些|有什么|包括哪些|都有哪些|对应|要做|应做|相关)", q):
        return False
    return bool(re.search(r"(试验|测试)?项目", q))


def _looks_like_enum_list(question: str, *, node_hint: str) -> bool:
    q = (question or "").strip()
    if not re.search(r"(有哪些|都有什么|有什么|列出|列举|清单|登记|全部|类型有哪些|类别)", q):
        return False
    if node_hint == "TestProject":
        if looks_like_equipment_by_project(q):
            return False
        if _looks_like_equip_and_env_test_projects(q):
            return False
        q_compact = re.sub(r"\s+", "", q)
        if re.search(r"查询.+在.+环境.+(试验项目|测试项目)清单", q_compact):
            return False
        if re.search(
            r"^[\u4e00-\u9fffA-Za-z0-9·\-]{2,32}.{0,12}(有哪些|有什么|包括哪些).{0,8}(试验项目|测试项目)",
            q_compact,
        ):
            return False
    if node_hint == "Environment":
        if re.search(r"环境.{0,12}(下|中|里|条件|对应).{0,12}(有哪些|有什么|包括哪些|要做|应做)", q):
            return False
    if node_hint == "Indicator":
        # 「某装备有哪些考核指标」不是全库指标枚举
        if _looks_like_equipment_indicators(q):
            return False
        from .rel_inference import looks_like_equipment_by_indicator

        if looks_like_equipment_by_indicator(q):
            return False
    hints = {
        "TestProject": r"(试验项目|测试项目)",
        "Environment": r"(试验环境|环境类型|环境类别|环境清单)",
        "Indicator": r"(考核指标|指标清单|全部指标)",
        "EquipmentType": r"(装备类型|装备类别|二级装备类型)",
    }
    pat = hints.get(node_hint, "")
    return bool(pat and re.search(pat, q))


def _looks_like_indicator_methods(question: str) -> bool:
    q = (question or "").strip()
    if not re.search(r"方法", q):
        return False
    if not re.search(r"(指标|考核)", q):
        return False
    return bool(
        re.search(r"(对应|关联|有哪些|有什么|用什么|采用)", q)
        or re.search(r"考核.+用什么方法", q)
    )


def _looks_like_method_content(question: str) -> bool:
    from .method_inference import looks_like_method_content_question

    q = (question or "").strip()
    if re.search(r"第\s*\d+(?:\.\d+)*\s*节", q):
        return False
    return looks_like_method_content_question(question)


def _looks_like_composite_project_method(question: str) -> bool:
    q = (question or "").strip()
    return bool(re.search(r"(试验项目和方法|测试项目和方法|项目和方法)", q))


def _looks_like_composite_indicator_method(question: str) -> bool:
    q = (question or "").strip()
    return bool(re.search(r"(考核指标和试验方法|指标和试验方法|指标与试验方法)", q))


def _looks_like_standard_metadata(question: str) -> bool:
    q = (question or "").strip()
    return bool(
        re.search(r"标准包含哪些试验方法", q)
        or re.search(r"标准文件在知识库中的标识", q)
        or re.search(r"在知识库中的标识是什么", q)
    )


def _looks_like_composite_env_settings(question: str) -> bool:
    q = (question or "").strip()
    if re.search(r"做.+试验时的环境条件", q):
        return True
    return bool(re.search(r"推荐的环境设置", q) and re.search(r"环境", q))


def _looks_like_multi_step_project_method(question: str) -> bool:
    q = re.sub(r"\s+", "", (question or "").strip())
    return bool(
        re.search(r"请先.+试验项目.+再.+方法", q)
        or re.search(r"先查询.+试验项目.+再说.+方法", q)
        or re.search(r"请先告诉我.+试验项目.+再告诉我.+方法", q)
    )


def _looks_like_multi_step_indicator_method(question: str) -> bool:
    q = re.sub(r"\s+", "", (question or "").strip())
    return bool(
        re.search(r"先查询.+考核指标.+再说.+方法", q)
        or re.search(r"先查询.+指标.+再说.+方法", q)
    )


def _looks_like_composite_project_env_method(question: str) -> bool:
    q = re.sub(r"\s+", "", (question or "").strip())
    return bool(
        re.search(r".+环境下?有哪些试验项目.+用什么方法", q)
        or re.search(r".+环境环境下有哪些试验项目.+这些项目用什么方法", q)
        or re.search(r".+在.+环境.+有哪些试验项目.+方法", q)
    )


DEDICATED_COMPOSITE_ROUTE_IDS = frozenset(
    {
        "R-C-PROJECT_THEN_METHOD-default",
        "R-C-INDICATOR_THEN_METHOD-default",
        "R-C-EQUIP_PROJECT_ENV_SETTINGS-default",
        "R-C-PROJECT_ENV_METHOD-default",
    }
)


def is_dedicated_composite_route_lock(lock: Optional["RouteLock"]) -> bool:
    if not lock:
        return False
    return str(lock.route_id or "") in DEDICATED_COMPOSITE_ROUTE_IDS


def _resolve_composite_route_locks(q: str) -> Optional["RouteLock"]:
    """拼装/多步/多实体复合路由，优先于单跳 project_methods 与 intent_plan。"""
    if _looks_like_composite_project_env_method(q):
        return _entity_route_lock(
            purpose="composite_project_env_method",
            route_id="R-C-PROJECT_ENV_METHOD-default",
            probe_labels=["Equipment", "Environment"],
            slot_key="equipment_keywords",
            reason="composite_project_env_method",
        )
    if _looks_like_multi_step_project_method(q) or _looks_like_composite_project_method(q):
        return _entity_route_lock(
            purpose="composite_project_then_method",
            route_id="R-C-PROJECT_THEN_METHOD-default",
            probe_labels=["Equipment"],
            slot_key="equipment_keywords",
            reason="composite_project_method",
        )
    if _looks_like_multi_step_indicator_method(q) or _looks_like_composite_indicator_method(q):
        return _entity_route_lock(
            purpose="composite_indicator_then_method",
            route_id="R-C-INDICATOR_THEN_METHOD-default",
            probe_labels=["Equipment"],
            slot_key="equipment_keywords",
            reason="composite_indicator_method",
        )
    if _looks_like_composite_env_settings(q):
        extra: Dict[str, Any] = {}
        if re.search(r"推荐的环境设置", q):
            equip, env = _extract_composite_env_by_environment_parts(q)
            extra["scenario"] = "by_environment"
            if env:
                extra["environment_keywords"] = [env]
            if equip:
                extra["equipment_keywords"] = [equip]
        else:
            equip, proj = _extract_composite_env_by_project_parts(q)
            extra["scenario"] = "by_project"
            if proj:
                extra["project_keywords"] = [proj]
            if equip:
                extra["equipment_keywords"] = [equip]
        return _entity_route_lock(
            purpose="composite_equip_project_env_settings",
            route_id="R-C-EQUIP_PROJECT_ENV_SETTINGS-default",
            probe_labels=["Equipment", "Environment", "TestProject"],
            slot_key="equipment_keywords",
            reason="composite_env_settings",
            extra=extra or None,
        )
    return None


def _extract_composite_env_by_environment_parts(question: str) -> Tuple[str, str]:
    """从「装备在环境下推荐的环境设置」问句提取装备短语与环境短语。"""
    q = re.sub(r"\s+", "", (question or "").strip())
    m = re.search(r"^(.+?型)在(.+?)推荐的环境设置", q)
    if not m:
        return "", ""
    equip = (m.group(1) or "").strip()
    env = (m.group(2) or "").strip()
    env = re.sub(r"(推荐|的|环境设置).*$", "", env).strip()
    if len(equip) >= 2 and len(env) >= 2:
        return equip, env
    return "", ""


def _extract_composite_env_by_project_parts(question: str) -> Tuple[str, str]:
    """从「装备做某项目试验时的环境条件」问句提取装备短语与项目短语。"""
    q = re.sub(r"\s+", "", (question or "").strip())
    m = re.search(r"^(.+?型)做(.+?)试验时的环境条件", q)
    if not m:
        return "", ""
    equip = (m.group(1) or "").strip()
    proj = (m.group(2) or "").strip()
    if len(equip) >= 2 and len(proj) >= 2:
        return equip, proj
    return "", ""


def _extract_composite_env_settings_parts(question: str) -> Tuple[str, str]:
    """兼容旧调用：按问句模式返回 (装备, 环境或项目)。"""
    equip, env = _extract_composite_env_by_environment_parts(question)
    if equip and env:
        return equip, env
    equip, proj = _extract_composite_env_by_project_parts(question)
    return equip, proj


def _extract_indicator_focus(question: str) -> str:
    q = re.sub(r"\s+", "", (question or "").strip())
    for pat in (
        r"^([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]+?)指标对应的试验方法",
        r"^考核([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]+?)用什么方法",
        r"^([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]+?)有哪些试验方法",
        r"^([\u4e00-\u9fffA-Za-z0-9·\-（）()≥≤%,./\-]+?)指标",
    ):
        m = re.match(pat, q)
        if m:
            focus = re.sub(r"(的|与|和|及|分别)$", "", (m.group(1) or "").strip())
            if len(focus) >= 2:
                return focus
    return ""


def _extract_document_title(question: str) -> str:
    q = (question or "").strip()
    for pat in (
        r"^(.+?)第[\d.]+\s*节的内容",
        r"^(.+?)标准的主要内容",
        r"^(.+?)中关于",
    ):
        m = re.match(pat, q)
        if m:
            return (m.group(1) or "").strip()
    return q[:120]


def _looks_like_document_question(question: str) -> bool:
    from .method_inference import looks_like_method_content_question

    q = (question or "").strip()
    if looks_like_method_content_question(q):
        return False
    if re.search(r"(GB/?T|GJB|标准号|标准文件)", q, re.I):
        return True
    if re.search(r"(第\s*\d+(?:\.\d+)*\s*(?:节|章)|章节|条文)", q):
        return True
    return bool(
        re.search(r"标准的主要内容", q)
        or re.search(r"中关于", q)
        or (re.search(r"向量库", q) and re.search(r"(章节|概述|关键词|文档)", q))
    )


def _looks_like_standard_condition(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    # 「某项目有哪些相关标准/试验方法」是项目维度的标准/方法列举，不是标准条文里的条件参数
    if re.search(r"(试验|测试)?项目", q) and re.search(
        r"(相关标准|依据标准|标准文件|适用标准|试验方法|测试方法)", q
    ):
        return False
    if re.search(r"(有哪些|有什么).{0,12}(试验|测试)?项目", q):
        return False
    # 须出现标准号（GB/T、GJB），不能仅凭「相关标准」等短语里的「标准」二字
    has_std = bool(re.search(r"(GB[/／]?T|GJB)\s*[-‐]?\s*\d", q, re.I))
    if not has_std:
        return False
    has_param = bool(
        re.search(r"[\-−]?\d+\s*[℃°度]", q)
        or re.search(
            r"(湿度|贮存|存储|持续|保持|暴露).{0,16}(多久|多长时间|多少|小时|天|min|分钟|h)", q, re.I
        )
        or re.search(r"(试验条件|考核条件|极限|上下限|温度|湿热|低温|高温)", q)
    )
    return has_param


def _looks_like_project_standards(question: str) -> bool:
    """某试验项目相关标准（经项目→方法→标准链检索）。"""
    q = (question or "").strip()
    if not re.search(r"(相关标准|依据标准|标准文件|适用标准|执行标准|依据哪些标准)", q):
        return False
    if re.search(r"(试验|测试)?项目", q):
        return True
    return bool(extract_project_keywords_for_methods(q))


def _resolve_document_lock(question: str) -> Optional[RouteLock]:
    q = (question or "").strip()
    if not _looks_like_document_question(q):
        return None
    title = _extract_document_title(q)
    if re.search(r"第\s*\d+(?:\.\d+)*\s*(?:节|章)", q):
        return RouteLock(
            purpose="query_document",
            route_id="R-STANDARD-chapter_content",
            probe_labels=[],
            slot_key="standard_keywords",
            focus=title[:120],
            keywords=[title[:120]] if title else [],
            reason="document_chapter",
        )
    return RouteLock(
        purpose="query_document",
        route_id="R-KNOWLEDGE-keyword_doc",
        probe_labels=[],
        slot_key="standard_keywords",
        focus=title[:120],
        keywords=[title[:120]] if title else [],
        reason="document_keyword",
    )


def resolve_route_lock(question: str) -> Optional[RouteLock]:
    """问句 → 强制路由锁；无匹配返回 None。优先级：level2 > belongs_to > under_type > …"""
    try:
        q = str(question or "").strip()
    except Exception:
        return None
    if not q:
        return None
    try:
        return _resolve_route_lock_rules(q)
    except Exception as exc:
        logger.warning("[ZNWD4][route_lock][rules] resolve failed q=%r: %s", q[:120], exc)
        return None


def _resolve_route_lock_rules(q: str) -> Optional[RouteLock]:
    if _looks_like_meta_info(q):
        return RouteLock(
            purpose=PURPOSE_META,
            route_id="R-META-stats",
            probe_labels=[],
            slot_key="",
            focus="",
            reason="meta_info",
        )

    if _looks_like_standard_condition(q):
        title = _extract_document_title(q) or q[:80]
        return RouteLock(
            purpose="query_standard_condition",
            route_id="R-STANDARD-condition_param",
            probe_labels=["File"],
            slot_key="standard_keywords",
            focus=title[:120],
            keywords=[title[:120]] if title else [],
            reason="standard_condition_param",
        )

    if _looks_like_method_content(q):
        from .method_inference import extract_method_standard_keyword

        focus = extract_method_standard_keyword(q) or ""
        if not focus:
            m = re.match(r"^(.+?)(?:的(?:试验方法内容|具体步骤)|方法正文)", re.sub(r"\s+", "", q))
            focus = (m.group(1) or "").strip() if m else ""
        return RouteLock(
            purpose="query_method_content",
            route_id="R-TEST_METHOD-content",
            probe_labels=["TestMethod"],
            slot_key="method_keywords",
            focus=focus,
            keywords=[focus] if focus else [],
            reason="method_content",
        )

    doc_lock = _resolve_document_lock(q)
    if doc_lock:
        return doc_lock

    composite_lock = _resolve_composite_route_locks(q)
    if composite_lock:
        return composite_lock

    if _looks_like_project_standards(q):
        return _entity_route_lock(
            purpose="query_project_standards",
            route_id="R-STANDARD-for_projects",
            probe_labels=["TestProject"],
            slot_key="project_keywords",
            reason="project_standards",
        )

    if _looks_like_project_methods(q):
        return _entity_route_lock(
            purpose="query_project_methods",
            route_id="R-TEST_METHOD-by_project",
            probe_labels=["TestProject"],
            slot_key="project_keywords",
            reason="project_methods",
        )

    if looks_like_equipment_by_project(q):
        proj_kws = extract_project_keywords(q)
        return RouteLock(
            purpose="query_equipment_by_project",
            route_id="R-EQUIPMENT-by_project",
            probe_labels=["TestProject"],
            slot_key="project_keywords",
            focus=proj_kws[0] if proj_kws else "",
            keywords=list(proj_kws),
            reason="equipment_by_project",
        )

    if looks_like_equipment_by_indicator(q):
        ind_kws = extract_indicator_keywords(q)
        return RouteLock(
            purpose="query_equipment_by_indicator",
            route_id="R-EQUIPMENT-by_indicator",
            probe_labels=["Indicator"],
            slot_key="indicator_keywords",
            focus=ind_kws[0] if ind_kws else "",
            keywords=list(ind_kws),
            reason="equipment_by_indicator",
        )

    if looks_like_projects_by_method(q):
        meth_kws = extract_method_keywords(q)
        return RouteLock(
            purpose="query_projects_by_method",
            route_id="R-TEST_PROJECT-by_method",
            probe_labels=["TestMethod"],
            slot_key="method_keywords",
            focus=meth_kws[0] if meth_kws else "",
            keywords=list(meth_kws),
            reason="projects_by_method",
        )

    if _looks_like_enum_list(q, node_hint="EquipmentType") and not _looks_like_level2_equipment_types(q):
        return RouteLock(
            purpose=PURPOSE_ENUM,
            route_id="R-EQUIPMENT_TYPE-level1_list",
            probe_labels=[],
            slot_key="",
            focus="",
            reason="enum_equipment_type",
        )

    if _looks_like_enum_equipment(q):
        return RouteLock(
            purpose=PURPOSE_ENUM,
            route_id="R-EQUIPMENT-list",
            probe_labels=[],
            slot_key="",
            focus="",
            reason="enum_equipment",
            extra={"node_type": "Equipment"},
        )

    if _looks_like_equip_and_env_test_projects(q):
        equip, env = _extract_equip_env_test_project_parts(q)
        return RouteLock(
            purpose="query_equip_env_test_projects",
            route_id="R-TEST_PROJECT-by_equip_and_env",
            probe_labels=["Equipment", "Environment"],
            slot_key="equipment_keywords",
            focus=equip,
            keywords=[equip],
            reason="equip_and_env_test_projects",
            extra={
                "scenario": "by_equip_and_env",
                "equipment_keywords": [equip],
                "environment_keywords": [env],
            },
        )

    if _looks_like_enum_list(q, node_hint="TestProject"):
        return RouteLock(
            purpose=PURPOSE_ENUM,
            route_id="R-TEST_PROJECT-enum",
            probe_labels=[],
            slot_key="",
            focus="",
            reason="enum_test_project",
        )

    if _looks_like_enum_list(q, node_hint="Environment"):
        return RouteLock(
            purpose=PURPOSE_ENUM,
            route_id="R-ENVIRONMENT-list",
            probe_labels=[],
            slot_key="",
            focus="",
            reason="enum_environment",
        )

    if _looks_like_equipment_indicators(q):
        return _entity_route_lock(
            purpose="query_equipment_indicators",
            route_id="R-INDICATOR-by_equipment",
            probe_labels=["Equipment"],
            slot_key="equipment_keywords",
            reason="equipment_indicators",
        )

    if _looks_like_enum_list(q, node_hint="Indicator"):
        return RouteLock(
            purpose=PURPOSE_ENUM,
            route_id="R-INDICATOR-list_all",
            probe_labels=[],
            slot_key="",
            focus="",
            reason="enum_indicator",
        )

    if _looks_like_standard_metadata(q):
        m = re.match(r"^(.+?)标准", q)
        focus = (m.group(1) or "").strip() if m else q[:80]
        return RouteLock(
            purpose="query_standard_methods",
            route_id="R-STANDARD-methods_in_standard",
            probe_labels=["File"],
            slot_key="standard_keywords",
            focus=focus,
            keywords=[focus] if focus else [],
            reason="standard_metadata",
        )

    if _looks_like_indicator_methods(q):
        return _entity_route_lock(
            purpose="query_indicator_methods",
            route_id="R-TEST_METHOD-by_indicator",
            probe_labels=["Indicator"],
            slot_key="indicator_keywords",
            reason="indicator_methods",
        )

    if _looks_like_level2_equipment_types(q):
        return _entity_route_lock(
            purpose="query_level2_equipment_types",
            route_id="R-EQUIPMENT_TYPE-level2_under_level1",
            probe_labels=["EquipmentType"],
            slot_key="equipment_type_keywords",
            reason="level2_equipment_types",
        )

    if _looks_like_belongs_to_question(q):
        return _entity_route_lock(
            purpose="query_equipment_belongs_to_type",
            route_id="R-EQUIPMENT_TYPE-belongs_to",
            probe_labels=["Equipment"],
            slot_key="equipment_keywords",
            reason="equipment_belongs_to_type",
        )

    if looks_like_type_equipment_relation(q) or re.search(r"类装备有哪些", q) or re.search(
        r"属于.+类型的装备有哪些", q
    ):
        return _entity_route_lock(
            purpose="query_equipment_under_type",
            route_id="R-EQUIPMENT-under_type",
            probe_labels=["EquipmentType"],
            slot_key="equipment_type_keywords",
            reason="equipment_under_type",
        )

    if _looks_like_environment_test_projects(q):
        return _entity_route_lock(
            purpose="query_environment_test_projects",
            route_id="R-TEST_PROJECT-by_environment",
            probe_labels=["Environment"],
            slot_key="environment_keywords",
            reason="environment_test_projects",
        )

    if _looks_like_project_environment(q):
        return _entity_route_lock(
            purpose="query_project_environment",
            route_id="R-ENVIRONMENT-for_project",
            probe_labels=["TestProject"],
            slot_key="project_keywords",
            reason="project_environment",
        )

    if _looks_like_environment_properties(q):
        return _entity_route_lock(
            purpose="query_environment_properties",
            route_id="R-ENVIRONMENT-properties",
            probe_labels=["Environment"],
            slot_key="environment_keywords",
            reason="environment_properties",
        )

    if _looks_like_equipment_test_projects(q):
        return _entity_route_lock(
            purpose="query_equipment_test_projects",
            route_id="R-TEST_PROJECT-by_equipment",
            probe_labels=["Equipment"],
            slot_key="equipment_keywords",
            reason="equipment_test_projects",
        )

    return None


def build_locked_step2(lock: RouteLock) -> Dict[str, Any]:
    params = apply_route_lock_params({}, lock)
    kws = list(lock.keywords) if lock.keywords else ([lock.focus] if lock.focus else [])
    return {
        "route_id": lock.route_id,
        "params_draft": params,
        "keywords": kws,
        "focus": lock.focus,
        "match_reason": f"program_lock:{lock.reason}",
    }


def apply_route_lock_to_step1(step1: Dict[str, Any], lock: RouteLock, question: str = "") -> Dict[str, Any]:
    out = dict(step1 or {})
    out["purpose"] = lock.purpose
    out["purpose_zh"] = _purpose_zh(lock.purpose)
    out["route_lock_reason"] = lock.reason
    slot_kws = list(lock.keywords) if lock.keywords else ([lock.focus] if lock.focus else [])
    if slot_kws:
        out["keyword"] = slot_kws[0]
        kws = out.get("keywords") if isinstance(out.get("keywords"), list) else []
        merged = slot_kws + [k for k in kws if k not in slot_kws]
        out["keywords"] = merged
    return out


def inject_locked_route(candidates: List[RouteEntry], lock: RouteLock) -> List[RouteEntry]:
    locked = get_route(lock.route_id)
    if not locked:
        return candidates
    rest = [c for c in candidates if c.route_id != lock.route_id]
    return [locked] + rest


def locked_slot_values(lock: RouteLock) -> List[str]:
    """程序锁 authoritative 槽位值（多值优先）。"""
    if lock.keywords:
        return list(lock.keywords)
    if lock.focus:
        return [lock.focus]
    return []


def apply_route_lock_params(params: Dict[str, Any], lock: RouteLock) -> Dict[str, Any]:
    out = dict(params or {})
    slot_kws = list(lock.keywords) if lock.keywords else ([lock.focus] if lock.focus else [])
    if slot_kws and lock.slot_key:
        out["focus"] = slot_kws[0]
        out[lock.slot_key] = slot_kws
        if lock.slot_key == "project_keywords":
            out["project_name"] = slot_kws[0]
        if lock.slot_key == "environment_keywords":
            out["environment_value_list"] = slot_kws
        if lock.slot_key == "equipment_keywords":
            out["equipment_value_list"] = slot_kws
        wrong_slots = (
            "equipment_keywords",
            "equipment_type_keywords",
            "environment_keywords",
            "project_keywords",
            "indicator_keywords",
            "standard_keywords",
        )
        for slot in wrong_slots:
            if lock.extra and slot in lock.extra:
                continue
            if slot != lock.slot_key and slot in out:
                del out[slot]
    if lock.extra:
        out.update({k: v for k, v in lock.extra.items() if v is not None})
    return out


def apply_route_lock_to_plan(plan: Dict[str, Any], lock: RouteLock) -> Dict[str, Any]:
    resolved = resolve_route(lock.route_id) or {}
    out = dict(plan or {})
    out["route_id"] = lock.route_id
    out["purpose"] = lock.purpose
    out["user_biz"] = resolved.get("user_biz", "")
    out["scenario"] = str(lock.extra.get("scenario") or resolved.get("scenario", "default"))
    out["match_confidence"] = "high"
    out["answer_strategy"] = resolved.get("answer_strategy", out.get("answer_strategy", "db_direct"))
    draft = out.get("params_draft") if isinstance(out.get("params_draft"), dict) else {}
    out["params_draft"] = apply_route_lock_params(draft, lock)
    if lock.keywords:
        out["keyword"] = lock.keywords[0]
    elif lock.focus:
        out["keyword"] = lock.focus
    out["route_lock_reason"] = lock.reason
    return out


__all__ = [
    "RouteLock",
    "resolve_route_lock",
    "apply_route_lock_to_step1",
    "build_locked_step2",
    "inject_locked_route",
    "apply_route_lock_params",
    "apply_route_lock_to_plan",
    "skip_llm_routing_when_locked",
    "skip_llm_for_lock",
    "lock_is_slotless",
    "_looks_like_level2_equipment_types",
    "_looks_like_project_methods",
    "extract_project_keywords_for_methods",
    "extract_project_keywords",
    "extract_indicator_keywords",
    "extract_method_keywords",
    "locked_slot_values",
]
