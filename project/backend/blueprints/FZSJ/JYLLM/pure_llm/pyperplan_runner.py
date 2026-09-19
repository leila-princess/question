"""调用 pyperplan 求解 PDDL；失败时回退 strips_planner。"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .pddl_parser import PddlModel, parse_pddl_bundle
from .strips_planner import PlanResult, solve_strips

logger = logging.getLogger(__name__)


def solve_pddl(
    domain_text: str,
    problem_text: str,
    work_dir: Optional[Path] = None,
) -> PlanResult:
    model = parse_pddl_bundle(domain_text, problem_text)
    if work_dir:
        (work_dir / "domain.pddl").write_text(domain_text, encoding="utf-8")
        (work_dir / "problem.pddl").write_text(problem_text, encoding="utf-8")

    pp = _try_pyperplan(domain_text, problem_text, work_dir)
    if pp and pp.goal_reached:
        return pp

    logger.info("[pure_llm] pyperplan unavailable or failed, using Python STRIPS")
    py = solve_strips(model)
    py.solver = "python" if not pp else f"python_fallback_after_{pp.solver}"
    return py


def _try_pyperplan(domain_text: str, problem_text: str, work_dir: Optional[Path]) -> Optional[PlanResult]:
    try:
        import pyperplan  # noqa: F401
    except ImportError:
        return None

    td = work_dir or Path(tempfile.mkdtemp(prefix="pure_llm_pp_"))
    td.mkdir(parents=True, exist_ok=True)
    dom = td / "domain.pddl"
    prob = td / "problem.pddl"
    dom.write_text(domain_text, encoding="utf-8")
    prob.write_text(problem_text, encoding="utf-8")
    plan_file = td / "plan"

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyperplan", str(dom), str(prob)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(td),
        )
        if proc.returncode != 0:
            logger.warning("[pure_llm] pyperplan rc=%s stderr=%s", proc.returncode, proc.stderr[:500])
            return PlanResult(plan=[], solver="pyperplan_failed", expanded=0, goal_reached=False)

        # pyperplan 输出 plan 到 stdout 或文件
        plan_lines = []
        if plan_file.is_file():
            plan_lines = [ln.strip() for ln in plan_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        elif proc.stdout:
            plan_lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip() and not ln.startswith(";")]

        actions = []
        for ln in plan_lines:
            parts = ln.split()
            if len(parts) >= 2:
                actions.append(parts[1].strip(")").strip("("))

        return PlanResult(
            plan=actions,
            solver="pyperplan",
            expanded=0,
            goal_reached=bool(actions),
        )
    except Exception as e:
        logger.warning("[pure_llm] pyperplan run error: %s", e)
        return None
