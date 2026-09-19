"""PDDL 质量校验与 LLM 输出块提取。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .pddl_parser import parse_pddl_bundle
from .pddl_strips import normalize_requirements_strips, validate_strips_subset


@dataclass
class PddlBundle:
    domain: str
    problem: str
    rationale: str
    raw: str


def extract_pddl_blocks(text: str) -> PddlBundle:
    raw = text or ""
    domain = _extract_codeblock(raw, "pddl-domain") or _extract_codeblock(raw, "lisp") or ""
    problem = _extract_codeblock(raw, "pddl-problem") or ""
    if not domain:
        m = re.search(r"```pddl-domain\s*([\s\S]*?)```", raw, re.I)
        if m:
            domain = m.group(1).strip()
    if not problem:
        m = re.search(r"```pddl-problem\s*([\s\S]*?)```", raw, re.I)
        if m:
            problem = m.group(1).strip()
    domain = normalize_requirements_strips(domain)
    rationale = ""
    m = re.search(r"规划说明[：:]\s*([\s\S]{0,200})", raw)
    if m:
        rationale = m.group(1).strip()[:150]
    return PddlBundle(domain=domain, problem=problem, rationale=rationale, raw=raw)


def _extract_codeblock(text: str, lang: str) -> str:
    m = re.search(rf"```\s*{re.escape(lang)}\s*([\s\S]*?)```", text, re.I)
    return m.group(1).strip() if m else ""


def validate_pddl_quality(domain: str, problem: str, method_ids: List[str]) -> List[str]:
    errors: List[str] = []
    if not domain or not problem:
        errors.append("缺少 pddl-domain 或 pddl-problem 代码块")
        return errors
    if re.search(r"done-n\d|step\d", domain + problem, re.I):
        errors.append("禁止使用 done-n1 / step1 等序号谓词")
    if domain.count(":action") < 4:
        errors.append("domain 中 :action 过少（需≥4，含分支/备用/维修等）")
    if "(or " not in domain and domain.count(":action") < 6:
        errors.append("需至少一个 (or ...) 分支前提，或≥6 个互斥/可选动作")
    if ":goal" not in problem:
        errors.append("problem 缺少 :goal")
    errors.extend(validate_strips_subset(domain, problem))
    return errors
