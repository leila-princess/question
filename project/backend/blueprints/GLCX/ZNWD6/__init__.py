"""
ZNWD6 智能问答蓝图：本体驱动 + 高召回 τ + LLM 判别 binding + ZNWD4 执行层。

入口：POST /api/znwd6/chat/stream

与 ZNWD5 隔离：不 patch ZNWD4；通过 initial_plan 注入路由结果。
"""

from __future__ import annotations

import json
import logging
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context
from openai import OpenAI

from blueprints.GLCX.ZNWD4.answer_correction import answer_feedback_enabled, validate_feedback_payload
from blueprints.GLCX.ZNWD4.memory_store import get_memory_store, init_memory_store
from blueprints.GLCX.ZNWD4.routing_telemetry import log_answer_feedback
from blueprints.GLCX.ZNWD4.source_table_bridge import hydrate_source_table_payload
from blueprints.GLCX.ZNWD4.znwd4_langchain_routing import ROUTE_PLAN_INTERNAL
from blueprints.GLCX.ZNWD4.znwd4_thinking_emit import emit_milestone, instant_thinking_enabled, sanitize_thinking_for_sse
from blueprints.GLCX.ZNWD4.route_lock_experiment import apply_route_lock_experiment_patch

apply_route_lock_experiment_patch()

logger = logging.getLogger(__name__)

znwd6_bp = Blueprint("znwd6", __name__, url_prefix="/api/znwd6")


class _Neo4jEncoder(json.JSONEncoder):
    def default(self, o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)


def _sse_pack(event_type: str, evt_data: dict) -> str:
    return (
        f"event: {event_type}\ndata: "
        f"{json.dumps(evt_data, ensure_ascii=False, cls=_Neo4jEncoder)}\n\n"
    )


@znwd6_bp.before_app_request
def _ensure_znwd6_memory():
    from flask import current_app

    redis_client = current_app.config.get("REDIS_CLIENT")
    if redis_client and get_memory_store() is None:
        init_memory_store(
            redis_client,
            current_app.config.get("REDIS_MEMORY_TTL", 1800),
            max_messages=int(current_app.config.get("REDIS_MEMORY_MAX_MESSAGES", 10)),
            compress_trigger=int(current_app.config.get("REDIS_MEMORY_COMPRESS_AFTER", 8)),
            keep_recent=int(current_app.config.get("REDIS_MEMORY_KEEP_RECENT", 4)),
        )


@znwd6_bp.route("/chat/stream", methods=["POST", "OPTIONS"])
def chat_stream():
    if request.method == "OPTIONS":
        return "", 200

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    session_id = (data.get("sessionId") or "").strip() or str(uuid.uuid4())
    user_id = (data.get("userId") or request.headers.get("X-User-Id") or "anonymous").strip()
    llm_model_key = (data.get("llmModelKey") or data.get("llm_model_key") or "").strip()
    route_clarify = data.get("routeClarify") if isinstance(data.get("routeClarify"), dict) else None
    entity_clarify = data.get("entityClarify") if isinstance(data.get("entityClarify"), dict) else None
    answer_correction = data.get("answerCorrection") if isinstance(data.get("answerCorrection"), dict) else None

    logger.info("[ZNWD6][入口] question=%r session_id=%r", question, session_id)

    def generate():
        from flask import current_app

        yield ": ZNWD6-instant\n\n"
        yield ":" + (" " * 2048) + "\n\n"
        if instant_thinking_enabled():
            yield _sse_pack("thinking_start", {})
            boot_think = emit_milestone("已收到您的问题，ZNWD6 正在分析…", kind="system")
            yield _sse_pack(boot_think[0], boot_think[1])

        from blueprints.XTGL.MXGL.routes import resolve_llm_model

        from .pipeline import stream_znwd6_chat

        default_client = current_app.config.get("OPENAI_CLIENT")
        model_config = resolve_llm_model(llm_model_key, user_id)
        model = model_config.get("model_name") or current_app.config.get("OPENAI_MODEL")
        raw_base = (model_config.get("api_url") or "").strip()
        raw_key = (model_config.get("api_key") or "").strip()
        default_base = (current_app.config.get("OPENAI_BASE_URL") or "").strip()
        default_key = (current_app.config.get("OPENAI_API_KEY") or "").strip()
        model_base_url = raw_base or default_base
        model_api_key = raw_key if llm_model_key else (raw_key or default_key)

        openai_client = default_client
        try:
            if model_base_url:
                client_kwargs = {"base_url": model_base_url.rstrip("/"), "max_retries": 0}
                if model_api_key:
                    client_kwargs["api_key"] = model_api_key
                else:
                    client_kwargs["api_key"] = "EMPTY"
                    client_kwargs["default_headers"] = {"Authorization": ""}
                openai_client = OpenAI(**client_kwargs)
        except Exception as e:
            logger.warning("[ZNWD6] 客户端初始化失败，回退默认: %s", e)
            openai_client = default_client

        neo4j_driver = current_app.config.get("NEO4J_DRIVER")
        chroma_collection = current_app.config.get("CHROMA_COLLECTION_OBJ")
        max_tokens_limit = int(current_app.config.get("MAX_CONTEXT_TOKENS", 8192))
        llm_limits = {
            "context_window": int(model_config.get("context_window") or max_tokens_limit),
            "max_output_tokens": int(model_config.get("max_output_tokens") or 4096),
        }

        full_answer = ""
        skip_instant = instant_thinking_enabled()
        from blueprints.GLCX.ZNWD4.routing_context import (
            begin_llm_call_tracking,
            clear_routing_llm_context,
            log_llm_call_summary,
            set_routing_llm_context,
        )
        from blueprints.GLCX.ZNWD4.route_lock_resolver import begin_route_lock_request_cache, clear_route_lock_request_cache

        set_routing_llm_context(openai_client, model, llm_limits)
        begin_route_lock_request_cache()
        begin_llm_call_tracking()
        try:
            for event_type, evt_data in stream_znwd6_chat(
                openai_client,
                model,
                question,
                user_id=user_id,
                session_id=session_id,
                neo4j_driver=neo4j_driver,
                chroma_collection=chroma_collection,
                llm_limits=llm_limits,
                skip_instant_thinking=skip_instant,
                route_clarify=route_clarify,
                answer_correction=answer_correction,
                entity_clarify=entity_clarify,
            ):
                if event_type == ROUTE_PLAN_INTERNAL:
                    continue
                if skip_instant and event_type == "thinking_start":
                    continue
                if skip_instant and event_type == "thinking":
                    boot_marker = str(evt_data.get("content") or "")
                    if boot_marker.startswith("已收到您的问题"):
                        continue
                if event_type == "thinking" and isinstance(evt_data, dict):
                    cleaned = sanitize_thinking_for_sse(str(evt_data.get("content") or ""))
                    if not cleaned:
                        continue
                    evt_data = {**evt_data, "content": cleaned}
                    if "kind" not in evt_data:
                        evt_data = {**evt_data, "kind": "system"}
                if event_type == "done" and isinstance(evt_data, dict) and evt_data.get("fullThinking"):
                    evt_data = {
                        **evt_data,
                        "fullThinking": sanitize_thinking_for_sse(str(evt_data.get("fullThinking") or "")),
                    }
                yield _sse_pack(event_type, evt_data if isinstance(evt_data, dict) else {})
                if event_type == "answer":
                    full_answer += evt_data.get("content", "")
        except Exception as e:
            logger.exception("[ZNWD6] stream failed: %s", e)
            from blueprints.GLCX.ZNWD4.znwd4_llm_errors import is_llm_unavailable_error

            if is_llm_unavailable_error(e):
                msg = "大模型服务暂时不可用，请稍后重试。"
            else:
                msg = f"问答流水线异常：{type(e).__name__}: {e}"
            yield _sse_pack("error", {"message": msg})
        finally:
            log_llm_call_summary(question)
            clear_route_lock_request_cache()
            clear_routing_llm_context()

        logger.info("[ZNWD6][完成] full_answer长度=%d", len(full_answer))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@znwd6_bp.route("/feedback", methods=["POST", "OPTIONS"])
def answer_feedback():
    if request.method == "OPTIONS":
        return "", 200
    if not answer_feedback_enabled():
        return jsonify({"ok": False, "error": "answer feedback disabled"}), 403
    data = request.get_json(silent=True) or {}
    ok, err = validate_feedback_payload(data)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    feedback_id = log_answer_feedback(
        turn_id=str(data.get("turnId") or ""),
        routing_event_id=str(data.get("routingEventId") or ""),
        rating=str(data.get("rating") or "negative").strip().lower(),
        feedback_kind=str(data.get("feedbackKind") or ""),
        question=str(data.get("originalQuestion") or data.get("question") or ""),
        predicted_route_id=str(data.get("predictedRouteId") or ""),
        predicted_slots=data.get("predictedSlots") if isinstance(data.get("predictedSlots"), dict) else {},
        corrected_route_id=str(data.get("correctedRouteId") or ""),
        corrected_slots=data.get("correctedSlots") if isinstance(data.get("correctedSlots"), dict) else {},
        comment=str(data.get("comment") or ""),
        answer_preview=str(data.get("answerPreview") or ""),
        rerun_requested=bool(data.get("rerun")),
        user_id=str(data.get("userId") or request.headers.get("X-User-Id") or "anonymous"),
        session_id=str(data.get("sessionId") or ""),
    )
    return jsonify({"ok": True, "feedbackId": feedback_id})


@znwd6_bp.route("/index/status", methods=["GET"])
def index_status():
    from .config import ROUTING_FIX_REV
    from .ontology_loader import load_ontology

    ont = load_ontology()
    graph_count = 0
    doc_count = 0
    zvec_ok = False
    backend = "none"
    try:
        from blueprints.GLCX.ZNWD5.zvec_index_manager import get_zvec_manager, zvec_available

        mgr = get_zvec_manager()
        mgr.open(read_only=True)
        graph_count = mgr.graph_count()
        doc_count = mgr.doc_count()
        zvec_ok = zvec_available()
        backend = "zvec" if mgr.use_zvec else "fallback"
    except Exception as exc:
        logger.warning("[ZNWD6] index status: %s", exc)

    return jsonify(
        {
            "engine": "znwd6",
            "zvec_available": zvec_ok,
            "backend": backend,
            "graph_entities_count": graph_count,
            "documents_count": doc_count,
            "ontology_templates": len(ont.get("templates") or {}),
            "ontology_node_labels": ont.get("node_labels") or [],
            "routing_fix_rev": ROUTING_FIX_REV,
        }
    )


@znwd6_bp.route("/ontology", methods=["GET"])
def ontology_info():
    from .ontology_loader import load_ontology

    ont = load_ontology()
    return jsonify({"ok": True, "ontology": ont})


@znwd6_bp.route("/guided/schema", methods=["GET", "OPTIONS"])
def guided_schema():
    if request.method == "OPTIONS":
        return "", 200
    from .entity_search import load_guided_schema

    return jsonify({"ok": True, "data": load_guided_schema()})


def _znwd6_stream_response(question: str, *, stream_fn, stream_kwargs: dict):
    """Shared SSE wrapper for ZNWD6 chat / guided execute."""

    def generate():
        from flask import current_app

        yield ": ZNWD6-instant\n\n"
        yield ":" + (" " * 2048) + "\n\n"
        if instant_thinking_enabled():
            yield _sse_pack("thinking_start", {})
            boot_think = emit_milestone("已收到您的问题，ZNWD6 正在分析…", kind="system")
            yield _sse_pack(boot_think[0], boot_think[1])

        from blueprints.XTGL.MXGL.routes import resolve_llm_model

        default_client = current_app.config.get("OPENAI_CLIENT")
        user_id = stream_kwargs.get("user_id") or "anonymous"
        llm_model_key = stream_kwargs.pop("llm_model_key", "")
        model_config = resolve_llm_model(llm_model_key, user_id)
        model = model_config.get("model_name") or current_app.config.get("OPENAI_MODEL")
        raw_base = (model_config.get("api_url") or "").strip()
        raw_key = (model_config.get("api_key") or "").strip()
        default_base = (current_app.config.get("OPENAI_BASE_URL") or "").strip()
        default_key = (current_app.config.get("OPENAI_API_KEY") or "").strip()
        model_base_url = raw_base or default_base
        model_api_key = raw_key if llm_model_key else (raw_key or default_key)

        openai_client = default_client
        try:
            if model_base_url:
                client_kwargs = {"base_url": model_base_url.rstrip("/"), "max_retries": 0}
                if model_api_key:
                    client_kwargs["api_key"] = model_api_key
                else:
                    client_kwargs["api_key"] = "EMPTY"
                    client_kwargs["default_headers"] = {"Authorization": ""}
                openai_client = OpenAI(**client_kwargs)
        except Exception as e:
            logger.warning("[ZNWD6] 客户端初始化失败，回退默认: %s", e)
            openai_client = default_client

        neo4j_driver = current_app.config.get("NEO4J_DRIVER")
        chroma_collection = current_app.config.get("CHROMA_COLLECTION_OBJ")
        max_tokens_limit = int(current_app.config.get("MAX_CONTEXT_TOKENS", 8192))
        llm_limits = {
            "context_window": int(model_config.get("context_window") or max_tokens_limit),
            "max_output_tokens": int(model_config.get("max_output_tokens") or 4096),
        }

        full_answer = ""
        skip_instant = instant_thinking_enabled()
        from blueprints.GLCX.ZNWD4.routing_context import (
            begin_llm_call_tracking,
            clear_routing_llm_context,
            log_llm_call_summary,
            set_routing_llm_context,
        )
        from blueprints.GLCX.ZNWD4.route_lock_resolver import begin_route_lock_request_cache, clear_route_lock_request_cache

        set_routing_llm_context(openai_client, model, llm_limits)
        begin_route_lock_request_cache()
        begin_llm_call_tracking()
        try:
            for event_type, evt_data in stream_fn(
                openai_client,
                model,
                question,
                user_id=stream_kwargs.get("user_id"),
                session_id=stream_kwargs.get("session_id"),
                neo4j_driver=neo4j_driver,
                chroma_collection=chroma_collection,
                llm_limits=llm_limits,
                **{k: v for k, v in stream_kwargs.items() if k not in ("user_id", "session_id")},
            ):
                if event_type == ROUTE_PLAN_INTERNAL:
                    continue
                if skip_instant and event_type == "thinking_start":
                    continue
                if skip_instant and event_type == "thinking":
                    boot_marker = str(evt_data.get("content") or "")
                    if boot_marker.startswith("已收到您的问题"):
                        continue
                if event_type == "thinking" and isinstance(evt_data, dict):
                    cleaned = sanitize_thinking_for_sse(str(evt_data.get("content") or ""))
                    if not cleaned:
                        continue
                    evt_data = {**evt_data, "content": cleaned}
                    if "kind" not in evt_data:
                        evt_data = {**evt_data, "kind": "system"}
                if event_type == "done" and isinstance(evt_data, dict) and evt_data.get("fullThinking"):
                    evt_data = {
                        **evt_data,
                        "fullThinking": sanitize_thinking_for_sse(str(evt_data.get("fullThinking") or "")),
                    }
                yield _sse_pack(event_type, evt_data if isinstance(evt_data, dict) else {})
                if event_type == "answer":
                    full_answer += evt_data.get("content", "")
        except Exception as e:
            logger.exception("[ZNWD6] stream failed: %s", e)
            from blueprints.GLCX.ZNWD4.znwd4_llm_errors import is_llm_unavailable_error

            if is_llm_unavailable_error(e):
                msg = "大模型服务暂时不可用，请稍后重试。"
            else:
                msg = f"问答流水线异常：{type(e).__name__}: {e}"
            yield _sse_pack("error", {"message": msg})
        finally:
            log_llm_call_summary(question)
            clear_route_lock_request_cache()
            clear_routing_llm_context()

        logger.info("[ZNWD6][完成] full_answer长度=%d", len(full_answer))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@znwd6_bp.route("/guided/execute", methods=["POST", "OPTIONS"])
def guided_execute():
    if request.method == "OPTIONS":
        return "", 200

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    template_id = (data.get("templateId") or data.get("template_id") or "").strip()
    bindings = data.get("bindings") if isinstance(data.get("bindings"), list) else []
    goal_type = (data.get("goalType") or data.get("goal_type") or "").strip()
    intent_id = (data.get("intentId") or data.get("intent_id") or "").strip()

    if not question:
        return jsonify({"error": "问题不能为空"}), 400
    if not template_id:
        return jsonify({"error": "templateId 不能为空"}), 400

    session_id = (data.get("sessionId") or "").strip() or str(uuid.uuid4())
    user_id = (data.get("userId") or request.headers.get("X-User-Id") or "anonymous").strip()
    llm_model_key = (data.get("llmModelKey") or data.get("llm_model_key") or "").strip()

    logger.info(
        "[ZNWD6][guided/execute] template=%r question=%r bindings=%d",
        template_id,
        question,
        len(bindings),
    )

    from .guided_execute import stream_znwd6_guided_execute

    return _znwd6_stream_response(
        question,
        stream_fn=stream_znwd6_guided_execute,
        stream_kwargs={
            "template_id": template_id,
            "bindings": bindings,
            "user_id": user_id,
            "session_id": session_id,
            "llm_model_key": llm_model_key,
            "goal_type": goal_type or None,
            "intent_id": intent_id or None,
        },
    )


@znwd6_bp.route("/entities/search", methods=["GET", "OPTIONS"])
def entities_search():
    if request.method == "OPTIONS":
        return "", 200
    from flask import current_app

    from .entity_search import search_entities

    q = (request.args.get("q") or request.args.get("query") or "").strip()
    node_type = (request.args.get("type") or request.args.get("nodeType") or "").strip()
    try:
        limit = int(request.args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    driver = current_app.config.get("NEO4J_DRIVER")
    items = search_entities(q, node_type=node_type, driver=driver, limit=limit)
    return jsonify({"ok": True, "items": items})


@znwd6_bp.route("/entities/enum", methods=["GET", "OPTIONS"])
def entities_enum():
    if request.method == "OPTIONS":
        return "", 200
    from flask import current_app

    from .entity_search import enum_entities

    node_type = (request.args.get("type") or request.args.get("nodeType") or "").strip()
    try:
        limit = int(request.args.get("limit") or 200)
    except (TypeError, ValueError):
        limit = 200
    driver = current_app.config.get("NEO4J_DRIVER")
    items = enum_entities(node_type=node_type, driver=driver, limit=limit)
    return jsonify({"ok": True, "items": items})


@znwd6_bp.route("/source/table", methods=["POST", "OPTIONS"])
def znwd6_source_table():
    if request.method == "OPTIONS":
        return "", 200

    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or data.get("ssyqTableKeyword") or "").strip()
    user_question = (data.get("userQuestion") or data.get("question") or "").strip()
    neo4j_hits = data.get("neo4jHits") or data.get("nodes") or []
    if not isinstance(neo4j_hits, list):
        neo4j_hits = []

    try:
        payload = hydrate_source_table_payload(keyword, neo4j_hits, user_question=user_question)
        return jsonify({"success": True, "data": payload})
    except Exception as e:
        logger.error("[ZNWD6] source_table failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@znwd6_bp.route("/memory", methods=["GET", "DELETE", "OPTIONS"])
def memory_ops():
    if request.method == "OPTIONS":
        return "", 200
    mem_store = get_memory_store()
    user_id = request.args.get("userId", request.headers.get("X-User-Id", "anonymous"))
    if request.method == "DELETE":
        session_id = request.args.get("sessionId")
        if mem_store:
            mem_store.delete(user_id, session_id)
        return jsonify({"success": True})
    all_memory = mem_store.get_all_for_user(user_id) if mem_store else {}
    return jsonify({"userId": user_id, "memories": all_memory, "count": len(all_memory)})
