"""
ZNWD4 智能问答蓝图（独立实现，不 import ZNWD）。

入口：POST /api/znwd4/chat/stream
"""
from __future__ import annotations

# 修复 vLLM tool call arguments 解析容错（治本方案）
import json as _std_json
try:
    import vllm.entrypoints.chat_utils as _vllm_chat_utils
    _orig_loads = _vllm_chat_utils.json.loads
    def _robust_json_loads(s, *a, **kw):
        try:
            return _orig_loads(s, *a, **kw)
        except _std_json.JSONDecodeError as e:
            if "Extra data" in str(e):
                s = (s or "").strip()
                if s.startswith("{"):
                    depth, end = 0, -1
                    for i, ch in enumerate(s):
                        depth += 1 if ch == "{" else (-1 if ch == "}" else 0)
                        if depth == 0:
                            end = i
                            break
                    if end > 0:
                        try: return _orig_loads(s[:end+1], *a, **kw)
                        except: pass
            raise
    _vllm_chat_utils.json.loads = _robust_json_loads
except Exception:
    pass

import json
import logging
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context
from openai import OpenAI

from .memory_store import get_memory_store, init_memory_store
from .route_lock_experiment import apply_route_lock_experiment_patch

apply_route_lock_experiment_patch()

from .pipeline import stream_znwd4_chat
from .source_table_bridge import hydrate_source_table_payload
from .answer_correction import answer_feedback_enabled, validate_feedback_payload
from .routing_telemetry import log_answer_feedback
from .znwd4_langchain_routing import ROUTE_PLAN_INTERNAL
from .znwd4_thinking_emit import emit_milestone, instant_thinking_enabled, sanitize_thinking_for_sse
from ...XTGL.MXGL.routes import resolve_llm_model

logger = logging.getLogger(__name__)

znwd4_bp = Blueprint("znwd4", __name__, url_prefix="/api/znwd4")


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


@znwd4_bp.before_app_request
def _ensure_znwd4_memory():
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


@znwd4_bp.route("/chat/stream", methods=["POST", "OPTIONS"])
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

    logger.info("=" * 80)
    logger.info("[ZNWD4][DEBUG][入口] question=%r session_id=%r user_id=%r llm_model_key=%r answer_correction=%s",
                question, session_id, user_id, llm_model_key, bool(answer_correction))

    def generate():
        from flask import current_app

        # 先刷 SSE 首包，再解析模型配置（避免 resolve_llm_model/DB 阻塞 HTTP 首字节）
        yield ": ZNWD4-instant\n\n"
        yield ":" + (" " * 2048) + "\n\n"
        if instant_thinking_enabled():
            yield _sse_pack("thinking_start", {})
            boot_think = emit_milestone("已收到您的问题，正在分析…", kind="system")
            yield _sse_pack(boot_think[0], boot_think[1])

        default_client = current_app.config.get("OPENAI_CLIENT")
        model_config = resolve_llm_model(llm_model_key, user_id)
        model = model_config.get("model_name") or current_app.config.get("OPENAI_MODEL")
        raw_base = (model_config.get("api_url") or "").strip()
        raw_key = (model_config.get("api_key") or "").strip()
        default_base = (current_app.config.get("OPENAI_BASE_URL") or "").strip()
        default_key = (current_app.config.get("OPENAI_API_KEY") or "").strip()
        model_base_url = raw_base or default_base
        model_api_key = raw_key if llm_model_key else (raw_key or default_key)

        logger.info("[ZNWD4][DEBUG][模型配置] model=%r base_url=%r model_config_keys=%s",
                    model, model_base_url, list(model_config.keys()) if isinstance(model_config, dict) else "N/A")

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
            logger.warning("[ZNWD4] 客户端初始化失败，回退默认: %s", e)
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
        from .routing_context import clear_routing_llm_context, set_routing_llm_context, begin_llm_call_tracking, log_llm_call_summary
        from .route_lock_resolver import begin_route_lock_request_cache, clear_route_lock_request_cache

        set_routing_llm_context(openai_client, model, llm_limits)
        begin_route_lock_request_cache()
        begin_llm_call_tracking()
        try:
            try:
                for event_type, evt_data in stream_znwd4_chat(
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
                logger.exception("[ZNWD4] stream failed: %s", e)
                from .znwd4_llm_errors import is_llm_unavailable_error

                if is_llm_unavailable_error(e):
                    msg = "大模型服务暂时不可用（502/503 等），请稍后重试或检查模型网关。"
                else:
                    msg = f"问答流水线异常：{type(e).__name__}: {e}"
                yield _sse_pack("error", {"message": msg})
        finally:
            log_llm_call_summary(question)
            clear_route_lock_request_cache()
            clear_routing_llm_context()

        logger.info("[ZNWD4][DEBUG][完成] full_answer长度=%d question=%r", len(full_answer), question)
        logger.info("=" * 80)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@znwd4_bp.route("/feedback", methods=["POST", "OPTIONS"])
def answer_feedback():
    """Record post-answer user feedback without triggering rerun."""
    if request.method == "OPTIONS":
        return "", 200

    if not answer_feedback_enabled():
        return jsonify({"ok": False, "error": "answer feedback disabled"}), 403

    data = request.get_json(silent=True) or {}
    ok, err = validate_feedback_payload(data)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400

    session_id = (data.get("sessionId") or "").strip()
    user_id = (data.get("userId") or request.headers.get("X-User-Id") or "anonymous").strip()
    rating = str(data.get("rating") or "negative").strip().lower()
    corrected_slots = data.get("correctedSlots") if isinstance(data.get("correctedSlots"), dict) else {}
    predicted_slots = data.get("predictedSlots") if isinstance(data.get("predictedSlots"), dict) else {}

    feedback_id = log_answer_feedback(
        turn_id=str(data.get("turnId") or ""),
        routing_event_id=str(data.get("routingEventId") or ""),
        rating=rating,
        feedback_kind=str(data.get("feedbackKind") or ""),
        question=str(data.get("originalQuestion") or data.get("question") or ""),
        predicted_route_id=str(data.get("predictedRouteId") or ""),
        predicted_slots=predicted_slots,
        corrected_route_id=str(data.get("correctedRouteId") or ""),
        corrected_slots=corrected_slots,
        comment=str(data.get("comment") or ""),
        answer_preview=str(data.get("answerPreview") or ""),
        rerun_requested=bool(data.get("rerun")),
        user_id=user_id,
        session_id=session_id,
    )
    return jsonify({"ok": True, "feedbackId": feedback_id})


@znwd4_bp.route("/source/table", methods=["POST", "OPTIONS"])
def znwd4_source_table():
    """根据问答 Neo 命中补全「设备×环境×项目×指标」表格（与 SSYQ /search 一致）。"""
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
        logger.error("[ZNWD4] znwd4_source_table failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@znwd4_bp.route("/memory", methods=["GET", "DELETE", "OPTIONS"])
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
