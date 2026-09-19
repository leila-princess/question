"""
JYLLM 辅助设计蓝图 - 路由定义 v2
"""
import json
import uuid
import os
import re
from io import BytesIO
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from flask import Blueprint, request, jsonify, Response, stream_with_context, send_file

from config import Config
from utils.neo4j_flgl_status import flgl_status_cypher_param
from blueprints.FZSJ.JYLLM.multi_agent_engine import (
    emit_event,
    run_planner,
    continue_generation,
    rewrite_chapter_stream,
    get_task,
    load_template,
    search_context,
)
from blueprints.FZSJ.JYLLM.skeleton_builder import (
    build_method_tree_and_skeleton,
    iter_plan_stream_events,
    build_outline_tree,
)
from blueprints.FZSJ.JYLLM.content_generator import (
    store_v2_task,
    generate_content_stream_v2,
    get_v2_task,
    update_v2_task,
    scheme_feedback_rewrite_stream,
    region_rewrite_stream,
)
from blueprints.GLCX.JQCX.routes import (
    get_equipment_tree as jq_equipment_tree,
    get_projects as jq_projects,
    get_indicators as jq_indicators,
    get_methods as jq_methods,
    get_environments as jq_environments,
    get_environment_detail as jq_environment_detail,
    get_projects_by_env as jq_projects_by_env,
    get_indicators_by_env as jq_indicators_by_env
)
from blueprints.FZSJ.JYLLM.search_tool import get_search_tool
from services.jyllm_history_manager import get_history_manager
from blueprints.XTGL.MXGL.routes import resolve_llm_model
from utils.llm_context_budget import probe_llm_connection
import pymysql

logger = logging.getLogger(__name__)


def _strip_duplicate_leading_chapter_heading(chapter_title: str, md: str) -> str:
    """与前端 chapterStreamDisplay.stripDuplicateLeadingChapterHeading 一致，避免章标题重复。"""
    rest = (md or "").lstrip("\ufeff")
    core = re.sub(r"^\d+\.?\s*", "", (chapter_title or "").strip()).strip()
    if not core:
        return md or ""
    for _ in range(2):
        lines = rest.split("\n")
        if not lines:
            break
        first = lines[0].strip()
        hm = re.match(r"^(#{1,6})\s+(.+)$", first)
        if not hm:
            break
        h_text = hm.group(2).strip()
        heading_core = re.sub(r"^\d+\.?\s*", "", h_text).strip()
        if heading_core == core or h_text == (chapter_title or "").strip():
            rest = "\n".join(lines[1:]).lstrip("\n")
        else:
            break
    return rest


def _full_scheme_markdown_from_chapters(chapters) -> str:
    parts = []
    for i, ch in enumerate(chapters or []):
        title = ch.get("title", "章节 " + str(ch.get("index", i + 1)))
        t = str(title)
        body = _strip_duplicate_leading_chapter_heading(t, str(ch.get("content", "") or ""))
        parts.append(f"## {t}\n\n{body}")
    return "\n\n".join(parts)


def _jy_normalized_chapter_rows(chapters):
    rows = []
    for ch in chapters or []:
        rows.append(
            {
                "chapter_index": ch.get("index"),
                "chapter_title": ch.get("title"),
                "writer_prompt": ch.get("writer_prompt", ""),
                "reviewer_prompt": ch.get("reviewer_prompt", ""),
                "content": ch.get("content", ""),
                "thinking": ch.get("thinking", ""),
                "keywords": ch.get("keywords", ""),
                "search_context": ch.get("searchContext", ""),
                "auto_score": ch.get("score", 0),
                "user_score": ch.get("userScore"),
                "user_comment": ch.get("userComment"),
                "review_status": ch.get("status", "complete"),
            }
        )
    return rows


def _compact_outline_tree_for_storage(nodes: Any) -> List[Dict[str, Any]]:
    """持久化用精简目录树：去掉 dbFull，预览截断，参考文件仅保留 id/name/relevance。"""
    if not isinstance(nodes, list):
        return []
    out: List[Dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        cn: Dict[str, Any] = {
            "id": n.get("id"),
            "title": n.get("title"),
            "type": n.get("type"),
            "level": n.get("level"),
            "hasDbData": n.get("hasDbData"),
        }
        if n.get("hasDbData"):
            preview = str(n.get("dbPreview") or "")[:180]
            if preview:
                cn["dbPreview"] = preview
        refs = n.get("suggestedReferenceFiles") or []
        if isinstance(refs, list) and refs:
            cn["suggestedReferenceFiles"] = [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "relevance": r.get("relevance"),
                }
                for r in refs[:10]
                if isinstance(r, dict) and r.get("id")
            ]
        children = n.get("children")
        if children:
            cn["children"] = _compact_outline_tree_for_storage(children)
        out.append(cn)
    return out


def _build_scheme_extra_meta_json(
    device_display_name: str = "",
    environment_conditions_text: str = "",
    outline_selection: Any = None,
    outline_tree: Any = None,
) -> str:
    """构建可写入 scheme_description 的 JSON；控制体积避免超出列限制。"""
    extra_meta: Dict[str, Any] = {}
    if device_display_name:
        extra_meta["device_display_name"] = device_display_name
    if environment_conditions_text:
        extra_meta["environment_conditions_text"] = environment_conditions_text
    if outline_selection:
        extra_meta["outline_selection"] = outline_selection
    if outline_tree:
        extra_meta["outline_tree"] = _compact_outline_tree_for_storage(outline_tree)
    if not extra_meta:
        return ""
    raw = json.dumps(extra_meta, ensure_ascii=False)
    # TEXT 上限约 64KB；超出则丢弃 outline_tree 仅保留选择与条件
    if len(raw.encode("utf-8")) > 60000:
        extra_meta.pop("outline_tree", None)
        raw = json.dumps(extra_meta, ensure_ascii=False)
    return raw


def _jy_merge_history_metadata_for_update(task_id, metadata, previous):
    """update 时前端可能只传部分 metadata，用库中已有行补齐。"""
    m = dict(metadata or {})
    p = previous or {}
    sel_eq = m.get("devices")
    if sel_eq is None:
        sel_eq = p.get("selected_equipment", [])
    sel_env = m.get("environments")
    if sel_env is None:
        sel_env = p.get("selected_environments", [])
    sel_proj = m.get("projects")
    if sel_proj is None:
        sel_proj = p.get("selected_projects", [])
    sel_ind = m.get("indicators")
    if sel_ind is None:
        sel_ind = p.get("selected_indicators", [])
    sel_met = m.get("methods")
    if sel_met is None:
        sel_met = p.get("selected_methods", [])
    sel_files = m.get("selected_files")
    if sel_files is None:
        sel_files = p.get("selected_files", [])
    return {
        "user_id": m.get("user_id", p.get("user_id", "admin")),
        "scheme_name": m.get("scheme_name") or p.get("scheme_name") or f"方案 {str(task_id)[:8]}",
        "scheme_description": m.get("scheme_description", p.get("scheme_description", "")),
        "devices": sel_eq,
        "environments": sel_env,
        "projects": sel_proj,
        "indicators": sel_ind,
        "methods": sel_met,
        "selected_files": sel_files,
        "environment_config": m.get("environment_config", p.get("environment_config", "")),
        "template_id": m.get("template_id", p.get("template_id", "")),
        "template_name": m.get("template_name", p.get("template_name", "")),
        "messages": m.get("messages", p.get("messages", [])),
        "planner_prompts": m.get("planner_prompts", p.get("planner_prompts", [])),
        "selected_equipment_keys": m.get(
            "selected_equipment_keys", p.get("selected_equipment_keys", [])
        ),
        "page_type": m.get("page_type", p.get("page_type", "jyllm")),
        "current_step": m.get("current_step", p.get("current_step", "complete")),
        "overall_score": m.get("overall_score", p.get("overall_score")),
        "overall_comment": m.get("overall_comment", p.get("overall_comment")),
    }


SCHEME_DIR = Config.SCHEME_DIR
os.makedirs(SCHEME_DIR, exist_ok=True)

jyllm_bp = Blueprint('jyllm', __name__, url_prefix='/api/fzsj/jyllm')


@jyllm_bp.route('/llm-health', methods=['GET'])
def jyllm_llm_health():
    """
    诊断 JYLLM 实际使用的 LLM 地址是否可达。
    ?llm_model_key= 与前端所选模型一致；未传则用模型管理列表首项或 .env。
    """
    key = (request.args.get('llm_model_key') or request.args.get('llmModelKey') or '').strip()
    cfg = resolve_llm_model(key, '')
    probe = probe_llm_connection(cfg)
    return jsonify({
        'success': True,
        'data': {
            **probe,
            'env_base_url': getattr(Config, 'OPENAI_BASE_URL', ''),
            'openai_client_ready': Config.OPENAI_CLIENT is not None,
        },
    })

_history_table_initialized = False


def _ensure_history_table():
    global _history_table_initialized
    if _history_table_initialized or not Config.MYSQL_POOL:
        return
    ddl = """
    CREATE TABLE IF NOT EXISTS createfa_history (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      task_id VARCHAR(100) NOT NULL,
      agent_name VARCHAR(50) NOT NULL,
      role VARCHAR(20) NOT NULL,
      content LONGTEXT,
      metadata JSON,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_task_id (task_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(ddl)
            conn.commit()
    except Exception as e:
        logger.warning(f"历史表初始化失败: {e}")
        return
    _history_table_initialized = True


def _parse_sse(event_data: str) -> tuple[str, dict]:
    event_type = ''
    payload = {}
    for line in event_data.strip().splitlines():
        line = line.strip()
        if line.startswith('event:'):
            event_type = line.split(':', 1)[1].strip()
        elif line.startswith('data:'):
            try:
                payload = json.loads(line.split(':', 1)[1].strip())
            except json.JSONDecodeError:
                pass
    return event_type, payload


def _save_history(task_id: str, agent_name: str, role: str, content: str, metadata: Optional[dict] = None):
    if not Config.MYSQL_POOL:
        return
    _ensure_history_table()
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO createfa_history (task_id, agent_name, role, content, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        task_id,
                        agent_name,
                        role,
                        content or '',
                        json.dumps(metadata or {}, ensure_ascii=False)
                    )
                )
            conn.commit()
    except Exception as e:
        logger.warning(f"创建历史记录失败: {e}")



@jyllm_bp.route('/template', methods=['GET'])
def get_template():
    template = load_template()
    return jsonify({"success": True, "template": template, "length": len(template)})


@jyllm_bp.route('/equipment-tree', methods=['GET'])
def equipment_tree():
    return jq_equipment_tree()


@jyllm_bp.route('/projects', methods=['GET'])
def projects():
    return jq_projects()


@jyllm_bp.route('/indicators', methods=['GET'])
def indicators():
    return jq_indicators()


@jyllm_bp.route('/methods', methods=['POST'])
def methods():
    return jq_methods()


@jyllm_bp.route('/environments', methods=['GET'])
def environments():
    return jq_environments()


@jyllm_bp.route('/environment/detail/<env_id>', methods=['GET'])
def environment_detail(env_id):
    return jq_environment_detail(env_id)


@jyllm_bp.route('/projects-by-env', methods=['POST'])
def projects_by_env():
    return jq_projects_by_env()


@jyllm_bp.route('/indicators-by-env', methods=['POST'])
def indicators_by_env():
    return jq_indicators_by_env()


@jyllm_bp.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    query = data.get('query', '').strip()
    if not query:
        return jsonify({"success": False, "error": "查询词不能为空"}), 400
    tool = get_search_tool()
    results = tool.search_by_keyword(query, n_results=data.get('n_results', 5))
    text_result = tool.search_hybrid(query, n_results=data.get('n_results', 5))
    return jsonify({"success": True, "query": query, "results": results, "formatted_text": text_result, "count": len(results)})


@jyllm_bp.route('/build_skeleton', methods=['POST'])
def build_skeleton():
    """
    确定性方案 V2：根据用户选择与 Neo4j 构建 method_tree 与第2章标题骨架（无 LLM）。
    前端确认后再调用 /generate_content 流式填充正文。
    """
    try:
        data = request.get_json() or {}
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    task_id = (data.get("task_id") or str(uuid.uuid4())[:12]).strip()
    _llm_key = (data.get("llm_model_key") or data.get("llmModelKey") or "").strip()

    try:
        payload = build_method_tree_and_skeleton(
            equipment=data.get("equipment") or [],
            environments=data.get("environments") or [],
            projects=data.get("projects") or [],
            indicators=data.get("indicators") or [],
            methods=data.get("methods") or [],
        )
    except Exception as e:
        logger.exception("build_skeleton failed")
        return jsonify({"success": False, "error": str(e)}), 500

    store_payload = {
        **payload,
        "task_id": task_id,
        "task_source": (data.get("task_source") or data.get("taskSource") or "").strip(),
        "device_display_name": (data.get("device_display_name") or data.get("deviceDisplayName") or "").strip(),
        "environment_conditions": (
            data.get("environment_conditions")
            or data.get("environmentConditions")
            or data.get("environment_conditions_text")
            or data.get("environmentConditionsText")
            or ""
        ).strip(),
        "llm_model_key": _llm_key,
    }
    equip_names = "、".join(
        str(e.get("name") or "") for e in (payload.get("equipment") or []) if isinstance(e, dict)
    )
    store_payload["outline_tree"] = build_outline_tree(
        payload.get("method_tree") or {},
        device_display_name=store_payload["device_display_name"],
        environment_conditions=store_payload["environment_conditions"],
        equipment_names=equip_names,
        indicators=payload.get("indicators") or [],
    )
    store_v2_task(task_id, store_payload)

    return jsonify({"success": True, "task_id": task_id, **payload, "outline_tree": store_payload["outline_tree"]})


@jyllm_bp.route('/plan_stream', methods=['POST'])
def plan_stream():
    """
    规则化规划流：三步说明 + 结构预览 + 骨架（SSE），与 build_skeleton 数据一致并写入同一 V2 任务缓存。
    """
    try:
        data = request.get_json() or {}
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    task_id = (data.get("task_id") or str(uuid.uuid4())[:12]).strip()
    _llm_key = (data.get("llm_model_key") or data.get("llmModelKey") or "").strip()

    # 构建必须在首次 yield 之后执行，否则客户端在 Neo4j/图谱查询完成前收不到任何字节，表现为「卡在规划」。
    def event_stream():
        yield emit_event(
            "plan_building",
            {"task_id": task_id, "message": "正在根据图数据库与勾选构建方案结构，请稍候…"},
        )
        try:
            payload = build_method_tree_and_skeleton(
                equipment=data.get("equipment") or [],
                environments=data.get("environments") or [],
                projects=data.get("projects") or [],
                indicators=data.get("indicators") or [],
                methods=data.get("methods") or [],
            )
        except Exception as e:
            logger.exception("plan_stream build failed")
            yield emit_event("error", {"message": str(e)})
            return

        store_payload = {
            **payload,
            "task_id": task_id,
            "task_source": (data.get("task_source") or data.get("taskSource") or "").strip(),
            "device_display_name": (data.get("device_display_name") or data.get("deviceDisplayName") or "").strip(),
            "environment_conditions": (
                data.get("environment_conditions")
                or data.get("environmentConditions")
                or data.get("environment_conditions_text")
                or data.get("environmentConditionsText")
                or ""
            ).strip(),
            "llm_model_key": _llm_key,
        }
        equip_names = "、".join(
            str(e.get("name") or "") for e in (payload.get("equipment") or []) if isinstance(e, dict)
        )
        store_payload["outline_tree"] = build_outline_tree(
            payload.get("method_tree") or {},
            device_display_name=store_payload["device_display_name"],
            environment_conditions=store_payload["environment_conditions"],
            equipment_names=equip_names,
            indicators=payload.get("indicators") or [],
        )
        store_v2_task(task_id, store_payload)

        full_payload = {**store_payload, "task_id": task_id, "llm_model_key": _llm_key}
        for event_type, pdata in iter_plan_stream_events(full_payload):
            yield emit_event(event_type, pdata)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@jyllm_bp.route('/generate_content', methods=['POST'])
def generate_content():
    """确定性方案 V2：SSE 流式生成第1章 + 第2章方法正文（无 Reviewer）。"""
    try:
        data = request.get_json() or {}
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    task_id = (data.get("task_id") or "").strip()
    if not task_id:
        return jsonify({"success": False, "error": "task_id 不能为空"}), 400

    _llm_key = (data.get("llm_model_key") or data.get("llmModelKey") or "").strip()
    if _llm_key or data.get("selected_sections") is not None or data.get("section_options") is not None:
        from blueprints.FZSJ.JYLLM import content_generator as cg

        t = cg.get_v2_task(task_id)
        if t:
            if _llm_key:
                t["llm_model_key"] = _llm_key
            if data.get("selected_sections") is not None:
                t["selected_sections"] = data.get("selected_sections")
            if data.get("section_options") is not None:
                t["section_options"] = data.get("section_options")

    def event_stream():
        for event_data in generate_content_stream_v2(task_id):
            yield event_data

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@jyllm_bp.route('/planner', methods=['POST'])
def planner():
    """Phase 1: Planner规划"""
    try:
        data = request.get_json()
    except Exception as e:
        logger.error(f"JSON解析失败: {e}")
        return jsonify({"success": False, "error": f"请求体解析失败: {str(e)}"}), 400

    if not data:
        return jsonify({"success": False, "error": "请求体为空"}), 400

    task_id = data.get('task_id', str(uuid.uuid4())[:12])
    # 环境配置步骤下线：environments / environment_config 可为空列表与空串，Planner 提示词已改为「单节 2.1.1 试验环境」结构
    logger.info(f"Planner收到数据: task_id={task_id}, equipment={data.get('equipment')}, environments={data.get('environments')}")
    _save_history(task_id, 'planner', 'user', json.dumps({
        'equipment': data.get('equipment', []),
        'environments': data.get('environments', []),
        'projects': data.get('projects', []),
        'indicators': data.get('indicators', []),
        'methods': data.get('methods', []),
        'environment_config': data.get('environment_config', ''),
    }, ensure_ascii=False))
    _llm_key = (data.get('llm_model_key') or data.get('llmModelKey') or '').strip()

    def event_stream():
        for event_data in run_planner(
            task_id=task_id,
            equipment=data.get('equipment', []),
            environments=data.get('environments', []),
            projects=data.get('projects', []),
            indicators=data.get('indicators', []),
            methods=data.get('methods', []),
            environment_config=data.get('environment_config', ''),
            template_override=data.get('template', ''),
            llm_model_key=_llm_key,
        ):
            event_type, payload = _parse_sse(event_data)
            if event_type == 'planner_complete':
                _save_history(task_id, 'planner', 'assistant', json.dumps(payload, ensure_ascii=False))
            yield event_data

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream; charset=utf-8',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})


@jyllm_bp.route('/continue', methods=['POST'])
def continue_gen():
    """Phase 2: 用户确认后继续生成"""
    data = request.get_json()
    task_id = data.get('task_id')
    chapters = data.get('chapters', [])
    if not task_id:
        return jsonify({"success": False, "error": "task_id不能为空"}), 400

    _llm_key = (data.get('llm_model_key') or data.get('llmModelKey') or '').strip()

    _save_history(task_id, 'planner', 'user', json.dumps({
        'chapters': chapters,
        'equipment': data.get('equipment', []),
        'environments': data.get('environments', []),
        'projects': data.get('projects', []),
        'indicators': data.get('indicators', []),
        'methods': data.get('methods', []),
        'environment_config': data.get('environment_config', ''),
    }, ensure_ascii=False))

    def event_stream():
        for event_data in continue_generation(
            task_id, chapters,
            equipment=data.get('equipment', []),
            environments=data.get('environments', []),
            projects=data.get('projects', []),
            indicators=data.get('indicators', []),
            methods=data.get('methods', []),
            environment_config=data.get('environment_config', ''),
            user_supplement=data.get('user_supplement', ''),
            llm_model_key=_llm_key,
        ):
            event_type, payload = _parse_sse(event_data)
            if event_type == 'writer_parsed':
                _save_history(task_id, 'writer', 'assistant', payload.get('content', ''), {
                    'chapter_index': payload.get('chapter_index'),
                    'keywords': payload.get('keywords'),
                })
            if event_type == 'chapter_complete':
                _save_history(task_id, 'reviewer', 'assistant', payload.get('content', ''), {
                    'chapter_index': payload.get('chapter_index'),
                    'score': payload.get('score'),
                    'thinking': payload.get('thinking'),
                })
            yield event_data

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream; charset=utf-8',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})


@jyllm_bp.route('/task/<task_id>', methods=['GET'])
def get_task_info(task_id):
    task = get_task(task_id)
    if not task:
        return jsonify({"success": False, "error": "任务不存在"}), 404
    return jsonify({"success": True, "task": task})


@jyllm_bp.route('/user-feedback', methods=['POST'])
def user_feedback():
    data = request.get_json()
    task_id = data.get('task_id')
    chapter_index = data.get('chapter_index')
    score = data.get('score', 0)
    comment = data.get('comment', '')
    action = (data.get('action') or 'save').strip()
    if not task_id:
        return jsonify({"success": False, "error": "task_id不能为空"}), 400

    feedback = {
        "task_id": task_id,
        "chapter_index": chapter_index,
        "score": score,
        "comment": comment,
        "action": action,
        "timestamp": datetime.now().isoformat(),
        "needs_rewrite": False,
    }

    feedback_dir = os.path.join(SCHEME_DIR, task_id)
    os.makedirs(feedback_dir, exist_ok=True)
    feedback_file = os.path.join(feedback_dir, f"feedback_chapter_{chapter_index}.json")
    existing = []
    if os.path.exists(feedback_file):
        try:
            with open(feedback_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []
    existing.append(feedback)
    with open(feedback_file, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    user_id = data.get("user_id") or "admin"
    try:
        history_manager = get_history_manager()
        history_manager.save_user_action(
            task_id,
            user_id,
            "feedback_save",
            feedback,
        )
        if chapter_index == -1:
            history_manager.update_overall_feedback(
                task_id,
                user_id,
                score if score is not None else None,
                comment,
            )
    except Exception as e:
        logger.warning("save user feedback action failed: %s", e)

    return jsonify({"success": True, "feedback": feedback, "needs_rewrite": False})


@jyllm_bp.route('/scheme-feedback-rewrite', methods=['POST'])
def scheme_feedback_rewrite():
    """根据用户意见重写整体方案（SSE）。"""
    try:
        data = request.get_json() or {}
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    task_id = (data.get("task_id") or "").strip()
    comment = (data.get("comment") or "").strip()
    full_scheme = (data.get("full_scheme") or "").strip()
    base_config = data.get("base_config") or {}
    _llm_key = (data.get("llm_model_key") or data.get("llmModelKey") or "").strip()

    if not task_id:
        return jsonify({"success": False, "error": "task_id 不能为空"}), 400

    def event_stream():
        for event_data in scheme_feedback_rewrite_stream(
            task_id, full_scheme, comment, base_config, _llm_key
        ):
            yield event_data

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@jyllm_bp.route('/region-rewrite', methods=['POST'])
def region_rewrite():
    """按区域重写章节片段（SSE 流式）。"""
    try:
        data = request.get_json() or {}
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    task_id = (data.get("task_id") or "").strip()
    comment = (data.get("comment") or "").strip()
    chapter_index = int(data.get("chapter_index") or 1)
    region_type = (data.get("region_type") or "selection").strip()
    chapter_content = data.get("chapter_content") or ""
    base_config = data.get("base_config") or {}
    _llm_key = (data.get("llm_model_key") or data.get("llmModelKey") or "").strip()

    if not task_id:
        return jsonify({"success": False, "error": "task_id 不能为空"}), 400
    if not comment:
        return jsonify({"success": False, "error": "comment 不能为空"}), 400

    def event_stream():
        for event_data in region_rewrite_stream(
            task_id,
            chapter_index,
            region_type,
            comment,
            chapter_content,
            selected_text=(data.get("selected_text") or "").strip(),
            start_offset=data.get("start_offset"),
            end_offset=data.get("end_offset"),
            heading_id=(data.get("heading_id") or "").strip(),
            method_id=(data.get("method_id") or "").strip(),
            chapter_title=(data.get("chapter_title") or "").strip(),
            heading_text=(data.get("heading_text") or data.get("label") or "").strip(),
            heading_breadcrumb=(data.get("heading_breadcrumb") or "").strip(),
            full_scheme_excerpt=(data.get("full_scheme_excerpt") or "").strip(),
            original_region=(data.get("original_region") or "").strip(),
            base_config=base_config,
            llm_model_key=_llm_key,
        ):
            yield event_data

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@jyllm_bp.route('/rewrite', methods=['POST'])
def rewrite():
    """重写单个章节"""
    data = request.get_json()
    task_id = data.get('task_id')
    chapter_index = data.get('chapter_index')
    comment = data.get('comment', '')
    if not task_id:
        return jsonify({"success": False, "error": "task_id不能为空"}), 400

    _llm_key = (data.get('llm_model_key') or data.get('llmModelKey') or '').strip()

    def event_stream():
        for event_data in rewrite_chapter_stream(task_id, chapter_index, comment, llm_model_key=_llm_key):
            event_type, payload = _parse_sse(event_data)
            if event_type == 'rewrite_complete':
                _save_history(task_id, 'writer', 'assistant', payload.get('content', ''), {
                    'chapter_index': payload.get('chapter_index'),
                    'comment': comment
                })
            yield event_data

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream; charset=utf-8',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})


@jyllm_bp.route('/scheme_asset', methods=['POST'])
def upload_scheme_asset():
    """插图上传至 MinIO：方案/{task_id}/assets/{uuid}.ext，返回可嵌入正文的同源 URL。"""
    import base64
    from io import BytesIO

    payload = request.get_json(silent=True) or {}
    task_id = (payload.get('task_id') or '').strip()
    raw_b64 = (payload.get('base64') or '').strip()
    content_type = (payload.get('content_type') or 'image/png').split(';')[0].strip().lower()
    if not task_id or not raw_b64:
        return jsonify({"success": False, "error": "task_id 与 base64 不能为空"}), 400
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO 未配置"}), 503
    try:
        blob = base64.b64decode(raw_b64)
    except Exception as e:
        return jsonify({"success": False, "error": f"base64 无效: {e}"}), 400
    if not blob:
        return jsonify({"success": False, "error": "空文件"}), 400
    ext_map = {
        'image/png': 'png',
        'image/jpeg': 'jpg',
        'image/jpg': 'jpg',
        'image/gif': 'gif',
        'image/webp': 'webp',
    }
    ext = ext_map.get(content_type, 'png')
    fname = f"{uuid.uuid4().hex[:16]}.{ext}"
    object_name = f"方案/{task_id}/assets/{fname}"
    try:
        Config.MINIO_CLIENT.put_object(
            Config.MINIO_BUCKET_NAME,
            object_name,
            BytesIO(blob),
            length=len(blob),
            content_type=content_type or 'application/octet-stream',
        )
    except Exception as e:
        logger.exception("scheme_asset put_object failed")
        return jsonify({"success": False, "error": str(e)}), 500
    pub_url = f"/api/fzsj/jyllm/scheme_asset/{task_id}/{fname}"
    return jsonify({"success": True, "data": {"url": pub_url, "object": object_name}})


@jyllm_bp.route('/scheme_asset/<task_id>/<filename>', methods=['GET'])
def get_scheme_asset(task_id, filename):
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO 未配置"}), 503
    if not filename or '/' in filename or '..' in filename or '\\' in filename:
        return jsonify({"success": False, "error": "非法文件名"}), 400
    object_name = f"方案/{task_id}/assets/{filename}"
    try:
        resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, object_name)
        data = resp.read()
        resp.close()
        resp.release_conn()
    except Exception as e:
        logger.warning("get_scheme_asset %s: %s", object_name, e)
        return jsonify({"success": False, "error": "对象不存在或读取失败"}), 404
    ct = 'application/octet-stream'
    low = filename.lower()
    if low.endswith('.png'):
        ct = 'image/png'
    elif low.endswith('.jpg') or low.endswith('.jpeg'):
        ct = 'image/jpeg'
    elif low.endswith('.gif'):
        ct = 'image/gif'
    elif low.endswith('.webp'):
        ct = 'image/webp'
    return Response(data, mimetype=ct)


@jyllm_bp.route('/method_asset/<method_id>', methods=['GET'])
def get_method_asset(method_id):
    """从 MinIO 读取试验方法相关图片；object 为桶内完整对象键（须以「试验方法/」开头）。"""
    from urllib.parse import unquote

    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO 未配置"}), 503
    raw_obj = (request.args.get("object") or "").strip()
    if not raw_obj or not method_id:
        return jsonify({"success": False, "error": "缺少 object 或 method_id"}), 400
    object_name = unquote(raw_obj).replace("\\", "/").lstrip("/")
    if ".." in object_name or object_name.startswith("/"):
        return jsonify({"success": False, "error": "非法路径"}), 400
    if not object_name.startswith("试验方法/"):
        return jsonify({"success": False, "error": "仅允许试验方法/前缀的对象"}), 403
    try:
        resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, object_name)
        data = resp.read()
        resp.close()
        resp.release_conn()
    except Exception as e:
        logger.warning("get_method_asset %s: %s", object_name, e)
        return jsonify({"success": False, "error": "对象不存在或读取失败"}), 404
    ct = "application/octet-stream"
    low = object_name.lower()
    if low.endswith(".png"):
        ct = "image/png"
    elif low.endswith(".jpg") or low.endswith(".jpeg"):
        ct = "image/jpeg"
    elif low.endswith(".gif"):
        ct = "image/gif"
    elif low.endswith(".webp"):
        ct = "image/webp"
    elif low.endswith(".svg"):
        ct = "image/svg+xml"
    return Response(data, mimetype=ct)


@jyllm_bp.route('/save', methods=['POST'])
def save_scheme():
    data = request.get_json()
    task_id = data.get('task_id', str(uuid.uuid4())[:12])
    full_scheme = data.get('full_scheme', '')
    chapters = data.get('chapters', [])
    metadata = data.get('metadata', {})
    if not full_scheme:
        return jsonify({"success": False, "error": "方案内容不能为空"}), 400

    scheme_dir = os.path.join(SCHEME_DIR, task_id)
    os.makedirs(scheme_dir, exist_ok=True)

    with open(os.path.join(scheme_dir, "scheme.md"), 'w', encoding='utf-8') as f:
        f.write(full_scheme)

    scheme_data = {"task_id": task_id, "created_at": datetime.now().isoformat(),
                   "updated_at": datetime.now().isoformat(), "metadata": metadata,
                   "chapters": chapters, "full_scheme": full_scheme, "word_count": len(full_scheme)}
    with open(os.path.join(scheme_dir, "scheme.json"), 'w', encoding='utf-8') as f:
        json.dump(scheme_data, f, ensure_ascii=False, indent=2)

    db_results = {"minio": None, "chroma": None, "neo4j": None, "mysql": None}

    try:
        history_manager = get_history_manager()
        if not getattr(history_manager, "mysql_pool", None):
            db_results["mysql"] = "skipped_no_pool"
        else:
            ok = history_manager.save_scheme_history({
                "task_id": task_id,
                "user_id": metadata.get("user_id", "admin"),
                "scheme_name": metadata.get("scheme_name", f"方案 {task_id[:8]}"),
                "scheme_description": metadata.get("scheme_description", ""),
                "selected_equipment": metadata.get("devices", []),
                "selected_environments": metadata.get("environments", []),
                "selected_projects": metadata.get("projects", []),
                "selected_indicators": metadata.get("indicators", []),
                "selected_methods": metadata.get("methods", []),
                "selected_files": metadata.get("selected_files", []),
                "environment_config": metadata.get("environment_config", ""),
                "template_id": metadata.get("template_id", ""),
                "template_name": metadata.get("template_name", ""),
                "generated_content": full_scheme,
                "word_count": len(full_scheme),
                "generation_status": "completed",
                "current_step": metadata.get("current_step") or data.get("current_step", "complete"),
                "messages": metadata.get("messages", data.get("messages", [])),
                "planner_prompts": metadata.get("planner_prompts", data.get("planner_prompts", [])),
                "selected_equipment_keys": metadata.get(
                    "selected_equipment_keys", data.get("selected_equipment_keys", [])
                ),
                "page_type": metadata.get("page_type", data.get("page_type", "jyllm")),
                "overall_score": metadata.get("overall_score"),
                "overall_comment": metadata.get("overall_comment"),
            })
            if not ok:
                db_results["mysql"] = "error: save_scheme_history returned False"
            else:
                db_results["mysql"] = "ok"
                if chapters:
                    ch_ok = history_manager.save_chapters(task_id, _jy_normalized_chapter_rows(chapters))
                    if not ch_ok:
                        db_results["mysql"] = "error: save_chapters failed"
    except Exception as e:
        logger.exception("Save history to MySQL failed")
        db_results["mysql"] = f"error: {e}"

    if metadata.get('save_to_minio'):
        try:
            from Database.minIo.main import upload_original_file
            scheme_file = os.path.join(scheme_dir, "scheme.md")
            minio_path = upload_original_file(
                Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, scheme_file,
                task_id, "试验方案", "方案", "", "", "", "admin"
            )
            db_results["minio"] = minio_path
        except Exception as e:
            logger.error(f"MinIO save failed: {e}")
            db_results["minio"] = f"error: {e}"
    
    if metadata.get('save_to_chroma', True) and Config.CHROMA_COLLECTION_OBJ:
        try:
            from Database.Chroma.main import process_single_file
            scheme_file = os.path.join(scheme_dir, "scheme.md")
            from Process_data.extractor import DocumentExtractor
            extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
            chroma_result = process_single_file(
                scheme_file, extractor,
                Config.CHROMA_CLIENT, Config.CHROMA_COLLECTION_OBJ,
                Config.MINIO_BUCKET_NAME, Config.MINIO_CLIENT, "",
                pre_extracted_result={
                    "total_pages": 1,
                    "pages": [{"page_num": 1, "text": full_scheme, "tables": [], "figures": []}],
                    "full_text": full_scheme,
                    "all_tables": [],
                    "all_figures": [],
                    "file_type": "md",
                },
                pre_doc_id=task_id, pre_title="试验方案", category="方案"
            )
            db_results["chroma"] = chroma_result
        except Exception as e:
            logger.error(f"Chroma save failed: {e}")
            db_results["chroma"] = f"error: {e}"
    
    if metadata.get('save_to_neo4j'):
        try:
            node_type = metadata.get('neo4j_node_type', 'File')
            with Config.NEO4J_DRIVER.session() as session:
                session.run(f"""
                    CREATE (n:{node_type} {{
                        Id: $id, Name: $name, Type: $type,
                        minIo_id: $minio_id, Status: '启用',
                        User_id: 'admin', Create_time: $create_time
                    }})
                """, id=f"{node_type}_{task_id}", name="试验方案", type="方案",
                    minio_id=db_results.get("minio", ""),
                    create_time=datetime.now().isoformat())
            db_results["neo4j"] = f"{node_type}_{task_id}"
        except Exception as e:
            logger.error(f"Neo4j save failed: {e}")
            db_results["neo4j"] = f"error: {e}"

    return jsonify({"success": True, "task_id": task_id, "saved_at": scheme_data["created_at"], 
                    "word_count": scheme_data["word_count"], "db_results": db_results})


@jyllm_bp.route('/save-draft', methods=['POST'])
def save_draft():
    data = request.get_json()
    task_id = data.get('task_id', str(uuid.uuid4())[:12])
    user_id = data.get('user_id', 'admin')
    scheme_name = data.get('scheme_name', f'草稿 {task_id[:8]}')
    current_step = data.get('current_step', 'device')
    messages = data.get('messages', [])
    planner_prompts = data.get('planner_prompts', [])
    selected_equipment = data.get('selected_equipment', [])
    selected_environments = data.get('selected_environments', [])
    selected_projects = data.get('selected_projects', [])
    selected_indicators = data.get('selected_indicators', [])
    selected_methods = data.get('selected_methods', [])
    selected_equipment_keys = data.get('selected_equipment_keys', [])
    chapters = data.get('chapters', [])
    page_type = data.get('page_type', 'jyllm')
    device_display_name = (data.get('device_display_name') or data.get('deviceDisplayName') or '').strip()
    environment_conditions_text = (
        data.get('environment_conditions_text') or data.get('environmentConditionsText') or ''
    ).strip()
    outline_selection = data.get('outline_selection')
    outline_tree = data.get('outline_tree')

    scheme_description = _build_scheme_extra_meta_json(
        device_display_name=device_display_name,
        environment_conditions_text=environment_conditions_text,
        outline_selection=outline_selection,
        outline_tree=outline_tree,
    )

    full_scheme = _full_scheme_markdown_from_chapters(chapters) if chapters else ''

    try:
        history_manager = get_history_manager()
        success = history_manager.save_scheme_history({
            "task_id": task_id,
            "user_id": user_id,
            "scheme_name": scheme_name,
            "scheme_description": scheme_description,
            "selected_equipment": selected_equipment,
            "selected_environments": selected_environments,
            "selected_projects": selected_projects,
            "selected_indicators": selected_indicators,
            "selected_methods": selected_methods,
            "selected_files": [],
            "environment_config": "",
            "template_id": "",
            "template_name": "",
            "generated_content": full_scheme,
            "word_count": len(full_scheme),
            "generation_status": "generating",
            "current_step": current_step,
            "messages": messages,
            "planner_prompts": planner_prompts,
            "selected_equipment_keys": selected_equipment_keys,
            "page_type": page_type,
            "overall_score": data.get("overall_score"),
            "overall_comment": data.get("overall_comment"),
        })
        
        if success and chapters:
            normalized_chapters = []
            for ch in chapters:
                normalized_chapters.append({
                    "chapter_index": ch.get("index"),
                    "chapter_title": ch.get("title"),
                    "writer_prompt": ch.get("writer_prompt", ""),
                    "reviewer_prompt": ch.get("reviewer_prompt", ""),
                    "content": ch.get("content", ""),
                    "thinking": ch.get("thinking", ""),
                    "keywords": ch.get("keywords", ""),
                    "search_context": ch.get("searchContext", ""),
                    "auto_score": ch.get("score", 0),
                    "user_score": ch.get("userScore"),
                    "user_comment": ch.get("userComment"),
                    "review_status": ch.get("status", "pending"),
                })
            history_manager.save_chapters(task_id, normalized_chapters)
        
        if success:
            return jsonify({"success": True, "task_id": task_id, "message": "草稿已保存"})
        else:
            return jsonify({"success": False, "error": "保存失败"}), 500
    except Exception as e:
        logger.error(f"Save draft failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@jyllm_bp.route('/update/<task_id>', methods=['POST'])
def update_scheme(task_id):
    """更新已保存的方案：落盘 + MySQL 历史与章节 +（可选）MinIO / Chroma。"""
    data = request.get_json()
    full_scheme = data.get('full_scheme', '')
    chapters = data.get('chapters', [])
    metadata = data.get('metadata', {})
    if not full_scheme:
        return jsonify({"success": False, "error": "方案内容不能为空"}), 400

    scheme_dir = os.path.join(SCHEME_DIR, task_id)
    os.makedirs(scheme_dir, exist_ok=True)

    json_file = os.path.join(scheme_dir, "scheme.json")
    prev_created = None
    if os.path.exists(json_file):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                prev_doc = json.load(f)
                prev_created = prev_doc.get("created_at")
        except Exception:
            pass

    with open(os.path.join(scheme_dir, "scheme.md"), "w", encoding="utf-8") as f:
        f.write(full_scheme)

    scheme_data = {
        "task_id": task_id,
        "created_at": prev_created or datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "metadata": metadata,
        "chapters": chapters,
        "full_scheme": full_scheme,
        "word_count": len(full_scheme),
    }
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(scheme_data, f, ensure_ascii=False, indent=2)

    db_results = {"minio": None, "chroma": None, "neo4j": None, "mysql": None}

    user_id = metadata.get("user_id", "admin")
    try:
        history_manager = get_history_manager()
        prev = (
            history_manager.get_scheme_detail(task_id, user_id)
            if getattr(history_manager, "mysql_pool", None)
            else None
        )
        merged = _jy_merge_history_metadata_for_update(task_id, metadata, prev)
        if not getattr(history_manager, "mysql_pool", None):
            db_results["mysql"] = "skipped_no_pool"
        else:
            ok = history_manager.save_scheme_history(
                {
                    "task_id": task_id,
                    "user_id": merged["user_id"],
                    "scheme_name": merged["scheme_name"],
                    "scheme_description": merged["scheme_description"],
                    "selected_equipment": merged["devices"],
                    "selected_environments": merged["environments"],
                    "selected_projects": merged["projects"],
                    "selected_indicators": merged["indicators"],
                    "selected_methods": merged["methods"],
                    "selected_files": merged["selected_files"],
                    "environment_config": merged["environment_config"],
                    "template_id": merged["template_id"],
                    "template_name": merged["template_name"],
                    "generated_content": full_scheme,
                    "word_count": len(full_scheme),
                    "generation_status": "completed",
                    "current_step": merged["current_step"],
                    "messages": merged["messages"],
                    "planner_prompts": merged["planner_prompts"],
                    "selected_equipment_keys": merged["selected_equipment_keys"],
                    "page_type": merged["page_type"],
                    "overall_score": merged.get("overall_score"),
                    "overall_comment": merged.get("overall_comment"),
                }
            )
            if not ok:
                db_results["mysql"] = "error: save_scheme_history returned False"
            else:
                db_results["mysql"] = "ok"
                if chapters:
                    ch_ok = history_manager.save_chapters(task_id, _jy_normalized_chapter_rows(chapters))
                    if not ch_ok:
                        db_results["mysql"] = "error: save_chapters failed"
    except Exception as e:
        logger.exception("update_scheme MySQL failed")
        db_results["mysql"] = f"error: {e}"

    if metadata.get("save_to_minio"):
        try:
            from Database.minIo.main import upload_original_file

            scheme_file = os.path.join(scheme_dir, "scheme.md")
            minio_path = upload_original_file(
                Config.MINIO_CLIENT,
                Config.MINIO_BUCKET_NAME,
                scheme_file,
                task_id,
                "试验方案",
                "方案",
                "",
                "",
                "",
                "admin",
            )
            db_results["minio"] = minio_path
        except Exception as e:
            logger.error(f"MinIO save failed: {e}")
            db_results["minio"] = f"error: {e}"

    if metadata.get("save_to_chroma", True) and Config.CHROMA_COLLECTION_OBJ:
        try:
            from Database.Chroma.main import process_single_file
            from Process_data.extractor import DocumentExtractor

            scheme_file = os.path.join(scheme_dir, "scheme.md")
            extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
            chroma_result = process_single_file(
                scheme_file,
                extractor,
                Config.CHROMA_CLIENT,
                Config.CHROMA_COLLECTION_OBJ,
                Config.MINIO_BUCKET_NAME,
                Config.MINIO_CLIENT,
                "",
                pre_extracted_result={
                    "total_pages": 1,
                    "pages": [{"page_num": 1, "text": full_scheme, "tables": [], "figures": []}],
                    "full_text": full_scheme,
                    "all_tables": [],
                    "all_figures": [],
                    "file_type": "md",
                },
                pre_doc_id=task_id,
                pre_title="试验方案",
                category="方案",
            )
            db_results["chroma"] = chroma_result
        except Exception as e:
            logger.error(f"Chroma save failed: {e}")
            db_results["chroma"] = f"error: {e}"

    return jsonify(
        {
            "success": True,
            "task_id": task_id,
            "updated_at": scheme_data["updated_at"],
            "word_count": scheme_data["word_count"],
            "db_results": db_results,
        }
    )


@jyllm_bp.route('/scheme/<task_id>', methods=['GET'])
def get_scheme(task_id):
    json_file = os.path.join(SCHEME_DIR, task_id, "scheme.json")
    if not os.path.exists(json_file):
        return jsonify({"success": False, "error": "方案不存在"}), 404
    with open(json_file, 'r', encoding='utf-8') as f:
        scheme_data = json.load(f)
    return jsonify({"success": True, "scheme": scheme_data})


@jyllm_bp.route('/history/list', methods=['GET'])
def history_list():
    user_id = request.args.get('user_id', 'admin')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 50))
    try:
        history_manager = get_history_manager()
        result = history_manager.get_user_history_list(user_id, page, page_size)
        return jsonify(result)
    except Exception as e:
        logger.error(f"获取历史列表失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@jyllm_bp.route('/history/<task_id>', methods=['GET'])
def get_history(task_id):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未初始化"}), 500
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    "SELECT id, agent_name, role, content, metadata, created_at FROM createfa_history WHERE task_id=%s ORDER BY id",
                    (task_id,)
                )
                rows = cursor.fetchall()
        return jsonify({"success": True, "history": rows})
    except Exception as e:
        logger.error(f"查询历史失败: {e}")
        return jsonify({"success": False, "error": "查询历史失败"}), 500


@jyllm_bp.route('/schemes', methods=['GET'])
def list_schemes():
    schemes = []
    if not os.path.exists(SCHEME_DIR):
        return jsonify({"success": True, "schemes": []})
    for tid in os.listdir(SCHEME_DIR):
        json_file = os.path.join(SCHEME_DIR, tid, "scheme.json")
        if os.path.exists(json_file):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                schemes.append({"task_id": tid, "created_at": data.get("created_at", ""),
                                "updated_at": data.get("updated_at", ""), "word_count": data.get("word_count", 0),
                                "metadata": data.get("metadata", {}), "chapter_count": len(data.get("chapters", []))})
            except:
                pass
    schemes.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"success": True, "schemes": schemes, "total": len(schemes)})


@jyllm_bp.route('/export/<task_id>', methods=['POST'])
def export_scheme(task_id):
    data = request.get_json()
    export_format = data.get('format', 'md')
    scheme_dir = os.path.join(SCHEME_DIR, task_id)
    md_file = os.path.join(scheme_dir, "scheme.md")

    if os.path.exists(md_file):
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()
    else:
        task = get_task(task_id)
        if task and task.get("full_scheme"):
            content = task["full_scheme"]
            os.makedirs(scheme_dir, exist_ok=True)
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(content)
        else:
            return jsonify({"success": False, "error": "方案不存在，请先保存后再导出"}), 404

    if export_format == 'md':
        return jsonify({"success": True, "content": content, "format": "markdown", "filename": f"scheme_{task_id}.md"})
    elif export_format == 'pdf':
        try:
            from utils.scheme_pdf_export import export_markdown_to_pdf_bytes

            scheme_dir = os.path.join(SCHEME_DIR, task_id)
            scheme_file = os.path.join(scheme_dir, "scheme.md")
            if not os.path.exists(scheme_file):
                return jsonify({"success": False, "error": "方案文件不存在"}), 404

            with open(scheme_file, 'r', encoding='utf-8') as f:
                md_content = f.read()

            doc_title = f"试验方案-{task_id}"
            scheme_json = os.path.join(scheme_dir, "scheme.json")
            if os.path.exists(scheme_json):
                try:
                    with open(scheme_json, 'r', encoding='utf-8') as jf:
                        sj = json.load(jf)
                    meta = sj.get("metadata") or {}
                    name = meta.get("scheme_name") or meta.get("name")
                    if name:
                        doc_title = str(name)
                except Exception:
                    pass

            pdf_content, pdf_err = export_markdown_to_pdf_bytes(
                md_content, doc_title=doc_title, doc_id=task_id
            )
            if pdf_content:
                return Response(
                    pdf_content,
                    mimetype='application/pdf',
                    headers={
                        'Content-Disposition': f'attachment; filename=scheme_{task_id}.pdf'
                    },
                )
            return jsonify({"success": False, "error": pdf_err or "PDF生成失败"}), 500
        except Exception as e:
            logger.error(f"导出PDF失败: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    elif export_format == 'word':
        try:
            from utils.scheme_markdown_to_docx import markdown_to_docx_bytes

            # 使用内存流下发，避免 Windows 上先写磁盘再 send_file 时出现路径不存在/句柄等问题
            docx_bytes = markdown_to_docx_bytes(content)
            buf = BytesIO(docx_bytes)
            buf.seek(0)
            return send_file(
                buf,
                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                as_attachment=True,
                download_name=f"scheme_{task_id}.docx",
            )
        except ImportError:
            return jsonify({"success": False, "error": "Word导出需要安装python-docx: pip install python-docx"}), 500
        except Exception as e:
            logger.error(f"导出Word失败: {e}", exc_info=True)
            return jsonify({"success": False, "error": str(e) or "Word生成失败"}), 500
    return jsonify({"success": False, "error": "不支持的导出格式"}), 400


@jyllm_bp.route('/librarian', methods=['POST'])
def librarian():
    """资料员LLM端点：根据设备+环境给出环境设置参考答案"""
    from blueprints.FZSJ.JYLLM.prompts import get_prompt
    from blueprints.FZSJ.JYLLM.multi_agent_engine import call_llm, extract_json_from_text

    data = request.get_json()
    equipment_ids = data.get('equipment_ids', [])
    environment_id = data.get('environment_id', '')

    if not environment_id:
        return jsonify({"success": False, "error": "environment_id不能为空"}), 400

    try:
        env_detail = None
        env_equipment_list = []
        try:
            from blueprints.GLCX.JQCX.routes import _run_cypher, _get_node_props
            
            cypher = """
            MATCH (env:Environment)
            WHERE env.Id = $envId AND (env).Status IN $flgl_enabled_status
            RETURN env
            """
            records = _run_cypher(cypher, {**flgl_status_cypher_param(), "envId": environment_id})
            if records:
                env_node = records[0].get('env', {})
                env_detail = _get_node_props(env_node)
                
                try:
                    eq_cypher = """
                    MATCH (env:Environment {Id: $envId})-[:CON|HAS*0..2]-(eq:Equipment)
                    WHERE (env).Status IN $flgl_enabled_status AND (eq).Status IN $flgl_enabled_status
                    RETURN DISTINCT eq.Name as name, eq.Id as id
                    ORDER BY eq.Name
                    """
                    eq_records = _run_cypher(
                        eq_cypher, {**flgl_status_cypher_param(), "envId": environment_id}
                    )
                    env_equipment_list = [r.get('name', '') for r in eq_records if r.get('name')]
                except Exception as eq_err:
                    logger.warning(f"查询环境关联设备失败: {eq_err}")
        except Exception as e:
            logger.warning(f"查询环境节点失败: {e}")

        system_prompt = get_prompt("librarian_system")
        user_prompt = get_prompt("librarian_user",
                                 equipment_list=', '.join(equipment_ids) if equipment_ids else '未指定',
                                 env_name=env_detail.get('name', environment_id) if env_detail else environment_id,
                                 env_type=env_detail.get('type', '未知') if env_detail else '未知',
                                 env_class=env_detail.get('class', '未知') if env_detail else '未知',
                                 env_request=env_detail.get('request', '无') if env_detail else '无',
                                 env_limit=env_detail.get('limit', '无') if env_detail else '无',
                                 env_combination=env_detail.get('combination', '无') if env_detail else '无',
                                 env_equipment=', '.join(env_equipment_list) if env_equipment_list else '无')

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response_text = call_llm(messages, temperature=0.3, max_tokens=2048)
        result = extract_json_from_text(response_text)

        if result:
            return jsonify({
                "success": True,
                "data": {
                    "suggestion": result.get("suggestion", response_text),
                    "env_details": env_detail or {},
                    "reasoning": result.get("reasoning", "")
                }
            })
        else:
            return jsonify({
                "success": True,
                "data": {
                    "suggestion": response_text,
                    "env_details": env_detail or {},
                    "reasoning": ""
                }
            })
    except Exception as e:
        logger.error(f"资料员调用失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ========== 数据查询接口（不复用GLCX） ==========

def _run_jyllm_cypher(cypher, params=None):
    """执行Cypher查询，返回记录列表（节点自动转为字典）"""
    if not Config.NEO4J_DRIVER:
        return []
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run(cypher, params or {})
            records = []
            for r in result:
                record_dict = {}
                for key in r.keys():
                    val = r[key]
                    if hasattr(val, 'items') and hasattr(val, 'id'):
                        record_dict[key] = dict(val)
                    else:
                        record_dict[key] = val
                records.append(record_dict)
            return records
    except Exception as e:
        logger.warning(f"Neo4j查询失败: {e}")
        return []


def _jyllm_get_node_props(node_data):
    """从Neo4j节点数据中提取标准属性"""
    if not node_data:
        return {}
    props = {}
    for key, val in node_data.items():
        if key == 'labels' or key == '_labels':
            continue
        lower_key = key.lower()
        if lower_key in (
            'id', 'name', 'label', 'description', 'status', 'type', 'class', 'limit', 'request',
            'sytj', 'sygc', 'jgpd',
        ):
            props[lower_key] = val
    return props


@jyllm_bp.route('/equipment-tree', methods=['GET'])
def get_equipment_tree():
    """三级设备树: 分类 → 类型 → 设备"""
    try:
        cypher = """
        MATCH (et:EquipmentType)-[:HAS]->(e:Equipment)
        WHERE (et).Status IN $flgl_enabled_status AND (e).Status IN $flgl_enabled_status
        RETURN et, e
        ORDER BY et.Name, e.Name
        """
        records = _run_jyllm_cypher(cypher, {**flgl_status_cypher_param()})

        tree = {}
        for record in records:
            et_data = _jyllm_get_node_props(record.get('et', {}))
            e_data = _jyllm_get_node_props(record.get('e', {}))
            et_type = et_data.get('type', '未分类') or et_data.get('name', '未分类')
            et_id = et_data.get('id', '')

            if et_type not in tree:
                tree[et_type] = {'label': et_type, 'types': {}}
            if et_id not in tree[et_type]['types']:
                tree[et_type]['types'][et_id] = {'type': et_data, 'equipment': []}
            tree[et_type]['types'][et_id]['equipment'].append(e_data)

        result = []
        for et_type, info in tree.items():
            types_list = []
            for et_id, et_info in info['types'].items():
                types_list.append({
                    'title': et_info['type'].get('name', et_id),
                    'key': f'et_{et_id}',
                    'children': [
                        {'title': eq.get('name', eq.get('id', '')), 'key': eq.get('id', ''), 'isLeaf': True}
                        for eq in et_info['equipment']
                    ],
                })
            result.append({
                'title': info['label'],
                'key': f'type_{et_type}',
                'children': types_list,
            })

        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"设备树查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@jyllm_bp.route('/projects', methods=['GET'])
def get_projects():
    try:
        equipment_ids = request.args.get('equipment_id', '')
        if not equipment_ids:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        eq_id_list = [e.strip() for e in equipment_ids.split(',') if e.strip()]
        if not eq_id_list:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        cypher = """
        MATCH (eq:Equipment)-[:CON]->(test:TestProject {Type: 'test'})
        WHERE eq.Id IN $eqIds
          AND (eq).Status IN $flgl_enabled_status
          AND (test).Status IN $flgl_enabled_status
        OPTIONAL MATCH (cat:TestProject {Type: 'category'})-[:HAS]->(test)
        WHERE (cat) IS NULL OR (cat).Status IN $flgl_enabled_status
        OPTIONAL MATCH (env:Environment)-[:CON]->(test)
        WHERE (env) IS NULL OR (env).Status IN $flgl_enabled_status
        RETURN DISTINCT test, cat, env
        ORDER BY cat.Name, test.Name
        """
        records = _run_jyllm_cypher(cypher, {**flgl_status_cypher_param(), 'eqIds': eq_id_list})

        categories = {}
        all_envs = []
        for record in records:
            test_data = _jyllm_get_node_props(record.get('test', {}))
            cat_data = _jyllm_get_node_props(record.get('cat', {}))
            env_data = _jyllm_get_node_props(record.get('env', {}))

            test_data['_env'] = env_data.get('name', '') if env_data else ''
            cat_id = cat_data.get('id', '') if cat_data else ''

            if env_data and env_data.get('name'):
                env_entry = {'id': env_data.get('id', ''), 'name': env_data.get('name', '')}
                if not any(e['id'] == env_entry['id'] for e in all_envs):
                    all_envs.append(env_entry)

            if cat_id:
                if cat_id not in categories:
                    categories[cat_id] = {
                        'category': cat_data,
                        'children': [],
                        'env_names': set()
                    }
                categories[cat_id]['children'].append(test_data)
                if env_data:
                    categories[cat_id]['env_names'].add(env_data.get('name', ''))

        nodes = []
        for cat_id, cat_info in categories.items():
            cat_node = cat_info['category']
            cat_node['children'] = cat_info['children']
            if cat_info['env_names']:
                cat_node['_env'] = '、'.join(sorted(cat_info['env_names']))
            nodes.append(cat_node)

        return jsonify({"success": True, "data": nodes, "environments": all_envs})
    except Exception as e:
        logger.error(f"项目查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@jyllm_bp.route('/indicators', methods=['GET'])
def get_indicators():
    """获取指标列表（返回完整节点数据）"""
    try:
        equipment_ids = request.args.get('equipment_id', '')
        if not equipment_ids:
            return jsonify({"success": False, "error": "equipment_id required"}), 400

        eq_list = [e.strip() for e in equipment_ids.split(',') if e.strip()]

        all_indicators = []
        indicator_ids = set()

        for eq_id in eq_list:
            for rel_type in ['CON', 'BELONGS_TO']:
                cypher = f"""
                MATCH (eq:Equipment {{Id: $eqId}})-[:{rel_type}]->(i:Indicator)
                WHERE (eq).Status IN $flgl_enabled_status AND (i).Status IN $flgl_enabled_status
                RETURN DISTINCT i ORDER BY i.Name
                """
                records = _run_jyllm_cypher(
                    cypher, {**flgl_status_cypher_param(), 'eqId': eq_id}
                )
                for r in records:
                    ind = _jyllm_get_node_props(r.get('i', {}))
                    iid = ind.get('id', '')
                    if iid and iid not in indicator_ids:
                        indicator_ids.add(iid)
                        all_indicators.append(ind)
                if all_indicators:
                    break
            if not all_indicators:
                cypher = """
                MATCH (eq:Equipment {Id: $eqId})-[:CON]->(p:TestProject)-[:CON]->(i:Indicator)
                WHERE (eq).Status IN $flgl_enabled_status
                  AND (p).Status IN $flgl_enabled_status
                  AND (i).Status IN $flgl_enabled_status
                RETURN DISTINCT i ORDER BY i.Name
                """
                records = _run_jyllm_cypher(
                    cypher, {**flgl_status_cypher_param(), 'eqId': eq_id}
                )
                for r in records:
                    ind = _jyllm_get_node_props(r.get('i', {}))
                    iid = ind.get('id', '')
                    if iid and iid not in indicator_ids:
                        indicator_ids.add(iid)
                        all_indicators.append(ind)

        return jsonify({"success": True, "data": all_indicators})
    except Exception as e:
        logger.error(f"指标查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


def _jyllm_norm_id(v):
    """与前端、图库 Id 比较时统一为字符串，避免 int/str 导致 linkedProjectIds 为空。"""
    if v is None:
        return ''
    return str(v).strip()


@jyllm_bp.route('/methods', methods=['POST'])
def get_methods():
    try:
        data = request.get_json()
        project_ids_raw = data.get('projectIds', []) or []
        indicator_ids_raw = data.get('indicatorIds', []) or []
        project_ids = [_jyllm_norm_id(x) for x in project_ids_raw if _jyllm_norm_id(x)]
        indicator_ids = [_jyllm_norm_id(x) for x in indicator_ids_raw if _jyllm_norm_id(x)]
        project_ids_set = set(project_ids)
        indicator_ids_set = set(indicator_ids)

        if not project_ids and not indicator_ids:
            return jsonify({"success": True, "data": []})

        method_data = {}

        if project_ids:
            cypher = """
            MATCH (p:TestProject)-[:CON]->(m:TestMethod)
            WHERE p.Id IN $projectIds
              AND (p).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN m, collect(DISTINCT p.Id) AS projIds, collect(DISTINCT f) AS files
            ORDER BY m.Name
            """
            records = _run_jyllm_cypher(
                cypher, {**flgl_status_cypher_param(), 'projectIds': project_ids}
            )
            for r in records:
                method = _jyllm_get_node_props(r.get('m', {}))
                method_id = method.get('id', '')
                if method_id not in method_data:
                    method_data[method_id] = {
                        'method': method,
                        'proj_ids': set(),
                        'ind_ids': set(),
                        'files': []
                    }
                for pid in r.get('projIds', []):
                    if _jyllm_norm_id(pid) in project_ids_set:
                        method_data[method_id]['proj_ids'].add(_jyllm_norm_id(pid))
                for f in r.get('files', []):
                    if f:
                        method_data[method_id]['files'].append(_jyllm_get_node_props(f))

        if indicator_ids:
            cypher = """
            MATCH (i:Indicator)-[:CON]->(m:TestMethod)
            WHERE i.Id IN $indicatorIds
              AND (i).Status IN $flgl_enabled_status
              AND (m).Status IN $flgl_enabled_status
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            WHERE (f) IS NULL OR (f).Status IN $flgl_enabled_status
            RETURN m, collect(DISTINCT i.Id) AS indIds, collect(DISTINCT f) AS files
            ORDER BY m.Name
            """
            records = _run_jyllm_cypher(
                cypher, {**flgl_status_cypher_param(), 'indicatorIds': indicator_ids}
            )
            for r in records:
                method = _jyllm_get_node_props(r.get('m', {}))
                method_id = method.get('id', '')
                if method_id not in method_data:
                    method_data[method_id] = {
                        'method': method,
                        'proj_ids': set(),
                        'ind_ids': set(),
                        'files': []
                    }
                for iid in r.get('indIds', []):
                    if _jyllm_norm_id(iid) in indicator_ids_set:
                        method_data[method_id]['ind_ids'].add(_jyllm_norm_id(iid))
                for f in r.get('files', []):
                    if f:
                        method_data[method_id]['files'].append(_jyllm_get_node_props(f))

        methods = []
        for method_id, data_item in method_data.items():
            method = data_item['method']
            files = list({f.get('id', ''): f for f in data_item['files']}.values())
            if files:
                method['files'] = files

            proj_ids = sorted(data_item['proj_ids'], key=lambda x: (len(x), x))
            ind_ids = sorted(data_item['ind_ids'], key=lambda x: (len(x), x))

            if project_ids and indicator_ids:
                has_project = len(proj_ids) > 0
                has_indicator = len(ind_ids) > 0
                if has_project and has_indicator:
                    method['_relationType'] = 'both'
                elif has_project:
                    method['_relationType'] = 'project-only'
                else:
                    method['_relationType'] = 'indicator-only'
            elif project_ids:
                method['_relationType'] = 'project-only'
            else:
                method['_relationType'] = 'indicator-only'

            # 供 JYGZ 等页面按项目/指标精确挂载方法（避免「每个项目下列出全部方法」）
            method['linkedProjectIds'] = proj_ids
            method['linkedIndicatorIds'] = ind_ids

            methods.append(method)

        methods.sort(key=lambda x: x.get('name', ''))
        return jsonify({"success": True, "data": methods})
    except Exception as e:
        logger.error(f"方法查询出错: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

