"""Natural-language route choice copy for user-facing disambiguation."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .keyword_probe import extract_focus_candidates
from .routing_catalog import RouteEntry, get_route

# (user_biz, scenario) → builder(question, focus_parts) -> str
_NLG_BUILDERS: Dict[Tuple[str, str], Any] = {}


def _register(ubiz: str, scenario: str):
    def deco(fn):
        _NLG_BUILDERS[(ubiz, scenario)] = fn
        return fn

    return deco


def _focus_parts(question: str) -> List[str]:
    cands = extract_focus_candidates(question)
    out: List[str] = []
    for c in cands:
        s = re.sub(r"\s+", "", (c or "").strip())
        if len(s) >= 2 and s not in out:
            out.append(s)
    return out[:3]


def _q_snippet(question: str, max_len: int = 48) -> str:
    q = re.sub(r"\s+", " ", (question or "").strip())
    if len(q) <= max_len:
        return q
    return q[: max_len - 1] + "…"


def _one_focus(parts: List[str], *, role: str = "对象") -> str:
    if not parts:
        return f"您问题里提到的{role}"
    if len(parts) == 1:
        return f"「{parts[0]}」"
    return "、".join(f"「{p}」" for p in parts[:2])


def _two_focus(parts: List[str], role_a: str, role_b: str) -> Tuple[str, str]:
    if len(parts) >= 2:
        return f"「{parts[0]}」", f"「{parts[1]}」"
    if len(parts) == 1:
        return f"「{parts[0]}」", f"相关{role_b}"
    return f"某{role_a}", f"某{role_b}"


@_register("UBIZ_TEST_PROJECT", "by_equipment")
def _tp_eq(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="装备")
    return f"想查 {f} 这台装备/设备关联了哪些试验项目"


@_register("UBIZ_TEST_PROJECT", "by_environment")
def _tp_env(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="试验环境")
    return f"想查在 {f} 这类试验环境下，清单里有哪些试验项目"


@_register("UBIZ_TEST_PROJECT", "by_equip_and_env")
def _tp_both(q: str, p: List[str]) -> str:
    eq, env = _two_focus(p, "装备", "环境")
    return f"想查 {eq} 在 {env} 条件下需要开展哪些试验项目"


@_register("UBIZ_TEST_PROJECT", "enum")
def _tp_enum(q: str, p: List[str]) -> str:
    return "想浏览或列举知识库中现有的全部试验项目"


@_register("UBIZ_INDICATOR", "by_equipment")
def _ind_eq(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="装备")
    return f"想查 {f} 这台装备需要考核/关注哪些性能指标"


@_register("UBIZ_INDICATOR", "by_equip_type")
def _ind_et(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="装备类型")
    return f"想查 {f} 这一类装备通常考核哪些指标"


@_register("UBIZ_INDICATOR", "list_all")
def _ind_all(q: str, p: List[str]) -> str:
    return "想浏览知识库中有哪些考核/性能指标"


@_register("UBIZ_TEST_METHOD", "by_project")
def _tm_proj(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="试验项目")
    return f"想查 {f} 这个试验项目对应有哪些试验方法"


@_register("UBIZ_TEST_METHOD", "by_indicator")
def _tm_ind(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="指标")
    return f"想查与 {f} 这一指标关联的试验方法有哪些"


@_register("UBIZ_TEST_METHOD", "by_equipment_via_project")
def _tm_eq_tp(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="装备")
    return f"想查 {f} 经其试验项目所关联的试验方法（走项目路径）"


@_register("UBIZ_TEST_METHOD", "by_equipment_via_indicator")
def _tm_eq_ind(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="装备")
    return f"想查 {f} 经其考核指标所关联的试验方法（走指标路径）"


@_register("UBIZ_TEST_METHOD", "content")
def _tm_content(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="方法或项目")
    return f"想查看 {f} 相关试验方法的具体步骤、正文或评定要求"


@_register("UBIZ_EQUIPMENT", "list")
def _eq_list(q: str, p: List[str]) -> str:
    return "想浏览知识库中登记的全部具体装备/设备"


@_register("UBIZ_EQUIPMENT", "under_type")
def _eq_under(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="装备类型")
    return f"想查 {f} 这一装备类型下有哪些具体装备"


@_register("UBIZ_EQUIPMENT", "by_project")
def _eq_proj(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="试验项目")
    return f"想查 {f} 这个试验项目涉及或适用哪些装备"


@_register("UBIZ_EQUIPMENT", "by_indicator")
def _eq_ind(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="指标")
    return f"想查与 {f} 这一指标相关的装备有哪些"


@_register("UBIZ_EQUIPMENT_TYPE", "level1_list")
def _et_l1(q: str, p: List[str]) -> str:
    return "想查一级装备/设备类别有哪些"


@_register("UBIZ_EQUIPMENT_TYPE", "level2_under_level1")
def _et_l2(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="一级类别")
    return f"想查 {f} 下面有哪些二级装备/设备类别"


@_register("UBIZ_EQUIPMENT_TYPE", "belongs_to")
def _et_belongs(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="装备")
    return f"想确认 {f} 属于哪一类装备/设备类型"


@_register("UBIZ_ENVIRONMENT", "list")
def _env_list(q: str, p: List[str]) -> str:
    return "想浏览知识库中有哪些试验环境类型"


@_register("UBIZ_ENVIRONMENT", "properties")
def _env_prop(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="环境")
    return f"想查 {f} 这一试验环境的分类、分级、组合方式或约束条件"


@_register("UBIZ_ENVIRONMENT", "for_project")
def _env_proj(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="试验项目")
    return f"想查 {f} 这个试验项目应在什么/哪种试验环境下进行"


@_register("UBIZ_STANDARD", "for_projects")
def _std_proj(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="试验项目")
    return f"想查 {f} 相关试验项目依据哪些标准/规范文件"


@_register("UBIZ_STANDARD", "methods_in_standard")
def _std_tm(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="标准或方法")
    return f"想查 {f} 这份标准/文件中包含哪些试验方法条目"


@_register("UBIZ_STANDARD", "chapter_content")
def _std_doc(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="标准")
    return f"想从文档库检索 {f} 相关标准条文的正文内容"


@_register("UBIZ_STANDARD", "condition_param")
def _std_cond(q: str, p: List[str]) -> str:
    f = _one_focus(p, role="标准")
    return f"想查 {f} 标准中规定的试验条件或参数要求"


@_register("UBIZ_KNOWLEDGE", "keyword_doc")
def _know_doc(q: str, p: List[str]) -> str:
    snippet = _q_snippet(q)
    return f"想在试验知识文档库中检索与「{snippet}」相关的说明材料"


_COMPOSITE_NLG: Dict[str, Any] = {
    "COMP_PROJECT_THEN_METHOD": lambda q, p: (
        f"想先查 {_one_focus(p, role='装备')} 的试验项目，再查对应试验方法"
        if p
        else "想先查试验项目，再查该项目下的试验方法"
    ),
    "COMP_PROJECT_ENV_METHOD": lambda q, p: (
        f"想查 {_one_focus(p, role='装备')} 在特定环境条件下的试验项目及对应方法"
        if p
        else "想查装备在特定环境下的试验项目及方法"
    ),
    "COMP_INDICATOR_THEN_METHOD": lambda q, p: (
        f"想先查 {_one_focus(p, role='装备')} 的考核指标，再查指标对应的方法"
        if p
        else "想先查考核指标，再查指标对应的试验方法"
    ),
    "COMP_EQUIP_PROJECT_ENV_SETTINGS": lambda q, p: (
        f"想了解 {_one_focus(p, role='装备')} 做某项试验时环境应如何设置"
        if p
        else "想了解装备做某项试验时试验环境应如何设置"
    ),
}


def phrase_route_option(question: str, route_id: str, entry: Optional[RouteEntry] = None) -> str:
    """Turn route into a complete natural-language intent (no route_id / symbols)."""
    entry = entry or get_route(route_id)
    parts = _focus_parts(question)
    if not entry:
        snippet = _q_snippet(question)
        return f"按「{snippet}」相关的方式在知识库中检索"

    internal = entry.internal or {}
    ubiz = str(internal.get("user_biz") or "").strip()
    scenario = str(internal.get("scenario") or "").strip()
    comp_id = str(internal.get("composite_id") or ubiz).strip()

    if comp_id in _COMPOSITE_NLG:
        body = _COMPOSITE_NLG[comp_id](question, parts)
    else:
        builder = _NLG_BUILDERS.get((ubiz, scenario))
        if builder:
            body = builder(question, parts)
        else:
            title = (entry.title or "").strip()
            snippet = _q_snippet(question)
            if title:
                body = f"想按「{title}」来理解您关于「{snippet}」的提问"
            else:
                body = f"想按另一种业务理解方式来回答「{snippet}」"

    body = body.rstrip("。，；")
    return f"{body}。"


def build_clarify_prompt(question: str) -> str:
    snippet = _q_snippet(question, 56)
    return (
        f"关于您刚才问的「{snippet}」，系统还不能完全确定您想查哪一类信息。"
        f"请从下面选一项最贴近您意图的说法，我会按您的选择继续检索并回答。"
    )


__all__ = ["phrase_route_option", "build_clarify_prompt", "_focus_parts"]
