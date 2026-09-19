"""
纯 Python STRIPS 正向搜索（pyperplan 不可用时的回退）。
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .pddl_parser import Action, PddlModel, expr_to_str, _expr_atoms


@dataclass
class PlanResult:
    plan: List[str]
    solver: str
    expanded: int
    goal_reached: bool
    alternatives: Dict[str, List[str]] = field(default_factory=dict)
    final_state: Set[str] = field(default_factory=set)


def _holds(expr, state: Set[str]) -> bool:
    if expr is None or expr == ():
        return True
    if isinstance(expr, str):
        return expr in state
    if isinstance(expr, tuple) and expr:
        op = expr[0]
        if op == "and":
            return all(_holds(expr[i], state) for i in range(1, len(expr)))
        if op == "or":
            return any(_holds(expr[i], state) for i in range(1, len(expr)))
        if op == "not" and len(expr) > 1:
            inner = expr[1]
            if isinstance(inner, str):
                return inner not in state
            return not _holds(inner, state)
        atom = " ".join(str(x) for x in expr)
        return atom in state
    return False


def _apply_effect(effect, state: Set[str]) -> Set[str]:
    s = set(state)
    if isinstance(effect, str):
        s.add(effect)
        return s
    if isinstance(effect, tuple) and effect:
        if effect[0] == "and":
            for i in range(1, len(effect)):
                s = _apply_effect(effect[i], s)
            return s
        if effect[0] == "not" and len(effect) > 1:
            inner = effect[1]
            if isinstance(inner, str) and inner in s:
                s.remove(inner)
            return s
        atom = " ".join(str(x) for x in effect)
        s.add(atom)
    return s


def _goal_holds(goal, state: Set[str]) -> bool:
    return _holds(goal, state)


def _heuristic(state: Set[str], goal_atoms: Set[str]) -> int:
    return len(goal_atoms - state)


def solve_strips(model: PddlModel, max_expansions: int = 5000) -> PlanResult:
    goal_atoms = _expr_atoms(model.goal)
    start = frozenset(model.init)
    if _goal_holds(model.goal, set(start)):
        return PlanResult(plan=[], solver="python", expanded=0, goal_reached=True, final_state=set(start))

    frontier: List[Tuple[int, int, frozenset, List[str]]] = []
    heapq.heappush(frontier, (_heuristic(set(start), goal_atoms), 0, start, []))
    visited: Dict[frozenset, List[str]] = {start: []}
    expanded = 0
    alternatives: Dict[str, List[str]] = {}

    while frontier and expanded < max_expansions:
        _, cost, state, plan = heapq.heappop(frontier)
        state_set = set(state)
        if _goal_holds(model.goal, state_set):
            return PlanResult(
                plan=plan,
                solver="python",
                expanded=expanded,
                goal_reached=True,
                alternatives=alternatives,
                final_state=state_set,
            )
        sk = str(sorted(state_set))
        applicable = []
        for act in model.actions:
            if _holds(act.precondition, state_set):
                applicable.append(act.name)
        if applicable:
            alternatives[sk] = applicable
        for act in model.actions:
            if act.name not in applicable:
                continue
            new_state = frozenset(_apply_effect(act.effect, state_set))
            new_plan = plan + [act.name]
            if new_state in visited and len(visited[new_state]) <= len(new_plan):
                continue
            visited[new_state] = new_plan
            expanded += 1
            h = _heuristic(set(new_state), goal_atoms)
            heapq.heappush(frontier, (len(new_plan) + h, len(new_plan), new_state, new_plan))

    return PlanResult(
        plan=[],
        solver="python",
        expanded=expanded,
        goal_reached=False,
        alternatives=alternatives,
        final_state=set(start),
    )
