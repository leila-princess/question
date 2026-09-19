"""
实验挂载：将 question_route_lock.resolve_route_lock 替换为 route_lock_resolver 统一入口。
"""

from __future__ import annotations

import logging
import sys

from .znwd4_experiment_config import znwd4_route_lock_mode

logger = logging.getLogger(__name__)

_PATCHED = False

_REPATCH_MODULES = (
    "blueprints.GLCX.ZNWD4.znwd4_langchain_routing",
    "blueprints.GLCX.ZNWD4.znwd4_probe_hybrid_routing",
    "blueprints.GLCX.ZNWD4.znwd4_mlp_routing",
    "blueprints.GLCX.ZNWD4.plan_reconcile",
    "blueprints.GLCX.ZNWD4.pcsr_assembly",
    "blueprints.GLCX.ZNWD4.route_validator",
)


def apply_route_lock_experiment_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from . import question_route_lock as qrl
    from .route_lock_resolver import resolve_route_lock as unified_resolve

    qrl.resolve_route_lock = unified_resolve  # type: ignore[method-assign]
    _PATCHED = True

    for mod_name in _REPATCH_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "resolve_route_lock"):
            setattr(mod, "resolve_route_lock", unified_resolve)

    logger.info("[ZNWD4][route_lock_experiment] patched resolve_route_lock mode=%s", znwd4_route_lock_mode())
