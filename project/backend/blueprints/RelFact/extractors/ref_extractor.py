"""标准引用链抽取（规范性引用列表 + 正文引用句）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

STD_CODE_RE = re.compile(
    r"(?:GB|GB/T|GJB|GJB/Z|IEC|ISO)\s*[/／]?\s*[\d.]+(?:\.\d+)*\s*[—\-–]\s*\d{4}",
    re.I,
)

CITE_BODY_RE = re.compile(
    r"按\s*(GB[/／]?T?\s*[\d.]+(?:\.\d+)*)\s*中\s*([\d.]+)\s*的?规定"
    r"|见\s*(GB[/／]?T?\s*[\d.]+(?:\.\d+)*)\s*([\d.]+)?"
    r"|应符合\s*(GJB\s*[\d.]+)\s*第?\s*([\d.]+)\s*条?",
    re.I,
)

CLAUSE_IN_TITLE_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def _norm_std(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def extract_normative_ref_list(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for m in STD_CODE_RE.finditer(text):
        code = _norm_std(m.group(0))
        if code in seen:
            continue
        seen.add(code)
        out.append({"target_std": code, "target_clause": "", "ref_type": "normative_list"})
    return out


def extract_refs_from_text(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    seen = set()

    for m in CITE_BODY_RE.finditer(text):
        groups = [g for g in m.groups() if g]
        if len(groups) >= 2:
            target_std = _norm_std(groups[0])
            target_clause = str(groups[1]).strip()
        elif len(groups) == 1:
            target_std = _norm_std(groups[0])
            target_clause = ""
        else:
            continue
        key = (target_std, target_clause)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "target_std": target_std,
            "target_clause": target_clause,
            "ref_text": m.group(0)[:200],
            "ref_type": "normative",
        })

    out.extend(extract_normative_ref_list(text))
    return out
