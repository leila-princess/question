"""
问句槽位提取与规范化（程序锁 / sanitize 共用，避免 LLM 截断实体名）。
"""

from __future__ import annotations

import re
from typing import List

# 只剥操作后缀（环境下/条件下），保留「水文环境」等实体名末尾的「环境」
_ENV_SUFFIX_RE = re.compile(r"(环境条件下|条件下|环境下)$")
_ENV_DUP_RE = re.compile(r"环境+")

_EQUIP_FOR_INDICATOR = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)"
    r"(?:要|需要|应)?(?:考核|满足|符合)?(?:哪些|什么|哪(?:一)?(?:些|种)?)?(?:的)?(?:性能)?指标",
    re.I,
)
_EQUIP_FOR_INDICATOR_ALT = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)(?:的)?(?:考核|性能)?指标",
    re.I,
)
_EQUIP_FOR_PROJECT = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)"
    r"(?:有|具备|做|进行)?(?:哪些|什么|哪(?:一)?(?:些|种)?)?(?:的)?(?:试验|测试)?项目",
    re.I,
)
_EQUIP_FOR_TRIALS = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)"
    r"(?:要|需要|应)?(?:做|进行)?(?:哪些|什么|哪(?:一)?(?:些|种)?)?(?:的)?(?:试验|测试)(?!项目)",
    re.I,
)
_EQUIP_QUANTIFIER_STUBS = frozenset(
    {"有什么", "有哪些", "包括哪些", "都有哪些", "哪些", "什么", "有包括", "有什", "有什"}
)

_ENV_FOR_QUERY = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)"
    r"(?:环境)?(?:下|中|里|条件下)?(?:有|对应|相关|要做|应做)?(?:哪些|什么|哪(?:一)?(?:些|种)?)?"
    r"(?:的)?(?:试验|测试)?项目",
    re.I,
)
_ENV_INTRO = re.compile(
    r"(?:介绍|说明|描述|讲讲|说说).{0,6}"
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)(?:试验|测试)?环境",
    re.I,
)
_ENV_PROP = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)(?:试验|测试)?环境"
    r".{0,12}(?:分类|分级|组合|约束|要求|属性|类型|Level|Class)",
    re.I,
)


_QUERY_PREFIXES = (
    "请分别说明",
    "分别说明",
    "请说明",
    "请告诉我",
    "告诉我",
    "请列出",
    "列出",
    "查询一下",
    "请查询",
    "查询",
)


def strip_query_prefix(text: str) -> str:
    t = re.sub(r"\s+", "", (text or "").strip())
    changed = True
    while changed and t:
        changed = False
        for prefix in _QUERY_PREFIXES:
            if t.startswith(prefix):
                t = t[len(prefix) :].strip()
                changed = True
                break
    return t


def _clean_token(s: str) -> str:
    t = re.sub(r"\s+", "", (s or "").strip())
    t = re.sub(r"(的|与|和|及|分别|下|中|里)$", "", t).strip()
    return t


def normalize_environment_keyword(raw: str) -> str:
    s = _clean_token(raw)
    if not s:
        return ""
    s = _ENV_DUP_RE.sub("环境", s)
    for _ in range(3):
        ns = _ENV_SUFFIX_RE.sub("", s)
        if ns == s:
            break
        s = ns
    s = _ENV_DUP_RE.sub("环境", s)
    if s.endswith("环境环境"):
        s = s[:-2]
    return s.strip()


def expand_environment_keywords(raw: str) -> List[str]:
    base = normalize_environment_keyword(raw)
    if not base:
        return []
    out: List[str] = []
    seen: set = set()

    def add(v: str) -> None:
        v = normalize_environment_keyword(v)
        if len(v) < 1 or v in seen:
            return
        seen.add(v)
        out.append(v)

    add(base)
    if not base.endswith("环境") and len(base) >= 2:
        add(base + "环境")
    if base.endswith("环境"):
        add(base[:-2])
    if base.lower() == "test":
        add("test")
    return out[:6]


def extract_equipment_focus(text: str) -> str:
    """从问句提取具体装备名（指标/项目/归属等）。"""
    q = strip_query_prefix(text)
    if not q:
        return ""
    for pat in (_EQUIP_FOR_INDICATOR, _EQUIP_FOR_INDICATOR_ALT, _EQUIP_FOR_PROJECT, _EQUIP_FOR_TRIALS):
        m = pat.search(q)
        if m:
            focus = _clean_token(m.group(1) or "")
            if len(focus) >= 2 and focus not in ("装备", "设备", "哪些", "什么") and focus not in _EQUIP_QUANTIFIER_STUBS:
                return focus
    from .rel_inference import extract_equipment_focus_for_belongs_to_type, extract_type_focus_for_equipment

    focus = extract_equipment_focus_for_belongs_to_type(q)
    if focus:
        return focus
    return extract_type_focus_for_equipment(q) or ""


_ENV_EQUIP_ENV = re.compile(
    r"^(?:查询)?(.+?)在(.+?)(?:环境)?(?:下|中|里|条件下)?(?:有|对应|相关|要做|应做)?(?:哪些|什么|哪(?:一)?(?:些|种)?)?"
    r"(?:的)?(?:试验|测试)?项目",
    re.I,
)


def _extract_equip_env_environment(text: str) -> str:
    q = re.sub(r"\s+", "", (text or "").strip())
    if not q:
        return ""
    m = _ENV_EQUIP_ENV.search(q)
    if m:
        env = _clean_token(m.group(2) or "")
        if len(env) >= 1:
            return env
    return ""


def extract_environment_focus(text: str) -> str:
    q = re.sub(r"\s+", "", (text or "").strip())
    if not q:
        return ""
    env = _extract_equip_env_environment(q)
    if env:
        norm = normalize_environment_keyword(env)
        if norm:
            return norm
    m_prop = re.search(
        r"^([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)(?:试验|测试)?环境(?:环境)?(?:的)?"
        r"(?:分类|分级|组合|约束|要求|属性|Type|Class|Level)",
        q,
        re.I,
    )
    if m_prop:
        norm = normalize_environment_keyword(m_prop.group(1) or "")
        if norm:
            return norm
    for pat in (_ENV_INTRO, _ENV_PROP, _ENV_FOR_QUERY):
        m = pat.search(q)
        if m:
            raw = _clean_token(m.group(1) or "")
            norm = normalize_environment_keyword(raw)
            if len(norm) >= 1:
                return norm
    m = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32}?)(?:试验|测试)?环境",
        q,
    )
    if m:
        return normalize_environment_keyword(m.group(1) or "")
    return ""


def extract_environment_keywords(text: str) -> List[str]:
    focus = extract_environment_focus(text)
    if not focus:
        return []
    return expand_environment_keywords(focus)
