"""
试验方法「内容/具体/步骤」类问句：识别与参数推断。
优先读 TestMethod 节点 SYNR，其次 SYTJ/SYGC/JGPD，最后才走文档检索。
"""

from __future__ import annotations

import re
from typing import Any, Dict

_METHOD_CONTENT = re.compile(
    r"(?:试验|测试)?\s*(?:方法|步骤|流程).{0,24}(?:内容|正文|具体|是什么|包括哪些|有哪些部分|怎么做|怎样)"
    r"|(?:内容|正文|具体|步骤|流程|怎么做).{0,24}(?:试验|测试)?\s*(?:方法|步骤|流程)"
    r"|方法\s*(?:的)?\s*(?:具体)?\s*(?:内容|正文|步骤)"
    r"|(?:试验|测试)?方法正文",
    re.I,
)

_METHOD_LIST_ONLY = re.compile(
    r"(?:有什么|有哪些|包括哪些|都有哪些).{0,16}(?:试验|测试)?\s*方法"
    r"|(?:试验|测试)?\s*方法\s*(?:有什么|有哪些|包括哪些|都有哪些)",
    re.I,
)

_EQUIP_PROJECT_BY_METHOD = re.compile(
    r"(?:哪些|哪一些|什么|都有哪些|有哪些).{0,20}(?:的)?\s*(?:设备|装备)"
    r".{0,20}(?:的)?\s*(?:什么|哪些|哪一些)?\s*(?:的)?\s*(?:试验|测试)?\s*项目"
    r".{0,24}(?:有|具备|采用|使用|关联).{0,40}(?:试验|测试)?\s*方法",
    re.I,
)

_METHOD_STD_RE = re.compile(
    r"(G\s*B\s*/?\s*T\s*[\d\.]+(?:\s*[-–—]\s*\d{4})?|G\s*B\s*T\s*[\d\.]+(?:\s*[-–—]\s*\d{4})?)",
    re.I,
)


def looks_like_method_list_only(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if looks_like_equip_project_by_method(t):
        return False
    if _METHOD_CONTENT.search(t):
        return False
    return bool(_METHOD_LIST_ONLY.search(t))


def looks_like_equip_project_by_method(text: str) -> bool:
    """哪些/什么设备的什么项目有 X 试验方法（反查设备+项目，非文档检索）。"""
    t = (text or "").strip()
    if not t:
        return False
    if _EQUIP_PROJECT_BY_METHOD.search(t):
        return True
    if re.search(r"(?:哪些|什么|哪).{0,16}(?:设备|装备)", t) and _METHOD_GOAL.search(t):
        if re.search(r"项目", t) or re.search(r"G\s*B\s*/?\s*T", t, re.I):
            return True
    return False


_METHOD_GOAL = re.compile(r"(?:试验|测试)?\s*方法", re.I)


def normalize_method_standard_keyword(raw: str) -> str:
    s = re.sub(r"\s+", "", (raw or "").strip())
    s = re.sub(r"[／/]", "/", s)
    s = re.sub(r"[-–—]", "-", s)
    return s


def extract_method_standard_keyword(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    m = _METHOD_STD_RE.search(t)
    if m:
        return normalize_method_standard_keyword(m.group(0))
    m = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·\-/\.]{2,48}?)\s*(?:的)?\s*(?:试验|测试)\s*方法",
        t,
    )
    if m:
        cand = normalize_method_standard_keyword(m.group(1))
        if len(cand) >= 3 and cand not in ("试验方法", "测试方法", "方法"):
            return cand
    return ""


def expand_method_standard_keywords(kw: str) -> list:
    """GB/T 标准号及常见变体，用于 TestMethod.Name / Source_name 模糊匹配。"""
    base = normalize_method_standard_keyword(kw)
    if not base:
        return []
    out: list = []
    seen: set = set()

    def add(v: str) -> None:
        v = normalize_method_standard_keyword(v)
        if not v:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(v)

    add(base)
    compact = re.sub(r"[/\s]", "", base, flags=re.I)
    add(compact)
    add(re.sub(r"^GB/T", "GBT", compact, flags=re.I))
    add(re.sub(r"^GBT", "GB/T", compact, flags=re.I))
    m = re.search(r"([\d]+(?:\.\d+)+(?:-\d{4})?)", compact, re.I)
    if m:
        add(m.group(1))
        core = m.group(1).split("-")[0]
        if core:
            add(core)
    return out[:8]


def looks_like_method_content_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _METHOD_LIST_ONLY.search(t) and not _METHOD_CONTENT.search(t):
        return False
    return bool(_METHOD_CONTENT.search(t))


def _extract_project_for_method_content(text: str) -> str:
    try:
        from .question_route_lock import extract_project_keywords_for_methods

        kws = extract_project_keywords_for_methods(text)
        if kws:
            return kws[0]
    except ImportError:
        pass
    for pat in (
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:测试|试验)?\s*项目\s*(?:的)?\s*(?:试验|测试)?\s*方法",
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:的)?\s*(?:试验|测试)?\s*方法\s*(?:具体)?内容",
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,40}?)\s*(?:测试|试验)(?:项目)?\s*(?:的)?\s*方法",
    ):
        m = re.search(pat, text or "")
        if m:
            cand = re.sub(r"\s+", "", (m.group(1) or "").strip())
            cand = re.sub(r"(的|与|和|及)$", "", cand).strip()
            if len(cand) >= 2 and cand not in ("测试", "试验", "方法", "内容", "具体"):
                return cand[:40]
    return ""


def _extract_method_name(text: str) -> str:
    for pat in (
        r"([\u4e00-\u9fffA-Za-z0-9·\-《》]{2,48}?)\s*(?:试验|测试)?\s*方法\s*(?:的)?\s*(?:具体)?内容",
        r"「([\u4e00-\u9fffA-Za-z0-9·\-]{2,48}?)」\s*(?:的)?\s*(?:试验|测试)?\s*方法",
    ):
        m = re.search(pat, text or "")
        if m:
            cand = re.sub(r"\s+", "", (m.group(1) or "").strip())
            if len(cand) >= 2:
                return cand[:48]
    return ""


def resolve_method_content_params(
    question: str,
    retrieval_question: str = "",
    *,
    node_type: str = "",
    node_name: str = "",
    node_id: str = "",
    params_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    draft = params_draft or {}
    text = f"{question}\n{retrieval_question}"
    out: Dict[str, Any] = {}

    project = str(draft.get("project_name") or "").strip()
    method = str(draft.get("method_name") or "").strip()
    std_kw = extract_method_standard_keyword(text)
    if not method:
        method = std_kw or _extract_method_name(text)
    if not project and not std_kw:
        project = _extract_project_for_method_content(text)
    if std_kw and method and normalize_method_standard_keyword(method) == normalize_method_standard_keyword(std_kw):
        project = ""

    if node_type == "TestMethod":
        method = method or node_name
        if node_id:
            out["method_id"] = node_id
    elif node_type == "TestProject":
        project = project or node_name
        if node_id:
            out["project_id"] = node_id

    if project:
        out["project_name"] = project
    if method:
        out["method_name"] = method
    if not project and not method:
        kw = str(draft.get("keyword") or node_name or "").strip()
        if kw:
            out["project_name"] = kw
    return out


def resolve_equip_project_by_method_params(
    question: str,
    retrieval_question: str = "",
    *,
    node_type: str = "",
    node_name: str = "",
    node_id: str = "",
    plan_keyword: str = "",
    params_draft: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    draft = params_draft or {}
    text = f"{question}\n{retrieval_question}"
    method_kw = (
        str(draft.get("method_keyword") or draft.get("method_name") or "").strip()
        or extract_method_standard_keyword(text)
        or (node_name if node_type == "TestMethod" else "")
        or str(plan_keyword or "").strip()
    )
    out: Dict[str, Any] = {"method_keyword": method_kw}
    if node_type == "TestMethod" and node_id:
        out["method_id"] = node_id
    elif str(draft.get("method_id") or "").strip():
        out["method_id"] = str(draft.get("method_id")).strip()
    kws = expand_method_standard_keywords(method_kw)
    if kws:
        out["keyword_list"] = kws
    return out
