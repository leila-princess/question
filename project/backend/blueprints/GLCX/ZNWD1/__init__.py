"""
ZNWD1 智能问答蓝图（独立实现，不 import ZNWD）。

入口：POST /api/znwd1/chat/stream
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
from .pipeline import stream_znwd1_chat
from .source_table_bridge import hydrate_source_table_payload
from .znwd1_langchain_routing import ROUTE_PLAN_INTERNAL
from .znwd1_thinking_emit import emit_milestone, instant_thinking_enabled, sanitize_thinking_for_sse
from ...XTGL.MXGL.routes import resolve_llm_model

logger = logging.getLogger(__name__)

znwd1_bp = Blueprint("znwd1", __name__, url_prefix="/api/znwd1")


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


@znwd1_bp.before_app_request
def _ensure_znwd1_memory():
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


@znwd1_bp.route("/chat/stream", methods=["POST", "OPTIONS"])
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

    logger.info("=" * 80)
    logger.info("[ZNWD1][DEBUG][入口] question=%r session_id=%r user_id=%r llm_model_key=%r",
                question, session_id, user_id, llm_model_key)

    def generate():
        from flask import current_app

        # 先刷 SSE 首包，再解析模型配置（避免 resolve_llm_model/DB 阻塞 HTTP 首字节）
        yield ": znwd1-instant\n\n"
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

        logger.info("[ZNWD1][DEBUG][模型配置] model=%r base_url=%r model_config_keys=%s",
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
            logger.warning("[ZNWD1] 客户端初始化失败，回退默认: %s", e)
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
        try:
            for event_type, evt_data in stream_znwd1_chat(
                openai_client,
                model,
                question,
                user_id=user_id,
                session_id=session_id,
                neo4j_driver=neo4j_driver,
                chroma_collection=chroma_collection,
                llm_limits=llm_limits,
                skip_instant_thinking=skip_instant,
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
            logger.exception("[ZNWD1] stream failed: %s", e)
            yield _sse_pack("error", {"message": f"问答流水线异常：{type(e).__name__}: {e}"})

        logger.info("[ZNWD1][DEBUG][完成] full_answer长度=%d question=%r", len(full_answer), question)
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


@znwd1_bp.route("/source/table", methods=["POST", "OPTIONS"])
def znwd1_source_table():
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
        logger.error("[ZNWD1] znwd1_source_table failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@znwd1_bp.route("/memory", methods=["GET", "DELETE", "OPTIONS"])
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
