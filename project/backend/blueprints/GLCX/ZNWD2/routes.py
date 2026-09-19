"""
ZNWD2 HTTP 路由。

入口：POST /api/znwd2/chat/stream
"""

from __future__ import annotations

import json
import logging
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context
from openai import OpenAI

from .pipeline import stream_znwd2_chat
from .znwd2_thinking_emit import emit_milestone, instant_thinking_enabled, sanitize_thinking_for_sse
from ...XTGL.MXGL.routes import resolve_llm_model

logger = logging.getLogger(__name__)

znwd2_bp = Blueprint("znwd2", __name__, url_prefix="/api/znwd2")


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


@znwd2_bp.route("/chat/stream", methods=["POST", "OPTIONS"])
def chat_stream():
    if request.method == "OPTIONS":
        return "", 200

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    user_id = (data.get("userId") or request.headers.get("X-User-Id") or "anonymous").strip()
    llm_model_key = (data.get("llmModelKey") or data.get("llm_model_key") or "").strip()

    def generate():
        from flask import current_app

        yield ": znwd2-instant\n\n"
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
            logger.warning("[ZNWD2] 客户端初始化失败，回退默认: %s", e)
            openai_client = default_client

        neo4j_driver = current_app.config.get("NEO4J_DRIVER")
        skip_instant = instant_thinking_enabled()

        try:
            for event_type, evt_data in stream_znwd2_chat(
                openai_client,
                model,
                question,
                neo4j_driver=neo4j_driver,
                skip_instant_thinking=skip_instant,
            ):
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
        except Exception as e:
            logger.exception("[ZNWD2] stream failed: %s", e)
            yield _sse_pack("error", {"message": f"问答流水线异常：{type(e).__name__}: {e}"})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@znwd2_bp.route("/chat", methods=["POST", "OPTIONS"])
def chat_sync():
    """同步问答（非流式，便于调试）。"""
    if request.method == "OPTIONS":
        return "", 200

    from flask import current_app

    from .main import answer

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    user_id = (data.get("userId") or request.headers.get("X-User-Id") or "anonymous").strip()
    llm_model_key = (data.get("llmModelKey") or data.get("llm_model_key") or "").strip()

    default_client = current_app.config.get("OPENAI_CLIENT")
    model_config = resolve_llm_model(llm_model_key, user_id)
    model = model_config.get("model_name") or current_app.config.get("OPENAI_MODEL")

    text = answer(
        question,
        driver=current_app.config.get("NEO4J_DRIVER"),
        client=default_client,
        model=model,
    )
    return jsonify({"answer": text, "question": question})
