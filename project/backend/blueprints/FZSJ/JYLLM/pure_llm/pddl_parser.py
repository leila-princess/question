"""
STRIPS PDDL 子集解析（domain + problem）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

Expr = Union[str, tuple]  # 'atom' | ('and', ...) | ('or', ...) | ('not', ...)


@dataclass
class Action:
    name: str
    parameters: List[str] = field(default_factory=list)
    precondition: Expr = ()
    effect: Expr = ()
    comment: str = ""


@dataclass
class PddlModel:
    domain_name: str
    problem_name: str
    predicates: List[str] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    init: Set[str] = field(default_factory=set)
    goal: Expr = ()


def _strip_comments(text: str) -> str:
    return re.sub(r";[^\n]*", "", text)


def _extract_balanced_paren(text: str, start: int) -> Optional[str]:
    """从 text[start] 处的 '(' 起提取完整 S 表达式。"""
    if start >= len(text) or text[start] != "(":
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _field_sexpr(body: str, field: str) -> Optional[str]:
    m = re.search(rf":{field}\b", body, re.I)
    if not m:
        return None
    rest = body[m.end() :].lstrip()
    if rest.startswith("()"):
        return "()"
    if rest and rest[0] == "(":
        return _extract_balanced_paren(rest, 0)
    # 单 token 效果/前提
    tok = re.match(r"([\w-]+)", rest)
    return tok.group(1) if tok else None


def _tokenize_sexpr(s: str) -> List[str]:
    s = s.replace("\n", " ")
    tokens = []
    i = 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        if s[i] in "()":
            tokens.append(s[i])
            i += 1
        else:
            j = i
            while j < len(s) and s[j] not in " \n()":
                j += 1
            tokens.append(s[i:j])
            i = j
    return tokens


def _parse_expr(tokens: List[str], pos: int) -> Tuple[Expr, int]:
    if pos >= len(tokens):
        return (), pos
    if tokens[pos] != "(":
        return tokens[pos], pos + 1
    if pos + 1 >= len(tokens):
        return (), pos
    head = tokens[pos + 1].lower()
    pos += 2
    if head == "and":
        parts = []
        while pos < len(tokens) and tokens[pos] != ")":
            ex, pos = _parse_expr(tokens, pos)
            parts.append(ex)
        return ("and", *parts), pos + 1
    if head == "or":
        parts = []
        while pos < len(tokens) and tokens[pos] != ")":
            ex, pos = _parse_expr(tokens, pos)
            parts.append(ex)
        return ("or", *parts), pos + 1
    if head == "not":
        ex, pos = _parse_expr(tokens, pos)
        return ("not", ex), pos + 1
  # atom
    parts = [head]
    while pos < len(tokens) and tokens[pos] != ")":
        parts.append(tokens[pos])
        pos += 1
    return " ".join(parts) if len(parts) == 1 else tuple(parts), pos + 1


def _expr_atoms(expr: Expr) -> Set[str]:
    if expr is None or expr == ():
        return set()
    if isinstance(expr, str):
        return {expr}
    if isinstance(expr, tuple):
        if expr[0] in ("and", "or"):
            out: Set[str] = set()
            for p in expr[1:]:
                out |= _expr_atoms(p)
            return out
        if expr[0] == "not" and len(expr) > 1:
            return _expr_atoms(expr[1])
        return {" ".join(str(x) for x in expr)}
    return set()


def parse_pddl_bundle(domain_text: str, problem_text: str) -> PddlModel:
    domain_text = _strip_comments(domain_text)
    problem_text = _strip_comments(problem_text)
    dm = re.search(r"\(define\s+\(domain\s+([\w-]+)", domain_text, re.I)
    pr = re.search(r"\(define\s+\(problem\s+([\w-]+)", problem_text, re.I)
    model = PddlModel(
        domain_name=dm.group(1) if dm else "domain",
        problem_name=pr.group(1) if pr else "problem",
    )

    pred_block = re.search(r"\(:predicates([\s\S]*?)\)\s*(?:\(:action|\Z)", domain_text, re.I)
    if pred_block:
        tokens = _tokenize_sexpr("(" + pred_block.group(1).strip().strip(")") + ")")
        _, pos = _parse_expr(tokens, 0)
        # collect atoms from predicate list
        inner = pred_block.group(1)
        for m in re.finditer(r"\(([\w-]+)", inner):
            model.predicates.append(m.group(1))

    for am in re.finditer(
        r"\(:action\s+([\w-]+)([\s\S]*?)\)\s*(?=\(:action|\(:|\Z)",
        domain_text,
        re.I,
    ):
        name = am.group(1)
        body = am.group(2)
        act = Action(name=name)
        pre_s = _field_sexpr(body, "precondition")
        if pre_s and pre_s != "()":
            tok = _tokenize_sexpr(pre_s)
            act.precondition, _ = _parse_expr(tok, 0)
        eff_s = _field_sexpr(body, "effect")
        if eff_s:
            tok = _tokenize_sexpr(eff_s)
            act.effect, _ = _parse_expr(tok, 0)
        model.actions.append(act)

    init_m = re.search(r"\(:init([\s\S]*?)\)\s*\(:goal", problem_text, re.I)
    if init_m:
        init_body = init_m.group(1).strip()
        if init_body:
            wrapped = init_body
            if not re.match(r"\(\s*and\b", init_body, re.I):
                wrapped = f"(and {init_body})"
            tok = _tokenize_sexpr(wrapped)
            init_expr, _ = _parse_expr(tok, 0)
            model.init = _collect_init_atoms(init_expr)

    goal_m = re.search(r"\(:goal\b", problem_text, re.I)
    if goal_m:
        rest = problem_text[goal_m.end() :].lstrip()
        goal_s = _extract_balanced_paren(rest, 0) if rest.startswith("(") else rest.split(")")[0]
        if goal_s:
            tok = _tokenize_sexpr(goal_s)
            model.goal, _ = _parse_expr(tok, 0)

    return model


def _collect_init_atoms(expr: Expr) -> Set[str]:
    """将 :init 展平为零元谓词名（仅单 token，忽略含参数的元组）。"""
    out: Set[str] = set()
    if expr is None or expr == ():
        return out
    if isinstance(expr, str):
        if expr and expr not in ("and", "or", "not"):
            out.add(expr)
        return out
    if isinstance(expr, tuple) and expr:
        if expr[0] in ("and", "or"):
            for p in expr[1:]:
                out |= _collect_init_atoms(p)
            return out
        if expr[0] == "not" and len(expr) > 1:
            return out
        if len(expr) == 1:
            out.add(str(expr[0]))
        # 多 token 如 (uav-selected ZB_0001) — STRIPS 子集不支持，跳过
    return out


def expr_to_str(expr: Expr) -> str:
    if isinstance(expr, str):
        return f"({expr})" if expr and not expr.startswith("(") else expr
    if isinstance(expr, tuple) and expr:
        if expr[0] in ("and", "or"):
            inner = " ".join(expr_to_str(x) for x in expr[1:])
            return f"({expr[0]} {inner})"
        if expr[0] == "not":
            return f"(not {expr_to_str(expr[1])})"
        return "(" + " ".join(str(x) for x in expr) + ")"
    return "()"
