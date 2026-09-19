"""
STRIPS 子集：校验、谓词提取、problem goal 合并、domain 补谓词。
"""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from .pddl_parser import PddlModel, parse_pddl_bundle, _extract_balanced_paren, _strip_comments

# 方法动作默认门槛（按优先级，仅选用 domain 中已声明的零元谓词）
_METHOD_PRE_CANDIDATES = (
    ("baseline-done", "env-ready"),
    ("baseline-recorded", "env-ready"),
    ("baseline-done",),
    ("env-ready",),
    ("inspection-ok",),
)

_FORBIDDEN_REQ = (
    "disjunctive-preconditions",
    "typing",
    "negative-preconditions",
    "equality",
    "conditional-effects",
)


def validate_strips_subset(domain: str, problem: str) -> List[str]:
    """校验是否可被内置 STRIPS 规划器求解。"""
    errors: List[str] = []
    text = domain + "\n" + problem
    if "?" in text:
        errors.append("禁止 PDDL 参数 ?x（请用零元谓词，如 (damage-detected)）")
    if re.search(r"\(\s*=\s+", text):
        errors.append("禁止使用 (= ...) 数值/对象相等")
    req_m = re.search(r"\(:requirements\s+([^)]*)\)", domain, re.I)
    if req_m:
        req = req_m.group(1).lower()
        for bad in _FORBIDDEN_REQ:
            if bad in req:
                errors.append(f":requirements 不得包含 :{bad}，仅允许 :strips")
    else:
        errors.append("domain 须声明 (:requirements :strips)")
    if ":strips" not in (req_m.group(1).lower() if req_m else ""):
        errors.append("(:requirements :strips) 为必需")

    for am in re.finditer(r"\(:action\s+([\w-]+)([\s\S]*?)\)\s*(?=\(:action|\(:|\Z)", domain, re.I):
        body = am.group(2)
        if not re.search(r":parameters\s+\(\s*\)", body, re.I):
            errors.append(f"动作 {am.group(1)} 须使用 :parameters ()")

    try:
        model = parse_pddl_bundle(domain, problem)
    except Exception as e:
        errors.append(f"PDDL 解析失败: {e}")
        return errors

    declared = set(model.predicates)
    for act in model.actions:
        for atom in _atoms_in_action(act.precondition) | _atoms_in_action(act.effect):
            name = atom.split()[0] if " " in atom else atom
            if name not in declared and name not in ("and", "or", "not"):
                errors.append(f"动作 {act.name} 使用未声明谓词 ({name})")

    if not model.init:
        errors.append(":init 为空或无法解析（请用 (and (fact-a) (fact-b) ...)）")
    if not model.actions:
        errors.append("domain 无 :action")
    return errors


def _atoms_in_action(expr) -> Set[str]:
    from .pddl_parser import _expr_atoms

    return _expr_atoms(expr)


def list_domain_predicates(domain_text: str) -> List[str]:
    m = re.search(r"\(:predicates([\s\S]*?)\)\s*(?:\(:action|\Z)", domain_text, re.I)
    if not m:
        return []
    return re.findall(r"\(([\w-]+)\)", m.group(1))


def ensure_predicates_in_domain(domain_text: str, preds: List[str]) -> str:
    """在 (:predicates ...) 中补零元谓词。"""
    existing = set(list_domain_predicates(domain_text))
    to_add = [p for p in preds if p not in existing]
    if not to_add:
        return domain_text
    block_m = re.search(r"(\(:predicates)([\s\S]*?)(\))", domain_text, re.I)
    if not block_m:
        return domain_text
    insert = "".join(f" ({p})" for p in to_add)
    return domain_text[: block_m.end(2)] + insert + domain_text[block_m.end(2) :]


def pick_method_precondition(domain_text: str) -> str:
    """为图谱注入的 method-* 动作选择可执行前提（与 domain 谓词对齐）。"""
    preds = set(list_domain_predicates(domain_text))
    for combo in _METHOD_PRE_CANDIDATES:
        if all(p in preds for p in combo):
            if len(combo) == 1:
                return f"({combo[0]})"
            return f"(and {' '.join(f'({p})' for p in combo)})"
    if preds:
        p = sorted(preds)[0]
        return f"({p})"
    return "()"


def merge_goal_atoms(problem_text: str, atoms: List[str]) -> str:
    """向 :goal 的 (and ...) 合并原子，保持 S 表达式合法。"""
    problem_text = _strip_comments(problem_text)
    goal_m = re.search(r"\(:goal\b", problem_text, re.I)
    if not goal_m:
        return problem_text
    rest = problem_text[goal_m.end() :].lstrip()
    goal_s = _extract_balanced_paren(rest, 0)
    if not goal_s:
        return problem_text

    existing = set(re.findall(r"\(([\w-]+)\)", goal_s))
    new_atoms = [a for a in atoms if a not in existing]
    if not new_atoms:
        return problem_text

    extra = " ".join(f"({a})" for a in new_atoms)
    if goal_s.strip().startswith("(and"):
        inner_end = goal_s.rfind(")")
        new_goal = goal_s[:inner_end] + " " + extra + goal_s[inner_end:]
    else:
        new_goal = f"(and {goal_s.strip()} {extra})"

    start = goal_m.start()
    end = goal_m.end() + len(goal_s)
    return problem_text[:start] + f"(:goal {new_goal})" + problem_text[end:]


def normalize_requirements_strips(domain_text: str) -> str:
    """将 :requirements 规范为仅 :strips。"""
    if re.search(r"\(:requirements", domain_text, re.I):
        return re.sub(
            r"\(:requirements\s+[^)]*\)",
            "(:requirements :strips)",
            domain_text,
            count=1,
            flags=re.I,
        )
    return domain_text.replace(
        "(define (domain",
        "(define (domain",
        1,
    ).replace(
        "(:predicates",
        "(:requirements :strips)\n  (:predicates",
        1,
    )
