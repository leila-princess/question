"""
将检索到的 TestMethod 注入 PDDL domain（谓词/前提与 LLM 域对齐）并重规划。
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .pddl_strips import ensure_predicates_in_domain, pick_method_precondition
from .pyperplan_runner import solve_pddl
from .retrieval_bundle import RetrievalBundle
from .strips_planner import PlanResult


def _action_in_domain(domain: str, action_name: str) -> bool:
    return bool(re.search(rf"\(:action\s+{re.escape(action_name)}\b", domain, re.I))


def _method_pred_name(method_id: str) -> str:
    safe = re.sub(r"[^\w-]", "_", method_id)[:40]
    return f"method-applied-{safe}"


def enrich_domain_with_methods(
    domain_text: str,
    problem_text: str,
    bundle: RetrievalBundle,
    existing_plan_actions: List[str],
) -> Tuple[str, str, List[str]]:
    """返回 enriched_domain, enriched_problem, added_action_names"""
    pending: List[tuple] = []

    for m in bundle.neo_methods:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        safe = re.sub(r"[^\w-]", "_", mid)[:40]
        aname = f"method-{safe}"
        pred = _method_pred_name(mid)
        if _action_in_domain(domain_text, aname):
            continue
        pending.append((m, aname, pred))

    if not pending:
        return domain_text, problem_text, []

    new_preds = [p for _, _, p in pending]
    enriched_domain = ensure_predicates_in_domain(domain_text, new_preds)
    pre_cond = pick_method_precondition(enriched_domain)
    extra = "\n"
    added: List[str] = []
    for m, aname, pred in pending:
        added.append(aname)
        comment = str(m.get("name", ""))[:60].replace("\n", " ")
        extra += f"""
  (:action {aname}
    :parameters () ; {comment}
    :precondition {pre_cond}
    :effect ({pred}))
"""

    idx = enriched_domain.rfind(")")
    enriched_domain = (
        enriched_domain[:idx] + extra + enriched_domain[idx:] if idx > 0 else enriched_domain + extra
    )

    # 不修改 :goal，避免注入不可达目标；method-* 为可选扩展动作
    return enriched_domain, problem_text, added


def replan_enriched(
    domain_text: str,
    problem_text: str,
    bundle: RetrievalBundle,
    work_dir,
) -> Tuple[str, str, PlanResult, List[str]]:
    enriched_dom, enriched_prob, added = enrich_domain_with_methods(
        domain_text, problem_text, bundle, []
    )
    if work_dir:
        from pathlib import Path

        p = Path(work_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "06_full_plan_domain.pddl").write_text(enriched_dom, encoding="utf-8")
        (p / "06_full_plan_problem.pddl").write_text(enriched_prob, encoding="utf-8")
    result = solve_pddl(enriched_dom, enriched_prob, work_dir)
    return enriched_dom, enriched_prob, result, added
