"""
意图锁统一入口：按 ZNWD4_ROUTE_LOCK_MODE 在规则锁与 LLM 锁之间切换。
供 route_lock_experiment 挂载到 question_route_lock.resolve_route_lock。

单次问答请求内对同一问句缓存结果，避免流水线多阶段重复调用 LLM。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional

from .llm_route_lock import resolve_route_lock_llm
from .question_route_lock import RouteLock, _resolve_route_lock_rules
from .znwd4_experiment_config import route_lock_log_compare, znwd4_route_lock_mode

try:
    from blueprints.GLCX.ZNWD5.faq_lock_allowlist import filter_lock_for_planner_mode
except ImportError:
    def filter_lock_for_planner_mode(lock: Optional[RouteLock]) -> Optional[RouteLock]:
        return lock

logger = logging.getLogger(__name__)

_route_lock_cache_var: ContextVar[Optional[Dict[str, Optional[RouteLock]]]] = ContextVar(
    "znwd4_route_lock_cache",
    default=None,
)


def begin_route_lock_request_cache() -> None:
    """在单次 chat/stream 请求入口调用，合并多阶段重复 resolve。"""
    _route_lock_cache_var.set({})


def clear_route_lock_request_cache() -> None:
    _route_lock_cache_var.set(None)


def _resolve_route_lock_uncached(question: str) -> Optional[RouteLock]:
    mode = znwd4_route_lock_mode()
    if mode == "rules":
        return _resolve_route_lock_rules(question)

    if mode == "llm":
        lock = resolve_route_lock_llm(question)
        if lock:
            return lock
        return _resolve_route_lock_rules(question)

    # hybrid
    rules_lock = _resolve_route_lock_rules(question)
    if route_lock_log_compare():
        llm_lock: Optional[RouteLock] = None
        if rules_lock:
            logger.info(
                "[ZNWD4][route_lock_compare] q=%r rules=%s llm=skipped_rules_hit",
                question[:80],
                rules_lock.route_id,
            )
        else:
            llm_lock = resolve_route_lock_llm(question)
            logger.info(
                "[ZNWD4][route_lock_compare] q=%r rules=%s llm=%s",
                question[:80],
                None,
                llm_lock.route_id if llm_lock else None,
            )
        if rules_lock:
            return rules_lock
        return llm_lock

    if rules_lock:
        return rules_lock
    return resolve_route_lock_llm(question)


def resolve_route_lock(question: str) -> Optional[RouteLock]:
    try:
        q = str(question or "").strip()
    except Exception:
        logger.warning("[ZNWD4][route_lock] invalid question type: %r", type(question))
        return None
    if not q:
        return None

    cache = _route_lock_cache_var.get()
    if cache is not None:
        if q in cache:
            return cache[q]
        try:
            lock = _resolve_route_lock_uncached(q)
        except Exception as exc:
            logger.warning("[ZNWD4][route_lock] resolve failed q=%r: %s", q[:120], exc)
            try:
                lock = _resolve_route_lock_rules(q)
            except Exception:
                lock = None
        cache[q] = filter_lock_for_planner_mode(lock)
        return cache[q]

    try:
        lock = _resolve_route_lock_uncached(q)
    except Exception as exc:
        logger.warning("[ZNWD4][route_lock] resolve failed q=%r: %s", q[:120], exc)
        try:
            lock = _resolve_route_lock_rules(q)
        except Exception:
            lock = None
    return filter_lock_for_planner_mode(lock)


def safe_resolve_route_lock(question: Any) -> Optional[RouteLock]:
    """路由热路径入口：强制 str 问句，经统一 resolver，失败返回 None 不抛错。"""
    try:
        q = str(question or "").strip()
    except Exception:
        return None
    if not q:
        return None
    try:
        return resolve_route_lock(q)
    except Exception as exc:
        logger.warning("[ZNWD4][route_lock] safe resolve failed q=%r: %s", q[:120], exc)
        return None
