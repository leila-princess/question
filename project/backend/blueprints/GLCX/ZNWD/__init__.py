"""
智能问答 (ZNWD) 蓝图

现网主入口：POST /api/znwd/chat/stream_v4（默认 pipeline v6）。
端到端说明见同目录 FLOW.md。

遗留接口 /chat/stream、/stream_v2、/stream_v3 默认关闭（ZNWD_LEGACY_STREAM=1 可开）。
"""
import json
import logging
import os
import time
from datetime import datetime, date
import uuid
from openai import OpenAI

from flask import Blueprint, Response, request, current_app, jsonify, stream_with_context
import redis
from .services_v4 import llm_stream_single

class _Neo4jEncoder(json.JSONEncoder):
    def default(self, o):
        if hasattr(o, 'isoformat'):
            return o.isoformat()
        if isinstance(o, (bytes, bytearray)):
            return o.decode('utf-8', errors='replace')
        return str(o)

from .models import (
    save_conversation,
    get_history_list,
    get_history_detail,
    get_history_by_session,
    delete_history,
    clear_all_history,
    get_znwd_user_interest,
    save_znwd_user_interest,
)
# [遗留] 仅 ZNWD_LEGACY_STREAM=1 时下方 v1/v2/v3 路由会 import
_LEGACY_STREAM = os.environ.get("ZNWD_LEGACY_STREAM", "0").strip().lower() in (
    "1",
    "true",
    "yes",
)
if _LEGACY_STREAM:
    from .services import (
        neo4j_fulltext_search,
        chroma_semantic_search,
        build_context,
        llm_stream_response,
    )
    from .services_v2 import llm_stream_response_with_thinking
    from .services_v3 import llm_stream_with_queries, generate_conversation_summary
from .services_v4 import (
    llm_stream_single,
    estimate_context_tokens,
    TOOL_DEFINITIONS,
    query_neo4j,
    search_knowledge_base,
)
from .services_v5 import llm_stream_single_v5
from .services_v6 import llm_stream_single_v6
from .znwd_preflight import http_early_thinking_enabled
from .prompts_v4 import SUMMARY_MERGE_PROMPT
from .source_table import hydrate_source_table_payload
from .memory_store import init_memory_store, get_memory_store
from ...XTGL.MXGL.routes import resolve_llm_model, get_llm_models

logger = logging.getLogger(__name__)

znwd_bp = Blueprint('znwd', __name__, url_prefix='/api/znwd')

# ---------- 用户兴趣（一级设备类型 / 环境）缓存 ----------
_interest_user_cache: dict = {}
_INTEREST_USER_TTL = 60.0
_interest_options_cache: dict = {
    "t": 0.0,
    "data": {"equipmentTypes": [], "equipments": [], "environments": []},
}
_INTEREST_OPTIONS_TTL = 120.0
_znwd_v5_entity_warm_done = False


def _znwd_resolve_interest_equipment(driver, raw_ids: list) -> dict:
    """
    将用户勾选的一级设备类型（EquipmentType.Type 文本）、ZBLX_*、或遗留 ZB_* 解析为检索加权用集合。
    """
    raw_ids = [str(x).strip() for x in (raw_ids or []) if str(x).strip()]
    equipment_type_node_ids: list = []
    equipment_instance_ids: list = []
    primary_labels: list = []
    seen_z: set = set()
    seen_e: set = set()
    for r in raw_ids:
        if r.startswith("ZB_"):
            if r not in seen_e:
                seen_e.add(r)
                equipment_instance_ids.append(r)
        elif r.startswith("ZBLX_"):
            if r not in seen_z:
                seen_z.add(r)
                equipment_type_node_ids.append(r)
        else:
            primary_labels.append(r)
    if driver and primary_labels:
        from utils.neo4j_flgl_status import flgl_status_cypher_param

        params = dict(flgl_status_cypher_param())
        params["labels"] = list(dict.fromkeys(primary_labels))
        try:
            with driver.session() as sess:
                q = (
                    "MATCH (et:EquipmentType) WHERE et.Status IN $flgl_enabled_status AND ("
                    " toString(coalesce(et.Type, '')) IN $labels OR toString(coalesce(et.Id, et.id, '')) IN $labels"
                    ") RETURN DISTINCT coalesce(et.Id, et.id) AS id LIMIT 500"
                )
                for rec in sess.run(q, params):
                    did = str(rec.get("id") or "").strip()
                    if did and did not in seen_z:
                        seen_z.add(did)
                        equipment_type_node_ids.append(did)
        except Exception as e:
            logger.warning("_znwd_resolve_interest_equipment: %s", e)
    return {
        "equipment_type_node_ids": equipment_type_node_ids,
        "equipment_instance_ids": equipment_instance_ids,
        "primary_equipment_type_labels": list(dict.fromkeys(primary_labels)),
    }


def _znwd_interest_rows_for_user(user_id: str) -> dict:
    """供检索加权：一级设备类型、展开后的 EquipmentType.Id、环境。"""
    uid = (user_id or "").strip() or "anonymous"
    now = time.time()
    hit = _interest_user_cache.get(uid)
    if hit and now - hit[0] < _INTEREST_USER_TTL:
        return hit[1]
    raw = get_znwd_user_interest(uid)
    driver = current_app.config.get("NEO4J_DRIVER")
    raw_types = list(raw.get("equipmentTypeIds") or raw.get("equipmentIds") or [])
    resolved = _znwd_resolve_interest_equipment(driver, raw_types)
    shaped = {
        "equipment_ids": raw_types,
        "equipment_type_node_ids": resolved["equipment_type_node_ids"],
        "equipment_instance_ids": resolved["equipment_instance_ids"],
        "primary_equipment_type_labels": resolved["primary_equipment_type_labels"],
        "environment_ids": list(raw.get("environmentIds") or []),
    }
    _interest_user_cache[uid] = (now, shaped)
    return shaped


def _invalidate_znwd_interest_user_cache(user_id: str) -> None:
    _interest_user_cache.pop((user_id or "").strip() or "anonymous", None)


def znwd_fetch_interest_options(neo_driver):
    from utils.neo4j_flgl_status import flgl_status_cypher_param

    params = flgl_status_cypher_param()
    equipment_types: list = []
    environments = []
    if not neo_driver:
        return {"equipmentTypes": equipment_types, "equipments": equipment_types, "environments": environments}
    try:
        with neo_driver.session() as sess:
            q1 = (
                "MATCH (et:EquipmentType) WHERE et.Status IN $flgl_enabled_status "
                "AND coalesce(et.Type, '') <> '' "
                "RETURN DISTINCT trim(toString(et.Type)) AS t ORDER BY t LIMIT 200"
            )
            for r in sess.run(q1, params):
                t = str(r.get("t") or "").strip()
                if not t:
                    continue
                equipment_types.append({"id": t, "name": t})
            q2 = (
                "MATCH (env:Environment) WHERE env.Status IN $flgl_enabled_status "
                "RETURN DISTINCT coalesce(env.Id, env.id) AS id, coalesce(env.Name, env.name) AS name ORDER BY name LIMIT 800"
            )
            for r in sess.run(q2, params):
                did = str(r.get("id") or "").strip()
                if not did:
                    continue
                nm = str(r.get("name") or "").strip()
                environments.append({"id": did, "name": nm or did})
    except Exception as e:
        logger.error("znwd_fetch_interest_options: %s", e, exc_info=True)
    return {
        "equipmentTypes": equipment_types,
        "equipments": equipment_types,
        "environments": environments,
    }


@znwd_bp.route('/user/interest/options', methods=['GET', 'OPTIONS'])
def znwd_user_interest_options():
    if request.method == 'OPTIONS':
        return '', 200
    driver = current_app.config.get('NEO4J_DRIVER')
    now = time.time()
    if (
        now - float(_interest_options_cache.get("t") or 0) < _INTEREST_OPTIONS_TTL
        and (
            _interest_options_cache.get("data", {}).get("equipmentTypes")
            or _interest_options_cache.get("data", {}).get("environments")
        )
    ):
        return jsonify(_interest_options_cache["data"])
    payload = znwd_fetch_interest_options(driver)
    _interest_options_cache["t"] = now
    _interest_options_cache["data"] = payload
    return jsonify(payload)


@znwd_bp.route('/user/interest', methods=['GET', 'POST', 'OPTIONS'])
def znwd_user_interest():
    if request.method == 'OPTIONS':
        return '', 200
    uid = (request.args.get('userId') or request.headers.get('X-User-Id') or 'anonymous').strip() or 'anonymous'
    if request.method == 'GET':
        return jsonify(get_znwd_user_interest(uid))
    data = request.get_json(silent=True) or {}
    uid = (data.get('userId') or request.headers.get('X-User-Id') or uid or 'anonymous').strip() or 'anonymous'
    et = (
        data.get("equipmentTypeIds")
        or data.get("equipment_type_ids")
        or data.get("equipmentIds")
        or data.get("equipment_ids")
        or []
    )
    env = data.get("environmentIds") or data.get("environment_ids") or []
    save_znwd_user_interest(
        uid,
        env if isinstance(env, list) else [],
        equipment_type_ids=et if isinstance(et, list) else [],
    )
    _invalidate_znwd_interest_user_cache(uid)
    return jsonify({'ok': True, 'userId': uid})


def _build_graph_payload(records):
    nodes = []
    edges = []
    node_map = {}
    edge_map = {}

    def upsert_node(raw):
        if raw is None:
            return None
        labels = list(raw.labels) if hasattr(raw, "labels") else []
        props = _to_json_safe({k: v for k, v in raw.items() if v is not None}) if hasattr(raw, "items") else {}
        element_id = getattr(raw, "element_id", None)
        node_id = str(
            element_id
            or props.get("elementId")
            or props.get("id")
            or props.get("uid")
            or props.get("uuid")
            or props.get("code")
            or props.get("name")
            or props.get("title")
            or f"node_{len(node_map) + 1}"
        )
        if node_id not in node_map:
            node_map[node_id] = {
                "id": node_id,
                "label": str(props.get("name") or props.get("title") or node_id),
                "group": labels[0] if labels else "Node",
                "title": ", ".join(labels),
                "properties": {**props, "__labels": labels, "__elementId": element_id}
            }
        return node_id

    for rec in records:
        n = rec.get("n")
        m = rec.get("m")
        r = rec.get("r")
        n_id = upsert_node(n)
        m_id = upsert_node(m)
        if n_id and m_id and r is not None:
            rel_type = getattr(r, "type", "RELATED")
            edge_id = f"{n_id}-{rel_type}-{m_id}"
            if edge_id not in edge_map:
                edge_map[edge_id] = {
                    "id": edge_id,
                    "from": n_id,
                    "to": m_id,
                    "label": rel_type,
                    "properties": _to_json_safe(dict(r.items())) if hasattr(r, "items") else {}
                }

    nodes.extend(node_map.values())
    edges.extend(edge_map.values())
    return {"nodes": nodes, "edges": edges}


def _to_json_safe(value):
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_to_json_safe(v) for v in value]
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', errors='replace')
    return value


@znwd_bp.before_app_request
def _ensure_memory_initialized():
    global _znwd_v5_entity_warm_done
    redis_client = current_app.config.get('REDIS_CLIENT')
    memory_ttl = current_app.config.get('REDIS_MEMORY_TTL', 1800)
    max_msgs = int(current_app.config.get('REDIS_MEMORY_MAX_MESSAGES', 10))
    compress_after = int(current_app.config.get('REDIS_MEMORY_COMPRESS_AFTER', 8))
    keep_recent = int(current_app.config.get('REDIS_MEMORY_KEEP_RECENT', 4))
    if redis_client and get_memory_store() is None:
        init_memory_store(
            redis_client,
            memory_ttl,
            max_messages=max_msgs,
            compress_trigger=compress_after,
            keep_recent=keep_recent,
        )
    if not _znwd_v5_entity_warm_done:
        neo_driver = current_app.config.get('NEO4J_DRIVER')
        if neo_driver:
            try:
                from .services_v5 import warm_v5_entity_name_caches

                warm_v5_entity_name_caches(neo_driver)
                _znwd_v5_entity_warm_done = True
            except Exception as e:
                logger.warning("[ZNWD] V5 实体名称缓存预热失败: %s", e)


# ============================================================
# SSE 流式对话接口
# ============================================================

@znwd_bp.route('/chat/stream', methods=['POST', 'OPTIONS'])
def chat_stream():
    """[遗留 v1] 双路检索 + token 流。现网请用 /chat/stream_v4。"""
    if request.method == 'OPTIONS':
        return '', 200
    if not _LEGACY_STREAM:
        return (
            jsonify(
                {
                    "error": "遗留接口已关闭",
                    "use": "/api/znwd/chat/stream_v4",
                    "pipelineVersion": "v6",
                }
            ),
            410,
        )

    data = request.get_json()
    question = data.get('question', '').strip()
    if not question:
        return jsonify({'error': '问题不能为空'}), 400

    neo4j_driver = current_app.config.get('NEO4J_DRIVER')
    openai_client = current_app.config.get('OPENAI_CLIENT')
    model = current_app.config.get('OPENAI_MODEL', 'qwen-plus')
    chroma_collection = current_app.config.get('CHROMA_COLLECTION_OBJ')

    def generate():
        thinking_steps = []
        full_answer = ''

        def send_event(event_type: str, data: dict):
            return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, cls=_Neo4jEncoder)}\n\n"

        yield send_event('step', {
            'type': 'neo4j_search',
            'message': '正在图数据库中查找相关节点...',
            'status': 'loading'
        })

        neo4j_results = neo4j_fulltext_search(neo4j_driver, question, limit=10)

        neo4j_sample = []
        for node in neo4j_results[:3]:
            labels = ', '.join(node.get('labels', []))
            props = node.get('properties', {})
            name = props.get('name', props.get('title', str(props)[:30]))
            neo4j_sample.append(f"{labels}: {name}")

        neo4j_display_results = []
        for node in neo4j_results:
            labels = ', '.join(node.get('labels', []))
            props = node.get('properties', {})
            neo4j_display_results.append({
                'labels': labels,
                'properties': {k: v for k, v in props.items() if k != 'id'}
            })

        neo4j_step = {
            'type': 'neo4j_search',
            'message': f'图数据库检索完成，找到 {len(neo4j_results)} 个相关节点',
            'status': 'done',
            'hits': len(neo4j_results),
            'sample': neo4j_sample,
            'results': neo4j_display_results
        }
        thinking_steps.append(neo4j_step)
        yield send_event('step', neo4j_step)

        yield send_event('step', {
            'type': 'chroma_search',
            'message': '正在知识库中检索相关标准...',
            'status': 'loading'
        })

        matched_chunks, full_standards = chroma_semantic_search(chroma_collection, question)

        chroma_display_chunks = []
        for chunk in matched_chunks:
            chroma_display_chunks.append({
                'source': chunk.get('source', '未知'),
                'doc_id': chunk.get('doc_id', ''),
                'distance': round(chunk.get('distance', 0), 4) if chunk.get('distance') is not None else None,
                'preview': chunk.get('content', '')[:200]
            })

        chroma_display_standards = []
        for std in full_standards:
            chroma_display_standards.append({
                'doc_id': std['doc_id'],
                'title': std['title'],
                'chunk_count': std['chunk_count'],
                'content': std['content']
            })

        chroma_step = {
            'type': 'chroma_search',
            'message': f'知识库检索完成，找到 {len(full_standards)} 个相关标准',
            'status': 'done',
            'chunks': len(matched_chunks),
            'sources': list(set(c.get('source', '未知') for c in matched_chunks)),
            'matchedChunks': chroma_display_chunks,
            'fullStandards': chroma_display_standards
        }
        thinking_steps.append(chroma_step)
        yield send_event('step', chroma_step)

        total_hits = len(neo4j_results) + len(full_standards)
        fusion_step = {
            'type': 'fusion',
            'message': f'融合双路检索结果，共得到 {total_hits} 条相关信息',
            'status': 'done',
            'totalHits': total_hits,
            'neo4jHits': len(neo4j_results),
            'chromaHits': len(full_standards)
        }
        thinking_steps.append(fusion_step)
        yield send_event('step', fusion_step)

        generate_step = {
            'type': 'generating',
            'message': '正在基于检索结果生成答案...',
            'status': 'loading'
        }
        thinking_steps.append(generate_step)
        yield send_event('step', generate_step)

        neo4j_context, chroma_context = build_context(neo4j_results, full_standards)

        for token in llm_stream_response(openai_client, model, question, neo4j_context, chroma_context):
            full_answer += token
            yield send_event('token', {'content': token})

        thinking_steps[-1] = {
            'type': 'generating',
            'message': '答案生成完毕',
            'status': 'done'
        }
        yield send_event('step', thinking_steps[-1])

        history_id = save_history(question, full_answer, thinking_steps)

        yield send_event('done', {
            'historyId': history_id,
            'fullAnswer': full_answer
        })

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@znwd_bp.route('/chat/stream_v2', methods=['POST', 'OPTIONS'])
def chat_stream_v2():
    """[遗留 v2] 带 thinking 的流式对话。现网请用 /chat/stream_v4。"""
    if request.method == 'OPTIONS':
        return '', 200
    if not _LEGACY_STREAM:
        return (
            jsonify(
                {
                    "error": "遗留接口已关闭",
                    "use": "/api/znwd/chat/stream_v4",
                    "pipelineVersion": "v6",
                }
            ),
            410,
        )

    data = request.get_json()
    question = data.get('question', '').strip()
    if not question:
        return jsonify({'error': '问题不能为空'}), 400

    neo4j_driver = current_app.config.get('NEO4J_DRIVER')
    openai_client = current_app.config.get('OPENAI_CLIENT')
    model = current_app.config.get('OPENAI_MODEL', 'qwen-plus')
    chroma_collection = current_app.config.get('CHROMA_COLLECTION_OBJ')

    def generate():
        thinking_steps = []
        full_thinking = ''
        full_answer = ''

        def send_event(event_type: str, data: dict):
            return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, cls=_Neo4jEncoder)}\n\n"

        yield send_event('step', {
            'type': 'neo4j_search',
            'message': '正在图数据库中查找相关节点...',
            'status': 'loading'
        })

        neo4j_results = neo4j_fulltext_search(neo4j_driver, question, limit=10)

        neo4j_sample = []
        for node in neo4j_results[:3]:
            labels = ', '.join(node.get('labels', []))
            props = node.get('properties', {})
            name = props.get('name', props.get('title', str(props)[:30]))
            neo4j_sample.append(f"{labels}: {name}")

        neo4j_display_results = []
        for node in neo4j_results:
            labels = ', '.join(node.get('labels', []))
            props = node.get('properties', {})
            neo4j_display_results.append({
                'labels': labels,
                'properties': {k: v for k, v in props.items() if k != 'id'}
            })

        neo4j_step = {
            'type': 'neo4j_search',
            'message': f'图数据库检索完成，找到 {len(neo4j_results)} 个相关节点',
            'status': 'done',
            'hits': len(neo4j_results),
            'sample': neo4j_sample,
            'results': neo4j_display_results
        }
        thinking_steps.append(neo4j_step)
        yield send_event('step', neo4j_step)

        yield send_event('step', {
            'type': 'chroma_search',
            'message': '正在知识库中检索相关标准...',
            'status': 'loading'
        })

        matched_chunks, full_standards = chroma_semantic_search(chroma_collection, question)

        chroma_display_chunks = []
        for chunk in matched_chunks:
            chroma_display_chunks.append({
                'source': chunk.get('source', '未知'),
                'doc_id': chunk.get('doc_id', ''),
                'distance': round(chunk.get('distance', 0), 4) if chunk.get('distance') is not None else None,
                'preview': chunk.get('content', '')[:200]
            })

        chroma_display_standards = []
        for std in full_standards:
            chroma_display_standards.append({
                'doc_id': std['doc_id'],
                'title': std['title'],
                'chunk_count': std['chunk_count'],
                'content': std['content']
            })

        chroma_step = {
            'type': 'chroma_search',
            'message': f'知识库检索完成，找到 {len(full_standards)} 个相关标准',
            'status': 'done',
            'chunks': len(matched_chunks),
            'sources': list(set(c.get('source', '未知') for c in matched_chunks)),
            'matchedChunks': chroma_display_chunks,
            'fullStandards': chroma_display_standards
        }
        thinking_steps.append(chroma_step)
        yield send_event('step', chroma_step)

        total_hits = len(neo4j_results) + len(full_standards)
        fusion_step = {
            'type': 'fusion',
            'message': f'融合双路检索结果，共得到 {total_hits} 条相关信息',
            'status': 'done',
            'totalHits': total_hits,
            'neo4jHits': len(neo4j_results),
            'chromaHits': len(full_standards)
        }
        thinking_steps.append(fusion_step)
        yield send_event('step', fusion_step)

        generate_step = {
            'type': 'generating',
            'message': '正在基于检索结果生成答案...',
            'status': 'loading'
        }
        thinking_steps.append(generate_step)
        yield send_event('step', generate_step)

        neo4j_context, chroma_context = build_context(neo4j_results, full_standards)

        for event_type, content in llm_stream_response_with_thinking(openai_client, model, question, neo4j_context, chroma_context):
            if event_type == 'thinking_start':
                yield send_event('thinking_start', {})
            elif event_type == 'thinking':
                full_thinking += content
                yield send_event('thinking', {'content': content})
            elif event_type == 'answer_start':
                yield send_event('answer_start', {})
            elif event_type == 'answer':
                full_answer += content
                yield send_event('answer', {'content': content})
            elif event_type == 'error':
                yield send_event('error', {'message': content})

        thinking_steps[-1] = {
            'type': 'generating',
            'message': '答案生成完毕',
            'status': 'done'
        }
        yield send_event('step', thinking_steps[-1])

        # 将思考过程内容添加到thinking_steps中
        if full_thinking:
            thinking_steps.append({
                'type': 'thinking',
                'message': full_thinking,
                'status': 'done'
            })
        history_id = save_history(question, full_answer, thinking_steps)

        yield send_event('done', {
            'historyId': history_id,
            'fullAnswer': full_answer
        })

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


# ============================================================
# 历史记录接口
# ============================================================

@znwd_bp.route('/history', methods=['GET', 'DELETE', 'OPTIONS'])
def history_list():
    """获取历史列表 / 清空历史"""
    if request.method == 'OPTIONS':
        return '', 200

    if request.method == 'DELETE':
        count = clear_all_history()
        return jsonify({'success': True, 'deleted': count})

    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('pageSize', 20))
    result = get_history_list(page, page_size)
    return jsonify(result)


@znwd_bp.route('/history/<int:history_id>', methods=['GET', 'DELETE', 'OPTIONS'])
def history_detail(history_id):
    """获取/删除单条历史"""
    if request.method == 'OPTIONS':
        return '', 200

    if request.method == 'DELETE':
        success = delete_history(history_id)
        return jsonify({'success': success})

    detail = get_history_detail(history_id)
    if not detail:
        return jsonify({'error': '历史记录不存在'}), 404
    return jsonify(detail)


# ============================================================
# 文档管理接口
# ============================================================

@znwd_bp.route('/documents', methods=['GET', 'OPTIONS'])
def list_documents():
    """获取 Chroma 集合中的文档列表"""
    if request.method == 'OPTIONS':
        return '', 200

    try:
        collection = current_app.config.get('CHROMA_COLLECTION_OBJ')
        if not collection:
            return jsonify({'items': [], 'total': 0})

        count = collection.count()
        return jsonify({'items': [], 'total': count})
    except Exception as e:
        logger.error(f"获取文档列表失败: {e}")
        return jsonify({'items': [], 'total': 0, 'error': str(e)})


@znwd_bp.route('/documents/upload', methods=['POST', 'OPTIONS'])
def upload_document():
    if request.method == 'OPTIONS':
        return '', 200

    try:
        if 'file' not in request.files:
            return jsonify({'error': '未提供文件'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'error': '文件名为空'}), 400

        content = file.read().decode('utf-8', errors='ignore')

        collection = current_app.config.get('CHROMA_COLLECTION_OBJ')
        if not collection:
            return jsonify({'error': 'Chroma 未初始化'}), 500

        # 按段落切分
        chunks = [p.strip() for p in content.split('\n\n') if p.strip()]
        if not chunks:
            chunks = [content]

        ids = [f"{file.filename}_{i}" for i in range(len(chunks))]
        metadatas = [{'source': file.filename, 'chunk_index': i} for i in range(len(chunks))]

        collection.add(
            documents=chunks,
            ids=ids,
            metadatas=metadatas
        )

        return jsonify({
            'success': True,
            'filename': file.filename,
            'chunks': len(chunks)
        })

    except Exception as e:
        logger.error(f"文档上传失败: {e}")
        return jsonify({'error': str(e)}), 500


@znwd_bp.route('/chat/stream_v3', methods=['POST', 'OPTIONS'])
def chat_stream_v3():
    """[遗留 v3] 查询生成式流式。现网请用 /chat/stream_v4。"""
    if request.method == 'OPTIONS':
        return '', 200
    if not _LEGACY_STREAM:
        return (
            jsonify(
                {
                    "error": "遗留接口已关闭",
                    "use": "/api/znwd/chat/stream_v4",
                    "pipelineVersion": "v6",
                }
            ),
            410,
        )

    data = request.get_json()
    question = data.get('question', '').strip()
    session_id = data.get('sessionId', '')
    messages = data.get('messages', [])
    user_id = data.get('userId', request.headers.get('X-User-Id', 'anonymous'))

    if not question:
        return jsonify({'error': '问题不能为空'}), 400

    if not session_id:
        session_id = str(uuid.uuid4())

    neo4j_driver = current_app.config.get('NEO4J_DRIVER')
    openai_client = current_app.config.get('OPENAI_CLIENT')
    model = current_app.config.get('OPENAI_MODEL', 'qwen-plus')
    chroma_collection = current_app.config.get('CHROMA_COLLECTION_OBJ')

    mem_store = get_memory_store()
    memory_summary = mem_store.get(user_id, session_id) if mem_store else ''

    def generate():
        thinking_steps = []
        full_thinking = ''
        full_answer = ''

        context_length = sum(len(m.get('content', '')) for m in messages)
        if context_length > 8000:
            ctx_msg = {'message': '当前对话已过长，为保证回答质量，请点击左侧「新对话」保存并开启新会话后继续提问。'}
            event_str = f"event: context_too_long\ndata: {json.dumps(ctx_msg, ensure_ascii=False)}\n\n"
            yield event_str
            return

        def send_event(event_type: str, data: dict):
            event_str = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, cls=_Neo4jEncoder)}\n\n"
            logger.debug(f"SSE sending: {event_type}")
            return event_str

        logger.info(f"Starting SSE stream for question: {question[:50]}...")
        yield send_event('step', {
            'type': 'analyzing',
            'message': '正在分析问题并制定查询策略...',
            'status': 'loading'
        })

        thinking_steps.append({
            'type': 'analyzing',
            'message': '正在分析问题并制定查询策略...',
            'status': 'loading'
        })

        for event_type, content in llm_stream_with_queries(
            openai_client, model, question, neo4j_driver, chroma_collection,
            session_id=session_id, memory_summary=memory_summary
        ):
            if event_type == 'step':
                step_data = content
                yield send_event('step', step_data)

            elif event_type == 'thinking_start':
                yield send_event('thinking_start', {})

            elif event_type == 'thinking':
                full_thinking += content
                yield send_event('thinking', {'content': content})

            elif event_type == 'answer_start':
                yield send_event('answer_start', {})

            elif event_type == 'answer':
                full_answer += content
                yield send_event('answer', {'content': content})

            elif event_type == 'done':
                pass

            elif event_type == 'error':
                yield send_event('error', {'content': content})

        yield send_event('done', {
            'sessionId': session_id,
            'fullAnswer': full_answer,
            'fullThinking': full_thinking,
            'messageCount': len(messages) + 2
        })

        if session_id and full_answer and mem_store:
            import threading
            def _save_summary():
                summary = generate_conversation_summary(openai_client, model, question, full_answer)
                if summary:
                    old_summary = mem_store.get(user_id, session_id)
                    if old_summary:
                        new_summary = old_summary + '\n' + summary
                    else:
                        new_summary = summary
                    mem_store.set(user_id, session_id, new_summary)
            threading.Thread(target=_save_summary, daemon=True).start()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream; charset=utf-8',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
    )


@znwd_bp.route('/chat/session/<session_id>', methods=['GET'])
def get_session_history(session_id: str):
    """获取指定会话的历史记录"""
    try:
        data = get_history_by_session(session_id)
        if not data:
            return jsonify({'error': '会话不存在'}), 404
        return jsonify(data)
    except Exception as e:
        logger.error(f"获取会话历史失败: {e}")
        return jsonify({'error': str(e)}), 500


@znwd_bp.route('/chat/save', methods=['POST'])
def save_current_conversation():
    """手动保存当前对话"""
    try:
        data = request.get_json()
        session_id = data.get('sessionId', '')
        messages = data.get('messages', [])
        user_id = data.get('userId', '')
        user_name = data.get('userName', '')
        
        if not session_id:
            session_id = str(uuid.uuid4())
        
        title = None
        if messages:
            for msg in messages:
                if msg.get('role') == 'user' and msg.get('content'):
                    title = msg['content'][:30]
                    break
        
        history_id = save_conversation(session_id, messages, title, user_id, user_name)
        
        return jsonify({
            'success': True,
            'historyId': history_id,
            'sessionId': session_id
        })
    except Exception as e:
        logger.error(f"保存对话失败: {e}")
        return jsonify({'error': str(e)}), 500


@znwd_bp.route('/memory', methods=['GET', 'DELETE', 'OPTIONS'])
def memory_ops():
    if request.method == 'OPTIONS':
        return '', 200

    mem_store = get_memory_store()
    user_id = request.args.get('userId', request.headers.get('X-User-Id', 'anonymous'))

    if request.method == 'DELETE':
        session_id = request.args.get('sessionId')
        if mem_store:
            mem_store.delete(user_id, session_id)
        return jsonify({'success': True})

    all_memory = mem_store.get_all_for_user(user_id) if mem_store else {}
    return jsonify({
        'userId': user_id,
        'memories': all_memory,
        'count': len(all_memory)
    })


@znwd_bp.route('/graph/neighbors', methods=['POST', 'OPTIONS'])
def graph_neighbors():
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json(silent=True) or {}
    node_id = str(data.get('nodeId', '')).strip()
    element_id = str(data.get('elementId', '')).strip()
    properties = data.get('properties') or {}
    labels = data.get('labels') or []
    limit = int(data.get('limit', 20))
    limit = max(1, min(limit, 50))

    neo4j_driver = current_app.config.get('NEO4J_DRIVER')
    if not neo4j_driver:
        return jsonify({'nodes': [], 'edges': [], 'error': 'Neo4j driver not initialized'}), 500

    candidate_id = (
        properties.get('Id') or properties.get('id') or properties.get('uid') or properties.get('uuid')
        or properties.get('Code') or properties.get('code')
        or properties.get('Name') or properties.get('name')
        or properties.get('Label') or properties.get('title') or node_id
    )
    label = labels[0] if isinstance(labels, list) and labels else None

    try:
        with neo4j_driver.session() as session:
            if element_id:
                neighbors_cypher = """
                MATCH (n)
                WHERE elementId(n) = $element_id
                OPTIONAL MATCH (n)-[r]-(m)
                RETURN DISTINCT n, r, m
                LIMIT $limit
                """
                result = session.run(neighbors_cypher, element_id=element_id, limit=limit)
                records = list(result)
                payload = _to_json_safe(_build_graph_payload(records))
                return jsonify(payload)

            label_clause = f":`{label.replace('`', '')}`" if label else ""
            locate_cypher = f"""
            MATCH (n{label_clause})
            WITH n,
                 coalesce(toString(n.Id), toString(n.id), toString(n.uid), toString(n.uuid),
                          toString(n.Code), toString(n.code), toString(n.Name), toString(n.name),
                          toString(n.Label), toString(n.title), '') AS nid
            WHERE nid = $candidate OR toLower(nid) CONTAINS toLower($candidate)
            RETURN n
            ORDER BY CASE WHEN nid = $candidate THEN 0 ELSE 1 END
            LIMIT 1
            """
            center_record = session.run(locate_cypher, candidate=str(candidate_id)).single()
            if not center_record:
                return jsonify({'nodes': [], 'edges': []})

            center_node = center_record.get("n")
            center_key = None
            if center_node is not None:
                center_props = dict(center_node.items()) if hasattr(center_node, "items") else {}
                center_key = (
                    center_props.get("Id") or center_props.get("id") or center_props.get("uid")
                    or center_props.get("uuid") or center_props.get("Code") or center_props.get("code")
                    or center_props.get("Name") or center_props.get("name")
                    or center_props.get("Label") or center_props.get("title")
                )

            if center_key is None:
                return jsonify({'nodes': [], 'edges': []})

            neighbors_cypher = f"""
            MATCH (n{label_clause})
            WHERE coalesce(toString(n.Id), toString(n.id), toString(n.uid), toString(n.uuid),
                           toString(n.Code), toString(n.code), toString(n.Name), toString(n.name),
                           toString(n.Label), toString(n.title), '') = $center_key
            OPTIONAL MATCH (n)-[r]-(m)
            RETURN DISTINCT n, r, m
            LIMIT $limit
            """
            result = session.run(neighbors_cypher, center_key=str(center_key), limit=limit)
            records = list(result)
        payload = _to_json_safe(_build_graph_payload(records))
        return jsonify(payload)
    except Exception as e:
        logger.error(f"graph_neighbors failed: {e}", exc_info=True)
        return jsonify({'nodes': [], 'edges': [], 'error': str(e)}), 500


def _znwd_safe_minio_object_key(key: str) -> bool:
    k = (key or "").strip()
    if not k or ".." in k or k.startswith(("/", "\\")):
        return False
    if "\x00" in k:
        return False
    return True


def _znwd_parse_minio_source(raw: str, default_bucket: str) -> tuple:
    """解析 minio://bucket/key 或裸 object key（使用默认桶）。"""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return "", ""
    low = raw.lower()
    if low.startswith("minio://"):
        rest = raw[8:]
        if "/" not in rest:
            return (default_bucket or "").strip(), rest.lstrip("/")
        bucket, obj = rest.split("/", 1)
        b = (bucket or "").strip() or (default_bucket or "").strip()
        return b, (obj or "").strip().lstrip("/")
    return (default_bucket or "").strip(), raw.lstrip("/")


@znwd_bp.route('/file/preview', methods=['GET', 'OPTIONS'])
def znwd_file_preview():
    """按向量库元数据中的 minio:// 或 object 路径拉取文件，供前端 PDF 内嵌预览。"""
    if request.method == 'OPTIONS':
        return '', 200
    try:
        from config import Config
    except ImportError:
        from backend.config import Config

    raw = (request.args.get('url') or request.args.get('path') or '').strip()
    if not raw:
        return jsonify({'error': 'missing url or path query'}), 400

    bucket, object_name = _znwd_parse_minio_source(raw, str(Config.MINIO_BUCKET_NAME or ''))
    if not object_name or not _znwd_safe_minio_object_key(object_name):
        return jsonify({'error': 'invalid object path'}), 400
    if not bucket:
        return jsonify({'error': 'bucket not configured'}), 500

    client = Config.MINIO_CLIENT
    if not client:
        return jsonify({'error': 'MinIO client not available'}), 503

    try:
        # 先 stat 再读，便于记录大小
        client.stat_object(bucket, object_name)
    except Exception as e:
        logger.warning("znwd_file_preview stat failed bucket=%s key=%s: %s", bucket, object_name, e)
        return jsonify({'error': 'object not found or inaccessible'}), 404

    try:
        resp = client.get_object(bucket, object_name)
        try:
            data = resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except Exception as e:
        logger.warning("znwd_file_preview get failed: %s", e)
        return jsonify({'error': str(e)}), 500

    ext = os.path.splitext(object_name)[1].lower()
    mime = (
        'application/pdf'
        if ext == '.pdf'
        else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        if ext == '.docx'
        else 'application/msword'
        if ext == '.doc'
        else 'application/octet-stream'
    )
    filename = os.path.basename(object_name) or 'file'
    headers = {
        'Content-Disposition': f'inline; filename="{filename}"',
        'Cache-Control': 'private, max-age=300',
    }
    return Response(data, mimetype=mime, headers=headers)


@znwd_bp.route('/source/table', methods=['POST', 'OPTIONS'])
def znwd_source_table():
    """根据问答上下文中的 Neo4j 命中快照补全「设备×环境×项目×指标」表格（逻辑与 SSYQ /search 一致）。"""
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json(silent=True) or {}
    keyword = (data.get('keyword') or data.get('ssyqTableKeyword') or '').strip()
    user_question = (data.get('userQuestion') or data.get('question') or '').strip()
    neo4j_hits = data.get('neo4jHits') or data.get('nodes') or []
    if not isinstance(neo4j_hits, list):
        neo4j_hits = []

    try:
        payload = hydrate_source_table_payload(keyword, neo4j_hits, user_question=user_question)
        return jsonify({'success': True, 'data': payload})
    except Exception as e:
        logger.error('znwd_source_table failed: %s', e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@znwd_bp.route('/chat/sources', methods=['POST', 'OPTIONS'])
def chat_sources():
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'neo4jResults': [], 'chromaResults': [], 'queryPlan': {}}), 200

    neo4j_driver = current_app.config.get('NEO4J_DRIVER')
    chroma_collection = current_app.config.get('CHROMA_COLLECTION_OBJ')

    try:
        neo4j_results = query_neo4j(neo4j_driver, question, 20)
        chroma_results = search_knowledge_base(chroma_collection, question, top_k=6)
        neo4j_results = _to_json_safe(neo4j_results if isinstance(neo4j_results, list) else [])
        chroma_results = _to_json_safe(chroma_results if isinstance(chroma_results, list) else [])
        return jsonify({
            'neo4jResults': neo4j_results,
            'chromaResults': chroma_results,
            'queryPlan': {
                'neo4j': {'mode': 'fuzzy', 'query': question, 'limit': 20},
                'chroma': {'query': question, 'topK': 6}
            }
        })
    except Exception as e:
        logger.error(f"chat_sources failed: {e}", exc_info=True)
        return jsonify({
            'neo4jResults': [],
            'chromaResults': [],
            'queryPlan': {'neo4j': {'mode': 'fuzzy', 'query': question}, 'chroma': {'query': question}},
            'error': str(e)
        }), 500


# @znwd_bp.route('/chat/stream_v4', methods=['POST', 'OPTIONS'])
# def chat_stream_v4():
#     if request.method == 'OPTIONS':
#         return '', 200

#     data = request.get_json()
#     question = data.get('question', '').strip()
#     session_id = data.get('sessionId', '')
#     user_id = data.get('userId', request.headers.get('X-User-Id', 'anonymous'))

#     if not question:
#         return jsonify({'error': '问题不能为空'}), 400

#     if not session_id:
#         session_id = str(uuid.uuid4())

#     openai_client = current_app.config.get('OPENAI_CLIENT')
#     model = current_app.config.get('OPENAI_MODEL', 'qwen-plus')
#     neo4j_driver = current_app.config.get('NEO4J_DRIVER')
#     chroma_collection = current_app.config.get('CHROMA_COLLECTION_OBJ')
#     max_tokens = current_app.config.get('MAX_CONTEXT_TOKENS', 4000)
#     mem_store = get_memory_store()

#     logger.info(f"[V4] question={question[:50]} session_id={session_id} user_id={user_id}")
#     logger.info(f"[V4] openai_client={bool(openai_client)} model={model} neo4j_driver={bool(neo4j_driver)} chroma_collection={bool(chroma_collection)} mem_store={bool(mem_store)}")

#     summary = ''
#     last_exchange = None
#     if mem_store:
#         mem_data = mem_store.get(user_id, session_id)
#         if mem_data:
#             summary = mem_data.get('summary', '')
#             last_exchange = mem_data.get('last_exchange')

#     context_messages = []
#     if summary:
#         context_messages.append({"role": "system", "content": summary})
#     if last_exchange:
#         if last_exchange.get("user"):
#             context_messages.append({"role": "user", "content": last_exchange["user"]})
#         if last_exchange.get("assistant"):
#             context_messages.append({"role": "assistant", "content": last_exchange["assistant"]})
#     context_messages.append({"role": "user", "content": question})

#     estimated = estimate_context_tokens(context_messages)

#     if estimated > max_tokens:
#         title = last_exchange.get("user", question)[:30] if last_exchange else question[:30]

#         if mem_store:
#             try:
#                 save_conversation_mysql(session_id, summary, last_exchange, title)
#             except Exception as e:
#                 logger.error(f"V4 自动保存对话失败: {e}")
#             mem_store.delete(user_id, session_id)

#         ctx_msg = {'message': '对话过长已自动保存，请开启新对话'}
#         return Response(
#             f"event: error\ndata: {json.dumps(ctx_msg, ensure_ascii=False)}\n\n",
#             mimetype='text/event-stream; charset=utf-8',
#             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
#         )

#     sys_msg = {"role": "system", "content": SYSTEM_PROMPT_SINGLE}
#     api_messages = [sys_msg] + context_messages

#     def generate():
#         try:
#             def send_event(event_type: str, data: dict):
#                 return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, cls=_Neo4jEncoder)}\n\n"

#             output_list = []
#             logger.info("[V4] Starting LLM call...")
#             yield send_event('thinking_start', {})

#             final_content = llm_stream_single(
#                 openai_client, model, api_messages, neo4j_driver, chroma_collection, output_list
#             )
#             logger.info(f"[V4] LLM returned, output_list length: {len(output_list)}, final_content length: {len(final_content)}")

#             full_answer = ''
#             for event_type, payload in output_list:
#                 try:
#                     logger.info(f"[V4] Yielding event: {event_type}, payload_len={len(json.dumps(payload, ensure_ascii=False))}")
#                     if event_type in ('thinking', 'answer'):
#                         content = payload.get('content', '')
#                         if event_type == 'answer':
#                             full_answer += content
#                     yield send_event(event_type, payload)
#                 except Exception as e:
#                     logger.error(f"[V4] Error yielding event {event_type}: {e}")

#             logger.info(f"[V4] Final answer length: {len(full_answer)}")
#             if full_answer:
#                 yield send_event('done', {
#                     'sessionId': session_id,
#                     'fullAnswer': full_answer
#                 })
#             else:
#                 logger.warning("[V4] No full_answer, sending done with empty")
#                 yield send_event('done', {
#                     'sessionId': session_id,
#                     'fullAnswer': ''
#                 })

#             if mem_store and full_answer and final_content:
#                 import threading
#                 def _update_memory():
#                     try:
#                         merge_prompt = SUMMARY_MERGE_PROMPT.format(
#                             old_summary=summary,
#                             user_question=question,
#                             assistant_answer=full_answer
#                         )
#                         resp = openai_client.chat.completions.create(
#                             model=model,
#                             messages=[{"role": "user", "content": merge_prompt}],
#                             temperature=0.3,
#                             max_tokens=300
#                         )
#                         new_summary = (resp.choices[0].message.content or '').strip()
#                         mem_store.update_merge(user_id, session_id, new_summary, question, full_answer)
#                     except Exception as e:
#                         logger.error(f"V4 更新记忆失败: {e}")
#                         try:
#                             mem_store.update_merge(user_id, session_id, full_answer[:200], question, full_answer)
#                         except:
#                             pass
#                 threading.Thread(target=_update_memory, daemon=True).start()

#         except Exception as e:
#             logger.error(f"[V4] Generator exception: {e}", exc_info=True)
#             yield f"event: error\ndata: {json.dumps({'message': f'服务器内部错误: {str(e)}'}, ensure_ascii=False)}\n\n"

#     return Response(
#         stream_with_context(generate()),
#         mimetype='text/event-stream; charset=utf-8',
#         headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
#     )



@znwd_bp.route('/chat/stream_v4', methods=['POST'])
def chat_stream_v4():
    data = request.get_json(silent=True) or {}
    question = data.get('question', '').strip()
    session_id = data.get('sessionId', '')
    user_id = data.get('userId', 'anonymous')
    llm_model_key = (data.get('llmModelKey') or data.get('llm_model_key') or '').strip()
    
    logger.info(
        f"[V4] Received question: {question[:50]}... session_id={session_id} user_id={user_id} "
        f"llmModelKey={llm_model_key!r}"
    )
    
    if not session_id:
        session_id = str(uuid.uuid4())

    memory_store = get_memory_store()
    
    conversation_history = []
    summary = ""
    if memory_store:
        conversation_history = memory_store.get_messages(user_id, session_id)
        summary = memory_store.get_summary(user_id, session_id)
        logger.info(f"[V4] Loaded {len(conversation_history)} messages from Redis, summary length: {len(summary)}")

    # V4：system 提示词在 services_v4 内按阶段注入（检索方案 / 整合回答）
    messages = []
    if summary:
        messages.append({"role": "system", "content": f"历史对话摘要：{summary}"})
    
    for msg in conversation_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    
    messages.append({"role": "user", "content": question})

    default_client = current_app.config.get('OPENAI_CLIENT')
    model_config = resolve_llm_model(llm_model_key, user_id)
    model = model_config.get("model_name") or current_app.config.get('OPENAI_MODEL')
    # 每个请求按解析结果建客户端，实现实时切换
    raw_base = (model_config.get("api_url") or "").strip()
    raw_key = (model_config.get("api_key") or "").strip()
    default_base = (current_app.config.get("OPENAI_BASE_URL") or "").strip()
    default_key = (current_app.config.get("OPENAI_API_KEY") or "").strip()
    model_base_url = raw_base or default_base
    # 指定 llmModelKey 时，优先使用该模型自己的 key；避免误用默认 OPENAI_API_KEY
    if llm_model_key:
        model_api_key = raw_key
    else:
        model_api_key = raw_key or default_key
    openai_client = default_client
    try:
        if model_base_url:
            client_kwargs = {
                "base_url": model_base_url.rstrip("/"),
                "max_retries": 0,
            }
            if model_api_key:
                client_kwargs["api_key"] = model_api_key
            else:
                # 无密钥模型（如未开启鉴权的 vLLM）：显式清空 Authorization，避免网关因伪造 Bearer 值报错
                client_kwargs["api_key"] = "EMPTY"
                client_kwargs["default_headers"] = {"Authorization": ""}
            openai_client = OpenAI(**client_kwargs)
        else:
            logger.warning("[V4] 无 base_url，使用应用启动时的默认 OpenAI 客户端")
    except Exception as e:
        logger.warning(f"[V4] 选中模型客户端初始化失败，回退默认客户端: {e}")
        openai_client = default_client
    neo4j_driver = current_app.config.get('NEO4J_DRIVER')
    chroma_collection = current_app.config.get('CHROMA_COLLECTION_OBJ')
    max_rounds = current_app.config.get('REDIS_MAX_ROUNDS', 3)
    max_tokens = current_app.config.get('REDIS_MAX_TOKENS', 4000)
    max_tokens_limit = current_app.config.get('MAX_CONTEXT_TOKENS', 4000)
    llm_limits = {
        "context_window": int(model_config.get("context_window") or max_tokens_limit or 8192),
        "max_output_tokens": int(model_config.get("max_output_tokens") or 4096),
    }
    max_tokens_limit = min(int(max_tokens_limit), int(llm_limits["context_window"]))
    pipeline_version = (
        (data.get("pipelineVersion") or data.get("pipeline_version") or "").strip().lower()
        or (os.environ.get("ZNWD_PIPELINE_VERSION") or "v6").strip().lower()
    )
    use_v6 = pipeline_version == "v6"
    use_v5 = pipeline_version == "v5"

    estimated_tokens = estimate_context_tokens(messages)
    if estimated_tokens > max_tokens_limit:
        ctx_msg = {'message': '当前对话已接近 Token 限额，为保证回答质量，请点击左侧「新对话」保存当前会话后开启新对话继续提问', 'estimatedTokens': estimated_tokens, 'maxTokens': max_tokens_limit}
        return Response(
            f"event: token_limit\ndata: {json.dumps(ctx_msg, ensure_ascii=False)}\n\n",
            mimetype='text/event-stream; charset=utf-8',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
        )

    try:
        est_i = int(estimated_tokens)
        cw_i = int(llm_limits.get("context_window") or max_tokens_limit or 8192)
        max_out_i = int(llm_limits.get("max_output_tokens") or 4096)
        reserve = 256
        safe_out = max(256, min(max_out_i, cw_i - est_i - reserve))
        if safe_out < max_out_i:
            logger.info(
                "[ZNWD] max_output_tokens capped %s -> %s (estimated_input=%s context_window=%s)",
                max_out_i,
                safe_out,
                est_i,
                cw_i,
            )
        llm_limits["max_output_tokens"] = safe_out
        llm_limits["estimated_input_tokens"] = est_i
    except Exception as e:
        logger.warning("[ZNWD] completion token cap skipped: %s", e)

    user_interest = _znwd_interest_rows_for_user(user_id)

    def generate():
        full_answer = ""
        used_fallback = False
        active_model = model
        active_client = openai_client
        active_model_key = model_config.get("model_key")
        logger.info(
            f"[V4] model_key={model_config.get('model_key')} model={active_model} source={model_config.get('source')}"
        )
        req_llm_key = llm_model_key
        if use_v6 and req_llm_key and req_llm_key != str(model_config.get("model_key") or ""):
            logger.warning(
                "[V6][LLM] 请求 llmModelKey=%s 与解析结果 model_key=%s 不一致，已用解析项（请检查 MXGL 列表是否含该 key）",
                req_llm_key,
                model_config.get("model_key"),
            )
        logger.info(f"[V4] pipeline_version={pipeline_version}")
        logger.info(
            f"[V4] using base_url={model_base_url} api_key_len={len(model_api_key or '')} "
            f"no_auth={not bool(model_api_key)}"
        )
        stream_kwargs: dict = {"llm_limits": llm_limits}
        if use_v5 or use_v6:
            stream_kwargs["user_id"] = user_id
            stream_kwargs["session_id"] = session_id
            stream_kwargs["user_interest"] = user_interest
        if use_v6:
            stream_impl = llm_stream_single_v6
        elif use_v5:
            stream_impl = llm_stream_single_v5
        else:
            stream_impl = llm_stream_single
        if use_v6 and http_early_thinking_enabled():
            _early_step = json.dumps(
                {
                    "type": "intent_judge",
                    "status": "loading",
                    "message": "正在连接知识库…",
                    "phase": "bootstrap",
                },
                ensure_ascii=False,
            )
            yield f"event: step\ndata: {_early_step}\n\n"
        for event_type, evt_data in stream_impl(
            active_client, active_model, messages, neo4j_driver, chroma_collection, **stream_kwargs
        ):
            if event_type == "error" and not full_answer and not used_fallback:
                fallback_client = None
                fallback_model = ""
                fallback_key = ""
                failed_base = (model_base_url or "").strip().rstrip("/").lower()
                for cand in get_llm_models(active_only=True):
                    if cand.get("model_key") == active_model_key:
                        continue
                    cand_model = (cand.get("model_name") or "").strip()
                    cand_base = (cand.get("api_url") or "").strip()
                    cand_key = (cand.get("api_key") or "").strip()
                    if not cand_model or not cand_base:
                        continue
                    if failed_base and cand_base.strip().rstrip("/").lower() == failed_base:
                        continue
                    kwargs = {"base_url": cand_base.rstrip("/"), "max_retries": 0}
                    if cand_key:
                        kwargs["api_key"] = cand_key
                    else:
                        kwargs["api_key"] = "EMPTY"
                        kwargs["default_headers"] = {"Authorization": ""}
                    try:
                        fallback_client = OpenAI(**kwargs)
                        fallback_model = cand_model
                        fallback_key = cand.get("model_key") or ""
                        break
                    except Exception:
                        continue
                if not fallback_client:
                    # 最后兜底：应用默认客户端
                    fallback_client = default_client
                    fallback_model = current_app.config.get("OPENAI_MODEL") or ""
                    fallback_key = "env_default"

                can_fallback = bool(
                    fallback_client
                    and fallback_model
                    and (fallback_model != active_model or fallback_client is not active_client)
                )
                if can_fallback:
                    used_fallback = True
                    logger.warning(f"[V4] 当前模型失败，回退模型: {fallback_model} ({fallback_key})")
                    yield f"event: step\ndata: {json.dumps({'type': 'synthesis', 'status': 'loading', 'message': f'当前模型不可用，正在切换可用模型 {fallback_model} 重试'}, ensure_ascii=False)}\n\n"
                    try:
                        _fb = next(
                            (x for x in get_llm_models(active_only=True) if x.get("model_key") == fallback_key),
                            None,
                        )
                    except Exception:
                        _fb = None
                    fb_limits = (
                        {
                            "context_window": int(_fb.get("context_window") or llm_limits["context_window"]),
                            "max_output_tokens": int(_fb.get("max_output_tokens") or llm_limits["max_output_tokens"]),
                        }
                        if _fb
                        else llm_limits
                    )
                    fb_kwargs = dict(stream_kwargs)
                    fb_kwargs["llm_limits"] = fb_limits
                    for fb_type, fb_data in stream_impl(
                        fallback_client, fallback_model, messages, neo4j_driver, chroma_collection, **fb_kwargs
                    ):
                        yield f"event: {fb_type}\ndata: {json.dumps(fb_data, ensure_ascii=False, default=str)}\n\n"
                        if fb_type == "answer":
                            full_answer += fb_data.get("content", "")
                        elif fb_type == "done":
                            active_model = fallback_model
                            active_client = fallback_client
                            active_model_key = fallback_key
                    continue
                if not full_answer:
                    # 双模型都失败时，仍返回可见流式结果，避免前端长时间等待后空白
                    yield f"event: thinking\ndata: {json.dumps({'content': '模型服务暂不可用，已基于当前检索结果给出简要回复。'}, ensure_ascii=False)}\n\n"
                    yield f"event: answer\ndata: {json.dumps({'content': '当前模型服务异常（502），请稍后重试或切换模型。你也可以点击“查看来源”先查看检索到的清单文件/文件内容。'}, ensure_ascii=False)}\n\n"
                    yield f"event: done\ndata: {json.dumps({'fullAnswer': '当前模型服务异常（502），请稍后重试或切换模型。你也可以点击“查看来源”先查看检索到的清单文件/文件内容。'}, ensure_ascii=False)}\n\n"
                    return
            yield f"event: {event_type}\ndata: {json.dumps(evt_data, ensure_ascii=False, default=str)}\n\n"
            if event_type == "answer":
                full_answer += evt_data.get("content", "")
            elif event_type == "done":
                logger.info(f"[V4] Stream completed: answer_len={len(full_answer)}")
                if memory_store and not use_v5 and not use_v6:
                    memory_store.add_message(user_id, session_id, "user", question)
                    memory_store.add_message(user_id, session_id, "assistant", full_answer)
                    msg_count = memory_store.get_message_count(user_id, session_id)
                    if msg_count >= max_rounds * 2 or estimated_tokens > max_tokens:
                        try:
                            summary_resp = active_client.chat.completions.create(
                                model=active_model,
                                messages=[{"role": "user", "content": SUMMARY_MERGE_PROMPT.format(
                                    old_summary=summary,
                                    user_question=question,
                                    assistant_answer=full_answer
                                )}],
                                temperature=0.3,
                                max_tokens=300
                            )
                            new_summary = (summary_resp.choices[0].message.content or '').strip()
                            memory_store.set_summary(user_id, session_id, new_summary)
                            memory_store.clear_messages(user_id, session_id)
                        except Exception as e:
                            logger.error(f"V4 自动保存/摘要失败: {e}")

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream; charset=utf-8',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )