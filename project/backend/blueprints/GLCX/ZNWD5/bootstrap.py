"""
ZNWD5 启动补丁：将 ZNWD4 路由/执行层替换为 Zvec 增强实现。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_patched = False


def apply_znwd5_patches() -> None:
    global _patched
    if _patched:
        return

    from . import executor, keyword_probe

    import blueprints.GLCX.ZNWD4.executor as ex4
    import blueprints.GLCX.ZNWD4.keyword_probe as kp4
    import blueprints.GLCX.ZNWD4.pipeline as pl4
    import blueprints.GLCX.ZNWD4.plan_reconcile as pr4
    import blueprints.GLCX.ZNWD4.playbook_resolver as pbr4
    import blueprints.GLCX.ZNWD4.probe_preflight as pp4
    import blueprints.GLCX.ZNWD4.route_validator as rv4
    import blueprints.GLCX.ZNWD4.znwd4_domain_guard as dg4
    import blueprints.GLCX.ZNWD4.znwd4_tools as tools4

    kp4.probe_keyword = keyword_probe.probe_keyword
    pr4.probe_keyword = keyword_probe.probe_keyword
    rv4.probe_keyword = keyword_probe.probe_keyword
    dg4.probe_keyword = keyword_probe.probe_keyword
    tools4.probe_keyword = keyword_probe.probe_keyword

    ex4.execute_meta_steps = executor.execute_meta_steps
    ex4.iter_meta_steps = executor.iter_meta_steps
    pl4.execute_meta_steps = executor.execute_meta_steps

    import blueprints.GLCX.ZNWD4.routing_dispatcher as rd4

    _orig_stream_auto = rd4.stream_route_plan_auto

    def _znwd5_stream_route_plan_auto(client, model, question, **kwargs):
        from .action_planner_routing import stream_route_plan_znwd5_dispatch

        return stream_route_plan_znwd5_dispatch(_orig_stream_auto, client, model, question, **kwargs)

    rd4.stream_route_plan_auto = _znwd5_stream_route_plan_auto
    pl4.stream_route_plan_auto = rd4.stream_route_plan_auto

    from .embedding_service import embedding_backend
    from .zvec_index_manager import get_zvec_manager

    try:
        mgr = get_zvec_manager()
        mgr.open(read_only=True)
        logger.info(
            "[ZNWD5] Zvec 索引已打开 backend=%s graph=%d docs=%d embed=%s",
            "zvec" if mgr.use_zvec else "fallback",
            mgr.graph_count(),
            mgr.doc_count(),
            embedding_backend(),
        )
    except Exception as exc:
        logger.warning("[ZNWD5] Zvec 索引打开失败（将按需重试）: %s", exc)

    _patched = True
    mode = __import__("os").environ.get("ZNWD5_ROUTING_MODE", "legacy")
    logger.info(
        "[ZNWD5] 补丁已应用：probe=zvec hybrid, doc_engine=%s, routing_mode=%s",
        __import__("os").environ.get("ZNWD5_DOC_ENGINE", "hybrid"),
        mode,
    )
