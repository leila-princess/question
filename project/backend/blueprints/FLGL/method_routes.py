"""
方法提取 HTTP API — 包装洪实 GetTestMethod pipeline 为 REST 接口

接口:
  POST /api/FLGL/method-extract   上传标准文件，执行抽取+召回，返回方法列表+推荐关联节点
  POST /api/FLGL/method-import    将方法数据导入 Neo4j
"""
import os
import sys
import json
import tempfile
import logging
import threading
import queue
import uuid
import mimetypes
import re
import hashlib
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

from flask import Blueprint, request, jsonify, current_app, Response
from werkzeug.utils import secure_filename
from datetime import datetime, timezone

from utils.operation_log import log_user_action

from .method_block_markdown import text_block_to_html, text_block_to_markdown

logger = logging.getLogger(__name__)

# 目录路由版本：前端/调试可对照
FLGL_TOC_ROUTING_REV = "20260703-extract-figures-01"


def _finalize_parse_cache_like_word(parse_cache: Optional[dict]) -> Optional[dict]:
    """
    PDF / Word 统一 parse_cache 终化（与 Word LLM 同源解析输出对齐）：
    node_slices_md、node_slices、section.html、flat_headings。
    """
    if not isinstance(parse_cache, dict):
        return parse_cache
    sections = list(parse_cache.get("sections") or [])
    if not sections:
        return parse_cache

    full_md = str(parse_cache.get("full_markdown") or "").strip()
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import (
            _build_node_slices,
            _build_node_slices_md,
        )

        if full_md:
            if not parse_cache.get("node_slices_md"):
                node_slices_md = _build_node_slices_md(sections, full_markdown=full_md)
                if node_slices_md:
                    parse_cache["node_slices_md"] = node_slices_md
            node_slices = parse_cache.get("node_slices")
            if not isinstance(node_slices, dict) or not node_slices:
                parse_cache["node_slices"] = _build_node_slices(
                    sections,
                    full_markdown=full_md,
                    node_slices_md=dict(parse_cache.get("node_slices_md") or {}),
                )
    except Exception as exc:
        logger.warning("[method-extract] 补齐 node_slices 失败: %s", exc)

    node_slices = dict(parse_cache.get("node_slices") or {})
    node_slices_md = dict(parse_cache.get("node_slices_md") or {})
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("section_id") or "").strip()
        if sid and not str(sec.get("html") or "").strip() and node_slices.get(sid):
            sec["html"] = node_slices[sid]
        if sid and not str(sec.get("text") or "").strip() and node_slices_md.get(sid):
            sec["text"] = re.sub(r"<[^>]+>", " ", node_slices_md[sid]).strip()[:800]
    parse_cache["sections"] = sections

    if not parse_cache.get("flat_headings"):
        from blueprints.ZSGL.WJGL1.body_content_trim import (
            shorten_toc_display_title,
            strip_section_no_from_title,
        )

        flat: List[dict] = []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sid = str(sec.get("section_id") or "").strip()
            if not sid:
                continue
            sno = str(sec.get("section_no") or "").strip().lstrip(".")
            raw_title = str(sec.get("title") or sec.get("heading") or "").strip()
            display = (
                shorten_toc_display_title(strip_section_no_from_title(sno, raw_title))
                if sno
                else shorten_toc_display_title(raw_title)
            )
            flat.append({
                "section_id": sid,
                "sectionNo": sno,
                "title": display or raw_title,
                "level": max(1, int(sec.get("level") or (sno.count(".") + 1 if sno else 1))),
                "snippet": str(sec.get("text") or "")[:180],
                "source": "parse_sections",
            })
        parse_cache["flat_headings"] = flat

    return parse_cache


def _ensure_parse_cache_slices(parse_cache: Optional[dict]) -> Optional[dict]:
    """兼容旧调用名。"""
    return _finalize_parse_cache_like_word(parse_cache)


def _merge_ctx_trace_figures(ctx: dict) -> list:
    """从 ctx / parse_cache 合并图片索引，供「见图N」溯源（与 Word 同源 HTML 索引）。"""
    from .method_section_enrich import gather_figures_for_method_trace

    base = list(ctx.get("all_figures") or [])
    parse_cache = ctx.get("parse_cache") if isinstance(ctx.get("parse_cache"), dict) else {}
    gathered = gather_figures_for_method_trace(parse_cache)
    if not gathered:
        return base
    if not base:
        return gathered
    seen: set = set()
    merged: list = []
    for fig in base + gathered:
        if not isinstance(fig, dict):
            continue
        key = str(fig.get("html") or fig.get("path") or fig.get("figure_index") or id(fig))
        if key in seen:
            continue
        seen.add(key)
        merged.append(fig)
    return merged


def _flgl_cfg_int(main_config, env_key: str, default: int) -> int:
    """优先环境变量，其次主工程 Config 类属性；避免 GetTestMethod 覆盖 sys.modules 后 MainConfig 指向存根而缺字段。"""
    ev = os.environ.get(env_key)
    if ev is not None and str(ev).strip() != "":
        try:
            return int(ev)
        except ValueError:
            pass
    v = getattr(main_config, env_key, None)
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    return default


# 与 Neo4j 一致：仅允许三字符串；导入边方向 (Target)-[rel]->(TestMethod)
_METHOD_IMPORT_REL_TYPES = frozenset({'CON', 'HAS', 'BELONGS_TO', 'DERIVED_FROM'})


def _resolve_or_create_import_target(target_type: str, target_id: str = "", target_name: str = "") -> str:
    """method-import：按 Id 匹配已有节点，否则按名称查找或自动创建。"""
    from .routes import _run_cypher, _generate_id, _get_node_props, NODE_FIELDS

    ttype = str(target_type or "").strip()
    tid = str(target_id or "").strip()
    tname = str(target_name or "").strip()
    if not ttype:
        return tid
    if tid:
        recs = _run_cypher(f"MATCH (n:{ttype} {{Id: $id}}) RETURN n LIMIT 1", {"id": tid})
        if recs:
            return tid
    if not tname:
        return tid
    recs = _run_cypher(
        f"MATCH (n:{ttype}) WHERE n.Name = $name RETURN n LIMIT 1",
        {"name": tname},
    )
    if recs:
        existing = _get_node_props(recs[0].get("n", {}))
        return str(existing.get("Id") or existing.get("id") or "").strip()
    fields = list(NODE_FIELDS.get(ttype) or ["Id", "Name", "Status", "User_id", "Create_time"])
    new_id = _generate_id(ttype)
    props = {"Id": new_id, "Name": tname, "Status": "启用", "User_id": "管理员"}
    for f in fields:
        if f in props:
            continue
        if f == "Create_time":
            from .routes import _now
            props[f] = _now()
    set_str = ", ".join([f"{k}: ${k}" for k in props.keys()])
    cypher = f"CREATE (n:{ttype} {{{set_str}}}) RETURN n"
    records = _run_cypher(cypher, props)
    if records:
        node = _get_node_props(records[0].get("n", {}))
        return str(node.get("Id") or new_id).strip()
    return new_id


def _rows_from_graph_matches(
    graph_matches: dict,
    test_project_limit: int = 2,
    indicator_limit: int = 2,
    max_total_rows: int = 2,
) -> list:
    """从召回 dict 中取 TestProject、Indicator 推荐行；默认总共最多 max_total_rows 条（先项目后指标）。"""
    if not isinstance(graph_matches, dict):
        graph_matches = {}
    rows: list = []
    for item in (graph_matches.get('test_project') or [])[:test_project_limit]:
        it = item or {}
        raw_id = it.get('id') or it.get('Id')
        tid = str(raw_id).strip() if raw_id is not None else ''
        if not tid:
            continue
        nm = (it.get('name') or it.get('Name') or '').strip()
        rows.append({
            'target_type': 'TestProject',
            'target_id': tid,
            'node_name': nm or tid,
            'rel_type': 'CON',
            'rate': it.get('rate', it.get('score')),
        })
        if len(rows) >= max_total_rows:
            return rows
    for item in (graph_matches.get('indicator') or [])[:indicator_limit]:
        it = item or {}
        raw_id = it.get('id') or it.get('Id')
        tid = str(raw_id).strip() if raw_id is not None else ''
        if not tid:
            continue
        nm = (it.get('name') or it.get('Name') or '').strip()
        rows.append({
            'target_type': 'Indicator',
            'target_id': tid,
            'node_name': nm or tid,
            'rel_type': 'CON',
            'rate': it.get('rate', it.get('score')),
        })
        if len(rows) >= max_total_rows:
            break
    return rows


def _parse_rel_rate_for_import(rel: dict) -> tuple:
    """
    未带 rate/score 键或为空：合法，不 SET 相关度。
    优先读取 rate；兼容旧推荐结果中的 score。
    带了 rate/score 但不可解析为 float：非法，整行跳过 (返回 (None, False)）。
    """
    if 'rate' in rel:
        v = rel.get('rate')
    elif 'score' in rel:
        v = rel.get('score')
    else:
        return None, True
    if v is None or v == '':
        return None, True
    if isinstance(v, str) and not v.strip():
        return None, True
    try:
        rate = float(v)
    except (TypeError, ValueError):
        return None, False
    if rate < 0 or rate > 1:
        return None, False
    return rate, True


def _testmethod_props_by_name(name: str):
    """按「名称」在库中查 TestMethod，有则返回 _get_node_props 结果，否则 None。"""
    rows = _testmethod_list_by_name(name, limit=1)
    return rows[0] if rows else None


def _testmethod_list_by_name(name: str, limit: int = 20):
    """按名称查全部同名 TestMethod（供前端展示 SYNR 与一键复制）。"""
    n = (name or '').strip()
    if not n:
        return []
    from .routes import _run_cypher, _get_node_props
    lim = max(1, min(int(limit or 20), 50))
    recs = _run_cypher(
        "MATCH (n:TestMethod) WHERE n.Name = $name RETURN n ORDER BY n.Create_time DESC LIMIT $lim",
        {'name': n, 'lim': lim},
    )
    out = []
    for row in recs or []:
        props = _get_node_props(row.get('n', {}))
        if props:
            out.append(props)
    return out


def _file_md5_from_path(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _file_md5_from_upload(file_storage) -> str:
    pos = file_storage.tell() if hasattr(file_storage, 'tell') else None
    h = hashlib.md5()
    while True:
        chunk = file_storage.read(8192)
        if not chunk:
            break
        h.update(chunk)
    try:
        if pos is not None:
            file_storage.seek(pos)
        else:
            file_storage.seek(0)
    except Exception:
        pass
    return h.hexdigest()


def _file_node_props_from_neo(record_node) -> Dict[str, Any]:
    from .routes import _get_node_props
    props = _get_node_props(record_node)
    if not props:
        return {}
    fid = str(props.get('id') or props.get('Id') or '').strip()
    return {
        'id': fid,
        'name': str(props.get('name') or props.get('Name') or fid),
        'type': str(props.get('type') or props.get('Type') or ''),
        'label': str(props.get('label') or props.get('Label') or ''),
        'chroma_id': str(props.get('Chroma_id') or props.get('chroma_id') or ''),
        'chroma_chunks': int(props.get('Chroma_chunks') or props.get('chroma_chunks') or 0),
        'minio_id': str(props.get('minIo_id') or props.get('minio_id') or ''),
        'file_type': str(props.get('File_type') or props.get('file_type') or '').lower(),
        'file_md5': str(props.get('File_md5') or props.get('file_md5') or '').lower(),
        'create_time': str(props.get('Create_time') or props.get('create_time') or ''),
    }


def _find_file_node_by_md5(file_md5: str) -> Optional[Dict[str, Any]]:
    from .routes import _run_cypher
    md5 = (file_md5 or '').strip().lower()
    if not md5:
        return None
    try:
        recs = _run_cypher(
            "MATCH (f:File) WHERE toLower(coalesce(f.File_md5, '')) = $md5 RETURN f LIMIT 1",
            {'md5': md5},
        )
        if recs:
            return _file_node_props_from_neo(recs[0].get('f', {}))
    except Exception as exc:
        logger.warning("[FLGL] find file by md5 failed: %s", exc)
    return None


def _linked_test_methods_for_file(file_id: str) -> List[Dict[str, Any]]:
    from .routes import _run_cypher, _get_node_props
    fid = (file_id or '').strip()
    if not fid:
        return []
    try:
        recs = _run_cypher(
            """
            MATCH (f:File {Id: $fid})
            OPTIONAL MATCH (f)-[:BELONGS_TO]->(m1:TestMethod)
            OPTIONAL MATCH (m2:TestMethod)
            WHERE coalesce(m2.Source_id, '') = $fid
            WITH collect(DISTINCT m1) + collect(DISTINCT m2) AS ms
            UNWIND ms AS m
            WITH DISTINCT m WHERE m IS NOT NULL
            RETURN m ORDER BY coalesce(m.Name, m.Id)
            """,
            {'fid': fid},
        )
    except Exception as exc:
        logger.warning("[FLGL] linked methods for file %s: %s", fid, exc)
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in recs or []:
        props = _get_node_props(row.get('m', {}))
        if not props:
            continue
        mid = str(props.get('id') or props.get('Id') or '').strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({
            'id': mid,
            'name': str(props.get('name') or props.get('Name') or mid),
            'source_name': str(props.get('source_name') or props.get('Source_name') or ''),
        })
    return out


def _chroma_plain_text_for_doc(doc_id: str) -> str:
    doc_id = (doc_id or '').strip()
    if not doc_id:
        return ''
    coll = current_app.config.get('CHROMA_COLLECTION_OBJ')
    if not coll:
        return ''
    try:
        results = coll.get(where={'doc_id': doc_id}, include=['documents', 'metadatas'])
        docs = results.get('documents') or []
        metas = results.get('metadatas') or []
        pairs: List[Tuple[int, str]] = []
        for i, doc in enumerate(docs):
            if not doc:
                continue
            meta = metas[i] if i < len(metas) and isinstance(metas[i], dict) else {}
            idx = int(meta.get('chunk_index', i) or i)
            pairs.append((idx, str(doc)))
        pairs.sort(key=lambda x: x[0])
        return '\n\n'.join(t for _, t in pairs if t.strip())
    except Exception as exc:
        logger.warning("[FLGL] chroma plain text doc_id=%s: %s", doc_id, exc)
        return ''


def _prepare_method_extract_input_path(
    uploaded_path: str,
    original_filename: str,
    *,
    use_chroma_source: bool = False,
    source_file_id: str = '',
) -> Tuple[str, str]:
    """PDF 且向量库已有分片时，用 Chroma 文本作为 LLM 输入（转为临时 md）。"""
    ext = os.path.splitext(original_filename or uploaded_path)[1].lower()
    if not use_chroma_source or ext != '.pdf':
        return uploaded_path, ext
    doc_id = (source_file_id or '').strip()
    if doc_id.upper().startswith('WJ_'):
        doc_id = doc_id[3:]
    text = _chroma_plain_text_for_doc(doc_id)
    if not text.strip():
        logger.info("[method-extract] Chroma 无可用文本，仍用上传 PDF doc_id=%s", doc_id)
        return uploaded_path, ext
    md_path = os.path.join(os.path.dirname(uploaded_path), '_chroma_source.md')
    with open(md_path, 'w', encoding='utf-8') as mf:
        mf.write(text)
    logger.info("[method-extract] PDF 复用 Chroma 向量文本 doc_id=%s chars=%d", doc_id, len(text))
    return md_path, '.md'


def _normalize_wjgl_file_id(file_id: str) -> str:
    fid = (file_id or '').strip()
    if not fid:
        return ''
    if fid.upper().startswith('WJ_'):
        return fid
    return f'WJ_{fid.lstrip("_")}'


def _ensure_method_source_file_link(
    method_id: str,
    source_file_id: str,
    *,
    section_ids: Optional[List[str]] = None,
    section_nos: Optional[List[str]] = None,
    source_category: str = '',
    extract_source: str = '',
) -> None:
    """章节/文件提取的方法：建立 File -[:BELONGS_TO]-> TestMethod，并在边上记录来源章节。"""
    from .routes import _run_cypher

    fid = _normalize_wjgl_file_id(source_file_id)
    mid = (method_id or '').strip()
    if not fid or not mid:
        return
    props = {
        'source_section_ids': json.dumps(list(section_ids or []), ensure_ascii=False),
        'source_section_nos': json.dumps(list(section_nos or []), ensure_ascii=False),
        'source_category': (source_category or '').strip(),
        'extract_source': (extract_source or '').strip(),
    }
    try:
        _run_cypher(
            """
            MATCH (f:File {Id: $file_id})
            MATCH (m:TestMethod {Id: $method_id})
            MERGE (f)-[r:BELONGS_TO]->(m)
            SET r += $props
            """,
            {'file_id': fid, 'method_id': mid, 'props': props},
        )
    except Exception as exc:
        logger.warning("[FLGL] link method %s -> file %s failed: %s", mid, fid, exc)


def _parse_method_source_sections(method_data: dict) -> Tuple[List[str], List[str], str, str]:
    """从 method-import 载荷解析来源章节与分类。"""
    section_ids: List[str] = []
    section_nos: List[str] = []
    for key, out in (
        ('Source_section_ids', section_ids),
        ('source_section_ids', section_ids),
    ):
        raw = method_data.get(key)
        if isinstance(raw, list):
            out.extend(str(x).strip() for x in raw if str(x).strip())
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    out.extend(str(x).strip() for x in parsed if str(x).strip())
            except Exception:
                pass
    for key, out in (
        ('Source_section_nos', section_nos),
        ('source_section_nos', section_nos),
    ):
        raw = method_data.get(key)
        if isinstance(raw, list):
            out.extend(str(x).strip() for x in raw if str(x).strip())
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    out.extend(str(x).strip() for x in parsed if str(x).strip())
            except Exception:
                pass
    category = str(
        method_data.get('Source_category')
        or method_data.get('source_category')
        or ''
    ).strip()
    extract_source = str(
        method_data.get('Extract_source')
        or method_data.get('extract_source')
        or ''
    ).strip()
    return section_ids, section_nos, category, extract_source


def _resolve_method_auto_extract_file(
    tmp_dir: str,
    *,
    source_file_id: str = '',
    object_name: str = '',
    use_chroma_source: bool = False,
) -> Tuple[str, str, str]:
    """上传文件或按 object_name 从 MinIO 下载，供 LLM 自动提取使用。返回 (filepath, ext, original_filename)。"""
    uploaded = request.files.get('file')
    object_name = (object_name or request.form.get('object_name') or '').strip()
    source_file_id = (source_file_id or request.form.get('source_file_id') or 'WJ_auto0001').strip()

    if uploaded and uploaded.filename:
        original_filename = uploaded.filename
        ext = os.path.splitext(original_filename)[1].lower()
        filename = _safe_filename(original_filename)
        filepath = os.path.join(tmp_dir, filename)
        uploaded.save(filepath)
        filepath = _ensure_filepath_extension(filepath, ext)
        filepath, ext = _prepare_method_extract_input_path(
            filepath,
            original_filename,
            use_chroma_source=use_chroma_source,
            source_file_id=source_file_id,
        )
        filepath = _ensure_filepath_extension(filepath, ext)
        return filepath, ext, original_filename

    if not object_name:
        raise ValueError('没有文件')

    minio_client, bucket_name = _get_minio_runtime()
    if not minio_client or not bucket_name:
        raise ValueError('MinIO 未初始化')
    minio_path = object_name.replace('\\', '/')
    original_filename = os.path.basename(minio_path) or 'origin.bin'
    temp_path = os.path.join(tmp_dir, original_filename)
    minio_client.fget_object(bucket_name, minio_path, temp_path)
    ext = os.path.splitext(temp_path)[1].lower()
    filepath, ext = _prepare_method_extract_input_path(
        temp_path,
        original_filename,
        use_chroma_source=use_chroma_source,
        source_file_id=source_file_id,
    )
    filepath = _ensure_filepath_extension(filepath, ext)
    return filepath, ext, original_filename


def _dedupe_check_payload(file_md5: str, existing: Dict[str, Any]) -> Dict[str, Any]:
    file_id = str(existing.get('id') or '').strip()
    doc_id = file_id[3:] if file_id.upper().startswith('WJ_') else file_id
    chroma_chunks = int(existing.get('chroma_chunks') or 0)
    file_type = str(existing.get('file_type') or '').lower()
    is_pdf = file_type == 'pdf' or str(existing.get('name') or '').lower().endswith('.pdf')
    methods = _linked_test_methods_for_file(file_id)
    return {
        'duplicate': True,
        'file_md5': file_md5,
        'file_node_id': file_id,
        'doc_id': doc_id,
        'file_name': existing.get('name') or '',
        'minio_id': existing.get('minio_id') or '',
        'chroma_id': existing.get('chroma_id') or doc_id,
        'chroma_chunks': chroma_chunks,
        'has_chroma': chroma_chunks > 0,
        'is_pdf': is_pdf,
        'use_chroma_source': bool(is_pdf and chroma_chunks > 0),
        'linked_methods': methods,
        'linked_method_count': len(methods),
    }


def _dedupe_str_list(values):
    out = []
    seen = set()
    for value in values or []:
        if value is None:
            continue
        s = str(value).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


_KNOWN_UPLOAD_EXT_BARE = frozenset({'docx', 'doc', 'pdf', 'txt', 'md', 'bin'})


def _safe_filename(name: str) -> str:
    """保留扩展名；secure_filename 会 strip 中文导致落盘为 'docx' 无点号。"""
    raw = (name or '').strip() or 'upload.bin'
    ext = os.path.splitext(raw)[1].lower()
    if ext:
        ext = ext.replace('．', '.').replace('。', '.')
    safe = secure_filename(raw)
    ext_bare = ext.lstrip('.') if ext else ''
    stripped_to_ext_only = (
        bool(ext_bare)
        and safe.lower() == ext_bare
        or ('.' not in safe and safe.lower() in _KNOWN_UPLOAD_EXT_BARE)
    )
    if not safe or stripped_to_ext_only or ('.' not in safe and ext):
        fallback_ext = ext or (f".{safe.lower()}" if safe.lower() in _KNOWN_UPLOAD_EXT_BARE else '.bin')
        safe = f"upload_{uuid.uuid4().hex[:8]}{fallback_ext}"
    elif ext and not safe.lower().endswith(ext):
        safe = f"{safe}{ext}"
    return safe


def _ensure_filepath_extension(filepath: str, ext: str) -> str:
    """若落盘路径无扩展名但原始上传有 ext，rename 后返回新路径。"""
    if not filepath or not ext:
        return filepath
    ext = ext if ext.startswith('.') else f".{ext}"
    if os.path.splitext(filepath)[1].lower():
        return filepath
    new_path = filepath + ext
    try:
        if os.path.isfile(filepath) and not os.path.isfile(new_path):
            os.rename(filepath, new_path)
            logger.info("[method-extract] 落盘文件补扩展名: %s -> %s", os.path.basename(filepath), os.path.basename(new_path))
            return new_path
    except OSError as exc:
        logger.warning("[method-extract] 补扩展名失败: %s", exc)
    return filepath


def _html_escape(text: str) -> str:
    import html
    return html.escape(str(text or ''), quote=False)


def _plain_text_to_html(text: str) -> str:
    s = str(text or '').strip()
    if not s:
        return ''
    if re.search(r'<\s*(p|div|table|ul|ol|li|img|h[1-6]|br)\b', s, re.I):
        return s
    parts = [p.strip() for p in re.split(r'\n\s*\n+', s) if p.strip()]
    if not parts:
        return ''
    return ''.join(f'<p>{_html_escape(p).replace(chr(10), "<br>")}</p>' for p in parts)


def _first_section_heading_label(html: str) -> str:
    """从块内首个章节标题提取展示用标签（如「4 试验设备和仪表」）。"""
    h = str(html or "")
    if not h.strip():
        return ""
    m = re.search(
        r'data-section-no="([^"]+)"[^>]*>\s*<strong>\s*([^<]+?)\s*</strong>',
        h,
        re.I | re.S,
    )
    if m:
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        sno = m.group(1).strip()
        if label and not label.startswith(sno):
            return f"{sno} {label}".strip()
        return label or sno
    m2 = re.search(r"<strong>\s*(\d+(?:\.\d+)*)\s+([^<]+?)\s*</strong>", h, re.I)
    if m2:
        return f"{m2.group(1)} {m2.group(2).strip()}".strip()
    return ""


def _apply_synr_document_trace(
    synr_html: str,
    *,
    full_text: str = "",
    all_tables: Optional[list] = None,
    all_figures: Optional[list] = None,
    category: str = "",
    doc_id: str = "",
) -> str:
    """对 SYNR HTML 做全文级见/按/照/见表/见图溯源。"""
    html = str(synr_html or "").strip()
    if not html or not str(full_text or "").strip():
        return html
    try:
        from .method_section_enrich import apply_document_trace_to_html
    except ImportError:
        return html
    try:
        return apply_document_trace_to_html(
            html,
            full_text=full_text,
            all_tables=list(all_tables or []),
            all_figures=list(all_figures or []),
            category=category,
            doc_id=doc_id,
        )
    except Exception as exc:
        logger.warning("[method-extract] 全文溯源失败（已跳过）: %s", exc)
        return html


def _trace_context_from_parsed(extraction_data: dict, parsed_doc: dict) -> tuple:
    full_text = str((extraction_data or {}).get("full_text") or (parsed_doc or {}).get("full_text") or "")
    all_tables = list((parsed_doc or {}).get("all_tables") or [])
    all_figures = list((parsed_doc or {}).get("all_figures") or [])
    return full_text, all_tables, all_figures


def _trace_context_from_document_dir(document_dir: str) -> tuple:
    """从 word_llm 落盘目录加载全文，供溯源内联。"""
    full_text = ""
    all_tables: list = []
    all_figures: list = []
    if not document_dir or not os.path.isdir(document_dir):
        return full_text, all_tables, all_figures
    cp_path = os.path.join(document_dir, "extraction_checkpoint.json")
    if os.path.isfile(cp_path):
        try:
            with open(cp_path, "r", encoding="utf-8") as f:
                cp = json.load(f)
            full_text = str(cp.get("full_text") or full_text)
        except (OSError, json.JSONDecodeError):
            pass
    ft_path = os.path.join(document_dir, "document_full_text.txt")
    if not full_text.strip() and os.path.isfile(ft_path):
        try:
            with open(ft_path, "r", encoding="utf-8") as f:
                full_text = f.read()
        except OSError:
            pass
    parsed_path = os.path.join(document_dir, "parsed_document.json")
    if os.path.isfile(parsed_path):
        try:
            with open(parsed_path, "r", encoding="utf-8") as f:
                parsed_doc = json.load(f)
            if not full_text.strip():
                full_text = str(parsed_doc.get("full_text") or "")
            all_tables = list(parsed_doc.get("all_tables") or [])
            all_figures = list(parsed_doc.get("all_figures") or [])
        except (OSError, json.JSONDecodeError):
            pass
    return full_text, all_tables, all_figures


def _method_parts_to_synr(method: dict, gallery_html: str = '') -> str:
    for key in ('synr', 'SYNR', 'method_content', 'content'):
        v = method.get(key) if isinstance(method, dict) else None
        if v is not None and str(v).strip():
            raw = str(v).strip()
            if re.search(
                r"<\s*(?:p|div|table|ul|ol|li|figure|img|h[1-6]|strong)\b|wjgl-section-heading|data-section-no=",
                raw,
                re.I,
            ):
                return raw
            return _plain_text_to_html(raw)

    sytj_html = text_block_to_html(method.get('sytj'))
    gallery = str(gallery_html or '').strip()
    if gallery:
        sytj_html = f'{sytj_html}{gallery}'.strip() if sytj_html else gallery

    sections = [
        ('1. 试验条件', sytj_html),
        ('2. 试验过程', text_block_to_html(method.get('sygc'))),
        ('3. 结果评定', text_block_to_html(method.get('jgpd'))),
    ]
    chunks = []
    for default_title, body in sections:
        body = str(body or '').strip()
        if not body:
            continue
        chunks.append(f'<h4>{_html_escape(default_title)}</h4>{body}')
    return ''.join(chunks)


def _legacy_method_synr_from_node(node: dict) -> str:
    for key in ('synr', 'SYNR'):
        v = node.get(key)
        if v is not None and str(v).strip():
            return str(v)
    return _method_parts_to_synr({
        'sytj': node.get('sytj') or node.get('Sytj'),
        'sygc': node.get('sygc') or node.get('Sygc'),
        'jgpd': node.get('jgpd') or node.get('Jgpd'),
    })


def _method_image_url(object_path: str) -> str:
    return f"/api/FLGL_method/method-image?path={quote(str(object_path or ''), safe='')}"


def _preview_parse_image_url(cache_key: str, rel_path: str) -> str:
    return (
        f"/api/FLGL_method/preview-parse-image"
        f"?cache_key={quote(str(cache_key or ''), safe='')}"
        f"&path={quote(str(rel_path or ''), safe='')}"
    )


def _extract_method_image_paths(html: str) -> list:
    if not html:
        return []
    paths = []
    seen = set()
    for src in re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', str(html), flags=re.I):
        path = ''
        if 'method-image?' in src and 'path=' in src:
            path = src.split('path=', 1)[1].split('&', 1)[0]
            path = unquote(path)
        elif src.startswith('试验方法/'):
            path = src
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _ocr_figure_gallery_html_from_parsed_doc(
    parsed_document_path: str,
    draft_method_id: str,
    user_id: str,
) -> str:
    """
    将解析结果中 OCR 产生的本地附图上传到 MinIO「试验方法/{draft_method_id}/图片/」，
    返回可写入 WangEditor 的 HTML 片段（表格仍由 LLM 块中的 tables_html 提供）。
    """
    draft_method_id = (draft_method_id or "").strip()
    parsed_document_path = (parsed_document_path or "").strip()
    if not draft_method_id or not parsed_document_path or not os.path.isfile(parsed_document_path):
        return ""

    minio_client, bucket_name = _get_minio_runtime()
    if not minio_client or not bucket_name:
        logger.warning("OCR 附图上传跳过：MinIO 未初始化")
        return ""

    try:
        with open(parsed_document_path, "r", encoding="utf-8") as f:
            parsed = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取 parsed_document 失败，跳过 OCR 附图: %s", e)
        return ""

    pages = list(parsed.get("pages") or [])
    seen_local: set[str] = set()
    parts: list[str] = []

    try:
        from .routes import _run_cypher
    except Exception:
        _run_cypher = None

    if not minio_client.bucket_exists(bucket_name):
        try:
            minio_client.make_bucket(bucket_name)
        except Exception:
            pass

    for page in pages:
        for fig in page.get("figures") or []:
            if not isinstance(fig, dict):
                continue
            local_path = str(fig.get("path") or "").strip()
            if not local_path or local_path in seen_local or not os.path.isfile(local_path):
                continue
            seen_local.add(local_path)

            base_fn = _safe_filename(fig.get("filename") or os.path.basename(local_path))
            base, ext = os.path.splitext(base_fn)
            ext = ext or ".png"
            object_name = f"试验方法/{draft_method_id}/图片/{base_fn}"
            try:
                minio_client.stat_object(bucket_name, object_name)
                object_name = f"试验方法/{draft_method_id}/图片/{base}_{uuid.uuid4().hex[:8]}{ext}"
            except Exception:
                pass

            content_type = mimetypes.guess_type(base_fn)[0] or "image/png"
            try:
                minio_client.fput_object(bucket_name, object_name, local_path, content_type=content_type)
            except Exception as e:
                logger.warning("OCR 附图上传 MinIO 失败 %s: %s", local_path, e)
                continue

            public_url = _method_image_url(object_name)
            alt = _html_escape(str(fig.get("description") or fig.get("ocr_text") or "附图")[:200])
            parts.append(
                f'<p><img src="{public_url}" alt="{alt}" style="max-width:100%;height:auto;" /></p>'
            )

            if _run_cypher:
                file_id = f"WJ_method_img_{uuid.uuid4().hex[:12]}"
                props = {
                    "Id": file_id,
                    "Type": "方法图片",
                    "Label": "方法图片",
                    "Name": base_fn,
                    "Chroma_id": "",
                    "minIo_id": object_name,
                    "Image_true": True,
                    "Image_minIO_path": object_name,
                    "Status": "启用",
                    "User_id": user_id,
                    "Create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "File_type": ext.lstrip("."),
                }
                try:
                    _run_cypher(
                        """
                        MERGE (f:File {Id: $Id})
                        SET f.Type = $Type, f.Label = $Label, f.Name = $Name,
                            f.Chroma_id = $Chroma_id, f.minIo_id = $minIo_id,
                            f.Image_true = $Image_true, f.Image_minIO_path = $Image_minIO_path,
                            f.Status = $Status, f.User_id = $User_id, f.Create_time = $Create_time,
                            f.File_type = $File_type
                        RETURN f
                        """,
                        props,
                    )
                except Exception as fe:
                    logger.debug("OCR 附图 File 节点写入跳过: %s", fe)

    if not parts:
        return ""
    return '<h4>文档附图（OCR）</h4>' + "".join(parts)


def _get_minio_runtime():
    minio_client = current_app.config.get('MINIO_CLIENT')
    bucket_name = current_app.config.get('MINIO_BUCKET_NAME')
    if not minio_client or not bucket_name:
        try:
            from config import Config
            minio_client = minio_client or Config.MINIO_CLIENT
            bucket_name = bucket_name or Config.MINIO_BUCKET_NAME
        except Exception:
            pass
    return minio_client, bucket_name


flgl_method_bp = Blueprint('flgl_method', __name__, url_prefix='/api/FLGL_method')

# 任务状态存储（简单内存版，生产环境可换 Redis）
_TASK_STATUS = {}
_TASK_LOCK = threading.Lock()


def _set_task(task_id, **kwargs):
    with _TASK_LOCK:
        _TASK_STATUS[task_id] = kwargs


def _get_task(task_id):
    with _TASK_LOCK:
        return _TASK_STATUS.get(task_id)


@flgl_method_bp.route('/file-duplicate-check', methods=['POST', 'OPTIONS'])
def method_file_duplicate_check():
    """方法提取前：按文件 MD5 查重，返回已入库文件及关联试验方法。"""
    if request.method == 'OPTIONS':
        return '', 200
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有文件'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'error': '文件名为空'}), 400
    tmp_dir = tempfile.mkdtemp()
    try:
        safe = _safe_filename(file.filename)
        path = os.path.join(tmp_dir, safe)
        file.save(path)
        file_md5 = _file_md5_from_path(path)
        existing = _find_file_node_by_md5(file_md5)
        if not existing:
            return jsonify({'success': True, 'data': {'duplicate': False, 'file_md5': file_md5}})
        payload = _dedupe_check_payload(file_md5, existing)
        return jsonify({'success': True, 'data': payload})
    except Exception as exc:
        logger.error('[file-duplicate-check] failed: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@flgl_method_bp.route('/method-extract', methods=['POST'])
def method_extract():
    """上传标准文件，调用洪实 pipeline 提取方法 + 图谱召回"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    source_file_id = request.form.get('source_file_id', 'WJ_std0001')
    user_id = request.form.get('user_id', 'admin')
    draft_method_id = (request.form.get('draft_method_id') or request.form.get('method_id') or '').strip()
    original_filename = file.filename
    force_refresh = str(request.form.get('force_refresh', '') or '').strip().lower() in (
        '1', 'true', 'yes', 'on',
    )

    try:
        from .routes import _run_cypher, _get_node_props
        if not force_refresh:
            cache_records = _run_cypher(
                """
                MATCH (n:TestMethod)
                WHERE n.Source_name = $source_name
                RETURN n ORDER BY n.Create_time DESC LIMIT 20
                """,
                {'source_name': original_filename},
            )
            cached_methods = []
            for rec in cache_records or []:
                node = _get_node_props(rec.get('n', {}))
                if not node:
                    continue
                synr = _legacy_method_synr_from_node(node)
                cached_methods.append({
                    'id': node.get('id', ''),
                    'name': node.get('name', ''),
                    'type': node.get('type', ''),
                    'class': node.get('class', ''),
                    'label': node.get('label', ''),
                    'description': node.get('description', ''),
                    'sy': node.get('sy', ''),
                    'synr': synr,
                    'source_name': node.get('source_name', original_filename),
                    'source_id': node.get('source_id', ''),
                    'recommended_relations': [],
                    'name_duplicate': {'duplicate': True, 'existing': node},
                })
            if cached_methods:
                first = cached_methods[0]
                result_data = {
                    **first,
                    'methods_count': len(cached_methods),
                    'methods': cached_methods,
                    'all_methods': [{'name': m.get('name', ''), 'type': m.get('type', ''), 'summary': m.get('description', '')} for m in cached_methods],
                    'recommended_nodes': [],
                    'cache_hit': True,
                    'extract_source': 'database',
                }
                log_user_action(f"分类管理-方法抽取命中缓存 file={original_filename[:120]} methods={len(cached_methods)}")
                return jsonify({'success': True, 'data': result_data})
    except Exception as cache_e:
        logger.warning(f"方法提取缓存查询失败，继续调用抽取流程: {cache_e}")

    # 保存到临时目录；中文文件名下 secure_filename 可能为空，统一走安全兜底文件名。
    tmp_dir = tempfile.mkdtemp()
    filename = _safe_filename(file.filename)
    filepath = os.path.join(tmp_dir, filename)
    file.save(filepath)

    _saved_backend_config = None
    try:
        # 必须在注入 GetTestMethod 的 backend.config 之前 import，否则下方会
        # sys.modules['backend.config'] = 子包，再 import Config 得到的是子模块的 Config（无 FLGL_TESTMETHOD_*）
        from backend.config import Config as MainConfig

        # 延迟导入，避免启动时加载 pipeline 依赖
        import numpy as np
        if not hasattr(np, 'sctypes'):
            np.sctypes = {
                'int': [np.int8, np.int16, np.int32, np.int64],
                'uint': [np.uint8, np.uint16, np.uint32, np.uint64],
                'float': [np.float16, np.float32, np.float64],
                'complex': [np.complex64, np.complex128],
                'others': [np.bool_, np.object_, np.str_, np.bytes_],
            }
        # 与仓库一致：包在 GetTestMethod/ 下，无已废弃的 GetTestMethod/testmethod/ 目录
        _flgl_dir = os.path.dirname(os.path.abspath(__file__))
        _gtm_root = os.path.join(_flgl_dir, 'GetTestMethod')
        if _flgl_dir not in sys.path:
            sys.path.insert(0, _flgl_dir)
        if _gtm_root not in sys.path:
            sys.path.insert(0, _gtm_root)

        # 给 pipeline 的独立 backend.config 注入主项目已初始化好的运行时对象
        import importlib.util
        method_backend_config_path = os.path.join(_gtm_root, 'backend', 'config.py')
        if not os.path.isfile(method_backend_config_path):
            return jsonify({
                "success": False,
                "error": f"未找到方法抽取配置: {method_backend_config_path}",
            }), 500
        spec = importlib.util.spec_from_file_location('backend.config', method_backend_config_path)
        method_backend_config = importlib.util.module_from_spec(spec)
        _saved_backend_config = sys.modules.get('backend.config')
        sys.modules['backend.config'] = method_backend_config
        assert spec and spec.loader
        spec.loader.exec_module(method_backend_config)

        MethodConfig = method_backend_config.Config
        MethodConfig.OPENAI_CLIENT = current_app.config.get('OPENAI_CLIENT')
        MethodConfig.OPENAI_MODEL = (
            getattr(MainConfig, "FLGL_TESTMETHOD_OPENAI_MODEL", None)
            or current_app.config.get('OPENAI_MODEL', getattr(MethodConfig, 'OPENAI_MODEL', None))
        )
        MethodConfig.LLM_MAX_COMPLETION_TOKENS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_LLM_MAX_COMPLETION_TOKENS", 3072
        )
        MethodConfig.LLM_CONTEXT_TOKENS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_LLM_CONTEXT_TOKENS", 8192
        )
        MethodConfig.DISCOVERY_MAX_PROMPT_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DISCOVERY_MAX_PROMPT_CHARS", 11000
        )
        MethodConfig.DISCOVERY_CATALOGUE_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DISCOVERY_CATALOGUE_MAX_CHARS", 5000
        )
        MethodConfig.DISCOVERY_PREVIEW_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DISCOVERY_PREVIEW_MAX_CHARS", 6000
        )
        MethodConfig.DETAIL_MAX_PROMPT_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_MAX_PROMPT_CHARS", 10000
        )
        MethodConfig.DETAIL_SECTION_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_SECTION_MAX_CHARS", 2000
        )
        MethodConfig.DETAIL_CHUNK_BUDGET_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_CHUNK_BUDGET_CHARS", 8000
        )
        MethodConfig.DETAIL_TABLE_HTML_MAX_CHARS = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_TABLE_HTML_MAX_CHARS", 1200
        )
        MethodConfig.DETAIL_MAX_TABLES = _flgl_cfg_int(
            MainConfig, "FLGL_TESTMETHOD_DETAIL_MAX_TABLES", 40
        )
        MethodConfig.NEO4J_DRIVER = (
            current_app.config.get('FLGL_TESTMETHOD_NEO4J_DRIVER')
            or current_app.config.get('NEO4J_DRIVER')
        )
        MethodConfig.CHROMA_CLIENT = current_app.config.get('CHROMA_CLIENT')
        MethodConfig.CHROMA_COLLECTION_OBJ = current_app.config.get('CHROMA_COLLECTION_OBJ')
        MethodConfig.MINIO_CLIENT = current_app.config.get('MINIO_CLIENT')
        _bge = (getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_MODEL_PATH', None) or '').strip()
        MethodConfig.BGE_MODEL_PATH = os.path.abspath(_bge) if _bge else None
        MethodConfig.BGE_LOCAL_FILES_ONLY = bool(getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY', True))
        # 新请求使用当前 .env 中的 BGE 路径，避免单例缓存旧模型
        MethodConfig.TESTMETHOD_RETRIEVER = None
        if MethodConfig.BGE_MODEL_PATH:
            logger.info(
                "方法提取 BGE: %s, local_files_only=%s, Neo4j=%s",
                MethodConfig.BGE_MODEL_PATH,
                MethodConfig.BGE_LOCAL_FILES_ONLY,
                "FLGL 专用" if current_app.config.get("FLGL_TESTMETHOD_NEO4J_DRIVER") else "主库",
            )

        from GetTestMethod.pipeline import run_extract_only, run_recall_only

        # 解析：Word 原生；PDF 默认 MinerU（FLGL_PDF_FALLBACK=1 时可 fitz/OCR 回退）
        _f = os.path.abspath(__file__)
        _backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(_f)))
        _method_ocr_tmp = os.path.join(_backend_dir, "output_json", "ocr_tmp")

        logger.info(
            "[方法提取] 开始 parse+extract: file=%s ocr_tmp=%s",
            filename[:160],
            _method_ocr_tmp,
        )
        # 第一步：抽取
        extract_result = run_extract_only(
            file_path=filepath,
            source_file_id=source_file_id,
            user_id=user_id,
            output_root=os.path.join(tmp_dir, 'output'),
            ocr_output_dir=_method_ocr_tmp,
        )
        extraction_data = extract_result.get('extraction_data', {}) or {}
        logger.info(
            "[方法提取] 抽取完成: methods=%s checkpoint=%s",
            len(extraction_data.get('methods') or []),
            bool(extract_result.get('extraction_checkpoint_path')),
        )

        parsed_path = extract_result.get('parsed_document_path') or ''
        gallery_html = _ocr_figure_gallery_html_from_parsed_doc(parsed_path, draft_method_id, user_id)

        checkpoint_path = extract_result.get('extraction_checkpoint_path')
        methods = extraction_data.get('methods', [])
        if methods:
            m0 = methods[0]
            sytj0 = m0.get('sytj') if isinstance(m0.get('sytj'), dict) else {}
            logger.info(
                "[方法提取] 首条方法三段字数: sytj=%s sygc=%s jgpd=%s",
                len(str((sytj0 or {}).get('text', '') if isinstance(sytj0, dict) else sytj0) or ''),
                len(str((m0.get('sygc') or {}).get('text', '') if isinstance(m0.get('sygc'), dict) else '')),
                len(str((m0.get('jgpd') or {}).get('text', '') if isinstance(m0.get('jgpd'), dict) else '')),
            )

        parsed_doc: dict = {}
        if parsed_path and os.path.isfile(parsed_path):
            try:
                with open(parsed_path, "r", encoding="utf-8") as pf:
                    parsed_doc = json.load(pf)
            except (OSError, json.JSONDecodeError) as pe:
                logger.warning("[方法提取] 读取 parsed_document 失败: %s", pe)

        recall_result = None
        derived_from_links: list = []
        rel_fact_attach: dict = {}
        if checkpoint_path:
            try:
                logger.info("[方法提取] 开始向量召回: checkpoint=%s", checkpoint_path[:200])
                topk_config = {
                    'test_project': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT', 2),
                    'indicator': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR', 2),
                }
                recall_result = run_recall_only(
                    extraction_checkpoint_path=checkpoint_path,
                    source_file_id=source_file_id,
                    user_id=user_id,
                    output_root=os.path.join(tmp_dir, 'output'),
                    topk_config=topk_config,
                    save_method_json=False,
                )
                logger.info("[方法提取] 召回完成")
            except Exception as recall_e:
                logger.warning(f"方法提取召回失败，已降级为空推荐: {recall_e}")

        # RelFact：从 parse 结果挂接 fact_index / 关系候选
        try:
            if parsed_doc and source_file_id:
                graph_matches_payload: list = []
                enriched_for_attach = []
                if recall_result and isinstance(recall_result.get("result"), dict):
                    enriched_for_attach = recall_result.get("result", {}).get("methods") or []
                for idx, method in enumerate(methods or []):
                    gm = {}
                    if idx < len(enriched_for_attach) and isinstance(enriched_for_attach[idx], dict):
                        gm = enriched_for_attach[idx].get("graph_matches") or {}
                    graph_matches_payload.append({
                        "graph_matches": gm,
                        "test_method_id": draft_method_id or method.get("id") or "",
                    })
                from blueprints.RelFact.rel_fact_builder import attach_from_parsed, save_rel_facts_artifact

                std_code = str(extraction_data.get("source_file", {}).get("std_code") or "").strip()
                std_name = str(extraction_data.get("source_file", {}).get("file_name") or filename).strip()
                rel_fact_attach = attach_from_parsed(
                    file_node_id=source_file_id,
                    doc_id=source_file_id,
                    category="法规标准",
                    parsed=parsed_doc,
                    std_code=std_code,
                    std_name=std_name,
                    graph_matches=graph_matches_payload,
                )
                derived_from_links = list(rel_fact_attach.get("derived_links") or [])
                if rel_fact_attach.get("success"):
                    save_rel_facts_artifact("法规标准", source_file_id, rel_fact_attach)
        except Exception as rel_e:
            logger.warning("[方法提取] RelFact attach 失败（已跳过）: %s", rel_e)

        # 构造返回数据
        def _format_terms(terms):
            lines = []
            for item in terms or []:
                if isinstance(item, str):
                    s = item.strip()
                    if s:
                        lines.append(s)
                elif isinstance(item, dict):
                    term = str(item.get('term') or item.get('name') or item.get('text') or item.get('title') or '').strip()
                    definition = str(item.get('definition') or item.get('desc') or item.get('description') or '').strip()
                    if term and definition:
                        lines.append(f"{term}：{definition}")
                    elif term:
                        lines.append(term)
                    elif definition:
                        lines.append(definition)
                else:
                    s = str(item).strip()
                    if s:
                        lines.append(s)
            return '\n'.join(lines)

        if methods:
            first_method = methods[0]
            method_name = first_method.get('name', '')
            source_name = extraction_data.get('source_file', {}).get('file_name', filename)

            # 调试：打印 first_method 的 sytj/sygc/jgpd 结构
            logger.info(
                "[方法提取][调试] first_method sytj type=%s, sygc type=%s, jgpd type=%s",
                type(first_method.get('sytj')).__name__,
                type(first_method.get('sygc')).__name__,
                type(first_method.get('jgpd')).__name__,
            )
            logger.info(
                "[方法提取][调试] first_method sytj=%s",
                first_method.get('sytj'),
            )

            # 尝试从文档全文中提取标准全称（如"民用轻小型无人机系统环境试验方法 第7部分：湿热试验"）
            full_text = extraction_data.get('full_text', '') or ''
            import re
            # 匹配常见标准标题格式
            title_match = re.search(
                r'(民用轻小型无人机系统[^\n]{0,50}(?:第\s*\d+\s*部分[：:]\s*\S+))',
                full_text
            )
            if title_match:
                method_name = title_match.group(1).strip()
            elif ext == ".pdf" or re.search(r"(?:GB/?T?|GJB|HB|Q/)\s*[\d\.\-]+", full_text[:3000], re.I):
                from .method_section_enrich import recommend_standard_method_name_llm

                heading_outline = ""
                if isinstance(parsed_doc, dict):
                    headings = parsed_doc.get("flat_headings") or parsed_doc.get("toc_display") or []
                    if isinstance(headings, list):
                        heading_outline = "\n".join(
                            str(h.get("title") or h) if isinstance(h, dict) else str(h)
                            for h in headings[:40]
                        )
                std_m = re.search(r"((?:GB/?T?|GJB|HB|Q/)\s*[\d\.\-]+)", full_text[:5000], re.I)
                method_name = recommend_standard_method_name_llm(
                    file_name=source_name,
                    full_text=full_text,
                    heading_outline=heading_outline,
                    candidate_name=method_name,
                    std_code=std_m.group(1) if std_m else "",
                )
            elif method_name and source_name:
                # 如果 LLM 提取的名称不含标准编号，拼上文件名前缀
                base_name = os.path.splitext(source_name)[0]
                if base_name and base_name not in method_name:
                    method_name = f"{base_name} {method_name}"

            enriched_from_recall: list = []
            if recall_result and isinstance(recall_result.get('result'), dict):
                enriched_from_recall = recall_result.get('result', {}).get('methods') or []

            from .method_section_enrich import generate_method_description_llm, label_from_source_filename

            method_label = label_from_source_filename(source_name)
            trace_full_text, trace_all_tables, trace_all_figures = _trace_context_from_parsed(extraction_data, parsed_doc)

            formatted_methods = []
            for index, method in enumerate(methods):
                current_name = method.get('name', '')
                if index == 0 and method_name:
                    current_name = method_name
                graph_matches: dict = {}
                if index < len(enriched_from_recall) and isinstance(enriched_from_recall[index], dict):
                    gm = enriched_from_recall[index].get('graph_matches')
                    if isinstance(gm, dict):
                        graph_matches = gm
                rec_rows = _rows_from_graph_matches(
                    graph_matches,
                    _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT', 2),
                    _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR', 2),
                    max_total_rows=_flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_RELATION_ROWS', 2),
                )
                existing_by_name = _testmethod_props_by_name(current_name)
                synr_raw = _method_parts_to_synr(method, gallery_html if index == 0 else '')
                synr_traced = _apply_synr_document_trace(
                    synr_raw,
                    full_text=trace_full_text,
                    all_tables=trace_all_tables,
                    all_figures=trace_all_figures,
                )
                method_description = (
                    str(method.get('description', '') or method.get('summary', '') or '').strip()
                )
                if not method_description:
                    method_description = generate_method_description_llm(
                        current_name,
                        content_html=synr_traced,
                        source_name=source_name,
                        std_code=str(extraction_data.get('source_file', {}).get('std_code') or ''),
                    )
                formatted_methods.append({
                    'id': draft_method_id or method.get('id', ''),
                    'name': current_name,
                    'type': method.get('type', ''),
                    'label': method_label,
                    'description': method_description,
                    'sy': _format_terms(extraction_data.get('terms', [])),
                    'synr': synr_traced,
                    'sytj': text_block_to_markdown(method.get('sytj')),
                    'sygc': text_block_to_markdown(method.get('sygc')),
                    'jgpd': text_block_to_markdown(method.get('jgpd')),
                    'source_name': extraction_data.get('source_file', {}).get('file_name', filename),
                    'recommended_relations': rec_rows,
                    'name_duplicate': {
                        'duplicate': existing_by_name is not None,
                        'existing': existing_by_name,
                    },
                })

            first_synr = _apply_synr_document_trace(
                _method_parts_to_synr(first_method, gallery_html),
                full_text=trace_full_text,
                all_tables=trace_all_tables,
                all_figures=trace_all_figures,
            )
            first_description = (
                str(first_method.get('description', '') or first_method.get('summary', '') or '').strip()
            )
            if not first_description:
                first_description = generate_method_description_llm(
                    method_name,
                    content_html=first_synr,
                    source_name=source_name,
                    std_code=str(extraction_data.get('source_file', {}).get('std_code') or ''),
                )

            result_data = {
                'draft_method_id': draft_method_id,
                'id': draft_method_id,
                'name': method_name,
                'type': first_method.get('type', ''),
                'label': method_label,
                'description': first_description,
                'sy': _format_terms(extraction_data.get('terms', [])),
                'synr': first_synr,
                'extract_source': 'pipeline',
                'sytj': text_block_to_markdown(first_method.get('sytj')),
                'sygc': text_block_to_markdown(first_method.get('sygc')),
                'jgpd': text_block_to_markdown(first_method.get('jgpd')),
                'source_name': extraction_data.get('source_file', {}).get('file_name', filename),
                'methods_count': len(methods),
                'methods': formatted_methods,
                'all_methods': [
                    {
                        'name': m.get('name', ''),
                        'type': m.get('type', ''),
                        'summary': m.get('description', '') or m.get('summary', ''),
                    }
                    for m in methods
                ],
                'recommended_nodes': [],
                'derived_from_links': derived_from_links,
                'rel_fact': rel_fact_attach if rel_fact_attach else None,
            }

            # 调试：打印返回数据的关键字段长度
            logger.info(
                "[方法提取][调试] result_data sytj_len=%d sygc_len=%d jgpd_len=%d synr_len=%d",
                len(result_data.get('sytj', '') or ''),
                len(result_data.get('sygc', '') or ''),
                len(result_data.get('jgpd', '') or ''),
                len(result_data.get('synr', '') or ''),
            )
        else:
            result_data = {
                'draft_method_id': draft_method_id,
                'name': '',
                'type': '',
                'label': '',
                'description': '未提取到方法',
                'sy': '',
                'synr': '',
                'sytj': '',
                'sygc': '',
                'jgpd': '',
                'source_name': filename,
                'methods_count': 0,
                'all_methods': [],
                'methods': [],
                'recommended_nodes': [],
                'derived_from_links': derived_from_links,
                'rel_fact': rel_fact_attach if rel_fact_attach else None,
            }

        log_user_action(f"分类管理-检测方法抽取 file={filename[:120]} methods={result_data.get('methods_count', 0)}")
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logger.error(f"方法提取失败: {e}")
        return jsonify({"success": False, "error": f"提取失败: {str(e)}"}), 500
    finally:
        if _saved_backend_config is not None:
            sys.modules['backend.config'] = _saved_backend_config
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@flgl_method_bp.route('/method-draft-id', methods=['GET'])
def method_draft_id():
    """为新增方法预分配 TestMethod Id，供方法图片按 试验方法/id/图片 保存。"""
    try:
        from .routes import _generate_id
        return jsonify({'success': True, 'data': {'method_id': _generate_id('TestMethod')}})
    except Exception as e:
        logger.error(f"生成方法草稿ID失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@flgl_method_bp.route('/methods-by-relation', methods=['GET'])
def methods_by_relation():
    """按关联类型返回方法列表，并附带关联项目/指标名称。

    related_type=TestProject|Indicator，可选 equipment_id：
    - TestProject：只展示该设备下项目关联的方法
    - Indicator：只展示该设备下指标关联的方法
    """
    related_type = (request.args.get('related_type') or 'TestProject').strip()
    equipment_id = (request.args.get('equipment_id') or '').strip()
    if related_type not in ('TestProject', 'Indicator'):
        return jsonify({'success': False, 'error': 'related_type 仅支持 TestProject 或 Indicator'}), 400
    try:
        from .routes import _run_cypher, _get_node_props
        rel_label = related_type
        if equipment_id:
            if related_type == 'TestProject':
                cypher = """
                MATCH (rel:TestProject)-[:CON]->(m:TestMethod)
                MATCH (eq:Equipment {Id: $equipment_id})-[:CON]->(rel)
                OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
                OPTIONAL MATCH (m)-[:DERIVED_FROM]->(f2:File)
                OPTIONAL MATCH (eq_direct:Equipment)-[:CON]->(m)
                OPTIONAL MATCH (eq_via:Equipment)-[:CON]->(rel)
                RETURN m,
                       collect(DISTINCT rel.Id) AS rel_ids,
                       collect(DISTINCT rel.Name) AS rel_names,
                       collect(DISTINCT f.Id) + collect(DISTINCT f2.Id) AS file_ids,
                       collect(DISTINCT f.Name) + collect(DISTINCT f2.Name) AS file_names,
                       [x IN (collect(DISTINCT eq_direct.Id) + collect(DISTINCT eq_via.Id)) WHERE x IS NOT NULL] AS equipment_ids,
                       [x IN (collect(DISTINCT eq_direct.Name) + collect(DISTINCT eq_via.Name)) WHERE x IS NOT NULL] AS equipment_names
                ORDER BY m.Create_time DESC
                """
            else:
                cypher = """
                MATCH (rel:Indicator)-[:CON]->(m:TestMethod)
                MATCH (eq:Equipment {Id: $equipment_id})-[:CON]->(rel)
                OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
                OPTIONAL MATCH (m)-[:DERIVED_FROM]->(f2:File)
                OPTIONAL MATCH (eq_direct:Equipment)-[:CON]->(m)
                OPTIONAL MATCH (eq_via:Equipment)-[:CON]->(rel)
                RETURN m,
                       collect(DISTINCT rel.Id) AS rel_ids,
                       collect(DISTINCT rel.Name) AS rel_names,
                       collect(DISTINCT f.Id) + collect(DISTINCT f2.Id) AS file_ids,
                       collect(DISTINCT f.Name) + collect(DISTINCT f2.Name) AS file_names,
                       [x IN (collect(DISTINCT eq_direct.Id) + collect(DISTINCT eq_via.Id)) WHERE x IS NOT NULL] AS equipment_ids,
                       [x IN (collect(DISTINCT eq_direct.Name) + collect(DISTINCT eq_via.Name)) WHERE x IS NOT NULL] AS equipment_names
                ORDER BY m.Create_time DESC
                """
            params = {'equipment_id': equipment_id}
        else:
            cypher = f"""
            MATCH (rel:{rel_label})-[:CON]->(m:TestMethod)
            OPTIONAL MATCH (f:File)-[:BELONGS_TO]->(m)
            OPTIONAL MATCH (m)-[:DERIVED_FROM]->(f2:File)
            OPTIONAL MATCH (eq_direct:Equipment)-[:CON]->(m)
            OPTIONAL MATCH (eq_via:Equipment)-[:CON]->(rel)
            RETURN m,
                   collect(DISTINCT rel.Id) AS rel_ids,
                   collect(DISTINCT rel.Name) AS rel_names,
                   collect(DISTINCT f.Id) + collect(DISTINCT f2.Id) AS file_ids,
                   collect(DISTINCT f.Name) + collect(DISTINCT f2.Name) AS file_names,
                   [x IN (collect(DISTINCT eq_direct.Id) + collect(DISTINCT eq_via.Id)) WHERE x IS NOT NULL] AS equipment_ids,
                   [x IN (collect(DISTINCT eq_direct.Name) + collect(DISTINCT eq_via.Name)) WHERE x IS NOT NULL] AS equipment_names
            ORDER BY m.Create_time DESC
            """
            params = {}
        records = _run_cypher(cypher, params)
        rows = []
        for r in records:
            node = _get_node_props(r.get('m', {}))
            rel_ids = _dedupe_str_list(r.get('rel_ids') or [])
            rel_names = _dedupe_str_list(r.get('rel_names') or [])
            file_ids = _dedupe_str_list(r.get('file_ids') or [])
            file_names = _dedupe_str_list(r.get('file_names') or [])
            equipment_ids = _dedupe_str_list(r.get('equipment_ids') or [])
            equipment_names = _dedupe_str_list(r.get('equipment_names') or [])
            node['related_type'] = related_type
            node['related_ids'] = rel_ids
            node['related_names'] = rel_names
            node['related_project_names'] = rel_names if related_type == 'TestProject' else []
            node['related_indicator_names'] = rel_names if related_type == 'Indicator' else []
            node['file_ids'] = file_ids
            node['file_names'] = file_names
            node['equipment_ids'] = equipment_ids
            node['equipment_names'] = equipment_names
            node['equipment_id'] = equipment_ids[0] if equipment_ids else ''
            node['equipment_name'] = equipment_names[0] if equipment_names else ''
            rows.append(node)
        return jsonify({'success': True, 'data': rows, 'total': len(rows)})
    except Exception as e:
        logger.error(f"方法关联查询失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@flgl_method_bp.route('/upload-attachment', methods=['POST'])
def upload_method_attachment():
    """方法新增/编辑时上传来源文件或附件：MinIO + Neo4j File 节点。"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有文件'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'error': '文件名为空'}), 400
    minio_client = current_app.config.get('MINIO_CLIENT')
    bucket_name = current_app.config.get('MINIO_BUCKET_NAME')
    if not minio_client or not bucket_name:
        try:
            from config import Config
            minio_client = minio_client or Config.MINIO_CLIENT
            bucket_name = bucket_name or Config.MINIO_BUCKET_NAME
        except Exception:
            pass
    if not minio_client or not bucket_name:
        return jsonify({'success': False, 'error': 'MinIO 未初始化'}), 500

    tmp_dir = tempfile.mkdtemp()
    filename = _safe_filename(file.filename)
    tmp_path = os.path.join(tmp_dir, filename)
    try:
        file.save(tmp_path)
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)
        node_id = f"WJ_method_{uuid.uuid4().hex[:12]}"
        object_name = f"方法附件/{node_id}/{filename}"
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        metadata = {
            'x-amz-meta-doc-id': node_id,
            'x-amz-meta-file-title': quote(os.path.splitext(file.filename)[0][:100], safe=''),
            'x-amz-meta-upload-time': datetime.now(timezone.utc).isoformat(),
            'x-amz-meta-upload-userid': quote((request.form.get('user_id') or 'admin')[:80], safe=''),
        }
        minio_client.fput_object(bucket_name, object_name, tmp_path, content_type=content_type, metadata=metadata)

        from .routes import _run_cypher, _get_node_props
        props = {
            'Id': node_id,
            'Type': request.form.get('category') or '方法附件',
            'Label': request.form.get('label') or '方法附件',
            'Name': file.filename,
            'Chroma_id': '',
            'minIo_id': object_name,
            'Image_true': False,
            'Image_minIO_path': '',
            'Status': '启用',
            'User_id': request.form.get('user_id') or 'admin',
            'Create_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'File_type': os.path.splitext(file.filename)[1].lstrip('.'),
        }
        cypher = """
        CREATE (f:File {
            Id: $Id, Type: $Type, Label: $Label, Name: $Name,
            Chroma_id: $Chroma_id, minIo_id: $minIo_id,
            Image_true: $Image_true, Image_minIO_path: $Image_minIO_path,
            Status: $Status, User_id: $User_id, Create_time: $Create_time,
            File_type: $File_type
        }) RETURN f
        """
        recs = _run_cypher(cypher, props)
        node = _get_node_props(recs[0].get('f', {})) if recs else None
        log_user_action(f"分类管理-方法附件上传 file_id={node_id} name={file.filename[:80]}")
        return jsonify({'success': True, 'data': {'file_id': node_id, 'file_node': node, 'minio_path': object_name}})
    except Exception as e:
        logger.error(f"方法附件上传失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@flgl_method_bp.route('/upload-method-image', methods=['POST'])
def upload_method_image():
    """WangEditor 方法图片上传：MinIO 路径固定为 试验方法/{method_id}/图片/{文件名}，并创建 File 节点。"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有文件'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'success': False, 'error': '文件名为空'}), 400
    method_id = (request.form.get('method_id') or '').strip()
    if not method_id:
        return jsonify({'success': False, 'error': '缺少 method_id'}), 400

    minio_client, bucket_name = _get_minio_runtime()
    if not minio_client or not bucket_name:
        return jsonify({'success': False, 'error': 'MinIO 未初始化'}), 500

    tmp_dir = tempfile.mkdtemp()
    filename = _safe_filename(file.filename)
    tmp_path = os.path.join(tmp_dir, filename)
    try:
        file.save(tmp_path)
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)

        base, ext = os.path.splitext(filename)
        object_name = f"试验方法/{method_id}/图片/{filename}"
        try:
            minio_client.stat_object(bucket_name, object_name)
            object_name = f"试验方法/{method_id}/图片/{base}_{uuid.uuid4().hex[:8]}{ext}"
        except Exception:
            pass

        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        user_id = request.form.get('user_id') or 'admin'
        file_id = f"WJ_method_img_{uuid.uuid4().hex[:12]}"
        metadata = {
            'x-amz-meta-doc-id': file_id,
            'x-amz-meta-method-id': method_id,
            'x-amz-meta-upload-time': datetime.now(timezone.utc).isoformat(),
            'x-amz-meta-upload-userid': quote(user_id[:80], safe=''),
        }
        minio_client.fput_object(bucket_name, object_name, tmp_path, content_type=content_type, metadata=metadata)

        from .routes import _run_cypher, _get_node_props
        props = {
            'Id': file_id,
            'Type': '方法图片',
            'Label': '方法图片',
            'Name': file.filename,
            'Chroma_id': '',
            'minIo_id': object_name,
            'Image_true': True,
            'Image_minIO_path': object_name,
            'Status': '启用',
            'User_id': user_id,
            'Create_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'File_type': ext.lstrip('.'),
        }
        cypher = """
        MERGE (f:File {Id: $Id})
        SET f.Type = $Type, f.Label = $Label, f.Name = $Name,
            f.Chroma_id = $Chroma_id, f.minIo_id = $minIo_id,
            f.Image_true = $Image_true, f.Image_minIO_path = $Image_minIO_path,
            f.Status = $Status, f.User_id = $User_id, f.Create_time = $Create_time,
            f.File_type = $File_type
        RETURN f
        """
        recs = _run_cypher(cypher, props)
        node = _get_node_props(recs[0].get('f', {})) if recs else None
        log_user_action(f"分类管理-方法图片上传 method_id={method_id} file_id={file_id}")
        return jsonify({'success': True, 'data': {
            'file_id': file_id,
            'file_node': node,
            'minio_path': object_name,
            'url': _method_image_url(object_name),
        }})
    except Exception as e:
        logger.error(f"方法图片上传失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@flgl_method_bp.route('/method-image', methods=['GET'])
def get_method_image():
    object_path = (request.args.get('path') or '').strip()
    if not object_path:
        return jsonify({'success': False, 'error': '缺少图片路径'}), 400
    object_path = unquote(object_path)
    if not object_path.startswith('试验方法/') or '/图片/' not in object_path:
        return jsonify({'success': False, 'error': '图片路径不合法'}), 400

    minio_client, bucket_name = _get_minio_runtime()
    if not minio_client or not bucket_name:
        return jsonify({'success': False, 'error': 'MinIO 未初始化'}), 500

    try:
        obj = minio_client.get_object(bucket_name, object_path)
        data = obj.read()
        try:
            obj.close()
            obj.release_conn()
        except Exception:
            pass
        content_type = mimetypes.guess_type(object_path)[0] or 'application/octet-stream'
        return Response(data, mimetype=content_type, headers={'Cache-Control': 'public, max-age=86400'})
    except Exception as e:
        logger.error(f"读取方法图片失败 path={object_path}: {e}")
        return jsonify({'success': False, 'error': '图片不存在或读取失败'}), 404


@flgl_method_bp.route('/preview-parse-image', methods=['GET'])
def get_preview_parse_image():
    """分类管理 PDF 预览/章节提取：读取本地 MinerU parse 目录图片（不走 MinIO）。"""
    from flask import send_file
    from pdf_extract.mineru_wjgl_service import resolve_local_parse_asset

    cache_key = (request.args.get('cache_key') or request.args.get('doc_id') or '').strip()
    rel = (request.args.get('path') or '').strip().replace('\\', '/')
    if not cache_key or not rel:
        return jsonify({'success': False, 'error': '缺少 cache_key 或 path'}), 400
    fpath = resolve_local_parse_asset('_preview', cache_key, rel)
    if not fpath:
        return jsonify({'success': False, 'error': 'not found'}), 404
    ext = os.path.splitext(fpath.name)[1].lower()
    mime = mimetypes.guess_type(fpath.name)[0] or (
        'image/jpeg' if ext in ('.jpg', '.jpeg') else 'image/png' if ext == '.png' else 'application/octet-stream'
    )
    return send_file(
        str(fpath),
        mimetype=mime,
        conditional=False,
        max_age=0,
        etag=False,
        last_modified=None,
    )


@flgl_method_bp.route('/method-name-duplicate', methods=['GET'])
def method_name_duplicate_check():
    """按名称检查 TestMethod 是否与库中重复，供重命名后二次校验。"""
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({
            'success': True,
            'data': {'duplicate': False, 'existing': None},
        })
    try:
        matches = _testmethod_list_by_name(name, limit=20)
        ex = matches[0] if matches else None
        return jsonify({
            'success': True,
            'data': {
                'duplicate': len(matches) > 0,
                'existing': ex,
                'matches': matches,
            },
        })
    except Exception as e:
        logger.error(f"方法重名检查失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@flgl_method_bp.route('/method-import', methods=['POST'])
def method_import():
    """将方法数据导入 Neo4j（创建 TestMethod 节点 + 关联关系）"""
    data = request.json or {}
    method_data = data.get('method', {})
    relations = data.get('relations', [])

    if not method_data.get('Name'):
        return jsonify({"success": False, "error": "方法名称不能为空"}), 400

    try:
        from .routes import _gen_id, _generate_id, _run_cypher, _now, _get_node_props, NODE_FIELDS

        relation_details = data.get('relation_details', [])
        fields = NODE_FIELDS['TestMethod']
        if 'SYNR' not in fields:
            fields = [*fields, 'SYNR']
        replace_mode = (data.get('import_mode') or '').strip().lower() == 'replace'
        replace_id = (data.get('replace_node_id') or '').strip()
        source_name = method_data.get('Source_name', '')
        method_name = method_data.get('Name', '')

        if replace_mode and replace_id:
            if not _run_cypher(
                'MATCH (n:TestMethod {Id: $id}) RETURN n LIMIT 1',
                {'id': replace_id},
            ):
                return jsonify({'success': False, 'error': '要替换的方法节点不存在'}), 404
            node_id = replace_id
            upd = {}
            for f in fields:
                if f in ('Id', 'Create_time'):
                    continue
                if f in method_data and method_data[f] is not None:
                    upd[f] = method_data[f]
            upd['User_id'] = method_data.get('User_id') or upd.get('User_id') or '管理员'
            if method_data.get('Create_time'):
                upd['Create_time'] = method_data.get('Create_time')
            upd['Relation_details'] = json.dumps(relation_details, ensure_ascii=False)
            set_parts = [f'n.{k} = ${k}' for k in upd.keys()]
            cypher = f'MATCH (n:TestMethod {{Id: $node_id}}) SET {", ".join(set_parts)} RETURN n'
            params = {'node_id': node_id, **upd}
            records = _run_cypher(cypher, params)
            node = _get_node_props(records[0].get('n', {})) if records else None
            _run_cypher(
                """
                MATCH ()-[r:CON|HAS|BELONGS_TO]->(m:TestMethod {Id: $id})
                DELETE r
                """,
                {'id': node_id},
            )
            _run_cypher(
                """
                MATCH (m:TestMethod {Id: $id})-[r:DERIVED_FROM|BELONGS_TO]->()
                DELETE r
                """,
                {'id': node_id},
            )
        else:
            # 重复校验：仅按方法名称（同一来源文件可有多条方法）
            duplicate_records = _run_cypher(
                """
                MATCH (n:TestMethod)
                WHERE $method_name <> '' AND n.Name = $method_name
                RETURN n LIMIT 1
                """,
                {'method_name': method_name},
            )
            if duplicate_records:
                existing = _get_node_props(duplicate_records[0].get('n', {}))
                return jsonify({
                    'success': False,
                    'error': '该方法已存在，请勿重复保存',
                    'data': {'duplicate': True, 'existing': existing},
                }), 409

            requested_id = (method_data.get('Id') or method_data.get('id') or '').strip()
            if requested_id:
                exists = _run_cypher('MATCH (n:TestMethod {Id: $id}) RETURN n LIMIT 1', {'id': requested_id})
                if exists:
                    requested_id = ''
            node_id = requested_id or _generate_id('TestMethod')
            props = {'Id': node_id}
            for f in fields:
                if f in ('Id', 'Create_time'):
                    continue
                if f in method_data and method_data[f] is not None:
                    props[f] = method_data[f]
            props['Relation_details'] = json.dumps(relation_details, ensure_ascii=False)
            props['Create_time'] = method_data.get('Create_time') or _now()
            props['User_id'] = method_data.get('User_id') or props.get('User_id') or '管理员'
            props['Status'] = props.get('Status', '启用')

            set_str = ', '.join([f'{k}: ${k}' for k in props.keys()])
            cypher = f"CREATE (n:TestMethod {{{set_str}}}) RETURN n"
            records = _run_cypher(cypher, props)
            node = _get_node_props(records[0].get('n', {})) if records else None

        image_paths = _extract_method_image_paths(method_data.get('SYNR') or method_data.get('synr') or '')
        for image_path in image_paths:
            _run_cypher(
                """
                MATCH (f:File)
                WHERE f.minIo_id = $path OR f.Image_minIO_path = $path
                MATCH (m:TestMethod {Id: $method_id})
                MERGE (f)-[:BELONGS_TO]->(m)
                """,
                {'path': image_path, 'method_id': node_id},
            )

        source_file_id = str(method_data.get('Source_id') or method_data.get('source_file_id') or '').strip()
        section_ids, section_nos, source_category, extract_source = _parse_method_source_sections(method_data)
        if source_file_id:
            _ensure_method_source_file_link(
                node_id,
                source_file_id,
                section_ids=section_ids,
                section_nos=section_nos,
                source_category=source_category,
                extract_source=extract_source,
            )
        derived_rels = 0
        for dlink in data.get('derived_from_links') or []:
            if not isinstance(dlink, dict) or not source_file_id:
                continue
            props = {
                'slot': dlink.get('slot') or '',
                'clause_no': dlink.get('clause_no') or '',
                'clause_title': dlink.get('clause_title') or '',
                'excerpt': (dlink.get('excerpt') or '')[:200],
                'confidence': dlink.get('confidence') or 0.9,
                'conditions': json.dumps(dlink.get('conditions') or [], ensure_ascii=False),
            }
            _run_cypher(
                """
                MATCH (m:TestMethod {Id: $method_id})
                MATCH (f:File {Id: $file_id})
                MERGE (m)-[r:DERIVED_FROM]->(f)
                SET r += $props
                """,
                {'method_id': node_id, 'file_id': source_file_id, 'props': props},
            )
            derived_rels += 1

        # (Target {Id: target_id})-[CON|HAS|BELONGS_TO]->(TestMethod {Id: node_id})
        created_rels = derived_rels
        for rel in relations or []:
            if not isinstance(rel, dict):
                continue
            raw_tid = rel.get('target_id')
            target_id = str(raw_tid).strip() if raw_tid is not None else ''
            target_type = str(rel.get('target_type') or '').strip()
            target_name = str(rel.get('target_name') or rel.get('name') or '').strip()
            if not target_id and target_name and target_type:
                target_id = _resolve_or_create_import_target(target_type, '', target_name)
            if not target_id:
                continue
            rel_type = (rel.get('rel_type') or '').strip()
            if rel_type not in _METHOD_IMPORT_REL_TYPES:
                continue
            rate, rate_ok = _parse_rel_rate_for_import(rel)
            if not rate_ok:
                continue
            # 默认：目标节点 -> 本方法（项目/指标 CON 方法，文件 BELONGS_TO 方法）
            # 特例：本方法（分则） -> 目标方法（总则） BELONGS_TO。
            if target_type == 'TestMethod' and rel_type == 'BELONGS_TO':
                merge_tail = 'MERGE (a)-[r:BELONGS_TO]->(b)'
                cypher = (
                    'MATCH (a:TestMethod {Id: $to_id}) '
                    'MATCH (b:TestMethod {Id: $from_id}) ' + merge_tail
                )
            elif target_type == 'File' and rel_type == 'DERIVED_FROM':
                merge_tail = 'MERGE (a)-[r:DERIVED_FROM]->(b)'
                cypher = (
                    'MATCH (a:TestMethod {Id: $from_id}) '
                    'MATCH (b:File {Id: $to_id}) ' + merge_tail
                )
            else:
                merge_tail = {
                    'CON': 'MERGE (a)-[r:CON]->(b)',
                    'HAS': 'MERGE (a)-[r:HAS]->(b)',
                    'BELONGS_TO': 'MERGE (a)-[r:BELONGS_TO]->(b)',
                }.get(rel_type)
                if not merge_tail:
                    continue
                cypher = (
                    'MATCH (a {Id: $from_id}) '
                    'MATCH (b:TestMethod {Id: $to_id}) ' + merge_tail
                )
            if rate is not None:
                cypher += ' SET r.rate = $rate'
            cypher += ' RETURN 1 AS _ok'
            params = {
                'from_id': node_id if (target_type == 'File' and rel_type == 'DERIVED_FROM') else target_id,
                'to_id': target_id if (target_type == 'File' and rel_type == 'DERIVED_FROM') else node_id,
            }
            if rate is not None:
                params['rate'] = rate
            recs = _run_cypher(cypher, params)
            if recs:
                created_rels += 1

        log_user_action(
            f"分类管理-检测方法导入Neo4j id={node_id} name={method_name[:80]} rels={created_rels} replace={replace_mode}"
        )
        return jsonify({
            "success": True,
            "data": {
                "node": node,
                "relations_created": created_rels,
            }
        })

    except Exception as e:
        logger.error(f"方法导入失败: {e}")
        return jsonify({"success": False, "error": f"导入失败: {str(e)}"}), 500


# 与前端 @/constants/environments 中「短名」对齐，用于环境类排序（名称可能为「复杂电磁环境」等）
_ENV_ORDER_TOKENS = [
    '电磁', '地理', '气象', '水文', '水声', '高原', '海上', '其他',
]


def _env_sort_key(name: str) -> tuple:
    n = (name or '').strip()
    for i, tok in enumerate(_ENV_ORDER_TOKENS):
        if tok in n:
            return (0, i, n)
    return (1, 99, n)


def get_method_category_tree_response():
    """
    从 Neo4j 自动构建方法 Tab 左侧分类树数据：
    - 环境类：由「项目 -CON-> 方法」链上项目所关联的环境（Environment -CON-> TestProject 或 EnvironmentId）去重得到；
    - 指标类：任有关联「指标 -CON-> 方法」的方法归入「指标」；
    - 未分类：既无项目连接也无指标连接的方法。
    返回 categories（树节点）与 method_category_keys（方法 id -> 所属分类 key 列表）。
    """
    from .routes import _run_cypher

    try:
        # 方法 <- 项目 -> 环境（与 seed：Environment -CON-> TestProject，TestProject -CON-> TestMethod 一致）
        env_rows = _run_cypher(
            """
            MATCH (p:TestProject)-[:CON]->(m:TestMethod)
            OPTIONAL MATCH (e1:Environment)-[:CON]->(p)
            OPTIONAL MATCH (e2:Environment)
            WHERE p.EnvironmentId IS NOT NULL AND e2.Id = p.EnvironmentId
            WITH m, coalesce(e1, e2) AS e
            WHERE e IS NOT NULL
            RETURN DISTINCT m.Id AS mid, e.Id AS eid, e.Name AS ename
            """,
            {},
        )

        ind_rows = _run_cypher(
            """
            MATCH (i:Indicator)-[:CON]->(m:TestMethod)
            RETURN DISTINCT m.Id AS mid
            """,
            {},
        )

        all_m_rows = _run_cypher(
            "MATCH (m:TestMethod) RETURN m.Id AS mid",
            {},
        )

        method_keys: dict[str, list[str]] = {}
        env_meta: dict[str, str] = {}  # eid -> 展示名称

        for r in env_rows or []:
            mid = r.get('mid')
            eid = r.get('eid')
            ename = (r.get('ename') or '') or (eid or '')
            if not mid or not eid:
                continue
            key = f"env_{eid}"
            method_keys.setdefault(mid, []).append(key)
            env_meta[eid] = ename

        ind_set = {r.get('mid') for r in (ind_rows or []) if r.get('mid')}
        for mid in ind_set:
            method_keys.setdefault(mid, []).append("mcat_indicator")

        all_ids = {r.get('mid') for r in (all_m_rows or []) if r.get('mid')}
        for mid in all_ids:
            if mid not in method_keys or not method_keys[mid]:
                method_keys.setdefault(mid, []).append("mcat_unassigned")
            # 去重保序
            method_keys[mid] = list(dict.fromkeys(method_keys.get(mid, [])))

        # 各分类下方法数
        def count_for(k: str) -> int:
            return sum(1 for _mid, ks in method_keys.items() if k in ks)

        categories = []
        for eid, title in sorted(env_meta.items(), key=lambda x: _env_sort_key(x[1])):
            k = f"env_{eid}"
            c = count_for(k)
            categories.append({
                "key": k,
                "title": title,
                "type": "environment",
                "env_id": eid,
                "count": c,
            })

        categories.append({
            "key": "mcat_indicator",
            "title": "指标",
            "type": "indicator",
            "count": count_for("mcat_indicator"),
        })
        u_cnt = count_for("mcat_unassigned")
        if u_cnt > 0:
            categories.append({
                "key": "mcat_unassigned",
                "title": "未分类",
                "type": "unassigned",
                "count": u_cnt,
            })

        return jsonify({
            "success": True,
            "data": {
                "categories": categories,
                "method_category_keys": method_keys,
            },
        })
    except Exception as e:
        logger.error(f"方法分类树查询失败: {e}")
        return jsonify({"success": False, "error": f"查询失败: {str(e)}"}), 500


@flgl_method_bp.route('/method-categories', methods=['GET'])
def flgl_method_categories():
    """同 routes.method_categories，便于 /api/FLGL_method/ 下直接访问"""
    return get_method_category_tree_response()


def _get_neo4j_driver_for_recall():
    try:
        from config import Config
        return getattr(Config, "NEO4J_DRIVER", None)
    except Exception:
        return None


def _parse_wjgl_category_doc_id(object_name: str = "", file_node: Optional[dict] = None) -> tuple:
    """从 MinIO 路径或 File 节点解析 (category, doc_id)。"""
    category = ""
    doc_id = ""
    node = file_node or {}
    category = str(node.get("category") or node.get("Category") or "").strip()
    doc_id = str(node.get("doc_id") or node.get("Doc_id") or "").strip()
    if not doc_id:
        fid = str(node.get("id") or node.get("Id") or "").strip()
        if fid.startswith("WJ_"):
            doc_id = fid[3:]
    path = (object_name or str(node.get("minio_id") or node.get("minIo_id") or "")).replace("\\", "/")
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        if not category:
            category = parts[0]
        if not doc_id:
            doc_id = parts[1]
    return category, doc_id


def _load_parse_tables_html(category: str, doc_id: str, minio_client, bucket_name: str) -> list:
    """从 MinIO parse 缓存加载表格 HTML。"""
    if not category or not doc_id or not minio_client:
        return []
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import _parse_minio_key
        from io import BytesIO

        key = _parse_minio_key(category, doc_id, "tables.json")
        resp = minio_client.get_object(bucket_name, key)
        raw = json.loads(resp.read().decode("utf-8"))
        resp.close()
        resp.release_conn()
        out = []
        for t in raw.get("tables") or []:
            tid = str(t.get("table_id") or "").strip()
            asset = str(t.get("parse_asset") or (f"tables/{tid}.html" if tid else "")).strip()
            if not asset:
                continue
            html_key = _parse_minio_key(category, doc_id, asset)
            try:
                hresp = minio_client.get_object(bucket_name, html_key)
                html = hresp.read().decode("utf-8", errors="replace")
                hresp.close()
                hresp.release_conn()
                if html.strip():
                    out.append({
                        "html": html,
                        "table_no": t.get("table_no"),
                        "table_id": tid,
                    })
            except Exception:
                continue
        return out
    except Exception as e:
        logger.debug("load parse tables failed: %s", e)
        return []


def _load_all_tables_for_doc(
    category: str,
    doc_id: str,
    minio_client,
    bucket_name: str,
    parse_cache: Optional[dict] = None,
) -> list:
    """从 MinIO tables.json、parse 缓存或 document.html 加载表格 HTML。"""
    from .method_section_enrich import _index_tables_from_html, _load_document_tables_index

    out: list = []
    seen: set = set()
    for item in _load_parse_tables_html(category, doc_id, minio_client, bucket_name):
        html = str(item.get("html") or "").strip()
        if not html or html in seen:
            continue
        seen.add(html)
        out.append(item)

    tables_map = _load_document_tables_index(category, doc_id)
    if parse_cache:
        tables_map = {**_index_tables_from_html(str(parse_cache.get("full_html") or "")), **tables_map}
    for tno, html in sorted(tables_map.items(), key=lambda x: (len(x[0]), x[0])):
        html = str(html or "").strip()
        if not html or html in seen:
            continue
        seen.add(html)
        out.append({"html": html, "table_no": tno})
    return out


def _flgl_pdf_fallback_enabled() -> bool:
    try:
        from blueprints.FLGL.GetTestMethod.parser import _flgl_pdf_fallback_enabled as _enabled

        return _enabled()
    except Exception:
        return False


def _preview_cache_has_content(ctx: dict) -> bool:
    """preview 缓存是否含可用正文/章节切片。"""
    if not isinstance(ctx, dict):
        return False
    if str(ctx.get("full_text") or "").strip():
        return True
    parse_cache = ctx.get("parse_cache") if isinstance(ctx.get("parse_cache"), dict) else {}
    if parse_cache.get("sections") or parse_cache.get("node_slices"):
        return True
    if str(parse_cache.get("full_html") or "").strip() or str(parse_cache.get("full_markdown") or "").strip():
        return True
    if ctx.get("sections"):
        return True
    return False


def _parse_word_like_llm_extract(
    temp_path: str,
    ext: str,
    tmp_dir: str,
    *,
    preview_cache_key: str = "",
) -> Optional[dict]:
    """
    与 LLM 整文档提取共用 parse_upload_document + document_structure。
    再补齐 section.html / node_slices 供勾选章节提取。
    """
    if ext not in (".docx", ".doc"):
        return None

    try:
        from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import parse_upload_document
    except Exception as exc:
        logger.warning("[method-extract] parse_upload_document 不可用: %s", exc)
        return None

    try:
        parsed = parse_upload_document(temp_path)
    except Exception as e:
        logger.warning("[method-extract] Word LLM 同源解析失败: %s", e)
        return None

    sections_raw = list(parsed.get("sections") or [])
    pages = list(parsed.get("pages") or [])
    page_table_map = dict(parsed.get("page_table_map") or {})
    full_text = str(parsed.get("raw_full_text") or parsed.get("full_text") or "").strip()
    if not full_text and not sections_raw:
        return None

    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import (
            _build_node_slices,
            _build_node_slices_md,
            _build_sections_with_blocks,
            _sections_should_attach_page_tables,
        )

        attach = _sections_should_attach_page_tables(pages, sections_raw)
        sections = _build_sections_with_blocks(
            sections_raw,
            page_table_map,
            pages,
            embed_figures_base64=True,
            attach_page_tables=attach,
        )
        full_markdown = str(parsed.get("full_markdown") or "").strip()
        node_slices_md = _build_node_slices_md(sections, full_markdown=full_markdown)
        node_slices = _build_node_slices(
            sections,
            full_markdown=full_markdown,
            node_slices_md=node_slices_md,
        )
    except Exception as exc:
        logger.warning("[method-extract] sections html 构建失败，使用纯文本节: %s", exc)
        sections = sections_raw
        full_markdown = str(parsed.get("full_markdown") or "").strip()
        node_slices_md = {}
        node_slices = {
            str(s.get("section_id")): str(s.get("text") or "")
            for s in sections_raw
            if s.get("section_id")
        }

    flat_headings = list(parsed.get("flat_headings") or [])
    toc_display = list(parsed.get("toc_display") or [])
    if not flat_headings and sections_raw:
        from blueprints.ZSGL.WJGL1.document_structure import build_section_catalogue

        flat_headings = [
            {
                "section_id": s.get("section_id"),
                "sectionNo": s.get("section_no") or "",
                "title": s.get("title") or "",
                "level": int(s.get("level") or 1),
                "snippet": (s.get("text") or "")[:180],
                "source": "llm_parse",
            }
            for s in sections_raw
        ]

    all_tables: list = []
    seen_html: set = set()
    for tbl in parsed.get("all_tables") or []:
        if isinstance(tbl, dict):
            html = str(tbl.get("html") or "").strip()
            if html and html not in seen_html:
                seen_html.add(html)
                all_tables.append(tbl)
    for page in pages:
        for th in page.get("tables_html") or []:
            hs = str(th or "").strip()
            if hs and hs not in seen_html:
                seen_html.add(hs)
                all_tables.append({"html": hs, "page": page.get("page_number")})

    parse_cache = {
        "sections": sections,
        "node_slices": node_slices,
        "node_slices_md": node_slices_md,
        "full_html": str(parsed.get("full_html") or ""),
        "full_markdown": full_markdown,
        "raw_full_html": str(parsed.get("full_html") or ""),
        "flat_headings": flat_headings,
        "toc_display": toc_display,
        "section_catalogue": list(parsed.get("section_catalogue") or []),
        "parsed_document": {
            "file_name": parsed.get("file_name"),
            "parse_mode": parsed.get("parse_mode"),
            "structure_method": parsed.get("structure_method"),
            "raw_full_text": full_text,
            "full_markdown": full_markdown,
            "word_paragraph_map": parsed.get("word_paragraph_map") or [],
            "images_dir": parsed.get("images_dir") or "",
            "ocr_output_dir": parsed.get("ocr_output_dir") or "",
        },
        "stats": parsed.get("stats") or {},
    }
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import _ensure_section_anchors_in_html

        raw_html = str(parse_cache.get("full_html") or "").strip()
        if raw_html and sections:
            enhanced = _ensure_section_anchors_in_html(raw_html, sections)
            parse_cache["full_html"] = enhanced
            parse_cache["raw_full_html"] = enhanced
    except Exception as exc:
        logger.warning("[method-extract] full_html 锚点补齐失败: %s", exc)
    _finalize_parse_cache_like_word(parse_cache)

    category = "_preview" if preview_cache_key else ""
    doc_id = preview_cache_key or ""
    logger.info(
        "[method-extract] Word LLM 同源解析 sections=%s text_len=%s headings=%s method=%s",
        len(sections),
        len(full_text),
        len(flat_headings),
        parsed.get("structure_method"),
    )
    ctx = {
        "full_text": full_text,
        "pages": pages,
        "all_tables": all_tables,
        "all_figures": [],
        "parse_cache": parse_cache,
        "category": category,
        "doc_id": doc_id,
        "source": "word_llm_parse",
        "flat_headings": flat_headings,
        "toc_display": toc_display,
    }
    if preview_cache_key:
        try:
            from .section_extract_debug import save_word_parse_debug_bundle

            save_word_parse_debug_bundle(
                preview_cache_key,
                source_path=temp_path,
                ctx=ctx,
                stage="word_llm_parse",
            )
        except Exception as exc:
            logger.debug("[section-debug] Word 解析调试落盘失败: %s", exc)
    return ctx


def _parse_file_with_word_native_for_extract(
    temp_path: str,
    ext: str,
    tmp_dir: str,
    *,
    preview_cache_key: str = "",
) -> Optional[dict]:
    """方法提取：Word 与 LLM 提取共用 parse_upload_document，再生成 html 切片。"""
    return _parse_word_like_llm_extract(
        temp_path, ext, tmp_dir, preview_cache_key=preview_cache_key,
    )


def _parse_file_with_mineru_for_extract(
    temp_path: str,
    ext: str,
    tmp_dir: str,
    *,
    preview_cache_key: str = "",
) -> Optional[dict]:
    """方法提取：PDF 走 MinerU 解析目录与正文（Word 请用原生路径）。"""
    if ext != ".pdf":
        return None
    try:
        from blueprints.FLGL.GetTestMethod.parser import use_mineru_for_flgl

        if not use_mineru_for_flgl():
            return None
    except Exception:
        return None

    ocr_dir = os.path.join(tmp_dir, "mineru_extract")
    os.makedirs(ocr_dir, exist_ok=True)
    try:
        from pdf_extract.mineru_wjgl_service import (
            parse_pdf_for_wjgl_local,
            parse_pdf_save_local_and_minio,
        )

        if preview_cache_key:
            bundle = parse_pdf_save_local_and_minio(
                temp_path,
                "_preview",
                preview_cache_key,
                ocr_output_dir=ocr_dir,
            )
        else:
            bundle = parse_pdf_for_wjgl_local(temp_path, ocr_output_dir=ocr_dir)
    except Exception as e:
        logger.warning("[method-extract] MinerU 解析失败: %s", e)
        return None

    sections = bundle.get("sections") or []
    full_markdown = str(bundle.get("full_markdown") or "").strip()
    full_html = str(bundle.get("full_html") or "").strip()
    full_text = str(bundle.get("full_text") or "").strip()
    pages = bundle.get("pages") or []

    all_tables: list = []
    seen_html: set = set()
    for sec in sections:
        html = str(sec.get("html") or "").strip()
        if html and html not in seen_html:
            seen_html.add(html)
            all_tables.append({"html": html, "section_id": sec.get("section_id")})
    for tbl in bundle.get("table_registry") or []:
        html = str((tbl or {}).get("html") or "").strip()
        if html and html not in seen_html:
            seen_html.add(html)
            all_tables.append(tbl)

    parse_cache = {
        "sections": sections,
        "section_index": bundle.get("section_index") or [],
        "full_markdown": full_markdown,
        "full_html": full_html,
        "node_slices": bundle.get("node_slices") or {},
        "parsed_document": bundle.get("parsed_document") or {},
        "stats": bundle.get("stats") or {},
    }
    _finalize_parse_cache_like_word(parse_cache)

    from .method_section_enrich import gather_figures_for_method_trace

    trace_figures = gather_figures_for_method_trace(parse_cache)

    category = "_preview" if preview_cache_key else ""
    doc_id = preview_cache_key or ""
    logger.info(
        "[method-extract] MinerU 解析完成 sections=%s text_len=%s md_len=%s figures=%s local=%s",
        len(sections), len(full_text), len(full_markdown), len(trace_figures), preview_cache_key[:12] if preview_cache_key else "",
    )
    return {
        "full_text": full_text,
        "pages": pages,
        "all_tables": all_tables,
        "all_figures": trace_figures,
        "parse_cache": parse_cache,
        "category": category,
        "doc_id": doc_id,
        "source": "mineru",
    }


def _load_method_extract_context(
    temp_path: str,
    ext: str,
    tmp_dir: str,
    minio_client,
    bucket_name: str,
    object_name: str = "",
    file_node: Optional[dict] = None,
    preview_cache_key: str = "",
) -> dict:
    """
    加载方法提取上下文：优先 MinIO 缓存；Word 走原生解析；PDF 可走 MinerU。
    """
    category, doc_id = _parse_wjgl_category_doc_id(object_name, file_node)
    if preview_cache_key:
        category, doc_id = "_preview", preview_cache_key
        cached_ctx = _load_preview_extract_cache(preview_cache_key)
        if cached_ctx and not _preview_cache_has_content(cached_ctx):
            logger.warning(
                "[method-extract] preview 缓存无正文，忽略 key=%s，改走 MinIO/重解析",
                preview_cache_key[:24],
            )
            cached_ctx = None
        if cached_ctx:
            logger.info(
                "[method-extract] 使用 preview 缓存 key=%s text_len=%s",
                preview_cache_key[:24],
                len(str(cached_ctx.get("full_text") or "")),
            )
            cached_ctx["category"] = cached_ctx.get("category") or "_preview"
            cached_ctx["doc_id"] = cached_ctx.get("doc_id") or preview_cache_key
            pc = cached_ctx.get("parse_cache")
            if isinstance(pc, dict):
                cached_ctx["parse_cache"] = _finalize_parse_cache_like_word(pc)
            cached_ctx["all_figures"] = _merge_ctx_trace_figures(cached_ctx)
            return cached_ctx
        # 缓存缺失或为空：恢复 WJGL 文件的 category/doc_id，以便读 MinIO 章节存储
        category, doc_id = _parse_wjgl_category_doc_id(object_name, file_node)
    if category and doc_id and minio_client:
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import (
                load_parse_artifacts,
                parse_artifacts_ready,
                clean_document_text,
            )

            if parse_artifacts_ready(category, doc_id):
                cached = load_parse_artifacts(category, doc_id)
                has_content = bool(
                    cached
                    and (
                        cached.get("sections")
                        or cached.get("section_index")
                        or str(cached.get("full_markdown") or "").strip()
                        or str(cached.get("full_html") or "").strip()
                    )
                )
                if has_content:
                    sections = cached.get("sections") or []
                    full_markdown = str(cached.get("full_markdown") or "").strip()
                    if full_markdown:
                        try:
                            from pdf_extract.mineru_wjgl_service import _plain_from_markdown

                            full_text = clean_document_text(_plain_from_markdown(full_markdown))
                        except Exception:
                            full_text = clean_document_text(full_markdown)
                    elif str(cached.get("full_html") or "").strip():
                        full_text = clean_document_text(
                            re.sub(r"<[^>]+>", "\n", str(cached.get("full_html") or ""))
                        )
                    else:
                        full_text = "\n\n".join(
                            clean_document_text(str(s.get("text") or ""))
                            for s in sections
                            if str(s.get("text") or "").strip()
                        )
                    all_tables = _load_all_tables_for_doc(
                        category, doc_id, minio_client, bucket_name, parse_cache=cached,
                    )
                    logger.info(
                        "[method-extract] 使用 MinIO 章节存储 category=%s doc_id=%s sections=%s tables=%s",
                        category, doc_id, len(sections), len(all_tables),
                    )
                    cached = _finalize_parse_cache_like_word(cached)
                    trace_figures = _merge_ctx_trace_figures({
                        "all_figures": [],
                        "parse_cache": cached,
                    })
                    return {
                        "full_text": full_text,
                        "pages": [],
                        "all_tables": all_tables,
                        "all_figures": trace_figures,
                        "parse_cache": cached,
                        "category": category,
                        "doc_id": doc_id,
                        "source": "minio_section_store",
                    }
        except Exception as e:
            logger.warning("[method-extract] MinIO 章节存储加载失败，降级 MinerU: %s", e)

    if temp_path and ext in (".docx", ".doc"):
        word_ctx = _parse_file_with_word_native_for_extract(
            temp_path, ext, tmp_dir, preview_cache_key=preview_cache_key,
        )
        if word_ctx and str(word_ctx.get("full_text") or "").strip():
            return word_ctx

    if temp_path and ext == ".pdf":
        mineru_ctx = _parse_file_with_mineru_for_extract(
            temp_path, ext, tmp_dir, preview_cache_key=preview_cache_key,
        )
        if mineru_ctx and str(mineru_ctx.get("full_text") or "").strip():
            return mineru_ctx

    full_text = ""
    pages = []
    all_tables = []
    all_figures = []

    if ext == ".pdf" and _flgl_pdf_fallback_enabled():
        try:
            import fitz

            if not temp_path or not os.path.isfile(temp_path):
                raise FileNotFoundError(temp_path or "no pdf")
            doc = fitz.open(temp_path)
            page_texts = []
            for i, page in enumerate(doc):
                t = page.get_text() or ""
                page_texts.append(t)
                pages.append({"text": t, "tables": [], "figures": [], "page_number": i + 1})
            full_text = "\n\n".join(page_texts)
            doc.close()
            logger.info("[method-extract] fitz 回退抽取 PDF pages=%s text_len=%s", len(pages), len(full_text))
        except Exception as e:
            logger.warning("[method-extract] fitz 回退抽取失败: %s", e)
    elif ext in (".docx", ".doc") and _flgl_pdf_fallback_enabled():
        try:
            import docx as _docx

            doc = _docx.Document(temp_path)
            full_text = "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            logger.warning("[method-extract] docx 回退读取失败: %s", e)

    if not full_text.strip() and ext == ".pdf" and _flgl_pdf_fallback_enabled():
        try:
            from Process_data.extractor import DocumentExtractor

            extractor = DocumentExtractor(dpi=150, output_dir=tmp_dir)
            extract_result = extractor.extract(temp_path)
            full_text = extract_result.get("full_text", "") or ""
            pages = extract_result.get("pages", []) or []
            all_tables = extract_result.get("all_tables", []) or []
            all_figures = extract_result.get("all_figures", []) or []
            logger.info("[method-extract] DocumentExtractor 回退 OCR pages=%s", len(pages))
        except Exception as e:
            logger.warning("[method-extract] DocumentExtractor 回退失败: %s", e)

    return {
        "full_text": full_text,
        "pages": pages,
        "all_tables": all_tables,
        "all_figures": all_figures,
        "parse_cache": None,
        "source": "fitz_fallback" if pages and not all_figures else "fallback",
    }


def _normalize_section_no(raw: str) -> str:
    return str(raw or "").strip().lstrip(".")


def _section_no_is_strict_descendant(parent_no: str, child_no: str) -> bool:
    """child 是否为 parent 的严格子孙章节号（如 8.5.1 相对 8.5）。"""
    p = _normalize_section_no(parent_no)
    c = _normalize_section_no(child_no)
    if not p or not c or p == c:
        return False
    return c.startswith(p + ".")


def _normalize_section_title(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"^\d+(?:\.\d+)*[\s)）.、\-]*", "", t)
    return re.sub(r"\s+", "", t)


def _section_titles_compatible(requested: str, resolved: str) -> bool:
    """勾选标题与 section 记录标题是否一致（避免「试验」误匹配「温度和高度试验」）。"""
    req = _normalize_section_title(requested)
    res = _normalize_section_title(resolved)
    if not req:
        return True
    if not res:
        return False
    if req == res:
        return True
    min_len = max(4, len(req) // 2)
    if len(res) >= min_len and (req in res or res in req):
        return True
    return False


def _section_expected_span_len(
    sec: dict,
    sections: List[dict],
    full_text: str = "",
) -> int:
    """估算章节在 full_text 中应有的 span 长度，用于在重复切片间择优。"""
    if full_text and sec.get("start_offset") is not None:
        try:
            start_i = _span_start_for_decimal_section(sec, sections, full_text=full_text)
            end_i = _compute_section_span_end_offset(
                sec, sections, full_text_len=len(full_text), full_text=full_text,
            )
            return max(0, end_i - start_i)
        except Exception:
            pass
    return len(str(sec.get("text") or ""))


def _pick_best_section_for_no(
    sections: List[dict],
    section_no: str,
    *,
    hint: str = "",
    full_text: str = "",
) -> Optional[dict]:
    target = _normalize_section_no(section_no)
    if not target:
        return None
    matches = [
        s for s in sections
        if _normalize_section_no(str(s.get("section_no") or "")) == target
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    formal = [s for s in matches if _is_formal_decimal_heading(s, target)]
    if formal:
        matches = formal

    hint_l = str(hint or "").strip().lower()

    def _score(sec: dict) -> tuple:
        title = str(sec.get("title") or "")
        span_len = _section_expected_span_len(sec, sections, full_text)
        bonus = 0
        if "试验" in title and target.count(".") >= 1:
            bonus += 500
        if hint_l and hint_l[:24] in title.lower():
            bonus += 300
        if _is_formal_decimal_heading(sec, target):
            bonus += 200
        try:
            start = _span_start_for_decimal_section(sec, sections, full_text=full_text)
        except Exception:
            start = int(sec.get("start_offset") or 0)
        return (bonus + span_len, -start)

    return max(matches, key=_score)


def _resolve_parse_cache_section(
    parse_cache: Optional[dict],
    *,
    section_no: str = "",
    title: str = "",
    section_id: str = "",
    full_text: str = "",
) -> Optional[dict]:
    """将前端勾选节映射到 parse_cache sections（section_id 与 node_slices 同源）。"""
    sections = list((parse_cache or {}).get("sections") or [])
    if not sections:
        return None

    if not full_text.strip() and parse_cache:
        parsed_doc = parse_cache.get("parsed_document") or {}
        full_text = str(
            parsed_doc.get("raw_full_text")
            or parse_cache.get("raw_full_text")
            or parse_cache.get("full_text")
            or ""
        ).strip()

    by_id = {
        str(sec.get("section_id") or ""): sec
        for sec in sections
        if str(sec.get("section_id") or "").strip()
    }
    sid = str(section_id or "").strip()
    sno = _normalize_section_no(section_no)
    title_n = _normalize_section_title(title)

    if sid and sid in by_id:
        sec_by_id = by_id[sid]
        sec_no = _normalize_section_no(sec_by_id.get("section_no") or "")
        no_ok = (not sno) or (sec_no == sno)
        title_ok = _section_titles_compatible(title, str(sec_by_id.get("title") or ""))
        if no_ok and title_ok:
            return sec_by_id
        if sno:
            best = _pick_best_section_for_no(
                sections, sno, hint=title, full_text=full_text,
            )
            if best:
                return best
        return sec_by_id

    if sno:
        best = _pick_best_section_for_no(
            sections, sno, hint=title, full_text=full_text,
        )
        if best:
            if not title_n:
                return best
            best_title = _normalize_section_title(best.get("title") or "")
            if (
                best_title == title_n
                or title_n in best_title
                or best_title in title_n
            ):
                return best

    matches: List[dict] = []

    for sec in sections:
        sec_no = _normalize_section_no(sec.get("section_no") or "")
        if not sec_no:
            try:
                from blueprints.ZSGL.WJGL1.document_parse_service import _parse_section_no

                sec_no = _normalize_section_no(_parse_section_no(str(sec.get("title") or "")))
            except Exception:
                sec_no = ""
        sec_title = _normalize_section_title(sec.get("title") or "")

        if sno and sec_no != sno:
            continue
        if title_n:
            if sec_title == title_n or title_n == sec_title:
                matches.append(sec)
            elif sno and (title_n in sec_title or sec_title in title_n):
                matches.append(sec)
        elif sno:
            matches.append(sec)

    if not matches and title_n:
        for sec in sections:
            sec_title = _normalize_section_title(sec.get("title") or "")
            if sec_title == title_n:
                matches.append(sec)

    if len(matches) == 1:
        return matches[0]
    if sno and matches:
        return _pick_best_section_for_no(
            matches, sno, hint=title, full_text=full_text,
        )
    if title_n:
        for sec in sections:
            sec_title = _normalize_section_title(sec.get("title") or "")
            if title_n in sec_title or sec_title in title_n:
                return sec
    return None


def _section_no_sort_key_tuple(section_no: str) -> tuple:
    from blueprints.ZSGL.WJGL1.document_parse_service import _section_no_sort_key

    return _section_no_sort_key(section_no)


def _section_nos_from_group(group: dict) -> List[str]:
    """用户勾选的章节号列表（仅 parent + children，不做 GB 自动扩展）。"""
    return [
        _normalize_section_no(item.get("sectionNo"))
        for item in _explicit_section_items_from_group(group)
        if _normalize_section_no(item.get("sectionNo"))
    ]


def _explicit_section_items_from_group(group: dict) -> List[dict]:
    """目录勾选提取：仅合并用户显式勾选的 parent 与 children。"""
    items: List[dict] = []
    seen: set = set()

    def _add(sec: dict) -> None:
        if not isinstance(sec, dict):
            return
        sno = _normalize_section_no(sec.get("sectionNo") or sec.get("section_no"))
        title = str(sec.get("title") or "").strip()
        sid = str(sec.get("section_id") or sec.get("sectionId") or "").strip()
        if not sno and not title and not sid:
            return
        key = sid or f"{sno}\0{title}"
        if key in seen:
            return
        seen.add(key)
        items.append({"sectionNo": sno, "title": title, "section_id": sid})

    parent = group.get("parent") or {}
    _add(parent)
    for ch in group.get("children") or []:
        _add(ch)
    return items


def _sort_explicit_section_items(items: List[dict], parse_cache: Optional[dict] = None) -> List[dict]:
    """按文档章节顺序排列勾选节，避免合并后内容乱序。"""
    sections = (parse_cache or {}).get("sections") or []
    sid_order = {
        str(sec.get("section_id")): idx
        for idx, sec in enumerate(sections)
        if sec.get("section_id")
    }

    def _key(item: dict) -> tuple:
        sno = _normalize_section_no(item.get("sectionNo"))
        if sno:
            return (0, _section_no_sort_key_tuple(sno))
        sid = str(item.get("section_id") or "")
        if sid and sid in sid_order:
            return (1, sid_order[sid])
        return (2, str(item.get("title") or ""))

    return sorted(items, key=_key)


def _should_auto_split_peer_methods(parent_no: str, parent_title: str) -> bool:
    """边界性能等容器节：未指定子节时按并列试验子节拆成多个方法。"""
    sno = _normalize_section_no(parent_no)
    title = str(parent_title or "")
    if sno == "8.5" or "边界" in title:
        return True
    if "指标" in title and "试验" in title:
        return True
    return False


def _expand_peer_method_groups(
    group: dict,
    parse_cache: Optional[dict],
    full_text: str,
) -> List[dict]:
    """parent 为 8.5 等容器节且未勾选 children 时，按 8.5.1/8.5.2… 拆成多组。"""
    if group.get("children"):
        return [group]
    parent = group.get("parent") or {}
    parent_no = _normalize_section_no(parent.get("sectionNo") or "")
    parent_title = str(parent.get("title") or "")
    if not _should_auto_split_peer_methods(parent_no, parent_title):
        return [group]
    if not full_text.strip() or not parse_cache:
        return [group]

    sections = list((parse_cache or {}).get("sections") or [])
    parent_sec = _resolve_parse_cache_section(
        parse_cache,
        section_no=parent_no,
        title=parent_title,
        section_id=str(parent.get("section_id") or parent.get("sectionId") or ""),
        full_text=full_text,
    )
    if not parent_sec:
        return [group]

    start = _span_start_for_decimal_section(parent_sec, sections, full_text=full_text)
    end = _compute_section_span_end_offset(
        parent_sec, sections, full_text_len=len(full_text), full_text=full_text,
    )
    child_pat = re.compile(
        rf"(?:^|\n){re.escape(parent_no)}\.(\d+)\s+([^\n]{{2,80}})",
    )
    peers: List[tuple] = []
    for m in child_pat.finditer(full_text, start, end):
        off = m.start() + (1 if full_text[m.start()] == "\n" else 0)
        child_no = f"{parent_no}.{m.group(1)}"
        child_title = m.group(2).strip()
        if not re.search(r"试验|考核|性能|速度|重量|角速度", child_title):
            continue
        peers.append((off, child_no, child_title))

    if len(peers) < 2:
        return [group]

    out: List[dict] = []
    for _off, child_no, child_title in peers:
        child_sec = _pick_best_section_for_no(
            sections, child_no, hint=child_title, full_text=full_text,
        )
        out.append({
            "parent": {
                "sectionNo": child_no,
                "title": f"{child_no} {child_title}",
                "section_id": str((child_sec or {}).get("section_id") or ""),
                "level": int((child_sec or {}).get("level") or 0) or len(child_no.split(".")),
            },
            "children": [],
        })
    logger.info(
        "[method-extract-sections] 容器节 %s 自动拆分为 %d 个并列方法: %s",
        parent_no,
        len(out),
        [g["parent"]["sectionNo"] for g in out],
    )
    return out


def _min_covering_section_roots(section_nos: List[str]) -> List[str]:
    """去掉已被祖先章节覆盖的子节（抓取时 include_children=True）。"""
    unique = sorted(
        {_normalize_section_no(n) for n in section_nos if _normalize_section_no(n)},
        key=_section_no_sort_key_tuple,
    )
    roots: List[str] = []
    for n in unique:
        if any(n != r and n.startswith(r + ".") for r in unique):
            continue
        roots.append(n)
    return roots


def _text_offset_for_section_heading(
    full_text: str,
    section_no: str,
    *,
    title_hint: str = "",
    min_offset: int = 0,
) -> Optional[int]:
    """在 full_text 中定位节标题首次出现位置（避免 parse 重复切片偏移错位）。"""
    sno = _normalize_section_no(section_no)
    if not sno or not str(full_text or ""):
        return None

    candidates: List[int] = []
    th = str(title_hint or "").strip()
    if th:
        pos = full_text.find(th, min_offset)
        if pos >= 0:
            candidates.append(pos)

    pat = re.compile(rf"(?:^|\n){re.escape(sno)}\s+")
    for m in pat.finditer(full_text, min_offset):
        off = m.start()
        if off > 0 and full_text[off] == "\n":
            off += 1
        candidates.append(off)

    if not candidates:
        return None
    return min(candidates)


def _next_sibling_section_offset_in_full_text(
    full_text: str,
    section_no: str,
    start: int,
) -> Optional[int]:
    """在 full_text 中找 anchor 之后下一同级/上级章节标题位置（优先于 parse_cache offset）。"""
    anchor = _normalize_section_no(section_no)
    if not anchor or not full_text or start < 0:
        return None
    parts = [p for p in anchor.split(".") if p]
    if not parts:
        return None

    anchor_key = _section_no_sort_key_tuple(anchor)
    parent_prefix = ".".join(parts[:-1]) if len(parts) > 1 else ""
    pat = re.compile(r"(?:^|\n)(\d+(?:\.\d+)*)\s+")

    for m in pat.finditer(full_text, start + 1):
        off = m.start() + (1 if full_text[m.start()] == "\n" else 0)
        other_no = _normalize_section_no(m.group(1))
        if not other_no or not re.match(r"^\d", other_no):
            continue
        other_key = _section_no_sort_key_tuple(other_no)
        if other_key <= anchor_key:
            continue
        other_parts = [p for p in other_no.split(".") if p]
        if len(other_parts) == len(parts) and other_parts[:-1] == parts[:-1]:
            return off
        if len(parts) >= 2:
            if not other_no.startswith(anchor + ".") and other_no != anchor:
                prefix_key = _section_no_sort_key_tuple(parent_prefix or parts[0])
                if other_key >= prefix_key:
                    return off
        elif len(parts) == 1 and other_no.isdigit() and int(other_no) > int(parts[0]):
            return off
    return None


def _compute_section_span_end_offset(
    sec: dict,
    sections: List[dict],
    *,
    full_text_len: int = 0,
    full_text: str = "",
) -> int:
    """章节正文结束偏移：至下一个同级/更高级标题（含其下全部子内容）。"""
    if not sec or not sections:
        return full_text_len or 0

    try:
        start_i = _span_start_for_decimal_section(sec, sections, full_text=full_text)
    except (TypeError, ValueError):
        start_i = int(sec.get("start_offset") or 0)

    parse_end: Optional[int] = None
    decimal_end = _span_end_by_decimal_section_no(sec, sections, full_text_len)
    if decimal_end is not None:
        parse_end = decimal_end
    else:
        top_level_end = _span_end_for_top_level_chapter(sec, sections, full_text_len)
        if top_level_end is not None:
            parse_end = top_level_end

    if full_text:
        anchor = _normalize_section_no(sec.get("section_no") or "")
        text_end = _next_sibling_section_offset_in_full_text(full_text, anchor, start_i)
        if text_end is not None and text_end > start_i:
            return text_end

    if parse_end is not None:
        return parse_end

    sec_id = str(sec.get("section_id") or "")
    sec_level = int(sec.get("level") or 1)
    anchor_no = _normalize_section_no(sec.get("section_no") or "")
    try:
        sec_off = int(sec.get("start_offset") or 0)
    except (TypeError, ValueError):
        sec_off = 0

    ordered: List[tuple] = []
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            continue
        try:
            off = int(s.get("start_offset") or 0)
        except (TypeError, ValueError):
            off = 0
        ordered.append((off, i, s))
    ordered.sort(key=lambda x: (x[0], x[1]))

    my_idx = next(
        (j for j, (_, _, s) in enumerate(ordered) if str(s.get("section_id") or "") == sec_id),
        None,
    )
    if my_idx is None:
        return full_text_len or 0

    for j in range(my_idx + 1, len(ordered)):
        off, _, other = ordered[j]
        other_level = int(other.get("level") or 1)
        other_no = _normalize_section_no(other.get("section_no") or "")
        if off <= sec_off:
            continue
        if other_level <= sec_level:
            if anchor_no and other_no == anchor_no and other_level == sec_level:
                continue
            return off
    return full_text_len or 0


def _span_end_by_decimal_section_no(
    sec: dict,
    sections: List[dict],
    full_text_len: int,
) -> Optional[int]:
    """多级节号（如 8.2.3）按下一同级兄弟节（8.2.4）截断，包含中间列表项。"""
    anchor = _normalize_section_no(sec.get("section_no") or "")
    if not anchor or not re.match(r"^\d", anchor):
        return None
    parts = [p for p in anchor.split(".") if p]
    if len(parts) < 2:
        return None

    try:
        start = int(sec.get("start_offset") or 0)
    except (TypeError, ValueError):
        return None

    parent_prefix = ".".join(parts[:-1])
    anchor_key = _section_no_sort_key_tuple(anchor)

    ordered: List[tuple] = []
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            continue
        try:
            off = int(s.get("start_offset") or 0)
        except (TypeError, ValueError):
            off = 0
        ordered.append((off, i, s))
    ordered.sort(key=lambda x: (x[0], x[1]))

    for off, _, other in ordered:
        if off <= start:
            continue
        other_no = _normalize_section_no(other.get("section_no") or "")
        if not other_no or not re.match(r"^\d", other_no):
            continue
        other_parts = [p for p in other_no.split(".") if p]
        if len(other_parts) == len(parts) and other_parts[:-1] == parts[:-1]:
            if _section_no_sort_key_tuple(other_no) > anchor_key:
                return off
        elif _section_no_sort_key_tuple(other_no) >= _section_no_sort_key_tuple(parent_prefix):
            if not other_no.startswith(anchor + ".") and other_no != anchor:
                return off
    return full_text_len or 0


def _is_formal_decimal_heading(sec: dict, anchor: str) -> bool:
    """节号 anchor 是否为该切片的正式标题（禁止在其它节正文中误匹配节号字符串）。"""
    if not anchor:
        return False
    title = str(sec.get("title") or "").strip()
    if not title:
        other_no = _normalize_section_no(sec.get("section_no") or "")
        return other_no == anchor
    # 须为完整节号前缀，如 "8.5.3 标题"，排除 "8.5.3.1" 与正文内嵌节号
    pat = re.compile(r"^" + re.escape(anchor) + r"(?:\s|$|[^\d.])")
    return bool(pat.match(title))


def _trim_leading_before_formal_heading(text: str, section_no: str) -> str:
    """去掉正式节标题之前的噪声（parse 切片常混入上一节尾部）。"""
    anchor = _normalize_section_no(section_no)
    if not anchor or not str(text or "").strip():
        return str(text or "")
    pat = re.compile(rf"(?:^|\n){re.escape(anchor)}\s+")
    m = pat.search(text)
    if not m:
        return text
    off = m.start() + (1 if m.start() > 0 and text[m.start()] == "\n" else 0)
    if off <= 0:
        return text
    return text[off:].lstrip("\n")


def _span_start_for_decimal_section(
    sec: dict,
    sections: List[dict],
    *,
    full_text: str = "",
) -> int:
    """多级节号取最早出现的正式标题偏移（避免正文落在后续重复切片上）。"""
    anchor = _normalize_section_no(sec.get("section_no") or "")
    title = str(sec.get("title") or "")

    if full_text and anchor:
        text_off = _text_offset_for_section_heading(full_text, anchor, title_hint=title)
        if text_off is not None:
            return text_off

    if not anchor or len([p for p in anchor.split(".") if p]) < 2:
        try:
            return int(sec.get("start_offset") or 0)
        except (TypeError, ValueError):
            return 0

    starts: List[int] = []
    for other in [sec] + [s for s in sections if isinstance(s, dict) and s is not sec]:
        if not _is_formal_decimal_heading(other, anchor):
            continue
        try:
            starts.append(int(other.get("start_offset") or 0))
        except (TypeError, ValueError):
            continue
    if starts:
        return min(starts)
    try:
        return int(sec.get("start_offset") or 0)
    except (TypeError, ValueError):
        return 0


def _span_end_for_top_level_chapter(
    sec: dict,
    sections: List[dict],
    full_text_len: int,
) -> Optional[int]:
    """顶级章（如 8）跳过重复节号切片，延伸至下一章（如 9）。"""
    anchor = _normalize_section_no(sec.get("section_no") or "")
    if not anchor or not re.match(r"^\d+$", anchor):
        return None
    sec_level = int(sec.get("level") or 1)
    if sec_level > 2:
        return None
    try:
        start = int(sec.get("start_offset") or 0)
        chapter_no = int(anchor)
    except (TypeError, ValueError):
        return None

    ordered: List[tuple] = []
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            continue
        try:
            off = int(s.get("start_offset") or 0)
        except (TypeError, ValueError):
            off = 0
        ordered.append((off, i, s))
    ordered.sort(key=lambda x: (x[0], x[1]))

    for off, _, other in ordered:
        if off <= start:
            continue
        other_no = _normalize_section_no(other.get("section_no") or "")
        other_level = int(other.get("level") or 1)
        if other_level > sec_level:
            continue
        if other_no.isdigit() and int(other_no) > chapter_no:
            return off
    return full_text_len or 0


def _filter_items_covered_by_ancestor(
    items: List[dict],
    parse_cache: Optional[dict],
    *,
    full_text: str = "",
) -> List[dict]:
    """已勾选父节时，去掉其范围内的子孙节，避免重复拼接、拆开子项。"""
    if not items:
        return items

    sections = list((parse_cache or {}).get("sections") or [])
    ft = _resolve_extract_full_text(parse_cache, full_text)

    resolved: List[tuple] = []
    for item in items:
        sec = None
        if parse_cache:
            sec = _resolve_parse_cache_section(
                parse_cache,
                section_no=str(item.get("sectionNo") or ""),
                title=str(item.get("title") or ""),
                section_id=str(item.get("section_id") or ""),
            )
        resolved.append((item, sec))

    def _item_key(item: dict) -> str:
        sid = str(item.get("section_id") or "").strip()
        if sid:
            return sid
        sno = _normalize_section_no(str(item.get("sectionNo") or ""))
        title = str(item.get("title") or "").strip()
        return f"{sno}\0{title}"

    def _ancestor_has_body(sec: Optional[dict]) -> bool:
        if not sec:
            return False
        return _section_has_substantive_content(sec, parse_cache, full_text=ft)

    remove_keys: set = set()

    for i, (item_a, sec_a) in enumerate(resolved):
        key_a = _item_key(item_a)
        sno_a = _normalize_section_no(str(item_a.get("sectionNo") or ""))
        for j, (item_b, sec_b) in enumerate(resolved):
            if i == j:
                continue
            key_b = _item_key(item_b)
            sno_b = _normalize_section_no(str(item_b.get("sectionNo") or ""))

            # 章节号：当前节为另一节的子孙
            if sno_a and sno_b and _section_no_is_strict_descendant(sno_b, sno_a):
                if _ancestor_has_body(sec_b):
                    remove_keys.add(key_a)
                else:
                    remove_keys.add(key_b)
                continue

            # offset：当前节落在另一节 span 内
            if sec_a and sec_b and sections and sec_a.get("start_offset") is not None:
                try:
                    sa = int(sec_a.get("start_offset") or 0)
                    sb = int(sec_b.get("start_offset") or 0)
                except (TypeError, ValueError):
                    continue
                ea = _compute_section_span_end_offset(sec_a, sections, full_text_len=sa + 10**9)
                eb = _compute_section_span_end_offset(sec_b, sections, full_text_len=ea)
                inside = sb <= sa and ea <= eb and (sb < sa or ea < eb)
                if not inside:
                    continue
                if _ancestor_has_body(sec_b):
                    remove_keys.add(key_a)
                else:
                    remove_keys.add(key_b)

    keep = [item for item, _sec in resolved if _item_key(item) not in remove_keys]
    return keep


def _html_looks_title_only(html: str, title: str) -> bool:
    """判断 section html 是否仅有标题、无实质正文。"""
    from .method_section_enrich import content_looks_title_only

    return content_looks_title_only(html, title)


def _prefer_html_when_md_lacks_figures(
    parse_cache: Optional[dict],
    sid: str,
    content: str,
    fetch_path: str,
    content_fmt: str,
    resolved: Optional[dict],
) -> Tuple[str, str, str]:
    """预览 full_html / section.html 有图而 Markdown 切片只有「Visio图」文字时，改用 HTML。"""
    from .method_section_enrich import content_has_embedded_figure

    if content_has_embedded_figure(content):
        return content, fetch_path, content_fmt
    html_slices = (parse_cache or {}).get("node_slices") or {}
    sec = dict(resolved or {})
    candidates = [
        (str(html_slices.get(sid) or "").strip(), "node_slices", "html"),
        (str(sec.get("html") or "").strip(), "section_html", "html"),
    ]
    for body, path, fmt in candidates:
        if body and content_has_embedded_figure(body):
            title = str(sec.get("title") or "")
            if not _html_looks_title_only(body, title):
                return body, path, fmt
    return content, fetch_path, content_fmt


def _section_content_plain_len(content: str) -> int:
    raw = (content or "").strip()
    if not raw:
        return 0
    if re.search(r"<\s*(p|div|table|h[1-6]|ul|ol|li|br|strong)\b", raw, re.I):
        try:
            from .method_section_enrich import html_to_section_plain

            return len(html_to_section_plain(raw))
        except Exception:
            pass
    return len(raw)


def _section_content_looks_incomplete(
    content: str,
    expected_span: str,
    title: str = "",
) -> bool:
    """已取到的正文明显短于 offset 整段应有跨度时视为不完整。"""
    c_len = _section_content_plain_len(content)
    e_len = len((expected_span or "").strip())
    if c_len <= 0 or e_len <= 0:
        return False
    if _html_looks_title_only(content, title):
        return not _html_looks_title_only(expected_span, title)
    if e_len <= c_len + 120:
        return False
    if c_len / max(e_len, 1) >= 0.55:
        return False
    return e_len >= 250 or e_len > c_len * 1.5



def _apply_section_content_coverage_guard(
    content: str,
    expected_span: str,
    resolved: dict,
    *,
    parse_cache: Optional[dict],
    sections: List[dict],
    slices: dict,
    sid: str,
    full_text: str,
    title: str,
    category: str = "",
    doc_id: str = "",
) -> Tuple[str, str]:
    """node_slices_md 等路径截断时，回退 offset_span / HTML 取更长正文。"""
    resolved_title = str(resolved.get("title") or title)
    if not _section_content_looks_incomplete(content, expected_span, resolved_title):
        return content, ""
    c_len = _section_content_plain_len(content)
    e_len = len((expected_span or "").strip())
    # node_slices_md 明显长于 sections[].text 时，offset 残片不可信
    if c_len > 0 and e_len > 0 and c_len > e_len * 1.2:
        return content, ""
    richer, richer_path = _richer_section_content_fallback(
        resolved,
        parse_cache=parse_cache,
        sections=sections,
        slices=slices,
        sid=sid,
        full_text=full_text,
        title=title,
        category=category,
        doc_id=doc_id,
    )
    if len(richer) > len(content):
        return richer, richer_path or "offset_span_coverage"
    if expected_span.strip() and len(expected_span) > len(content):
        return expected_span.strip(), "offset_span_coverage"
    return content, ""


def _section_has_substantive_content(
    sec: Optional[dict],
    parse_cache: Optional[dict],
    *,
    full_text: str = "",
) -> bool:
    """节内是否有实质正文（非仅标题）。不用 offset 整段 span，避免父节因含子节而被误判。"""
    if not sec:
        return False
    title = str(sec.get("title") or sec.get("heading") or "")
    sid = str(sec.get("section_id") or "")
    md_slices = (parse_cache or {}).get("node_slices_md") or {}
    md = str(md_slices.get(sid) or "").strip()
    if md and not _html_looks_title_only(md, title):
        return True
    html = str(sec.get("html") or "").strip()
    if html and not _html_looks_title_only(html, title):
        return True
    text = str(sec.get("text") or "").strip()
    if text and not _html_looks_title_only(text, title):
        return True
    slices = (parse_cache or {}).get("node_slices") or {}
    node_html = str(slices.get(sid) or "").strip()
    if node_html and not _html_looks_title_only(node_html, title):
        return True
    return False


def _richer_section_content_fallback(
    resolved: dict,
    *,
    parse_cache: Optional[dict],
    sections: List[dict],
    slices: dict,
    sid: str,
    full_text: str,
    title: str,
    category: str = "",
    doc_id: str = "",
) -> Tuple[str, str]:
    """node_slices_md 仅标题时，回退 node_slices / merge / offset_span，取最长非空壳结果。"""
    resolved_title = str(resolved.get("title") or title)
    candidates: List[Tuple[str, str]] = []

    path_tags: List[str] = []
    md = _section_markdown_from_parse_cache(
        parse_cache,
        section_id=sid,
        resolved=resolved,
        category=category,
        doc_id=doc_id,
        path_out=path_tags,
    )
    if md.strip() and not _html_looks_title_only(md, resolved_title):
        candidates.append((md.strip(), path_tags[0] if path_tags else "node_slices_md"))

    ft = _resolve_extract_full_text(parse_cache, full_text)
    if ft and resolved.get("start_offset") is not None:
        span = _compute_expected_span_plain(resolved, sections, ft)
        if span.strip() and not _html_looks_title_only(span, resolved_title):
            candidates.append((span.strip(), "offset_span"))

    path_tags: List[str] = []
    html = _section_html_from_parse_cache(
        parse_cache,
        section_id=sid,
        resolved=resolved,
        full_text=ft,
        path_out=path_tags,
    )
    if html.strip() and not _html_looks_title_only(html, resolved_title):
        try:
            from .method_section_enrich import html_to_section_plain

            plain = html_to_section_plain(html)
            if plain.strip():
                candidates.append((plain.strip(), path_tags[0] if path_tags else "offset_span"))
        except Exception:
            candidates.append((html.strip(), path_tags[0] if path_tags else "parse_cache_html"))

    if sid and parse_cache:
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import merge_section_html

            merged = merge_section_html([sid], sections, slices)
            if merged.strip() and not _html_looks_title_only(merged, resolved_title):
                from .method_section_enrich import html_to_section_plain

                plain = html_to_section_plain(merged)
                if plain.strip():
                    candidates.append((plain.strip(), "node_slices_merge"))
        except Exception:
            pass

    if not candidates:
        return "", ""
    best = max(candidates, key=lambda x: len(x[0]))
    return best


def _section_content_html_from_resolved(
    resolved: dict,
    *,
    full_text: str = "",
) -> str:
    """从 section 记录回填正文 HTML（html 过短或仅标题时）。"""
    html = str(resolved.get("html") or "").strip()
    title = str(resolved.get("title") or resolved.get("heading") or "").strip()
    text_body = str(resolved.get("text") or "").strip()

    if html and not _html_looks_title_only(html, title):
        return html

    if text_body and (not title or len(text_body) > len(title) + 10):
        try:
            from blueprints.FLGL.method_section_enrich import _plain_to_html

            return _plain_to_html(text_body).strip()
        except Exception:
            pass
        import html as html_module

        parts = [
            f"<p>{html_module.escape(line)}</p>"
            for line in text_body.splitlines()
            if line.strip()
        ]
        if parts:
            return "".join(parts)

    start_off = resolved.get("start_offset")
    end_off = resolved.get("end_offset")
    if full_text and start_off is not None:
        try:
            from blueprints.ZSGL.WJGL1.document_structure import _slice_body_between_offsets

            end_i = int(end_off) if end_off is not None else len(full_text)
            chunk = _slice_body_between_offsets(
                full_text,
                int(start_off),
                end_i,
                title,
            )
            if chunk:
                from blueprints.FLGL.method_section_enrich import _plain_to_html

                return _plain_to_html(chunk).strip()
        except Exception:
            pass

    return html


def _resolve_extract_full_text(
    parse_cache: Optional[dict],
    fallback: str = "",
) -> str:
    """勾选提取时优先用完整原文（与 LLM 解析同源），供 offset 切片回填。"""
    if parse_cache:
        parsed_doc = parse_cache.get("parsed_document") or {}
        raw = str(parsed_doc.get("raw_full_text") or "").strip()
        if raw:
            return raw
        cached = str(parse_cache.get("raw_full_text") or parse_cache.get("full_text") or "").strip()
        if cached:
            return cached
    return str(fallback or "").strip()


def _compute_expected_span_plain(
    resolved: Optional[dict],
    sections: List[dict],
    full_text: str,
) -> str:
    """用 raw_full_text + offset 计算章节应有整段纯文本。"""
    if not resolved or not full_text:
        return str((resolved or {}).get("text") or "")
    if resolved.get("start_offset") is None:
        return str(resolved.get("text") or "")
    try:
        from blueprints.ZSGL.WJGL1.document_structure import _slice_body_between_offsets

        start_i = _span_start_for_decimal_section(resolved, sections, full_text=full_text)
        end_i = _compute_section_span_end_offset(
            resolved, sections, full_text_len=len(full_text), full_text=full_text,
        )
        if end_i <= start_i:
            end_i = len(full_text)
        title = str(resolved.get("title") or "")
        chunk = _slice_body_between_offsets(full_text, start_i, end_i, title)
        if not chunk:
            chunk = full_text[start_i:end_i].strip()
        return chunk
    except Exception:
        return str(resolved.get("text") or "")


def _section_markdown_from_parse_cache(
    parse_cache: Optional[dict],
    *,
    section_id: str = "",
    resolved: Optional[dict] = None,
    category: str = "",
    doc_id: str = "",
    path_out: Optional[List[str]] = None,
) -> str:
    """从 parse_cache 取章节 Markdown 切片（优先 node_slices_md）。"""
    if not parse_cache:
        return ""
    sec = dict(resolved or {})
    sid = str(section_id or sec.get("section_id") or "").strip()
    md_slices = parse_cache.get("node_slices_md") or {}
    if sid and str(md_slices.get(sid) or "").strip():
        if path_out is not None:
            path_out.append("node_slices_md")
        return str(md_slices[sid]).strip()

    if category and doc_id and sid:
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import load_section_markdown_from_minio

            md = load_section_markdown_from_minio(category, doc_id, sid)
            if md.strip():
                if path_out is not None:
                    path_out.append("minio_md")
                return md.strip()
        except Exception as exc:
            logger.debug("[method-extract] load section md failed sid=%s: %s", sid, exc)

    full_md = str(
        parse_cache.get("full_markdown")
        or (parse_cache.get("parsed_document") or {}).get("full_markdown")
        or ""
    ).strip()
    if full_md and sid and sec:
        try:
            from pdf_extract.mineru_wjgl_service import slice_markdown_by_sections

            chunk = slice_markdown_by_sections(full_md, [sec]).get(sid, "")
            if str(chunk or "").strip():
                if path_out is not None:
                    path_out.append("full_markdown_slice")
                return str(chunk).strip()
        except Exception as exc:
            logger.debug("[method-extract] markdown slice failed sid=%s: %s", sid, exc)

    raw_html = str(parse_cache.get("raw_full_html") or parse_cache.get("full_html") or "").strip()
    if raw_html and sec.get("start_offset") is not None:
        try:
            from blueprints.FLGL.method_section_enrich import html_to_section_plain

            full_text = _resolve_extract_full_text(parse_cache, "")
            sections = list(parse_cache.get("sections") or [])
            start_i = _span_start_for_decimal_section(sec, sections, full_text=full_text)
            end_i = _compute_section_span_end_offset(
                sec, sections, full_text_len=len(full_text), full_text=full_text,
            )
            pmap = (parse_cache.get("parsed_document") or {}).get("word_paragraph_map") or []
            if pmap and full_text:
                start_chars = [int(e.get("char_start") or 0) for e in pmap if e.get("char_start") is not None]
                end_chars = [int(e.get("char_end") or 0) for e in pmap if e.get("char_end") is not None]
                if start_chars and end_chars:
                    ratio_start = start_i / max(len(full_text), 1)
                    ratio_end = end_i / max(len(full_text), 1)
                    html_len = len(raw_html)
                    h_start = int(ratio_start * html_len)
                    h_end = int(ratio_end * html_len)
                    chunk = raw_html[h_start:h_end].strip()
                    if chunk:
                        if path_out is not None:
                            path_out.append("raw_html_span")
                        return html_to_section_plain(chunk)
        except Exception as exc:
            logger.debug("[method-extract] raw_html span failed sid=%s: %s", sid, exc)

    # 兜底：node_slices HTML → plain/md
    html_slices = parse_cache.get("node_slices") or {}
    if sid and str(html_slices.get(sid) or "").strip():
        try:
            from blueprints.FLGL.method_section_enrich import html_to_section_plain

            if path_out is not None:
                path_out.append("node_slices_html")
            return html_to_section_plain(str(html_slices[sid]))
        except Exception:
            pass
    return ""


def _section_html_from_parse_cache(
    parse_cache: Optional[dict],
    *,
    section_id: str = "",
    resolved: Optional[dict] = None,
    full_text: str = "",
    path_out: Optional[List[str]] = None,
) -> str:
    """从 parse_cache 取章节**整段**正文（含（1）（a）等子项，直到同级下一节）。"""
    if not parse_cache:
        return ""
    sec = dict(resolved or {})
    sid = str(section_id or sec.get("section_id") or "").strip()
    sections = list(parse_cache.get("sections") or [])
    title = str(sec.get("title") or "").strip()
    slices = parse_cache.get("node_slices") or {}

    # 优先 node_slices（与 04_sections / debug 05_node_slices 同源），offset 仅作兜底
    if sid and str(slices.get(sid) or "").strip():
        raw = str(slices[sid]).strip()
        if not _html_looks_title_only(raw, title):
            out = _section_content_html_from_resolved(
                {"html": raw, "text": str(sec.get("text") or ""), "title": title, **sec},
                full_text=full_text,
            )
            if out.strip() and not _html_looks_title_only(out, title):
                if path_out is not None:
                    path_out.append("node_slices")
                return out.strip()
            if raw and not _html_looks_title_only(raw, title):
                if path_out is not None:
                    path_out.append("node_slices")
                return raw

    if full_text and sec.get("start_offset") is not None:
        try:
            from blueprints.ZSGL.WJGL1.document_structure import _slice_body_between_offsets

            start_i = _span_start_for_decimal_section(sec, sections, full_text=full_text)
            end_i = _compute_section_span_end_offset(
                sec, sections, full_text_len=len(full_text), full_text=full_text,
            )
            if end_i <= start_i:
                end_i = len(full_text)
            chunk = _slice_body_between_offsets(full_text, start_i, end_i, title)
            if not chunk:
                chunk = full_text[start_i:end_i].strip()
            anchor_no = _normalize_section_no(sec.get("section_no") or "")
            if anchor_no:
                chunk = _trim_leading_before_formal_heading(chunk, anchor_no)
            if chunk.strip() and not _html_looks_title_only(chunk, title):
                if path_out is not None:
                    path_out.append("offset_span")
                return chunk.strip()
        except Exception as exc:
            logger.debug("[method-extract] offset 整段切片失败 sid=%s: %s", sid, exc)

    text_body = str(sec.get("text") or "").strip()
    html = str(sec.get("html") or "").strip()
    for raw, tag in ((html, "section_html"), (text_body, "section_text")):
        if not raw:
            continue
        out = _section_content_html_from_resolved(
            {"html": raw, "text": text_body, "title": title, **sec},
            full_text=full_text,
        )
        if out.strip() and not _html_looks_title_only(out, title):
            if path_out is not None:
                path_out.append(tag)
            return out.strip()

    return ""


def _section_record_by_id(parse_cache: Optional[dict], section_id: str) -> dict:
    sid = str(section_id or "").strip()
    if not sid or not parse_cache:
        return {}
    for sec in (parse_cache.get("sections") or []):
        if str(sec.get("section_id") or "").strip() == sid:
            return dict(sec)
    return {}


def _fetch_section_body_direct(
    parse_cache: Optional[dict],
    section_id: str,
    *,
    category: str = "",
    doc_id: str = "",
    resolved: Optional[dict] = None,
) -> Tuple[str, str, str]:
    """
    按 section_id 直读 parse 产物（与目录树 / 05_node_slices_md 同源）。
    返回 (content, fetch_path, content_format)；format 为 markdown|html|plain。
    不走 offset_span；sections[].text 仅作最后兜底。
    """
    if not parse_cache:
        return "", "", ""
    sid = str(section_id or "").strip()
    if not sid:
        return "", "", ""
    sec = dict(resolved or _section_record_by_id(parse_cache, sid))
    title = str(sec.get("title") or "")

    def _substantive(body: str) -> bool:
        return bool(str(body or "").strip()) and not _html_looks_title_only(body, title)

    md_slices = parse_cache.get("node_slices_md") or {}
    md = str(md_slices.get(sid) or "").strip()

    full_md = str(
        parse_cache.get("full_markdown")
        or (parse_cache.get("parsed_document") or {}).get("full_markdown")
        or ""
    ).strip()
    full_md_chunk = ""
    if full_md and sec:
        try:
            from pdf_extract.mineru_wjgl_service import slice_markdown_by_sections

            full_md_chunk = str(slice_markdown_by_sections(full_md, [sec]).get(sid) or "").strip()
        except Exception as exc:
            logger.debug("[method-extract] full_md slice sid=%s: %s", sid, exc)

    if _substantive(md):
        if _substantive(full_md_chunk) and len(full_md_chunk) > len(md) * 1.25:
            body, path, fmt = full_md_chunk, "full_markdown_slice", "markdown"
        else:
            body, path, fmt = md, "node_slices_md", "markdown"
        return _prefer_html_when_md_lacks_figures(
            parse_cache, sid, body, path, fmt, sec,
        )

    if category and doc_id:
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import load_section_markdown_from_minio

            mdm = load_section_markdown_from_minio(category, doc_id, sid)
            if _substantive(str(mdm or "")):
                return str(mdm).strip(), "minio_md", "markdown"
        except Exception as exc:
            logger.debug("[method-extract] minio md sid=%s: %s", sid, exc)

    if _substantive(full_md_chunk):
        return full_md_chunk, "full_markdown_slice", "markdown"

    html_slices = parse_cache.get("node_slices") or {}
    html = str(html_slices.get(sid) or "").strip()
    if _substantive(html):
        return html, "node_slices", "html"

    html_sec = str(sec.get("html") or "").strip()
    if _substantive(html_sec):
        return html_sec, "section_html", "html"

    text_sec = str(sec.get("text") or "").strip()
    if _substantive(text_sec):
        return text_sec, "section_text", "plain"

    return "", "", ""


def _fetch_section_offset_span(
    resolved: dict,
    sections: List[dict],
    full_text: str,
) -> str:
    """按 start_offset 在 full_text 上切整段正文（父节兜底）。"""
    if not full_text or not resolved or resolved.get("start_offset") is None:
        return ""
    title = str(resolved.get("title") or "").strip()
    try:
        from blueprints.ZSGL.WJGL1.document_structure import _slice_body_between_offsets

        start_i = _span_start_for_decimal_section(resolved, sections, full_text=full_text)
        end_i = _compute_section_span_end_offset(
            resolved, sections, full_text_len=len(full_text), full_text=full_text,
        )
        if end_i <= start_i:
            end_i = len(full_text)
        chunk = _slice_body_between_offsets(full_text, start_i, end_i, title)
        if not chunk:
            chunk = full_text[start_i:end_i].strip()
        anchor_no = _normalize_section_no(resolved.get("section_no") or "")
        if anchor_no:
            chunk = _trim_leading_before_formal_heading(chunk, anchor_no)
        return chunk.strip() if chunk else ""
    except Exception as exc:
        logger.debug("[method-extract] offset span failed sid=%s: %s", resolved.get("section_id"), exc)
        return ""


def _fetch_one_explicit_section_content(
    item: dict,
    *,
    parse_cache: Optional[dict],
    category: str,
    doc_id: str,
    temp_path: str,
    ext: str,
    all_tables: list,
    all_figures: list,
    tmp_dir: str,
    pages: Optional[list],
    full_text: str,
) -> Tuple[str, Dict[str, Any]]:
    """抓取单个勾选章节：section_id → node_slices_md / node_slices，与解析目录同源。"""
    full_text = _resolve_extract_full_text(parse_cache, full_text)
    sno = _normalize_section_no(item.get("sectionNo"))
    title = str(item.get("title") or "").strip()
    sid = str(item.get("section_id") or "").strip()
    sections = list((parse_cache or {}).get("sections") or [])
    slices = (parse_cache or {}).get("node_slices") or {}
    md_slices = (parse_cache or {}).get("node_slices_md") or {}
    trace: Dict[str, Any] = {
        "section_id": sid,
        "title": title,
        "sectionNo": sno,
        "fetch_path": "",
        "content_format": "",
        "has_start_offset": False,
        "section_text_len": 0,
        "node_slice_len": 0,
        "node_slices_md_len": 0,
        "chunk_len": 0,
        "html_title_only": False,
        "expected_span": "",
        "expected_span_len": 0,
        "source_hint": "node_slices_md → minio_md → full_markdown_slice → node_slices → section_html",
    }

    resolved = _resolve_parse_cache_section(
        parse_cache,
        section_no=sno,
        title=title,
        section_id=sid,
        full_text=full_text,
    )
    if resolved:
        sid = str(resolved.get("section_id") or sid).strip()
        trace["section_id"] = sid
        trace["has_start_offset"] = resolved.get("start_offset") is not None
        trace["section_text_len"] = len(str(resolved.get("text") or ""))
        trace["node_slice_len"] = len(str(slices.get(sid) or ""))
        trace["node_slices_md_len"] = len(str(md_slices.get(sid) or ""))
        trace["expected_span"] = _compute_expected_span_plain(resolved, sections, full_text)
        trace["expected_span_len"] = len(trace["expected_span"])

    content, fetch_path, content_fmt = _fetch_section_body_direct(
        parse_cache,
        sid,
        category=category,
        doc_id=doc_id,
        resolved=resolved,
    )

    exp_len = len(str((trace.get("expected_span") or ""))) if resolved else 0
    md_len = int(trace.get("node_slices_md_len") or 0)
    if (
        content.strip()
        and resolved
        and exp_len > len(content) * 1.5
        and md_len <= len(content) * 1.1
        and fetch_path in ("node_slices_md", "node_slices", "section_text", "section_html", "plain")
    ):
        path_tags: List[str] = []
        fallback = _section_html_from_parse_cache(
            parse_cache,
            section_id=sid,
            resolved=resolved,
            full_text=full_text,
            path_out=path_tags,
        )
        if fallback.strip() and len(fallback) > len(content) * 1.2:
            content = fallback.strip()
            fetch_path = path_tags[0] if path_tags else "offset_span_fallback"
            content_fmt = "html" if re.search(r"<\s*(p|div|table|h[1-6])\b", content, re.I) else "plain"

    # 仅当 parse 切片全缺失时，才用 offset 整段（旧文档 / 无 node_slices_md）
    if not content.strip() and resolved and parse_cache:
        path_tags: List[str] = []
        fallback = _section_html_from_parse_cache(
            parse_cache,
            section_id=sid,
            resolved=resolved,
            full_text=full_text,
            path_out=path_tags,
        )
        if fallback.strip():
            content = fallback.strip()
            fetch_path = path_tags[0] if path_tags else "offset_span_fallback"
            content_fmt = "html" if re.search(r"<\s*(p|div|table|h[1-6])\b", content, re.I) else "plain"

    if content.strip():
        trace["fetch_path"] = fetch_path or "direct"
        trace["content_format"] = content_fmt
        trace["chunk_len"] = len(content)
        trace["html_title_only"] = _html_looks_title_only(content, str((resolved or {}).get("title") or title))
        if trace["node_slices_md_len"] > 0 and trace["chunk_len"] < trace["node_slices_md_len"] * 0.5:
            trace["fetch_warning"] = "chunk 明显短于 node_slices_md，请检查 fetch_path"
        logger.info(
            "[method-extract] section_id=%s path=%s fmt=%s sno=%s title=%s chunk=%s md_slice=%s sec_text=%s",
            sid,
            trace["fetch_path"],
            content_fmt,
            sno,
            title[:40],
            trace["chunk_len"],
            trace["node_slices_md_len"],
            trace["section_text_len"],
        )
        return content.strip(), trace

    if category and doc_id and category != "_preview":
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import fetch_section_content_html

            html = fetch_section_content_html(
                category,
                doc_id,
                sno,
                section_title=title if not sno else "",
                include_children=False,
                sections=sections or None,
                node_slices=slices or None,
            )
            if html.strip():
                trace["fetch_path"] = "minio_html"
                trace["content_format"] = "html"
                trace["chunk_len"] = len(html)
                return html.strip(), trace
        except Exception as e:
            logger.debug("[method-extract] MinIO 单节读取失败 sno=%s: %s", sno, e)

    if ext in (".docx", ".doc") and temp_path and os.path.isfile(temp_path):
        chunk = _extract_section_from_docx(temp_path, sno, title)
        if chunk.strip():
            trace["fetch_path"] = "docx"
            trace["content_format"] = "plain"
            trace["chunk_len"] = len(chunk)
            return chunk.strip(), trace

    if temp_path and os.path.isfile(temp_path):
        chunk = _extract_section_from_pdf_with_tables_images(
            temp_path,
            sno,
            title,
            all_tables,
            all_figures,
            tmp_dir,
            pages=pages,
            full_text=full_text,
        )
        if chunk.strip():
            trace["fetch_path"] = "pdf"
            trace["content_format"] = "plain"
            trace["chunk_len"] = len(chunk)
            return chunk.strip(), trace

    trace["fetch_path"] = "empty"
    return "", trace


def _merge_explicit_selected_section_content(
    group: dict,
    *,
    parse_cache: Optional[dict],
    category: str,
    doc_id: str,
    temp_path: str,
    ext: str,
    all_tables: list,
    all_figures: list,
    tmp_dir: str,
    pages: Optional[list],
    full_text: str,
    debug_session_id: str = "",
) -> str:
    """按勾选顺序合并各节正文；父节已含子孙内容时不再重复拼接子节。"""
    items = _sort_explicit_section_items(
        _filter_items_covered_by_ancestor(
            _explicit_section_items_from_group(group),
            parse_cache,
            full_text=full_text,
        ),
        parse_cache,
    )
    if not items:
        return ""

    parts: List[str] = []
    chunk_results: List[dict] = []
    fetch_traces: List[dict] = []
    for item in items:
        chunk, trace = _fetch_one_explicit_section_content(
            item,
            parse_cache=parse_cache,
            category=category,
            doc_id=doc_id,
            temp_path=temp_path,
            ext=ext,
            all_tables=all_tables,
            all_figures=all_figures,
            tmp_dir=tmp_dir,
            pages=pages,
            full_text=full_text,
        )
        fetch_traces.append(trace)
        resolved = _resolve_parse_cache_section(
            parse_cache,
            section_no=str(item.get("sectionNo") or ""),
            title=str(item.get("title") or ""),
            section_id=str(item.get("section_id") or ""),
        )
        chunk_results.append({
            "section_id": (resolved or {}).get("section_id") or item.get("section_id"),
            "sectionNo": item.get("sectionNo"),
            "title": item.get("title"),
            "resolved_title": (resolved or {}).get("title"),
            "content_len": len(chunk or ""),
            "content": chunk,
            "fetch_path": trace.get("fetch_path"),
            "content_format": trace.get("content_format"),
        })
        if chunk.strip():
            parts.append(chunk.strip())

    merged = "\n\n".join(parts)

    if debug_session_id:
        try:
            from .section_extract_debug import save_section_extract_debug_bundle

            save_section_extract_debug_bundle(
                debug_session_id,
                group=group,
                resolved_items=items,
                chunk_results=chunk_results,
                fetch_traces=fetch_traces,
                merged_content=merged,
                stage="merge_sections",
            )
        except Exception as exc:
            logger.debug("[section-debug] 保存合并调试失败: %s", exc)

    if parts:
        logger.info(
            "[method-extract-sections] 勾选合并 sections=%d parts=%d nos=%s",
            len(items),
            len(parts),
            [_normalize_section_no(i.get("sectionNo")) or i.get("title") for i in items],
        )
        return "\n\n".join(parts)

    if category and doc_id:
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import fetch_section_content_html, parse_artifacts_ready

            if parse_artifacts_ready(category, doc_id):
                retry_parts: List[str] = []
                for item in items:
                    html = fetch_section_content_html(
                        category,
                        doc_id,
                        _normalize_section_no(item.get("sectionNo")),
                        section_title=str(item.get("title") or "") if not item.get("sectionNo") else "",
                        include_children=False,
                    )
                    if html.strip():
                        retry_parts.append(html.strip())
                if retry_parts:
                    return "\n".join(retry_parts)
        except Exception as e:
            logger.warning("[method-extract-sections] 单节重试失败: %s", e)
    return ""


def _extract_section_from_parse_cache(
    parse_cache: dict,
    section_no: str,
    section_title: str,
    category: str = "",
    doc_id: str = "",
    *,
    extra_section_nos: Optional[list] = None,
    section_nos: Optional[list] = None,
    section_id: str = "",
) -> str:
    """从 MinIO 已保存的章节切片读取 HTML（无 PDF 重解析）。"""
    if section_nos:
        fetch_roots = _min_covering_section_roots(section_nos)
    else:
        fetch_roots = [_normalize_section_no(section_no)] if _normalize_section_no(section_no) else []
        if extra_section_nos:
            for n in extra_section_nos:
                ns = _normalize_section_no(n)
                if ns and ns not in fetch_roots:
                    fetch_roots.append(ns)
        fetch_roots = _min_covering_section_roots(fetch_roots)

    parts: list[str] = []
    if category and doc_id:
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import fetch_section_content_html

            for pno in fetch_roots:
                html = fetch_section_content_html(
                    category,
                    doc_id,
                    pno,
                    section_title=section_title if pno == _normalize_section_no(section_no) else "",
                    include_children=True,
                    sections=parse_cache.get("sections") if parse_cache else None,
                    node_slices=parse_cache.get("node_slices") if parse_cache else None,
                )
                if html.strip():
                    parts.append(html.strip())
            if parts:
                logger.info(
                    "[method-extract] MinIO 章节合并 sections=%s roots=%s parts=%s",
                    len(section_nos or fetch_roots),
                    fetch_roots,
                    len(parts),
                )
                return "".join(parts)
        except Exception as e:
            logger.warning("[method-extract] MinIO 章节读取失败: %s", e)

    if not parse_cache:
        return ""
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import merge_section_html

        sections = parse_cache.get("sections") or []
        slices = parse_cache.get("node_slices") or {}
        target_ids: List[str] = []
        seen_ids: set = set()
        for sec in sections:
            sid = sec.get("section_id")
            if not sid:
                continue
            sno = _normalize_section_no(sec.get("section_no"))
            title = str(sec.get("title") or "")
            matched = False
            if section_id and sid == section_id:
                matched = True
            if fetch_roots:
                for root in fetch_roots:
                    if sno == root or sno.startswith(root + "."):
                        matched = True
                        break
            elif section_no:
                pno = _normalize_section_no(section_no)
                if sno == pno or sno.startswith(pno + "."):
                    matched = True
            elif section_title and (section_title in title or title in section_title):
                matched = True
            if matched and sid not in seen_ids:
                seen_ids.add(sid)
                target_ids.append(sid)
        if not target_ids:
            return ""
        html = merge_section_html(target_ids, sections, slices)
        return html.strip()
    except Exception as e:
        logger.warning("[method-extract] parse_cache 章节合并失败: %s", e)
        return ""


def _method_display_title(title: str) -> str:
    """方法名：去掉原文章节编号。"""
    t = str(title or "").strip()
    t = re.sub(r"^\d+(?:\.\d+)*[\s)）.、\-]*", "", t)
    t = re.sub(r"^第[一二三四五六七八九十百零0-9]+[章节部分篇]\s*", "", t)
    return t.strip() or str(title or "").strip()


def _format_single_section_method(
    *,
    temp_path: str,
    ext: str,
    group: dict,
    all_tables: list,
    all_figures: list,
    tmp_dir: str,
    full_text: str,
    draft_method_id: str,
    source_file_id: str,
    source_name: str,
    user_id: str,
    minio_client,
    bucket_name: str,
    global_method_index: int,
    use_llm_merge: bool = False,
    std_name: str = "",
    std_code: str = "",
    pages: Optional[list] = None,
    parse_cache: Optional[dict] = None,
    category: str = "",
    doc_id: str = "",
    debug_session_id: str = "",
) -> List[dict]:
    """从单个 parent/children 分组构建 1~N 个方法条目（LLM 可语义拆分）。"""
    from .method_section_enrich import (
        build_equipment_scoped_recommendations,
        build_method_blocks_from_parse_sections,
        enrich_section_method,
        finalize_section_extract_markdown,
        finalize_section_extract_synr,
        format_method_blocks_rule_based,
        generate_method_description_llm,
        is_section_extract_source,
        label_from_source_filename,
        llm_structure_section_methods,
        prepare_section_content_html,
        _blocks_have_core_text,
    )

    parent = group.get('parent', {})
    parent_no = str(parent.get('sectionNo') or '').strip()
    parent_title = str(parent.get('title') or '').strip()
    parent_section_id = str(parent.get('section_id') or parent.get('sectionId') or '').strip()
    display_title = _method_display_title(parent_title)
    if not parent_title and not display_title:
        return []

    section_nos = _section_nos_from_group(group)
    section_items = _explicit_section_items_from_group(group)
    source_section_ids = [
        str(x.get("section_id") or "").strip()
        for x in section_items
        if str(x.get("section_id") or "").strip()
    ]
    normalized_source_file_id = _normalize_wjgl_file_id(source_file_id)
    file_source_relation = (
        {
            'target_type': 'File',
            'target_id': normalized_source_file_id,
            'node_name': source_name,
            'rel_type': 'BELONGS_TO',
            'score': 1.0,
        }
        if normalized_source_file_id
        else None
    )
    method_content = _merge_explicit_selected_section_content(
        group,
        parse_cache=parse_cache,
        category=category,
        doc_id=doc_id,
        temp_path=temp_path,
        ext=ext,
        all_tables=all_tables,
        all_figures=all_figures,
        tmp_dir=tmp_dir,
        pages=pages,
        full_text=full_text,
        debug_session_id=debug_session_id,
    )

    if not method_content.strip():
        logger.warning(
            "[method-extract-sections] 章节 '%s' 内容为空，跳过 (selected=%s, section_id=%s)",
            parent_title,
            section_nos,
            parent_section_id,
        )
        return []

    gallery_html = ''
    if global_method_index == 0 and not parse_cache and all_figures:
        gallery_html = _ocr_figure_gallery_html_from_parsed_doc_for_sections(
            all_figures, draft_method_id, user_id, minio_client, bucket_name,
        )

    method_label = label_from_source_filename(source_name)
    structured_methods: List[Dict[str, Any]] = []

    if use_llm_merge:
        prepared = prepare_section_content_html(
            method_content,
            full_text=full_text,
            all_tables=all_tables,
            category=category,
            doc_id=doc_id,
            section_extract_mode=True,
        )
        structured = llm_structure_section_methods(
            prepared["content_html"],
            display_title or parent_title,
            tables_html=prepared["tables_html"],
            figures_html=gallery_html,
        )
        for sm in structured:
            blocks = sm.get("blocks") or {}
            short_name = str(sm.get("name") or display_title or parent_title).strip()
            if len(structured) == 1:
                method_name = f"{display_title or parent_title}-试验方法"
            elif short_name and short_name not in (parent_title, display_title):
                method_name = f"{display_title or parent_title}_{short_name}-试验方法"
            else:
                method_name = f"{short_name}-试验方法"
            plain = re.sub(r"<[^>]+>", "\n", _method_parts_to_synr(blocks, ""))
            rec_nodes, rec_rels, eq_recs = build_equipment_scoped_recommendations(
                f"{method_name}\n{plain[:3000]}",
                neo4j_driver=_get_neo4j_driver_for_recall(),
                std_name=std_name or source_name,
                std_code=std_code,
            )
            synr_html = _method_parts_to_synr(
                blocks,
                gallery_html if global_method_index == 0 and not structured_methods else '',
            )
            synr_html = finalize_section_extract_synr(synr_html)
            structured_methods.append({
                "name": method_name,
                "blocks": blocks,
                "synr_html": synr_html,
                "extract_source": "sections_llm_structure",
                "recommended_nodes": rec_nodes,
                "recommended_relations": rec_rels,
                "equipment_recommendations": eq_recs,
            })
    else:
        blocks_from_sections = None
        if isinstance(parse_cache, dict) and parse_cache.get("sections"):
            blocks_from_sections = build_method_blocks_from_parse_sections(parse_cache, group)
        if blocks_from_sections and _blocks_have_core_text(blocks_from_sections):
            blocks = format_method_blocks_rule_based(blocks_from_sections)
            synr_html = _method_parts_to_synr(
                blocks,
                gallery_html if global_method_index == 0 else '',
            )
            synr_html = finalize_section_extract_synr(synr_html)
            rec_nodes, rec_rels, eq_recs = build_equipment_scoped_recommendations(
                f"{display_title or parent_title}-试验方法\n{re.sub(r'<[^>]+>', ' ', synr_html)[:3000]}",
                neo4j_driver=_get_neo4j_driver_for_recall(),
                std_name=std_name or source_name,
                std_code=std_code,
            )
            structured_methods.append({
                "name": f"{display_title or parent_title}-试验方法",
                "blocks": blocks,
                "synr_html": synr_html,
                "extract_source": "sections_whole_chapter",
                "recommended_nodes": rec_nodes,
                "recommended_relations": rec_rels,
                "equipment_recommendations": eq_recs,
            })
        else:
            enriched = enrich_section_method(
                method_content,
                f"{display_title or parent_title}-试验方法",
                use_llm_merge=False,
                full_text=full_text,
                all_tables=all_tables,
                all_figures=all_figures,
                std_name=std_name or source_name,
                std_code=std_code,
                neo4j_driver=_get_neo4j_driver_for_recall(),
                gallery_html=gallery_html,
                category=category,
                doc_id=doc_id,
                section_extract_mode=True,
            )
            blocks = enriched.get("blocks") or {}
            prepared_html = str(enriched.get("content_html") or method_content or "")
            synr_html = str(enriched.get("formatted_single") or "").strip()
            if not synr_html:
                from .method_section_enrich import format_selected_content_rule_based

                synr_html = format_selected_content_rule_based(prepared_html or method_content)
            elif gallery_html and global_method_index == 0:
                synr_html = f'{gallery_html}{synr_html}'.strip()
            structured_methods.append({
                "name": f"{display_title or parent_title}-试验方法",
                "blocks": blocks,
                "synr_html": synr_html,
                "prepared_html": prepared_html,
                "extract_source": enriched.get("extract_source") or "sections_selected",
                "recommended_nodes": enriched.get("recommended_nodes") or [],
                "recommended_relations": enriched.get("recommended_relations") or [],
                "equipment_recommendations": enriched.get("equipment_recommendations") or [],
            })

    entries: List[dict] = []
    for mi, sm in enumerate(structured_methods):
        blocks = sm.get("blocks") or {}
        method_name = str(sm.get("name") or f"{display_title or parent_title}-试验方法")
        synr_html = str(sm.get("synr_html") or "").strip()
        if not synr_html:
            synr_html = method_content
        extract_source = str(sm.get("extract_source") or "sections")
        if not is_section_extract_source(extract_source):
            from .method_section_enrich import ensure_method_synr_html

            synr_html = ensure_method_synr_html(synr_html)
        else:
            from .method_section_enrich import apply_section_extract_editor_synr

            synr_html = apply_section_extract_editor_synr(
                synr_html,
                full_text=full_text,
                all_tables=all_tables,
                all_figures=all_figures,
                category=category,
                doc_id=doc_id,
                document_html=str((parse_cache or {}).get("full_html") or ""),
            )
        method_description = generate_method_description_llm(
            method_name,
            content_html=synr_html or method_content,
            source_name=source_name,
            std_code=std_code,
        )
        is_first = global_method_index == 0 and mi == 0
        entries.append({
            'id': draft_method_id if is_first else '',
            'name': method_name,
            'type': '分则',
            'class': '分则',
            'label': method_label,
            'description': method_description,
            'sy': '',
            'synr': synr_html,
            'sytj': '',
            'sygc': '',
            'jgpd': '',
            'source_name': source_name,
            'source_id': normalized_source_file_id or source_file_id,
            'source_section_ids': source_section_ids,
            'source_section_nos': section_nos,
            'source_category': category,
            'source_doc_id': doc_id,
            'preview_cache_key': doc_id if category == '_preview' else '',
            'recommended_relations': (
                ([file_source_relation] if file_source_relation else [])
                + list(sm.get('recommended_relations') or [])
            ),
            'recommended_nodes': sm.get('recommended_nodes') or [],
            'equipment_recommendations': sm.get('equipment_recommendations') or [],
            'name_duplicate': {'duplicate': False, 'existing': None},
            'extract_source': sm.get('extract_source') or 'sections',
        })

    if debug_session_id and entries:
        try:
            from .section_extract_debug import save_section_extract_debug_bundle

            first = entries[0]
            sm0 = structured_methods[0] if structured_methods else {}
            save_section_extract_debug_bundle(
                debug_session_id,
                group=group,
                merged_content=method_content,
                prepared_html=str(sm0.get("prepared_html") or ""),
                formatted_synr=str(first.get("synr") or ""),
                final_method={
                    "name": first.get("name"),
                    "synr_len": len(str(first.get("synr") or "")),
                    "merged_len": len(method_content or ""),
                },
                stage="format_method",
            )
        except Exception as exc:
            logger.debug("[section-debug] 保存格式化调试失败: %s", exc)

    return entries


@flgl_method_bp.route('/method-extract-sections', methods=['POST'])
def method_extract_sections():
    """
    按用户勾选的章节提取试验方法。
    接收: { source_file_id, sections: [{sectionNo, title}], user_id, draft_method_id }
    返回: 与 method-extract 一致格式的方法列表
    """
    data = request.json or {}
    logger.info(f"[method-extract-sections] 收到请求: keys={list(data.keys())}, source_file_id={data.get('source_file_id')}, object_name={data.get('object_name')}, sections_count={len(data.get('sections', []))}")
    source_file_id = (data.get('source_file_id') or '').strip()
    object_name = (data.get('object_name') or '').strip()
    preview_cache_key = (data.get('preview_cache_key') or '').strip()
    sections = data.get('sections', [])
    user_id = (data.get('user_id') or 'admin').strip()
    draft_method_id = (data.get('draft_method_id') or '').strip()
    use_llm_merge = bool(data.get('use_llm_merge') or data.get('llm_merge'))

    logger.info(f"[method-extract-sections] sections 数据: {sections[:3]}...")  # 打印前3个看结构

    if not source_file_id and not object_name:
        logger.warning(f"[method-extract-sections] source_file_id 和 object_name 都为空")
        return jsonify({"success": False, "error": "source_file_id 或 object_name 至少提供一个"}), 400
    if not sections:
        logger.warning(f"[method-extract-sections] sections 为空")
        return jsonify({"success": False, "error": "sections 不能为空"}), 400

    try:
        from .routes import _run_cypher, _get_node_props, _generate_id
        logger.info(f"[method-extract-sections] _run_cypher available: {_run_cypher is not None}")
    except Exception as e:
        logger.warning(f"[method-extract-sections] 导入 routes 失败: {e}")
        _run_cypher = None
        _get_node_props = None
        _generate_id = None

    minio_client, bucket_name = _get_minio_runtime()
    logger.info(f"[method-extract-sections] MinIO available: {minio_client is not None}, bucket: {bucket_name}")
    if not minio_client or not bucket_name:
        return jsonify({"success": False, "error": "MinIO 未初始化"}), 500

    try:
        file_node = None
        if _run_cypher:
            recs = _run_cypher(
                "MATCH (f:File {Id: $id}) RETURN f",
                {'id': source_file_id},
            )
            logger.info(f"[method-extract-sections] Neo4j 查询结果: {len(recs) if recs else 0} 条记录")
            if recs:
                file_node = _get_node_props(recs[0].get('f', {}))
                logger.info(f"[method-extract-sections] file_node: {file_node.get('name') if file_node else None}")

        if not file_node:
            logger.warning(f"[method-extract-sections] 按 ID 未找到节点，尝试按 MinIO 路径查找: source_file_id={source_file_id}")
            minio_path_search = source_file_id.replace('WJ_', '')
            recs2 = _run_cypher(
                "MATCH (f:File) WHERE f.minIo_id CONTAINS $path OR f.Name CONTAINS $name RETURN f LIMIT 1",
                {'path': minio_path_search, 'name': minio_path_search},
            )
            if recs2:
                file_node = _get_node_props(recs2[0].get('f', {}))
                logger.info(f"[method-extract-sections] 按路径找到节点: {file_node.get('id')}")

        if not file_node and object_name:
            logger.info(f"[method-extract-sections] 使用 object_name 直接下载: {object_name}")
            file_node = {
                'id': '',
                'name': os.path.basename(object_name),
                'minio_id': object_name,
                'minIo_id': object_name,
            }

        if not file_node:
            logger.warning(f"[method-extract-sections] 文件节点不存在: source_file_id={source_file_id}, object_name={object_name}")
            all_files = _run_cypher("MATCH (f:File) RETURN f.Id, f.Name, f.minIo_id LIMIT 10", {})
            logger.info(f"[method-extract-sections] 前10个文件节点: {[(r.get('f.Id'), r.get('f.Name')) for r in (all_files or [])]}")
            return jsonify({"success": False, "error": f"文件不存在"}), 404

        minio_path = file_node.get('minio_id') or file_node.get('minIo_id', '')
        if not minio_path:
            return jsonify({"success": False, "error": "文件 MinIO 路径不存在"}), 404

        import tempfile as _tf
        tmp_dir = _tf.mkdtemp()
        wjgl_category, wjgl_doc_id = _parse_wjgl_category_doc_id(minio_path, file_node)
        temp_path = ""
        ext = os.path.splitext(minio_path)[1].lower()

        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import parse_artifacts_ready

            section_store_ready = bool(wjgl_category and wjgl_doc_id and parse_artifacts_ready(wjgl_category, wjgl_doc_id))
        except Exception:
            section_store_ready = False

        temp_path = os.path.join(tmp_dir, os.path.basename(minio_path))
        try:
            minio_client.fget_object(bucket_name, minio_path, temp_path)
        except Exception as e:
            return jsonify({"success": False, "error": f"从 MinIO 下载文件失败: {e}"}), 500
        ext = os.path.splitext(temp_path)[1].lower()
        if section_store_ready:
            logger.info(
                "[method-extract-sections] MinIO 章节存储可用 category=%s doc_id=%s，已下载本地副本供 Word 回退",
                wjgl_category, wjgl_doc_id,
            )

        ctx = _load_method_extract_context(
            temp_path, ext, tmp_dir, minio_client, bucket_name,
            object_name=minio_path, file_node=file_node,
            preview_cache_key=preview_cache_key,
        )
        if preview_cache_key and not _preview_cache_has_content(ctx):
            ctx = _load_method_extract_context(
                temp_path, ext, tmp_dir, minio_client, bucket_name,
                object_name=minio_path, file_node=file_node,
                preview_cache_key="",
            )
            if _preview_cache_has_content(ctx):
                _save_preview_extract_cache(preview_cache_key, ctx)
        full_text = ctx.get("full_text") or ""
        all_tables = ctx.get("all_tables") or []
        all_figures = _merge_ctx_trace_figures(ctx)
        pages = ctx.get("pages") or []
        parse_cache = ctx.get("parse_cache")
        if isinstance(parse_cache, dict):
            parse_cache = _finalize_parse_cache_like_word(parse_cache)
            ctx["parse_cache"] = parse_cache
        wjgl_category = ctx.get("category") or wjgl_category
        wjgl_doc_id = ctx.get("doc_id") or wjgl_doc_id
        logger.info(
            "[method-extract-sections] context source=%s text_len=%s category=%s doc_id=%s md_slices=%s figures=%s",
            ctx.get("source"), len(full_text), wjgl_category, wjgl_doc_id,
            len((parse_cache or {}).get("node_slices_md") or {}),
            len(all_figures or []),
        )

        source_name = file_node.get('name') or file_node.get('Name', '') or os.path.basename(minio_path)
        std_name = str(file_node.get('std_name') or file_node.get('Std_name') or source_name)
        std_code = str(file_node.get('std_code') or file_node.get('Std_code') or '')

        if not draft_method_id:
            try:
                draft_method_id = _generate_id('TestMethod') if _generate_id else f'TM_{uuid.uuid4().hex[:12]}'
            except Exception:
                draft_method_id = f'TM_{uuid.uuid4().hex[:12]}'

        formatted_methods = []
        global_method_index = 0
        debug_session_id = preview_cache_key or source_file_id or draft_method_id or uuid.uuid4().hex
        expanded_groups: List[dict] = []
        for group in sections:
            expanded_groups.extend(
                _expand_peer_method_groups(group, parse_cache, full_text),
            )
        for group in expanded_groups:
            entries = _format_single_section_method(
                temp_path=temp_path,
                ext=ext,
                group=group,
                all_tables=all_tables,
                all_figures=all_figures,
                tmp_dir=tmp_dir,
                full_text=full_text,
                draft_method_id=draft_method_id,
                source_file_id=source_file_id,
                source_name=source_name,
                user_id=user_id,
                minio_client=minio_client,
                bucket_name=bucket_name,
                global_method_index=global_method_index,
                use_llm_merge=use_llm_merge,
                std_name=std_name,
                std_code=std_code,
                pages=pages,
                parse_cache=parse_cache,
                category=wjgl_category,
                doc_id=wjgl_doc_id,
                debug_session_id=debug_session_id,
            )
            formatted_methods.extend(entries)
            global_method_index += len(entries)

        debug_dir = ""
        try:
            from .section_extract_debug import (
                debug_session_dir,
                save_word_parse_debug_bundle,
                section_extract_debug_enabled,
            )

            if section_extract_debug_enabled():
                save_word_parse_debug_bundle(
                    debug_session_id,
                    source_path=temp_path,
                    ctx={
                        "full_text": full_text,
                        "parse_cache": parse_cache,
                        "source": ctx.get("source"),
                    },
                    stage="method_extract_sections",
                    extra={"sections_request": sections, "methods_count": len(formatted_methods)},
                )
                from .section_extract_debug import save_extract_pipeline_step

                save_extract_pipeline_step(
                    debug_session_id,
                    "99_extract_result.json",
                    {
                        "methods_count": len(formatted_methods),
                        "methods": [
                            {"name": m.get("name"), "source_section_ids": m.get("source_section_ids")}
                            for m in formatted_methods
                        ],
                    },
                    as_json=True,
                )
                debug_dir = debug_session_dir(debug_session_id)
        except Exception:
            pass

        result_data = {
            'draft_method_id': draft_method_id,
            'methods_count': len(formatted_methods),
            'methods': formatted_methods,
            'all_methods': [
                {'name': m['name'], 'type': m['type'], 'summary': m['description']}
                for m in formatted_methods
            ],
            'recommended_nodes': formatted_methods[0].get('recommended_nodes', []) if formatted_methods else [],
            'equipment_recommendations': formatted_methods[0].get('equipment_recommendations', []) if formatted_methods else [],
            'extract_source': 'sections_llm' if use_llm_merge else 'sections',
            'use_llm_merge': use_llm_merge,
            'debug_dir': debug_dir,
            'debug_session_id': debug_session_id,
        }

        log_user_action(f"分类管理-按章节提取方法 file={source_name[:120]} sections={len(sections)} methods={len(formatted_methods)}")
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logger.error(f"按章节提取方法失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": f"提取失败: {str(e)}"}), 500


def _classify_sections(sections: list) -> dict:
    """
    自动识别试验项目和考核指标章节。
    
    规则：
    - 试验项目：标题含"试验"、"测试"、"检验"等，但排除"试验方法"、"试验要求"、"试验大纲"等
    - 考核指标：标题含"指标"，或父章节标题含"考核指标"、"指标"
    
    返回：{'test_items': [...], 'indicators': [...]}
    """
    result = {'test_items': [], 'indicators': []}
    
    # 构建章节号到标题的映射，用于查找父章节
    section_map = {}
    for sec in sections:
        sec_no = str(sec.get('sectionNo') or '').strip()
        sec_title = str(sec.get('title') or '').strip()
        if sec_no:
            section_map[sec_no] = sec_title
    
    # 试验项目关键词（包含）
    test_item_keywords = ['试验', '测试', '检验', '检测', '实验']
    # 试验项目排除词（如果标题包含这些，则不是试验项目）
    test_item_exclude = ['试验方法', '试验要求', '试验大纲', '试验方案', '试验规程', '试验条件']
    # 考核指标关键词
    indicator_keywords = ['指标', '考核', '评价', '评估']
    
    for sec in sections:
        sec_no = str(sec.get('sectionNo') or '').strip()
        sec_title = str(sec.get('title') or '').strip()
        sec_level = int(sec.get('level') or 1)
        
        if not sec_title:
            continue
        
        # 查找父章节标题
        parent_title = ''
        if sec_no:
            parts = sec_no.split('.')
            if len(parts) > 1:
                parent_no = '.'.join(parts[:-1])
                parent_title = section_map.get(parent_no, '')
        
        # 判断是否为考核指标
        is_indicator = False
        for kw in indicator_keywords:
            if kw in sec_title or kw in parent_title:
                is_indicator = True
                break
        
        if is_indicator:
            result['indicators'].append({
                'sectionNo': sec_no,
                'title': sec_title,
                'level': sec_level,
                'category': '考核指标',
            })
            continue
        
        # 判断是否为试验项目
        is_test_item = False
        for kw in test_item_keywords:
            if kw in sec_title:
                is_test_item = True
                break
        
        if is_test_item:
            # 检查是否应排除
            should_exclude = False
            for excl in test_item_exclude:
                if excl in sec_title:
                    should_exclude = True
                    break
            
            if not should_exclude:
                result['test_items'].append({
                    'sectionNo': sec_no,
                    'title': sec_title,
                    'level': sec_level,
                    'category': '试验项目',
                })
    
    logger.info(f"[_classify_sections] 输入 {len(sections)} 个章节，识别试验项目 {len(result['test_items'])} 个，考核指标 {len(result['indicators'])} 个")
    
    # 如果没有识别到任何章节，则回退到所有非排除章节
    if not result['test_items'] and not result['indicators']:
        logger.warning(f"[_classify_sections] 未识别到试验项目或考核指标，回退到所有章节")
        for sec in sections:
            sec_no = str(sec.get('sectionNo') or '').strip()
            sec_title = str(sec.get('title') or '').strip()
            sec_level = int(sec.get('level') or 1)
            if sec_title:
                result['test_items'].append({
                    'sectionNo': sec_no,
                    'title': sec_title,
                    'level': sec_level,
                    'category': '试验项目',
                })
    
    return result


def _normalize_fullwidth(text: str) -> str:
    """将全角字符转为半角（数字、点、空白）"""
    result = []
    for c in text:
        cp = ord(c)
        if 0xFF10 <= cp <= 0xFF19:
            result.append(chr(cp - 0xFEE0))
        elif cp == 0xFF0E:
            result.append('.')
        elif cp == 0x3000:
            result.append(' ')
        else:
            result.append(c)
    return ''.join(result)


def _normalize_ocr_text(text: str) -> str:
    """去除 OCR 文本中汉字之间的多余空格，并转换全角字符"""
    text = _normalize_fullwidth(text)
    text = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)
    return text.strip()


_SECTION_RE = re.compile(r'^(\d+(?:\.\d+)*|\.\d+)\s+(.+)$')


def _extract_section_from_pdf_with_tables_images(
    pdf_path: str,
    section_no: str,
    section_title: str,
    all_tables: list,
    all_figures: list,
    output_dir: str,
    pages: Optional[list] = None,
    full_text: str = "",
) -> str:
    """
    从 PDF 提取指定章节内容，包含表格 HTML 和图片。
    优先使用已抽取的 pages / full_text，避免重复 OCR。
    """
    import base64

    if not pages:
        if full_text.strip():
            content = _extract_section_content(full_text, section_no, section_title)
            if content.strip():
                matched_tables = []
                for tbl in all_tables or []:
                    html = str(tbl.get("html") or "").strip()
                    if not html:
                        continue
                    tno = str(tbl.get("table_no") or "")
                    if tno and f"表{tno.lstrip('表')}" in content:
                        matched_tables.append(html)
                    elif html[:80] in content:
                        matched_tables.append(html)
                if matched_tables:
                    content += '\n\n<h4>表格</h4>\n' + '\n'.join(matched_tables)
                return content
        try:
            import fitz
            doc = fitz.open(pdf_path)
            pages = [{"text": p.get_text() or "", "tables": [], "figures": []} for p in doc]
            doc.close()
        except Exception as e:
            logger.warning("[_extract_section_from_pdf] fitz 快速抽取失败: %s", e)
            return _extract_section_content(full_text, section_no, section_title)

    section_text = []
    section_tables_html = []
    section_images_html = []
    in_section = False

    for page in pages or []:
        text = _normalize_ocr_text(page.get("text", ""))
        tables = page.get("tables", [])
        figures = page.get("figures", [])

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue

            if section_no:
                if line.startswith(section_no) or line.startswith('.' + section_no):
                    in_section = True
                    continue
                elif in_section and _SECTION_RE.match(line):
                    m2 = _SECTION_RE.match(line)
                    if m2:
                        sub_title = m2.group(2).strip()
                        if len(sub_title) <= 15:
                            break
            else:
                if line == section_title or line.startswith(section_title):
                    in_section = True
                    continue
                elif in_section:
                    cleaned = line.replace(' ', '')
                    if 2 <= len(cleaned) <= 10 and re.match(r'^[\u4e00-\u9fff]+$', cleaned):
                        break

            if in_section:
                section_text.append(line)

        if in_section:
            for table in tables:
                html = table.get("html", "")
                if html:
                    section_tables_html.append(html)

            for figure in figures:
                img_path = figure.get("path", "")
                if img_path and os.path.exists(img_path):
                    try:
                        with open(img_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode()
                        ext = os.path.splitext(img_path)[1].lower()
                        mime = "image/png" if ext == ".png" else "image/jpeg"
                        section_images_html.append(
                            f'<img src="data:{mime};base64,{img_data}" style="max-width:100%"/>'
                        )
                    except Exception as e:
                        logger.warning("[_extract_section_from_pdf] 图片读取失败: %s", e)

    if not section_text and full_text.strip():
        return _extract_section_content(full_text, section_no, section_title)

    content = '\n'.join(section_text)
    if section_tables_html:
        content += '\n\n<h4>表格</h4>\n' + '\n'.join(section_tables_html)
    elif all_tables and content.strip():
        extras = []
        for tbl in all_tables:
            html = str(tbl.get("html") or "").strip()
            if html:
                extras.append(html)
        if extras:
            content += '\n\n<h4>表格</h4>\n' + '\n'.join(extras[:5])
    if section_images_html:
        content += '\n\n<h4>图片</h4>\n' + '\n'.join(section_images_html)

    logger.info(
        "[_extract_section_from_pdf] sectionNo='%s' title='%s' len=%s tables=%s images=%s",
        section_no, section_title, len(content), len(section_tables_html), len(section_images_html),
    )
    return content


def _extract_section_content(full_text: str, section_no: str, section_title: str) -> str:
    """
    从全文中提取指定章节的内容。
    策略：找到章节标题，提取到下一个同级或上级章节标题之前的所有内容。
    """
    if not full_text:
        return ''

    search_patterns = []
    if section_no:
        search_patterns.append(re.compile(
            rf'(?:^|\n)\s*{re.escape(section_no)}\s*[)）.、\s]+{re.escape(section_title)}',
            re.MULTILINE,
        ))
        search_patterns.append(re.compile(
            rf'(?:^|\n)\s*{re.escape(section_no)}\s*{re.escape(section_title)}',
            re.MULTILINE,
        ))
    if section_title:
        search_patterns.append(re.compile(
            rf'(?:^|\n)\s*{re.escape(section_title)}\s*$',
            re.MULTILINE,
        ))

    start_pos = -1
    for pat in search_patterns:
        m = pat.search(full_text)
        if m:
            start_pos = m.start()
            break

    if start_pos < 0:
        return ''

    heading_re = re.compile(r'(?:^|\n)\s*(\d+(?:\.\d+){0,5})\s*[)）.、\s]')
    lines_after = full_text[start_pos:]
    first_newline = lines_after.find('\n')
    if first_newline < 0:
        return lines_after.strip()

    body_start = first_newline + 1
    rest = full_text[start_pos + body_start:]
    end_pos = len(rest)

    for m in heading_re.finditer(rest):
        found_num = m.group(1)
        if section_no:
            sec_parts = section_no.split('.')
            found_parts = found_num.split('.')
            if len(found_parts) <= len(sec_parts) and found_parts != sec_parts[:len(found_parts)]:
                end_pos = m.start()
                break
        else:
            if len(found_num.split('.')) <= 2:
                end_pos = m.start()
                break

    content = rest[:end_pos].strip()
    return content


def _parse_section_number(text: str) -> tuple:
    """从标题文本中解析章节号和显示标题。"""
    m = re.match(r'^(\d+(?:\.\d+){0,4})\s*[)）.、\s]+(.+)', text)
    if m:
        return m.group(1), m.group(2).strip() or text
    m = re.match(r'^(第[一二三四五六七八九十百\d]+[章节部分篇])\s*(.*)', text)
    if m:
        return m.group(1), m.group(2).strip() or text
    return '', text


def _extract_section_from_docx(docx_path: str, section_no: str, section_title: str) -> str:
    """
    直接从 docx 文件提取章节内容，包含段落文本、表格 HTML 和图片 base64。
    """
    try:
        import docx
        from docx.oxml.ns import qn
        import base64
        doc = docx.Document(docx_path)
    except Exception as e:
        logger.warning(f"[_extract_section_from_docx] 无法打开文档: {e}")
        return ''

    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    # Step 1: 使用 doc.paragraphs 构建标题索引（与 TOC 提取一致）
    heading_paras = []  # [(para_idx, level, sec_no, title), ...]
    for i, para in enumerate(doc.paragraphs):
        style = (para.style.name or '').lower() if para.style else ''
        if 'heading' not in style:
            continue
        m = re.search(r'heading\s*(\d+)', style)
        level = int(m.group(1)) if m else 1
        text = para.text.strip()
        sec_no, title = _parse_section_number(text)
        if not title:
            title = text
        heading_paras.append((i, level, sec_no, title))

    if not heading_paras:
        logger.warning(f"[_extract_section_from_docx] 未找到任何标题")
        return ''

    logger.info(f"[_extract_section_from_docx] 找到 {len(heading_paras)} 个标题，查找: sectionNo='{section_no}' title='{section_title}'")

    # Step 2: 查找目标标题
    target_para_idx = -1
    target_level = -1
    for i, level, sno, stitle in heading_paras:
        if _section_matches(sno, stitle, section_no, section_title):
            target_para_idx = i
            target_level = level
            logger.info(f"[_extract_section_from_docx] 找到目标: H{level} para#{i} sno='{sno}' stitle='{stitle}'")
            break

    if target_para_idx < 0:
        logger.warning(f"[_extract_section_from_docx] 未找到目标标题: sectionNo='{section_no}' title='{section_title}'")
        return ''

    # Step 3: 查找结束段落（下一个同级或上级标题）
    end_para_idx = len(doc.paragraphs)
    for i, level, sno, stitle in heading_paras:
        if i <= target_para_idx:
            continue
        if level <= target_level:
            end_para_idx = i
            break

    # Step 4: 遍历 body 子元素，直接计数段落索引
    content_parts = []
    in_section = False
    para_idx = 0

    for element in doc.element.body:
        tag = element.tag.split('}')[-1] if '}' in element.tag else element.tag

        if tag == 'p':
            if para_idx < target_para_idx:
                para_idx += 1
                continue

            if para_idx >= end_para_idx:
                break

            if para_idx == target_para_idx:
                in_section = True
                para_idx += 1
                continue

            in_section = True

            # 检查是否是子标题
            is_sub_heading = False
            for i, level, sno, stitle in heading_paras:
                if i == para_idx:
                    if level > target_level:
                        content_parts.append(f'<h4>{stitle}</h4>')
                        is_sub_heading = True
                    break

            if not is_sub_heading:
                text = doc.paragraphs[para_idx].text.strip()
                if text:
                    content_parts.append(f'<p>{text}</p>')

            para_idx += 1

        elif tag == 'tbl':
            if not in_section:
                continue
            html = _table_element_to_html(element, ns)
            if html:
                content_parts.append(html)

        elif tag == 'p' and in_section:
            img_html = _extract_images_from_paragraph_element(element, doc)
            if img_html:
                content_parts.append(img_html)

    result = '\n'.join(content_parts)
    logger.info(f"[_extract_section_from_docx] 提取完成: sectionNo='{section_no}' title='{section_title}', 段落={len(content_parts)}, 长度={len(result)}")
    return result


def _get_element_text(element, ns: str) -> str:
    """从 XML 元素中提取文本。"""
    texts = element.findall(f'.//{{{ns}}}t')
    return ''.join(t.text or '' for t in texts).strip()


def _section_matches(sno: str, stitle: str, target_no: str, target_title: str) -> bool:
    """判断标题是否匹配目标章节。"""
    if sno and target_no and sno == target_no:
        return True
    if stitle and target_title and stitle == target_title:
        return True
    return False


def _table_element_to_html(tbl_element, ns: str) -> str:
    """将 Word 表格 XML 元素转换为 HTML table。"""
    rows = tbl_element.findall(f'{{{ns}}}tr')
    html_rows = []
    for row in rows:
        cells = row.findall(f'{{{ns}}}tc')
        html_cells = []
        for cell in cells:
            texts = cell.findall(f'.//{{{ns}}}t')
            cell_text = ''.join(t.text or '' for t in texts)
            html_cells.append(f'<td>{cell_text}</td>')
        html_rows.append(f'<tr>{"".join(html_cells)}</tr>')
    return f'<table border="1" style="border-collapse:collapse">{"".join(html_rows)}</table>'


def _extract_images_from_paragraph_element(p_element, doc) -> str:
    """从段落元素中提取图片引用 HTML。"""
    blips = p_element.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}blip')
    parts = []
    for blip in blips:
        embed_id = blip.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
        if embed_id and embed_id in doc.part.rels:
            rel = doc.part.rels[embed_id]
            if 'image' in rel.reltype:
                parts.append(f'<img src="data:image/png;base64,{_rel_to_base64(rel)}" style="max-width:100%"/>')
    return '\n'.join(parts) if parts else ''


def _rel_to_base64(rel) -> str:
    """将 rel 中的图片 blob 转为 base64。"""
    import base64
    try:
        return base64.b64encode(rel.target_part.blob).decode('utf-8')
    except Exception:
        return ''


def _ocr_figure_gallery_html_from_parsed_doc_for_sections(
    figures: list,
    draft_method_id: str,
    user_id: str,
    minio_client,
    bucket_name: str,
) -> str:
    """
    将提取结果中的图片上传到 MinIO「试验方法/{draft_method_id}/图片/」，
    返回 HTML 片段。
    """
    draft_method_id = (draft_method_id or "").strip()
    if not draft_method_id or not figures:
        return ""

    if not minio_client.bucket_exists(bucket_name):
        try:
            minio_client.make_bucket(bucket_name)
        except Exception:
            pass

    parts = []
    seen_local = set()

    try:
        from .routes import _run_cypher
    except Exception:
        _run_cypher = None

    for fig in figures:
        if not isinstance(fig, dict):
            continue
        local_path = str(fig.get("path") or "").strip()
        if not local_path or local_path in seen_local or not os.path.isfile(local_path):
            continue
        seen_local.add(local_path)

        base_fn = _safe_filename(fig.get("filename") or os.path.basename(local_path))
        base, ext = os.path.splitext(base_fn)
        ext = ext or ".png"
        object_name = f"试验方法/{draft_method_id}/图片/{base_fn}"
        try:
            minio_client.stat_object(bucket_name, object_name)
            object_name = f"试验方法/{draft_method_id}/图片/{base}_{uuid.uuid4().hex[:8]}{ext}"
        except Exception:
            pass

        content_type = mimetypes.guess_type(base_fn)[0] or "image/png"
        try:
            minio_client.fput_object(bucket_name, object_name, local_path, content_type=content_type)
        except Exception as e:
            logger.warning("OCR 附图上传 MinIO 失败 %s: %s", local_path, e)
            continue

        public_url = _method_image_url(object_name)
        alt = _html_escape(str(fig.get("description") or fig.get("ocr_text") or "附图")[:200])
        parts.append(
            f'<p><img src="{public_url}" alt="{alt}" style="max-width:100%;height:auto;" /></p>'
        )

        if _run_cypher:
            file_id = f"WJ_method_img_{uuid.uuid4().hex[:12]}"
            props = {
                "Id": file_id,
                "Type": "方法图片",
                "Label": "方法图片",
                "Name": base_fn,
                "Chroma_id": "",
                "minIo_id": object_name,
                "Image_true": True,
                "Image_minIO_path": object_name,
                "Status": "启用",
                "User_id": user_id,
                "Create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "File_type": ext.lstrip("."),
            }
            try:
                _run_cypher(
                    """
                    MERGE (f:File {Id: $Id})
                    SET f.Type = $Type, f.Label = $Label, f.Name = $Name,
                        f.Chroma_id = $Chroma_id, f.minIo_id = $minIo_id,
                        f.Image_true = $Image_true, f.Image_minIO_path = $Image_minIO_path,
                        f.Status = $Status, f.User_id = $User_id, f.Create_time = $Create_time,
                        f.File_type = $File_type
                    RETURN f
                    """,
                    props,
                )
            except Exception as fe:
                logger.debug("OCR 附图 File 节点写入跳过: %s", fe)

    if not parts:
        return ""
    return '<h4>文档附图（OCR）</h4>' + "".join(parts)


@flgl_method_bp.route('/method-extract-sections-file', methods=['POST'])
def method_extract_sections_file():
    """
    从上传的本地文件按勾选章节提取试验方法（文件尚未保存到 MinIO）。
    接收: multipart/form-data: file + sections(JSON) + user_id + draft_method_id
    返回: 与 method-extract 一致格式的方法列表
    """
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400

    uploaded_file = request.files['file']
    if not uploaded_file.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    sections_raw = (request.form.get('sections') or '[]')
    try:
        sections = json.loads(sections_raw)
    except Exception:
        return jsonify({"success": False, "error": "sections 格式错误"}), 400

    if not sections:
        return jsonify({"success": False, "error": "sections 不能为空"}), 400

    user_id = (request.form.get('user_id') or 'admin').strip()
    draft_method_id = (request.form.get('draft_method_id') or '').strip()
    preview_cache_key = (request.form.get('preview_cache_key') or '').strip()
    use_llm_merge = str(request.form.get('use_llm_merge') or request.form.get('llm_merge') or '').lower() in ('1', 'true', 'yes', 'on')
    source_name = uploaded_file.filename

    import tempfile as _tf
    import shutil

    tmp_dir = _tf.mkdtemp()
    orig_ext = os.path.splitext(uploaded_file.filename or '')[1].lower()
    temp_path = os.path.join(tmp_dir, _safe_filename(uploaded_file.filename))
    try:
        uploaded_file.save(temp_path)
        temp_path = _ensure_filepath_extension(temp_path, orig_ext)
        ext = os.path.splitext(temp_path)[1].lower()

        minio_client, bucket_name = _get_minio_runtime()

        ctx = _load_method_extract_context(
            temp_path, ext, tmp_dir, minio_client, bucket_name,
            preview_cache_key=preview_cache_key,
        )
        if preview_cache_key and not _preview_cache_has_content(ctx):
            ctx = _load_method_extract_context(
                temp_path, ext, tmp_dir, minio_client, bucket_name,
                preview_cache_key="",
            )
            if _preview_cache_has_content(ctx):
                _save_preview_extract_cache(preview_cache_key, ctx)
        full_text = ctx.get("full_text") or ""
        all_tables = ctx.get("all_tables") or []
        all_figures = _merge_ctx_trace_figures(ctx)
        pages = ctx.get("pages") or []
        parse_cache = ctx.get("parse_cache")
        if isinstance(parse_cache, dict):
            parse_cache = _finalize_parse_cache_like_word(parse_cache)
            ctx["parse_cache"] = parse_cache
        wjgl_category = ctx.get("category") or ""
        wjgl_doc_id = ctx.get("doc_id") or ""
        logger.info(
            "[method-extract-sections-file] context source=%s text_len=%s md_slices=%s figures=%s",
            ctx.get("source"),
            len(full_text),
            len((parse_cache or {}).get("node_slices_md") or {}),
            len(all_figures or []),
        )

        if not draft_method_id:
            try:
                from .routes import _generate_id
                draft_method_id = _generate_id('TestMethod')
            except Exception:
                draft_method_id = f'TM_{uuid.uuid4().hex[:12]}'

        minio_client, bucket_name = _get_minio_runtime()
        source_file_id = ''

        formatted_methods = []
        global_method_index = 0
        debug_session_id = preview_cache_key or draft_method_id or uuid.uuid4().hex
        expanded_groups: List[dict] = []
        for group in sections:
            expanded_groups.extend(
                _expand_peer_method_groups(group, parse_cache, full_text),
            )
        for group in expanded_groups:
            entries = _format_single_section_method(
                temp_path=temp_path,
                ext=ext,
                group=group,
                all_tables=all_tables,
                all_figures=all_figures,
                tmp_dir=tmp_dir,
                full_text=full_text,
                draft_method_id=draft_method_id,
                source_file_id=source_file_id,
                source_name=source_name,
                user_id=user_id,
                minio_client=minio_client,
                bucket_name=bucket_name,
                global_method_index=global_method_index,
                use_llm_merge=use_llm_merge,
                pages=pages,
                parse_cache=parse_cache,
                category=wjgl_category,
                doc_id=wjgl_doc_id,
                debug_session_id=debug_session_id,
            )
            formatted_methods.extend(entries)
            global_method_index += len(entries)

        debug_dir = ""
        try:
            from .section_extract_debug import (
                debug_session_dir,
                save_word_parse_debug_bundle,
                section_extract_debug_enabled,
            )

            if section_extract_debug_enabled():
                save_word_parse_debug_bundle(
                    debug_session_id,
                    source_path=temp_path,
                    ctx={
                        "full_text": full_text,
                        "parse_cache": parse_cache,
                        "source": ctx.get("source"),
                    },
                    stage="method_extract_sections_file",
                    extra={"sections_request": sections, "methods_count": len(formatted_methods)},
                )
                from .section_extract_debug import save_extract_pipeline_step

                save_extract_pipeline_step(
                    debug_session_id,
                    "99_extract_result.json",
                    {
                        "methods_count": len(formatted_methods),
                        "methods": [
                            {"name": m.get("name"), "source_section_ids": m.get("source_section_ids")}
                            for m in formatted_methods
                        ],
                    },
                    as_json=True,
                )
                debug_dir = debug_session_dir(debug_session_id)
        except Exception:
            pass

        result_data = {
            'draft_method_id': draft_method_id,
            'methods_count': len(formatted_methods),
            'methods': formatted_methods,
            'all_methods': [
                {'name': m['name'], 'type': m['type'], 'summary': m['description']}
                for m in formatted_methods
            ],
            'recommended_nodes': formatted_methods[0].get('recommended_nodes', []) if formatted_methods else [],
            'equipment_recommendations': formatted_methods[0].get('equipment_recommendations', []) if formatted_methods else [],
            'extract_source': 'sections_file_llm' if use_llm_merge else 'sections_file',
            'use_llm_merge': use_llm_merge,
            'debug_dir': debug_dir,
            'debug_session_id': debug_session_id,
        }

        log_user_action(f"分类管理-按章节提取方法(本地文件) file={source_name[:120]} sections={len(sections)} methods={len(formatted_methods)}")
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logger.error(f"按章节提取方法(本地文件)失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": f"提取失败: {str(e)}"}), 500
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 预览解析缓存 / 全文标题 / LLM 自动提取 facade
# ---------------------------------------------------------------------------

_HEADING_SECTION_NO_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
_OUTLINE_MARKER_RE = re.compile(r"试验项目[一二三四五六七八九十0-9]+[：:]", re.MULTILINE)
_OUTLINE_FIVE_SECTION_RE = re.compile(
    r"4\.\d+\.\d+\s*(?:试验目的|试验条件|试验方法|试验数据处理|结果评定)",
    re.MULTILINE,
)


def _preview_extract_cache_dir() -> str:
    _f = os.path.abspath(__file__)
    _backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(_f)))
    cache_dir = os.path.join(_backend_dir, "output_json", "flgl_preview_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _preview_extract_cache_path(key: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", str(key or "").strip())[:120]
    return os.path.join(_preview_extract_cache_dir(), f"{safe}.json")


def _slim_parse_cache_for_local_json(parse_cache: Optional[dict]) -> Optional[dict]:
    """MinIO 已存全文时，本地 JSON 只保留索引字段，避免与线上一致性冲突。"""
    if not isinstance(parse_cache, dict):
        return parse_cache
    if parse_cache.get("_storage") != "minio":
        return parse_cache
    slim = dict(parse_cache)
    for key in ("full_html", "raw_full_html", "full_markdown", "node_slices", "node_slices_md"):
        slim.pop(key, None)
    return slim


def _merge_preview_cache_from_minio(
    key: str,
    artifacts: dict,
    local_meta: Optional[dict],
) -> dict:
    local_meta = local_meta if isinstance(local_meta, dict) else {}
    sections = artifacts.get("sections") or []
    parse_cache = {
        "sections": sections,
        "node_slices": artifacts.get("node_slices") or {},
        "node_slices_md": artifacts.get("node_slices_md") or {},
        "full_html": artifacts.get("full_html") or "",
        "full_markdown": artifacts.get("full_markdown") or "",
        "raw_full_html": artifacts.get("full_html") or "",
        "_storage": "minio",
    }
    parse_cache = _finalize_parse_cache_like_word(parse_cache) or parse_cache
    full_text = str(local_meta.get("full_text") or artifacts.get("full_text") or "").strip()
    if not full_text and parse_cache.get("full_html"):
        full_text = re.sub(r"<[^>]+>", "\n", str(parse_cache.get("full_html") or "")).strip()
    return {
        **local_meta,
        "parse_cache": parse_cache,
        "category": "_preview",
        "doc_id": key,
        "storage": "minio",
        "full_text": full_text,
        "pages": local_meta.get("pages") or artifacts.get("pages") or [],
        "all_tables": local_meta.get("all_tables") or [],
        "all_figures": local_meta.get("all_figures") or [],
        "parse_bundle": local_meta.get("parse_bundle") or {},
    }


def _save_preview_extract_cache(key: str, ctx: dict) -> None:
    if not key:
        return
    try:
        from blueprints.FLGL.extraction.shared.parse_bundle import attach_bundle_to_context, bundle_to_cache_payload

        ctx = attach_bundle_to_context(ctx)
        bundle_payload = bundle_to_cache_payload(ctx)
    except Exception as exc:
        logger.debug("[method-extract] parse_bundle attach failed: %s", exc)
        bundle_payload = {}
    parse_cache = _finalize_parse_cache_like_word(
        ctx.get("parse_cache") if isinstance(ctx.get("parse_cache"), dict) else None
    )
    if isinstance(parse_cache, dict):
        try:
            from blueprints.ZSGL.WJGL1.document_parse_service import save_preview_parse_to_minio

            parse_cache = save_preview_parse_to_minio(key, parse_cache, ctx)
            ctx["parse_cache"] = parse_cache
        except Exception as exc:
            logger.warning("[method-extract] preview minio persist failed key=%s: %s", key[:24], exc)
    payload = {
        "full_text": str(ctx.get("full_text") or ""),
        "pages": list(ctx.get("pages") or []),
        "all_tables": list(ctx.get("all_tables") or []),
        "all_figures": list(ctx.get("all_figures") or []),
        "parse_cache": _slim_parse_cache_for_local_json(parse_cache),
        "category": ctx.get("category") or "_preview",
        "doc_id": ctx.get("doc_id") or key,
        "source": ctx.get("source") or "preview_cache",
        "sections": list((parse_cache or {}).get("sections") or []),
        "parse_bundle": bundle_payload,
        "storage": (parse_cache or {}).get("_storage") or "local",
    }
    path = _preview_extract_cache_path(key)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import save_preview_parse_local_bundle

        save_preview_parse_local_bundle(
            key,
            parse_cache if isinstance(parse_cache, dict) else {},
            full_text=str(payload.get("full_text") or ""),
            source=str(ctx.get("source") or "preview_cache"),
        )
    except Exception as exc:
        logger.debug("[method-extract] preview local bundle failed: %s", exc)


def _load_preview_extract_cache(key: str) -> Optional[dict]:
    category = "_preview"
    local_meta: Optional[dict] = None
    path = _preview_extract_cache_path(key)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                local_meta = json.load(f)
        except Exception as exc:
            logger.warning("[method-extract] 读取 preview 缓存失败 key=%s: %s", key[:24], exc)

    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import (
            load_parse_artifacts,
            parse_artifacts_ready,
        )

        if parse_artifacts_ready(category, key):
            artifacts = load_parse_artifacts(category, key)
            if artifacts and (
                str(artifacts.get("full_html") or "").strip()
                or artifacts.get("sections")
                or str(artifacts.get("full_markdown") or "").strip()
            ):
                return _merge_preview_cache_from_minio(key, artifacts, local_meta)
    except Exception as exc:
        logger.debug("[method-extract] preview minio load failed key=%s: %s", key[:24], exc)

    if isinstance(local_meta, dict):
        pc = local_meta.get("parse_cache")
        if isinstance(pc, dict) and pc.get("_storage") == "minio":
            logger.warning(
                "[method-extract] preview 本地索引存在但 MinIO 不可用 key=%s",
                key[:24],
            )
        return local_meta
    return None


def _is_outline_document(
    file_path: str,
    ext: str,
    full_text: str = "",
    doc_class_hint: str = "",
) -> bool:
    hint = str(doc_class_hint or "").strip().lower()
    if hint in ("outline", "大纲", "trial_outline", "word_llm"):
        return True
    if hint in ("standard", "标准", "gb", "pipeline"):
        return False
    if ext == ".pdf":
        return False
    text = str(full_text or "").strip()
    if not text and file_path and os.path.isfile(file_path):
        try:
            if ext == ".md":
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
            elif ext == ".txt":
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
        except OSError:
            text = ""
    if _OUTLINE_MARKER_RE.search(text):
        return True
    if _OUTLINE_FIVE_SECTION_RE.search(text):
        return True
    base = os.path.basename(file_path or "")
    if ext == ".docx" and "大纲" in base:
        return True
    return ext in (".md", ".txt", ".docx", ".doc")


def _sections_to_full_headings(sections: list) -> list:
    out: list = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading") or sec.get("title") or "").strip()
        if not heading or heading in ("正文", "前言"):
            continue
        section_no = ""
        title = heading
        m = _HEADING_SECTION_NO_RE.match(heading)
        if m:
            section_no = m.group(1)
            title = m.group(2).strip() or heading
        snippet = str(sec.get("text") or "").strip().replace("\n", " ")[:200]
        out.append({
            "section_id": sec.get("section_id"),
            "sectionNo": section_no,
            "title": title,
            "level": int(sec.get("level") or 0),
            "start_page": sec.get("start_page"),
            "end_page": sec.get("end_page"),
            "snippet": snippet,
            "source": "parser",
        })
    return out


def _extract_toc_for_headings(temp_path: str, ext: str) -> list:
    try:
        from blueprints.ZSGL.WJGL1.routes import _extract_toc_from_word, _extract_toc_from_pdf_fast
        from blueprints.ZSGL.WJGL1.body_content_trim import filter_toc_from_chapter_one
    except Exception as exc:
        logger.warning("[document-headings] 导入 TOC 提取器失败: %s", exc)
        return []
    try:
        if ext in (".docx", ".doc"):
            toc = _extract_toc_from_word(temp_path)
        elif ext == ".pdf":
            toc = _extract_toc_from_pdf_fast(temp_path)
        else:
            toc = []
        return filter_toc_from_chapter_one(toc or [])
    except Exception as exc:
        logger.warning("[document-headings] TOC 提取失败: %s", exc)
        return []


def _parse_sections_for_headings(temp_path: str, ext: str, ctx: dict) -> list:
    sections = list((ctx.get("parse_cache") or {}).get("sections") or [])
    if sections:
        return sections
    full_text = str(ctx.get("full_text") or "").strip()
    if not full_text:
        return []
    try:
        from blueprints.ZSGL.WJGL1.document_structure import analyze_document_structure

        structure = analyze_document_structure(
            full_text=full_text,
            file_path=temp_path if temp_path and os.path.isfile(temp_path) else None,
        )
        if structure.sections:
            return list(structure.sections)
    except Exception as exc:
        logger.warning("[document-headings] document_structure 失败: %s", exc)
    try:
        from blueprints.FLGL.GetTestMethod.parser import split_standard_sections
    except Exception:
        try:
            _gtm_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GetTestMethod")
            if _gtm_root not in sys.path:
                sys.path.insert(0, _gtm_root)
            from parser import split_standard_sections
        except Exception as exc:
            logger.warning("[document-headings] split_standard_sections 不可用: %s", exc)
            return []
    return split_standard_sections(full_text)


def _synr_is_rich_html(synr: str) -> bool:
    s = str(synr or "").strip()
    if not s:
        return False
    if re.search(r"<\s*br\s*/?>", s, re.I) and not re.search(
        r"<(?:p|div|table|ul|ol|li|h[1-6]|strong)\b", s, re.I
    ):
        return False
    if re.search(
        r"<(?:p|div|table|ul|ol|li|img|h[1-6]|strong|figure|blockquote)\b",
        s,
        re.I,
    ):
        return True
    return "doc-table" in s or "wjgl-section-heading" in s


def _method_legacy_field_text(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("text") or "").strip()
    return str(raw or "").strip()


def _normalize_method_for_panel(
    method: dict,
    *,
    source_name: str,
    draft_method_id: str = "",
    phase: str = "extract",
    method_index: int = 0,
    job_id: str = "",
    document_dir: str = "",
) -> dict:
    """将 word_llm / pipeline 单条方法转为 MethodPanel 兼容结构。"""
    from .method_section_enrich import is_section_extract_source, label_from_source_filename

    label = label_from_source_filename(source_name)
    name = str(method.get("name") or "").strip()
    synr = str(method.get("SYNR") or method.get("synr") or "").strip()
    extract_source = str(method.get("extract_source") or "").strip()

    trace_full_text, trace_all_tables, trace_all_figures = _trace_context_from_parsed(
        method.get("_extraction_data") or {},
        method.get("_parsed_doc") or {},
    )
    if not trace_full_text.strip() and document_dir:
        ft2, tb2, fg2 = _trace_context_from_document_dir(document_dir)
        trace_full_text = trace_full_text or ft2
        trace_all_tables = trace_all_tables or tb2
        trace_all_figures = trace_all_figures or fg2

    if not synr:
        try:
            from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import build_method_synr_from_native_html
            parsed_doc = method.get("_parsed_doc")
            if not isinstance(parsed_doc, dict) and document_dir:
                ppath = os.path.join(document_dir, "parsed_document.json")
                if os.path.isfile(ppath):
                    with open(ppath, encoding="utf-8") as pf:
                        parsed_doc = json.load(pf)
            if isinstance(parsed_doc, dict) and str(parsed_doc.get("full_html") or "").strip():
                synr = build_method_synr_from_native_html(method, parsed_doc)
        except Exception as exc:
            logger.warning("[normalize_method_for_panel] native synr 构建失败: %s", exc)

    if synr:
        from .method_section_enrich import (
            apply_section_extract_editor_synr,
            is_section_extract_source,
            polish_method_synr_for_editor,
        )

        doc_category = str(
            method.get("source_category") or method.get("category") or method.get("_category") or ""
        ).strip()
        doc_id = str(
            method.get("source_doc_id")
            or method.get("preview_cache_key")
            or method.get("doc_id")
            or method.get("_doc_id")
            or ""
        ).strip()
        document_html = ""
        if doc_category == "_preview" and doc_id:
            try:
                from pdf_extract.mineru_wjgl_service import resolve_local_doc_dir
                html_path = resolve_local_doc_dir(doc_category, doc_id) / "document.html"
                if html_path.is_file():
                    document_html = html_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
        if is_section_extract_source(extract_source):
            synr = apply_section_extract_editor_synr(
                synr,
                full_text=trace_full_text,
                all_tables=trace_all_tables,
                all_figures=trace_all_figures,
                category=doc_category,
                doc_id=doc_id,
                document_html=document_html,
            )
        else:
            synr = polish_method_synr_for_editor(
                synr,
                full_text=trace_full_text,
                all_tables=trace_all_tables,
                all_figures=trace_all_figures,
                force_restructure=True,
            )
    elif not synr:
        if is_section_extract_source(extract_source):
            pass
        else:
            synr = ""

    rich_synr = _synr_is_rich_html(synr)

    sytj = _method_legacy_field_text(method.get("sytj"))
    sygc = _method_legacy_field_text(method.get("syff") or method.get("sygc"))
    jgpd = _method_legacy_field_text(method.get("jgpd"))
    if rich_synr:
        sytj, sygc, jgpd = "", "", ""

    rec_rows = list(method.get("recommended_relations") or [])
    if not rec_rows and phase == "full":
        rec_rows = _rows_from_graph_matches(
            method.get("graph_matches") or {},
            max_total_rows=4,
        )

    equipment_candidates = list(method.get("equipment_candidates") or [])
    if not equipment_candidates:
        for n in method.get("recommended_nodes") or []:
            if not isinstance(n, dict):
                continue
            if str(n.get("node_type") or "").lower() != "equipment":
                continue
            equipment_candidates.append({
                "node_id": n.get("node_id"),
                "node_name": n.get("node_name"),
                "score": n.get("score"),
                "llm_reason": n.get("llm_reason", ""),
            })

    existing = _testmethod_props_by_name(name)
    formatted: dict = {
        "id": draft_method_id if method_index <= 1 else "",
        "name": name,
        "type": str(method.get("type") or "大纲"),
        "label": label,
        "description": str(method.get("description") or "").strip(),
        "sy": "",
        "synr": synr,
        "sytj": sytj,
        "sygc": sygc,
        "jgpd": jgpd,
        "source_name": source_name,
        "recommended_relations": rec_rows,
        "equipment_candidates": equipment_candidates,
        "name_duplicate": {
            "duplicate": existing is not None,
            "existing": existing,
        },
        "extract_source": method.get("extract_source") or "word_llm",
        "synr_format": method.get("synr_format") or "html",
        "content_slots": method.get("content_slots") or {},
        "method_no": method.get("method_no"),
        "test_project_name": method.get("test_project_name"),
        "method_index": method_index,
        "job_id": job_id,
        "document_dir": document_dir,
        "graph_matches_ref": bool(method.get("graph_matches") or method.get("graph_matches_embedding")),
    }
    if phase == "scoped" or method.get("equipment_recommendations"):
        formatted["equipment_recommendations"] = list(method.get("equipment_recommendations") or [])
        formatted["recommended_nodes"] = list(method.get("recommended_nodes") or [])
    else:
        formatted["equipment_recommendations"] = []
        formatted["recommended_nodes"] = []
    return formatted


def _normalize_word_llm_methods_for_panel(
    methods: list,
    *,
    source_name: str,
    draft_method_id: str,
    extraction_data: dict,
    phase: str = "extract",
    job_id: str = "",
    document_dir: str = "",
) -> list:
    formatted: list = []
    for index, method in enumerate(methods or []):
        if not isinstance(method, dict):
            continue
        formatted.append(
            _normalize_method_for_panel(
                method,
                source_name=source_name,
                draft_method_id=draft_method_id,
                phase=phase,
                method_index=index + 1,
                job_id=job_id,
                document_dir=document_dir,
            )
        )
    return formatted


class _FlglSseEncoder(json.JSONEncoder):
    def default(self, o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)


def _sse_pack_flgl(event_type: str, evt_data: dict) -> str:
    return (
        f"event: {event_type}\ndata: "
        f"{json.dumps(evt_data, ensure_ascii=False, cls=_FlglSseEncoder)}\n\n"
    )


@flgl_method_bp.route('/document-headings-upload/rev', methods=['GET'])
def document_headings_upload_rev():
    """部署自检：确认后端已加载最新目录建树逻辑。"""
    return jsonify({
        "success": True,
        "toc_routing_rev": FLGL_TOC_ROUTING_REV,
        "parse_sections_builder": "build_toc_preview_from_parse_sections",
        "preview_bundle": "save_preview_parse_to_minio",
    })


@flgl_method_bp.route('/preview-parse-content', methods=['GET'])
def preview_parse_content():
    """读取 preview MinIO 或本地缓存中的全文 HTML/Markdown。"""
    key = (request.args.get("key") or request.args.get("preview_cache_key") or "").strip()
    fmt = (request.args.get("format") or "html").strip().lower()
    if not key:
        return jsonify({"success": False, "error": "缺少 preview_cache_key"}), 400

    content = ""
    source = ""
    try:
        from blueprints.ZSGL.WJGL1.document_parse_service import (
            load_full_document_html,
            load_parse_artifacts,
            parse_artifacts_ready,
        )

        if parse_artifacts_ready("_preview", key):
            if fmt in ("md", "markdown"):
                artifacts = load_parse_artifacts("_preview", key) or {}
                content = str(artifacts.get("full_markdown") or "").strip()
                source = "minio"
            else:
                content = str(load_full_document_html("_preview", key) or "").strip()
                source = "minio"
    except Exception as exc:
        logger.debug("[preview-parse-content] minio load failed key=%s: %s", key[:24], exc)

    ctx = _load_preview_extract_cache(key) or {} if not content else {}
    parse_cache = ctx.get("parse_cache") if isinstance(ctx.get("parse_cache"), dict) else {}
    parse_cache = _finalize_parse_cache_like_word(parse_cache) or {}

    if not content:
        if fmt in ("md", "markdown"):
            content = str(parse_cache.get("full_markdown") or "").strip()
            source = "parse_cache"
            if not content:
                try:
                    from pdf_extract.mineru_wjgl_service import local_doc_dir

                    md_path = local_doc_dir("_preview", key) / "document.md"
                    if md_path.is_file():
                        content = md_path.read_text(encoding="utf-8", errors="replace").strip()
                        source = "local_document_md"
                except Exception:
                    pass
        else:
            content = str(parse_cache.get("full_html") or parse_cache.get("raw_full_html") or "").strip()
            source = "parse_cache"
            if not content:
                try:
                    from pdf_extract.mineru_wjgl_service import local_doc_dir

                    html_path = local_doc_dir("_preview", key) / "document.html"
                    if html_path.is_file():
                        content = html_path.read_text(encoding="utf-8", errors="replace").strip()
                        source = "local_document_html"
                except Exception:
                    pass

    local_dir = ""
    try:
        from pdf_extract.mineru_wjgl_service import local_doc_dir

        local_dir = str(local_doc_dir("_preview", key))
    except Exception:
        pass

    if fmt in ("md", "markdown"):
        if content:
            return Response(content, mimetype="text/markdown; charset=utf-8")
    elif content:
        return Response(content, mimetype="text/html; charset=utf-8")

    return jsonify({
        "success": False,
        "error": "预览正文尚未生成",
        "code": "parse_pending",
        "preview_cache_key": key,
        "local_dir": local_dir,
        "source": source,
    }), 409


@flgl_method_bp.route('/document-headings-upload', methods=['POST'])
def document_headings_upload():
    """
    上传文件，返回文档目录 + parser 全文标题 + preview_cache_key。
    """
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "没有文件"}), 400
    uploaded = request.files['file']
    if not uploaded.filename:
        return jsonify({"success": False, "error": "文件名为空"}), 400

    ext = os.path.splitext(uploaded.filename)[1].lower()
    if ext not in ('.pdf', '.doc', '.docx', '.md', '.txt'):
        return jsonify({"success": False, "error": f"不支持的文件类型: {ext}"}), 400

    tmp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(tmp_dir, _safe_filename(uploaded.filename))
    preview_cache_key = uuid.uuid4().hex
    try:
        uploaded.save(temp_path)
        temp_path = _ensure_filepath_extension(temp_path, ext)
        minio_client, bucket_name = _get_minio_runtime()
        ctx = _load_method_extract_context(
            temp_path,
            ext,
            tmp_dir,
            minio_client,
            bucket_name,
            preview_cache_key=preview_cache_key,
        )
        if isinstance(ctx.get("parse_cache"), dict):
            ctx["parse_cache"] = _finalize_parse_cache_like_word(ctx["parse_cache"])
        _save_preview_extract_cache(preview_cache_key, ctx)

        parse_sections = list((ctx.get("parse_cache") or {}).get("sections") or [])
        flat_headings = list(
            (ctx.get("parse_cache") or {}).get("flat_headings")
            or ctx.get("flat_headings")
            or []
        )
        toc: List[dict] = []
        full_headings: List[dict] = []
        extract_method = str(ctx.get("source") or "unknown")
        heading_result: Dict[str, Any] = {}

        if parse_sections:
            from blueprints.ZSGL.WJGL1.document_parse_service import build_toc_preview_from_parse_sections

            toc, full_headings = build_toc_preview_from_parse_sections(parse_sections)
            extract_method = f"{extract_method}_parse_sections_preview"
            heading_result = {
                "toc": toc,
                "full_headings": full_headings,
                "heading_count": len(full_headings),
                "extract_method": extract_method,
                "source": "parse_sections",
            }
            logger.info(
                "[document-headings-upload] toc=parse_sections_preview sections=%s headings=%s rev=%s",
                len(parse_sections),
                len(full_headings),
                FLGL_TOC_ROUTING_REV,
            )
        elif flat_headings:
            # 旧版 flat_headings 噪声大，优先用 heading_extract 重建目录
            from blueprints.ZSGL.WJGL1.document_heading_extract import extract_document_headings

            mode = (request.form.get('mode') or 'full').strip().lower()
            heading_result = extract_document_headings(temp_path, mode=mode)
            toc = heading_result.get('toc') or []
            full_headings = heading_result.get('headings') or heading_result.get('full_headings') or []
            extract_method = f"{extract_method}_heading_extract"
            if not toc:
                from blueprints.ZSGL.WJGL1.document_structure import nested_toc_from_flat_headings

                full_headings = flat_headings
                toc = nested_toc_from_flat_headings(flat_headings)
                extract_method = f"{extract_method}_llm_flat_headings_fallback"
            heading_result = {
                "toc": toc,
                "full_headings": full_headings,
                "heading_count": len(full_headings),
                "extract_method": extract_method,
                "source": "llm_parse",
            }
        else:
            from blueprints.ZSGL.WJGL1.document_heading_extract import extract_document_headings

            mode = (request.form.get('mode') or 'full').strip().lower()
            heading_result = extract_document_headings(temp_path, mode=mode)
            toc = heading_result.get('toc') or []
            full_headings = heading_result.get('headings') or heading_result.get('full_headings') or []
            extract_method = f"{extract_method}_heading_extract"
            heading_result = {
                "toc": toc,
                "full_headings": full_headings,
                "heading_count": len(full_headings),
                "extract_method": extract_method,
                "source": "heading_extract",
            }

        debug_dir = ""
        try:
            from .section_extract_debug import save_word_parse_debug_bundle

            debug_dir = save_word_parse_debug_bundle(
                preview_cache_key,
                source_path=temp_path,
                ctx=ctx,
                heading_result=heading_result,
                stage="document_headings_upload",
                extra={"toc_count": len(toc), "headings_count": len(full_headings)},
            )
        except Exception as exc:
            logger.debug("[section-debug] headings 调试落盘失败: %s", exc)

        parse_cache = ctx.get("parse_cache") if isinstance(ctx.get("parse_cache"), dict) else {}
        preview_html = str(parse_cache.get("full_html") or parse_cache.get("raw_full_html") or "").strip()
        preview_markdown = str(parse_cache.get("full_markdown") or "").strip()
        preview_local_dir = ""
        try:
            from pdf_extract.mineru_wjgl_service import local_doc_dir

            preview_local_dir = str(local_doc_dir("_preview", preview_cache_key))
        except Exception:
            pass
        has_preview_content = bool(
            preview_html or preview_markdown or parse_sections or _preview_cache_has_content(ctx)
        )

        return jsonify({
            "success": True,
            "data": {
                "toc": toc,
                "full_headings": full_headings,
                "full_text_len": len(str(ctx.get("full_text") or "")),
                "preview_cache_key": preview_cache_key,
                "heading_count": len(full_headings),
                "extract_method": extract_method,
                "toc_source": "parse_sections" if parse_sections else str(heading_result.get("source") or ""),
                "parse_source": ctx.get("source"),
                "sections_count": len(parse_sections),
                "toc_routing_rev": FLGL_TOC_ROUTING_REV,
                "debug_dir": debug_dir,
                "has_preview_content": has_preview_content,
                "preview_html_len": len(preview_html),
                "preview_markdown_len": len(preview_markdown),
                "preview_local_dir": preview_local_dir,
            },
        })
    except Exception as e:
        logger.error("[document-headings-upload] 失败: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@flgl_method_bp.route('/method-extract-auto', methods=['POST'])
def method_extract_auto():
    """
    统一 LLM 自动提取入口：大纲 → word_llm；标准 → pipeline。
    返回结构与 method-extract / MethodPanel 审阅兼容。
    """
    if 'file' not in request.files and not (request.form.get('object_name') or '').strip():
        return jsonify({"success": False, "error": "没有文件"}), 400

    source_file_id = (request.form.get('source_file_id') or 'WJ_auto0001').strip()
    user_id = (request.form.get('user_id') or 'admin').strip()
    draft_method_id = (request.form.get('draft_method_id') or request.form.get('method_id') or '').strip()
    doc_class_hint = (request.form.get('doc_class_hint') or request.form.get('document_class') or '').strip()
    skip_recall = str(request.form.get('skip_recall', '0')).lower() in ('1', 'true', 'yes', 'on')
    use_chroma_source = str(request.form.get('use_chroma_source', '0')).lower() in ('1', 'true', 'yes', 'on')

    tmp_dir = tempfile.mkdtemp()
    try:
        filepath, ext, original_filename = _resolve_method_auto_extract_file(
            tmp_dir,
            source_file_id=source_file_id,
            object_name=(request.form.get('object_name') or '').strip(),
            use_chroma_source=use_chroma_source,
        )
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as dl_exc:
        return jsonify({"success": False, "error": f"从 MinIO 下载文件失败: {dl_exc}"}), 500

    _saved_backend_config = None
    try:
        if not draft_method_id:
            try:
                from .routes import _generate_id
                draft_method_id = _generate_id('TestMethod')
            except Exception:
                draft_method_id = f"TM_{uuid.uuid4().hex[:8]}"

        _f = os.path.abspath(__file__)
        _backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(_f)))
        output_root = os.path.join(_backend_dir, "output_json", "word_llm_auto")

        use_outline = _is_outline_document(filepath, ext, doc_class_hint=doc_class_hint)
        if use_outline and ext == '.pdf':
            use_outline = False

        if use_outline:
            from blueprints.FLGL.extraction.config import get_extractor_version
            from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
                inject_flask_runtime_into_word_llm,
                run_word_llm_extract,
            )

            inject_flask_runtime_into_word_llm(current_app)
            use_v2 = get_extractor_version() == "v2"
            out = None

            if use_v2:
                try:
                    from blueprints.FLGL.extraction.llm.pipeline import (
                        LLMExtractError,
                        run_llm_extract_v2_stream,
                    )

                    out = run_llm_extract_v2_stream(
                        filepath,
                        source_file_id=source_file_id,
                        user_id=user_id,
                        output_root=output_root,
                        skip_recall=skip_recall,
                        equipment_display_mode=str(
                            request.form.get("equipment_display_mode") or "inline"
                        ).strip() or "inline",
                    )
                    methods_raw = list(out.get("methods") or [])
                    source_name = original_filename
                    formatted = [
                        _normalize_method_for_panel(
                            m,
                            source_name=source_name,
                            draft_method_id=draft_method_id,
                            phase="extract",
                            method_index=i + 1,
                            document_dir=str(out.get("document_dir") or ""),
                        )
                        for i, m in enumerate(methods_raw)
                    ]
                except Exception as v2_exc:
                    logger.warning("[method-extract-auto] v2 失败，降级 v1: %s", v2_exc)
                    use_v2 = False

            if not use_v2:
                out = run_word_llm_extract(
                    filepath,
                    source_file_id=source_file_id,
                    user_id=user_id,
                    output_root=output_root,
                    skip_recall=skip_recall,
                )
                extraction_data = out.get("extraction_data") or {}
                methods_raw = list(extraction_data.get("methods") or [])
                source_name = str(
                    (extraction_data.get("source_file") or {}).get("file_name") or original_filename
                )
                formatted = _normalize_word_llm_methods_for_panel(
                    methods_raw,
                    source_name=source_name,
                    draft_method_id=draft_method_id,
                    extraction_data=extraction_data,
                    phase="extract",
                    document_dir=str(out.get("document_dir") or ""),
                )
            else:
                extraction_data = {"methods": methods_raw, "source_file": {"file_name": source_name}}
            first = formatted[0] if formatted else {}
            result_data = {
                "draft_method_id": draft_method_id,
                "id": draft_method_id,
                "name": first.get("name", ""),
                "type": first.get("type", ""),
                "label": first.get("label", ""),
                "description": first.get("description", ""),
                "sy": "",
                "synr": first.get("synr", ""),
                "sytj": first.get("sytj", ""),
                "sygc": first.get("sygc", ""),
                "jgpd": first.get("jgpd", ""),
                "source_name": source_name,
                "methods_count": len(formatted),
                "methods": formatted,
                "all_methods": [
                    {"name": m.get("name", ""), "type": m.get("type", ""), "summary": m.get("description", "")}
                    for m in formatted
                ],
                "recommended_nodes": [],
                "extract_engine": "word_llm",
                "extract_source": "word_llm",
                "job_status": "completed",
                "document_dir": out.get("document_dir"),
                "method_count": out.get("method_count"),
                "test_project_count": out.get("test_project_count"),
                "method_json_paths": out.get("method_json_paths") or [],
            }
            log_user_action(
                f"分类管理-LLM自动提取(大纲) file={original_filename[:120]} methods={len(formatted)}"
            )
            return jsonify({"success": True, "data": result_data})

        # 标准文档：复用 pipeline（与 method-extract 相同逻辑，强制跳过 DB 缓存）
        from backend.config import Config as MainConfig
        import numpy as np
        if not hasattr(np, 'sctypes'):
            np.sctypes = {
                'int': [np.int8, np.int16, np.int32, np.int64],
                'uint': [np.uint8, np.uint16, np.uint32, np.uint64],
                'float': [np.float16, np.float32, np.float64],
                'complex': [np.complex64, np.complex128],
                'others': [np.bool_, np.object_, np.str_, np.bytes_],
            }
        _flgl_dir = os.path.dirname(os.path.abspath(__file__))
        _gtm_root = os.path.join(_flgl_dir, 'GetTestMethod')
        if _flgl_dir not in sys.path:
            sys.path.insert(0, _flgl_dir)
        if _gtm_root not in sys.path:
            sys.path.insert(0, _gtm_root)

        import importlib.util
        method_backend_config_path = os.path.join(_gtm_root, 'backend', 'config.py')
        spec = importlib.util.spec_from_file_location('backend.config', method_backend_config_path)
        method_backend_config = importlib.util.module_from_spec(spec)
        _saved_backend_config = sys.modules.get('backend.config')
        sys.modules['backend.config'] = method_backend_config
        assert spec and spec.loader
        spec.loader.exec_module(method_backend_config)

        MethodConfig = method_backend_config.Config
        MethodConfig.OPENAI_CLIENT = current_app.config.get('OPENAI_CLIENT')
        MethodConfig.OPENAI_MODEL = (
            getattr(MainConfig, "FLGL_TESTMETHOD_OPENAI_MODEL", None)
            or current_app.config.get('OPENAI_MODEL', getattr(MethodConfig, 'OPENAI_MODEL', None))
        )
        MethodConfig.NEO4J_DRIVER = (
            current_app.config.get('FLGL_TESTMETHOD_NEO4J_DRIVER')
            or current_app.config.get('NEO4J_DRIVER')
        )
        MethodConfig.CHROMA_CLIENT = current_app.config.get('CHROMA_CLIENT')
        MethodConfig.CHROMA_COLLECTION_OBJ = current_app.config.get('CHROMA_COLLECTION_OBJ')
        MethodConfig.MINIO_CLIENT = current_app.config.get('MINIO_CLIENT')
        _bge = (getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_MODEL_PATH', None) or '').strip()
        MethodConfig.BGE_MODEL_PATH = os.path.abspath(_bge) if _bge else None
        MethodConfig.BGE_LOCAL_FILES_ONLY = bool(getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY', True))
        MethodConfig.TESTMETHOD_RETRIEVER = None

        from GetTestMethod.pipeline import run_extract_only, run_recall_only

        _method_ocr_tmp = os.path.join(_backend_dir, "output_json", "ocr_tmp")
        extract_result = run_extract_only(
            file_path=filepath,
            source_file_id=source_file_id,
            user_id=user_id,
            output_root=os.path.join(tmp_dir, 'output'),
            ocr_output_dir=_method_ocr_tmp,
        )
        extraction_data = extract_result.get('extraction_data', {}) or {}
        parsed_path = extract_result.get('parsed_document_path') or ''
        gallery_html = _ocr_figure_gallery_html_from_parsed_doc(parsed_path, draft_method_id, user_id)
        methods = extraction_data.get('methods', []) or []

        parsed_doc: dict = {}
        if parsed_path and os.path.isfile(parsed_path):
            try:
                with open(parsed_path, "r", encoding="utf-8") as pf:
                    parsed_doc = json.load(pf)
            except (OSError, json.JSONDecodeError):
                parsed_doc = {}

        recall_result = None
        checkpoint_path = extract_result.get('extraction_checkpoint_path')
        if checkpoint_path and not skip_recall:
            try:
                recall_result = run_recall_only(
                    extraction_checkpoint_path=checkpoint_path,
                    source_file_id=source_file_id,
                    user_id=user_id,
                    output_root=os.path.join(tmp_dir, 'output'),
                    topk_config={
                        'test_project': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT', 2),
                        'indicator': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR', 2),
                    },
                    save_method_json=False,
                )
            except Exception as recall_e:
                logger.warning("[method-extract-auto] 召回失败: %s", recall_e)

        from .method_section_enrich import generate_method_description_llm, label_from_source_filename

        source_name = str((extraction_data.get('source_file') or {}).get('file_name') or original_filename)
        method_label = label_from_source_filename(source_name)
        trace_full_text, trace_all_tables, trace_all_figures = _trace_context_from_parsed(extraction_data, parsed_doc)
        enriched_from_recall = []
        if recall_result and isinstance(recall_result.get('result'), dict):
            enriched_from_recall = recall_result.get('result', {}).get('methods') or []

        formatted_methods = []
        for index, method in enumerate(methods):
            graph_matches = {}
            if index < len(enriched_from_recall) and isinstance(enriched_from_recall[index], dict):
                gm = enriched_from_recall[index].get('graph_matches')
                if isinstance(gm, dict):
                    graph_matches = gm
            rec_rows = _rows_from_graph_matches(
                graph_matches,
                _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT', 2),
                _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR', 2),
                max_total_rows=_flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_RELATION_ROWS', 2),
            )
            current_name = str(method.get('name') or '').strip()
            synr_traced = _apply_synr_document_trace(
                _method_parts_to_synr(method, gallery_html if index == 0 else ''),
                full_text=trace_full_text,
                all_tables=trace_all_tables,
                all_figures=trace_all_figures,
            )
            desc = str(method.get('description') or method.get('summary') or '').strip()
            if not desc:
                desc = generate_method_description_llm(
                    current_name,
                    content_html=synr_traced,
                    source_name=source_name,
                )
            formatted_methods.append({
                'id': draft_method_id if index == 0 else '',
                'name': current_name,
                'type': method.get('type', ''),
                'label': method_label,
                'description': desc,
                'sy': '',
                'synr': synr_traced,
                'sytj': text_block_to_markdown(method.get('sytj')),
                'sygc': text_block_to_markdown(method.get('sygc')),
                'jgpd': text_block_to_markdown(method.get('jgpd')),
                'source_name': source_name,
                'recommended_relations': rec_rows,
                'name_duplicate': {
                    'duplicate': _testmethod_props_by_name(current_name) is not None,
                    'existing': _testmethod_props_by_name(current_name),
                },
                'extract_source': 'pipeline',
            })

        first = formatted_methods[0] if formatted_methods else {}
        result_data = {
            'draft_method_id': draft_method_id,
            'id': draft_method_id,
            'name': first.get('name', ''),
            'type': first.get('type', ''),
            'label': first.get('label', ''),
            'description': first.get('description', '未提取到方法'),
            'sy': '',
            'synr': first.get('synr', ''),
            'sytj': first.get('sytj', ''),
            'sygc': first.get('sygc', ''),
            'jgpd': first.get('jgpd', ''),
            'source_name': source_name,
            'methods_count': len(formatted_methods),
            'methods': formatted_methods,
            'all_methods': [
                {'name': m.get('name', ''), 'type': m.get('type', ''), 'summary': m.get('description', '')}
                for m in formatted_methods
            ],
            'recommended_nodes': [],
            'extract_engine': 'pipeline',
            'extract_source': 'pipeline',
            'job_status': 'completed',
        }
        log_user_action(
            f"分类管理-LLM自动提取(标准) file={original_filename[:120]} methods={len(formatted_methods)}"
        )
        return jsonify({"success": True, "data": result_data})

    except Exception as e:
        logger.error("[method-extract-auto] 失败: %s", e)
        return jsonify({"success": False, "error": f"自动提取失败: {str(e)}"}), 500
    finally:
        if _saved_backend_config is not None:
            sys.modules['backend.config'] = _saved_backend_config
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


@flgl_method_bp.route('/method-extract-status', methods=['GET'])
def method_extract_status():
    """SSE 断线降级：读取 checkpoint 已落盘方法数量与路径。"""
    document_dir = (request.args.get('document_dir') or '').strip()
    job_id = (request.args.get('job_id') or '').strip()
    if not document_dir or not os.path.isdir(document_dir):
        return jsonify({"success": False, "error": "document_dir 无效"}), 400

    cp_path = os.path.join(document_dir, "extraction_checkpoint.json")
    if not os.path.isfile(cp_path):
        return jsonify({
            "success": True,
            "data": {
                "job_id": job_id,
                "document_dir": document_dir,
                "methods_count": 0,
                "status": "unknown",
                "methods": [],
            },
        })

    try:
        with open(cp_path, "r", encoding="utf-8") as f:
            extraction_data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"success": False, "error": f"读取 checkpoint 失败: {exc}"}), 500

    methods_raw = list(extraction_data.get("methods") or [])
    source_name = str(
        (extraction_data.get("source_file") or {}).get("file_name") or ""
    )
    status = str((extraction_data.get("document_meta") or {}).get("status") or "in_progress")

    panel_methods = [
        _normalize_method_for_panel(
            m,
            source_name=source_name,
            phase="extract",
            method_index=i + 1,
            job_id=job_id,
            document_dir=document_dir,
        )
        for i, m in enumerate(methods_raw)
        if isinstance(m, dict)
    ]

    return jsonify({
        "success": True,
        "data": {
            "job_id": job_id,
            "document_dir": document_dir,
            "methods_count": len(methods_raw),
            "status": status,
            "methods": panel_methods,
        },
    })


@flgl_method_bp.route('/method-recall-scoped', methods=['POST'])
def method_recall_scoped():
    """用户选定装备后，召回该装备下项目/指标。"""
    data = request.get_json(silent=True) or {}
    equipment_ids = data.get("equipment_ids") or []
    if not isinstance(equipment_ids, list) or not equipment_ids:
        return jsonify({"success": False, "error": "equipment_ids 不能为空"}), 400

    document_dir = (data.get("document_dir") or "").strip()
    method_index = int(data.get("method_index") or 0)
    method_name = (data.get("method_name") or "").strip()
    graph_matches = data.get("graph_matches") if isinstance(data.get("graph_matches"), dict) else None

    method_raw: Optional[dict] = None
    source_name = ""
    if document_dir and os.path.isdir(document_dir):
        cp_path = os.path.join(document_dir, "extraction_checkpoint.json")
        if os.path.isfile(cp_path):
            try:
                with open(cp_path, "r", encoding="utf-8") as f:
                    extraction_data = json.load(f)
                source_name = str(
                    (extraction_data.get("source_file") or {}).get("file_name") or ""
                )
                methods = extraction_data.get("methods") or []
                if 0 < method_index <= len(methods) and isinstance(methods[method_index - 1], dict):
                    method_raw = dict(methods[method_index - 1])
                elif method_name:
                    for m in methods:
                        if isinstance(m, dict) and str(m.get("name") or "").strip() == method_name:
                            method_raw = dict(m)
                            break
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("[method-recall-scoped] checkpoint 读取失败: %s", exc)

    if method_raw is None:
        return jsonify({"success": False, "error": "未找到对应方法，请提供 document_dir + method_index"}), 404

    if graph_matches:
        method_raw["graph_matches"] = graph_matches

    neo4j_driver = current_app.config.get("FLGL_TESTMETHOD_NEO4J_DRIVER") or current_app.config.get("NEO4J_DRIVER")
    try:
        from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import recall_method_scoped_for_equipment

        updated = recall_method_scoped_for_equipment(
            method_raw,
            [str(x).strip() for x in equipment_ids if str(x).strip()],
            neo4j_driver=neo4j_driver,
        )
    except Exception as exc:
        logger.error("[method-recall-scoped] 失败: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500

    formatted = _normalize_method_for_panel(
        updated,
        source_name=source_name or str(method_raw.get("source_name") or ""),
        phase="scoped",
        method_index=method_index or 0,
        document_dir=document_dir,
    )
    return jsonify({"success": True, "data": formatted})


def _plain_text_from_synr(synr: str) -> str:
    text = re.sub(r'<[^>]+>', '\n', synr or '')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _load_method_from_checkpoint(
    document_dir: str,
    method_index: int = 0,
    method_name: str = '',
) -> Tuple[Optional[dict], str]:
    """从 extraction_checkpoint.json 读取方法；返回 (method_dict, source_name)。"""
    if not document_dir or not os.path.isdir(document_dir):
        return None, ''
    cp_path = os.path.join(document_dir, 'extraction_checkpoint.json')
    if not os.path.isfile(cp_path):
        return None, ''
    try:
        with open(cp_path, 'r', encoding='utf-8') as f:
            extraction_data = json.load(f)
        source_name = str((extraction_data.get('source_file') or {}).get('file_name') or '')
        methods = extraction_data.get('methods') or []
        if 0 < method_index <= len(methods) and isinstance(methods[method_index - 1], dict):
            return dict(methods[method_index - 1]), source_name
        if method_name:
            for m in methods:
                if isinstance(m, dict) and str(m.get('name') or '').strip() == method_name:
                    return dict(m), source_name
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('[load_method_checkpoint] failed: %s', exc)
    return None, ''


def _equipment_name_from_neo4j(equipment_id: str) -> str:
    from .routes import _run_cypher
    eid = (equipment_id or '').strip()
    if not eid:
        return eid
    try:
        recs = _run_cypher(
            'MATCH (n:Equipment {Id: $id}) RETURN n.Name AS name LIMIT 1',
            {'id': eid},
        )
        if recs and recs[0].get('name'):
            return str(recs[0]['name']).strip() or eid
    except Exception as exc:
        logger.debug('[equipment_name] %s: %s', eid, exc)
    return eid


def _build_method_item_for_recall(
    form_method: dict,
    checkpoint_method: Optional[dict] = None,
) -> dict:
    """将表单字段与 checkpoint 合并为 recall 可用的 method_item。"""
    item = dict(checkpoint_method) if checkpoint_method else {}
    name = str(form_method.get('name') or item.get('name') or '').strip()
    desc = str(form_method.get('description') or item.get('description') or '').strip()
    synr = str(
        form_method.get('synr') or form_method.get('SYNR') or item.get('synr') or item.get('SYNR') or ''
    ).strip()
    plain = _plain_text_from_synr(synr) if synr else ''
    if not desc and plain:
        desc = plain[:2000]
    item['name'] = name or str(item.get('name') or '').strip()
    item['type'] = str(form_method.get('type') or item.get('type') or '分则').strip() or '分则'
    item['description'] = desc
    if synr:
        item['synr'] = synr
        if plain and not _method_legacy_field_text(item.get('sytj')):
            third = max(len(plain) // 3, 1)
            item['sytj'] = {'text': plain[:third]}
            item['sygc'] = {'text': plain[third: 2 * third]}
            item['jgpd'] = {'text': plain[2 * third:]}
    return item


def _ensure_equipment_in_graph_matches(
    graph_matches: dict,
    equipment_ids: List[str],
) -> dict:
    """确保用户选定装备出现在 graph_matches.equipment 中（便于 scoped 召回）。"""
    gm = dict(graph_matches) if isinstance(graph_matches, dict) else {}
    equip_list = list(gm.get('equipment') or [])
    existing = {
        str(e.get('id') or e.get('Id') or '').strip()
        for e in equip_list
        if isinstance(e, dict)
    }
    for eid in equipment_ids:
        eid = str(eid).strip()
        if not eid or eid in existing:
            continue
        ename = _equipment_name_from_neo4j(eid)
        equip_list.append({'id': eid, 'name': ename, 'score': 0.5})
        existing.add(eid)
    gm['equipment'] = equip_list
    return gm


def _equipment_tab_recs_have_links(eq_recs: List[Dict[str, Any]]) -> bool:
    for tab in eq_recs or []:
        if tab.get('projects') or tab.get('indicators'):
            return True
    return False


def _merge_equipment_tab_recs(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """合并 Relation_details 与图库 Cypher 还原的设备 Tab（按 equipment_id 去重）。"""
    merged_map: Dict[str, Dict[str, Any]] = {}
    UNASSIGNED = '__unassigned__'

    for src in (primary or []) + (secondary or []):
        if not isinstance(src, dict):
            continue
        eid = str(src.get('equipment_id') or '').strip()
        if not eid:
            continue
        if eid not in merged_map:
            merged_map[eid] = {
                'equipment_id': eid,
                'equipment_name': src.get('equipment_name') or eid,
                'score': src.get('score'),
                'projects': [],
                'indicators': [],
            }
        dest = merged_map[eid]
        src_name = str(src.get('equipment_name') or '').strip()
        if src_name and (not dest.get('equipment_name') or dest['equipment_name'] == eid):
            dest['equipment_name'] = src_name
        if src.get('score') is not None and dest.get('score') is None:
            dest['score'] = src.get('score')
        for slot in ('projects', 'indicators'):
            seen = {str(x.get('node_id') or '') for x in dest[slot]}
            for item in src.get(slot) or []:
                if not isinstance(item, dict):
                    continue
                nid = str(item.get('node_id') or '').strip()
                if not nid or nid in seen:
                    continue
                dest[slot].append({
                    'node_id': nid,
                    'node_name': item.get('node_name') or nid,
                    'score': item.get('score'),
                })
                seen.add(nid)

    out = [v for k, v in merged_map.items() if k != UNASSIGNED]
    unassigned = merged_map.get(UNASSIGNED)
    if unassigned and (unassigned.get('projects') or unassigned.get('indicators')):
        out.append(unassigned)
    return out


def _equipment_tabs_payload_for_method(method_id: str) -> Dict[str, Any]:
    """按设备维度组装方法关联（项目/指标），供 DeviceLinkTabs 还原。"""
    from .routes import _run_cypher

    mid = (method_id or '').strip()
    if not mid:
        return {'equipment_recommendations': [], 'equipment_tabs': []}

    rd_raw = ''
    try:
        rd_recs = _run_cypher(
            'MATCH (m:TestMethod {Id: $id}) RETURN m.Relation_details AS rd LIMIT 1',
            {'id': mid},
        )
        if rd_recs:
            rd_raw = str(rd_recs[0].get('rd') or '').strip()
    except Exception:
        rd_raw = ''

    from_rd_recs: Optional[List[Dict[str, Any]]] = None

    if rd_raw:
        try:
            details = json.loads(rd_raw)
            if isinstance(details, list) and details:
                tabs_map: Dict[str, Dict[str, Any]] = {}
                UNASSIGNED = '__unassigned__'

                def _ensure(eq_id: str, eq_name: str) -> Dict[str, Any]:
                    if eq_id not in tabs_map:
                        tabs_map[eq_id] = {
                            'equipment_id': eq_id,
                            'equipment_name': eq_name or eq_id,
                            'projects': [],
                            'indicators': [],
                        }
                    return tabs_map[eq_id]

                for d in details:
                    if not isinstance(d, dict):
                        continue
                    tt = str(d.get('target_type') or '').strip()
                    tid = str(d.get('target_id') or '').strip()
                    if not tid:
                        continue
                    rate = d.get('rate')
                    try:
                        score = float(rate) if rate is not None and rate != '' else None
                    except (TypeError, ValueError):
                        score = None
                    nm = str(d.get('node_name') or d.get('target_name') or tid)
                    eq_id = str(d.get('equipment_id') or '').strip()
                    if tt == 'Equipment':
                        _ensure(tid, nm)
                        if score is not None:
                            tabs_map[tid]['score'] = score
                        continue
                    if tt not in ('TestProject', 'Indicator'):
                        continue
                    bind_eq = eq_id or UNASSIGNED
                    tab = _ensure(
                        bind_eq,
                        '未绑定设备' if bind_eq == UNASSIGNED else (tabs_map.get(bind_eq, {}).get('equipment_name') or bind_eq),
                    )
                    slot = tab['projects'] if tt == 'TestProject' else tab['indicators']
                    if not any(x.get('node_id') == tid for x in slot):
                        slot.append({'node_id': tid, 'node_name': nm, 'score': score})

                eq_recs = [v for k, v in tabs_map.items() if k != UNASSIGNED]
                if UNASSIGNED in tabs_map and (tabs_map[UNASSIGNED]['projects'] or tabs_map[UNASSIGNED]['indicators']):
                    eq_recs.append(tabs_map[UNASSIGNED])
                if eq_recs and _equipment_tab_recs_have_links(eq_recs):
                    return {'equipment_recommendations': eq_recs, 'equipment_tabs': eq_recs}
                if eq_recs:
                    from_rd_recs = eq_recs
        except (json.JSONDecodeError, TypeError):
            pass

    eq_rows = _run_cypher(
        """
        MATCH (eq:Equipment)-[re:CON]->(m:TestMethod {Id: $id})
        RETURN eq.Id AS eq_id, eq.Name AS eq_name, re.rate AS eq_rate
        ORDER BY eq_name
        """,
        {'id': mid},
    ) or []
    tabs_map: Dict[str, Dict[str, Any]] = {}
    for row in eq_rows:
        eid = str(row.get('eq_id') or '').strip()
        if not eid:
            continue
        rate = row.get('eq_rate')
        try:
            escore = float(rate) if rate is not None and rate != '' else None
        except (TypeError, ValueError):
            escore = None
        tabs_map[eid] = {
            'equipment_id': eid,
            'equipment_name': str(row.get('eq_name') or eid),
            'score': escore,
            'projects': [],
            'indicators': [],
        }

    eq_proj = _run_cypher(
        """
        MATCH (eq:Equipment)-[:CON]->(p:TestProject)-[rp:CON]->(m:TestMethod {Id: $id})
        RETURN eq.Id AS eq_id, p.Id AS pid, p.Name AS pname, rp.rate AS prate
        """,
        {'id': mid},
    ) or []
    proj_bound: set = set()
    for row in eq_proj:
        eid = str(row.get('eq_id') or '').strip()
        pid = str(row.get('pid') or '').strip()
        if not eid or not pid or eid not in tabs_map:
            continue
        proj_bound.add(pid)
        pr = row.get('prate')
        try:
            pscore = float(pr) if pr is not None and pr != '' else None
        except (TypeError, ValueError):
            pscore = None
        tabs_map[eid]['projects'].append({
            'node_id': pid,
            'node_name': str(row.get('pname') or pid),
            'score': pscore,
        })

    eq_ind = _run_cypher(
        """
        MATCH (eq:Equipment)-[:CON]->(i:Indicator)-[ri:CON]->(m:TestMethod {Id: $id})
        RETURN eq.Id AS eq_id, i.Id AS iid, i.Name AS iname, ri.rate AS irate
        """,
        {'id': mid},
    ) or []
    ind_bound: set = set()
    for row in eq_ind:
        eid = str(row.get('eq_id') or '').strip()
        iid = str(row.get('iid') or '').strip()
        if not eid or not iid or eid not in tabs_map:
            continue
        ind_bound.add(iid)
        ir = row.get('irate')
        try:
            iscore = float(ir) if ir is not None and ir != '' else None
        except (TypeError, ValueError):
            iscore = None
        tabs_map[eid]['indicators'].append({
            'node_id': iid,
            'node_name': str(row.get('iname') or iid),
            'score': iscore,
        })

    orphan_proj = _run_cypher(
        """
        MATCH (p:TestProject)-[rp:CON]->(m:TestMethod {Id: $id})
        RETURN p.Id AS pid, p.Name AS pname, rp.rate AS prate
        """,
        {'id': mid},
    ) or []
    orphan_ind = _run_cypher(
        """
        MATCH (i:Indicator)-[ri:CON]->(m:TestMethod {Id: $id})
        RETURN i.Id AS iid, i.Name AS iname, ri.rate AS irate
        """,
        {'id': mid},
    ) or []

    single_eq = next(iter(tabs_map.keys())) if len(tabs_map) == 1 else ''
    unassigned: Optional[Dict[str, Any]] = None

    def _orphan_tab() -> Dict[str, Any]:
        nonlocal unassigned
        if unassigned is None:
            unassigned = {
                'equipment_id': '__unassigned__',
                'equipment_name': '未绑定设备',
                'projects': [],
                'indicators': [],
            }
        return unassigned

    for row in orphan_proj:
        pid = str(row.get('pid') or '').strip()
        if not pid or pid in proj_bound:
            continue
        pr = row.get('prate')
        try:
            pscore = float(pr) if pr is not None and pr != '' else None
        except (TypeError, ValueError):
            pscore = None
        entry = {
            'node_id': pid,
            'node_name': str(row.get('pname') or pid),
            'score': pscore,
        }
        if single_eq:
            tabs_map[single_eq]['projects'].append(entry)
        else:
            _orphan_tab()['projects'].append(entry)

    for row in orphan_ind:
        iid = str(row.get('iid') or '').strip()
        if not iid or iid in ind_bound:
            continue
        ir = row.get('irate')
        try:
            iscore = float(ir) if ir is not None and ir != '' else None
        except (TypeError, ValueError):
            iscore = None
        entry = {
            'node_id': iid,
            'node_name': str(row.get('iname') or iid),
            'score': iscore,
        }
        if single_eq:
            tabs_map[single_eq]['indicators'].append(entry)
        else:
            _orphan_tab()['indicators'].append(entry)

    eq_recs = list(tabs_map.values())
    if unassigned and (unassigned['projects'] or unassigned['indicators']):
        eq_recs.append(unassigned)
    if from_rd_recs:
        eq_recs = _merge_equipment_tab_recs(from_rd_recs, eq_recs)
    return {'equipment_recommendations': eq_recs, 'equipment_tabs': eq_recs}


@flgl_method_bp.route('/method/<method_id>/equipment-tabs', methods=['GET'])
def method_equipment_tabs(method_id):
    """读取已保存方法的按设备分组关联（项目/指标）。"""
    mid = (method_id or '').strip()
    if not mid:
        return jsonify({'success': False, 'error': 'method_id 不能为空'}), 400
    try:
        payload = _equipment_tabs_payload_for_method(mid)
        return jsonify({'success': True, 'data': payload})
    except Exception as exc:
        logger.error('[method-equipment-tabs] failed: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500


@flgl_method_bp.route('/method-recommend-relations', methods=['POST'])
def method_recommend_relations():
    """基于当前方法内容 LLM 推荐关联节点（装备/项目/指标，可按设备 scoped）。"""
    data = request.get_json(silent=True) or {}
    form_method = data.get('method') if isinstance(data.get('method'), dict) else {}
    equipment_ids = [
        str(x).strip() for x in (data.get('equipment_ids') or []) if str(x).strip()
    ]
    document_dir = (data.get('document_dir') or '').strip()
    method_index = int(data.get('method_index') or 0)
    method_name = (data.get('method_name') or str(form_method.get('name') or '')).strip()

    name = str(form_method.get('name') or '').strip()
    synr = str(form_method.get('synr') or form_method.get('SYNR') or '').strip()
    if not name and not synr:
        return jsonify({'success': False, 'error': '请填写方法名称或 SYNR'}), 400

    checkpoint_method, source_name = _load_method_from_checkpoint(
        document_dir, method_index, method_name,
    )
    method_item = _build_method_item_for_recall(form_method, checkpoint_method)

    neo4j_driver = (
        current_app.config.get('FLGL_TESTMETHOD_NEO4J_DRIVER')
        or current_app.config.get('NEO4J_DRIVER')
    )
    try:
        from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
            _enrich_single_method_with_recall,
            recall_method_scoped_for_equipment,
        )

        enriched = _enrich_single_method_with_recall(method_item, recall_phase='full')
        if equipment_ids:
            gm = enriched.get('graph_matches') or enriched.get('graph_matches_embedding') or {}
            enriched['graph_matches'] = _ensure_equipment_in_graph_matches(gm, equipment_ids)
            enriched = recall_method_scoped_for_equipment(
                enriched,
                equipment_ids,
                neo4j_driver=neo4j_driver,
            )
    except Exception as exc:
        logger.error('[method-recommend-relations] failed: %s', exc, exc_info=True)
        return jsonify({'success': False, 'error': str(exc)}), 500

    formatted = _normalize_method_for_panel(
        enriched,
        source_name=source_name or str(form_method.get('source_name') or ''),
        phase='scoped',
        method_index=method_index,
        document_dir=document_dir,
    )
    return jsonify({'success': True, 'data': formatted})


@flgl_method_bp.route('/method-extract-auto/stream', methods=['POST', 'OPTIONS'])
def method_extract_auto_stream():
    """LLM 自动提取 SSE 流：逐条 method_ready 推送。"""
    if request.method == 'OPTIONS':
        return '', 200

    object_name_form = (request.form.get('object_name') or '').strip()
    if 'file' not in request.files and not object_name_form:
        return jsonify({"success": False, "error": "没有文件"}), 400

    source_file_id = (request.form.get('source_file_id') or 'WJ_auto0001').strip()
    user_id = (request.form.get('user_id') or 'admin').strip()
    draft_method_id = (request.form.get('draft_method_id') or request.form.get('method_id') or '').strip()
    doc_class_hint = (request.form.get('doc_class_hint') or request.form.get('document_class') or '').strip()
    skip_recall = str(request.form.get('skip_recall', '0')).lower() in ('1', 'true', 'yes', 'on')
    use_chroma_source = str(request.form.get('use_chroma_source', '0')).lower() in ('1', 'true', 'yes', 'on')

    tmp_dir = tempfile.mkdtemp()
    try:
        filepath, ext, original_filename = _resolve_method_auto_extract_file(
            tmp_dir,
            source_file_id=source_file_id,
            object_name=object_name_form,
            use_chroma_source=use_chroma_source,
        )
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as dl_exc:
        return jsonify({"success": False, "error": f"从 MinIO 下载文件失败: {dl_exc}"}), 500

    job_id = uuid.uuid4().hex
    event_queue: queue.Queue = queue.Queue()
    ctx: Dict[str, str] = {"source_name": original_filename, "draft_method_id": draft_method_id, "document_dir": ""}
    flask_app = current_app._get_current_object()
    equipment_display_mode = str(
        request.form.get("equipment_display_mode") or "inline"
    ).strip() or "inline"

    def on_event(event_type: str, evt_data: dict) -> None:
        event_queue.put((event_type, evt_data))

    def run_extract_worker() -> None:
        saved_backend_config = None
        worker_draft_id = draft_method_id
        try:
            with flask_app.app_context():
                if not worker_draft_id:
                    try:
                        from .routes import _generate_id
                        worker_draft_id = _generate_id('TestMethod')
                    except Exception:
                        worker_draft_id = f"TM_{uuid.uuid4().hex[:8]}"
                ctx["draft_method_id"] = worker_draft_id

                _f = os.path.abspath(__file__)
                _backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(_f)))
                output_root = os.path.join(_backend_dir, "output_json", "word_llm_auto")

                use_outline = _is_outline_document(filepath, ext, doc_class_hint=doc_class_hint)
                if use_outline and ext == '.pdf':
                    use_outline = False

                if use_outline:
                    from blueprints.FLGL.extraction.config import get_extractor_version
                    from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
                        inject_flask_runtime_into_word_llm,
                        run_word_llm_extract,
                    )

                    inject_flask_runtime_into_word_llm(flask_app)
                    use_v2 = get_extractor_version() == "v2"

                    def _on_evt(et: str, ed: dict) -> None:
                        on_event(
                            et,
                            _stream_normalize_method_ready(
                                et, ed, ctx, worker_draft_id, job_id, original_filename,
                            ),
                        )

                    if use_v2:
                        try:
                            from blueprints.FLGL.extraction.llm.pipeline import (
                                LLMExtractError,
                                run_llm_extract_v2_stream,
                            )

                            out = run_llm_extract_v2_stream(
                                filepath,
                                source_file_id=source_file_id,
                                user_id=user_id,
                                output_root=output_root,
                                skip_recall=skip_recall,
                                job_id=job_id,
                                on_event=_on_evt,
                                equipment_display_mode=equipment_display_mode,
                            )
                            ctx["document_dir"] = str(out.get("document_dir") or "")
                        except Exception as v2_exc:
                            logger.warning(
                                "[method-extract-auto] v2 失败，降级 v1: %s", v2_exc, exc_info=True
                            )
                            on_event("extract_fallback", {
                                "job_id": job_id,
                                "reason": str(v2_exc)[:500],
                                "to": "v1",
                            })
                            use_v2 = False

                    if not use_v2:
                        out = run_word_llm_extract(
                            filepath,
                            source_file_id=source_file_id,
                            user_id=user_id,
                            output_root=output_root,
                            skip_recall=skip_recall,
                            on_event=_on_evt,
                            recall_phase="full",
                            job_id=job_id,
                        )
                    extraction_data = out.get("extraction_data") or {}
                    ctx["source_name"] = str(
                        (extraction_data.get("source_file") or {}).get("file_name") or original_filename
                    )
                    ctx["document_dir"] = str(out.get("document_dir") or ctx.get("document_dir") or "")
                else:
                    on_event("job_started", {
                        "job_id": job_id,
                        "document_dir": "",
                        "extract_engine": "pipeline",
                    })
                    from backend.config import Config as MainConfig
                    import numpy as np
                    if not hasattr(np, 'sctypes'):
                        np.sctypes = {
                            'int': [np.int8, np.int16, np.int32, np.int64],
                            'uint': [np.uint8, np.uint16, np.uint32, np.uint64],
                            'float': [np.float16, np.float32, np.float64],
                            'complex': [np.complex64, np.complex128],
                            'others': [np.bool_, np.object_, np.str_, np.bytes_],
                        }
                    _flgl_dir = os.path.dirname(os.path.abspath(__file__))
                    _gtm_root = os.path.join(_flgl_dir, 'GetTestMethod')
                    if _flgl_dir not in sys.path:
                        sys.path.insert(0, _flgl_dir)
                    if _gtm_root not in sys.path:
                        sys.path.insert(0, _gtm_root)

                    import importlib.util
                    method_backend_config_path = os.path.join(_gtm_root, 'backend', 'config.py')
                    spec = importlib.util.spec_from_file_location('backend.config', method_backend_config_path)
                    method_backend_config = importlib.util.module_from_spec(spec)
                    saved_backend_config = sys.modules.get('backend.config')
                    sys.modules['backend.config'] = method_backend_config
                    assert spec and spec.loader
                    spec.loader.exec_module(method_backend_config)

                    MethodConfig = method_backend_config.Config
                    MethodConfig.OPENAI_CLIENT = flask_app.config.get('OPENAI_CLIENT')
                    MethodConfig.OPENAI_MODEL = (
                        getattr(MainConfig, "FLGL_TESTMETHOD_OPENAI_MODEL", None)
                        or flask_app.config.get('OPENAI_MODEL', getattr(MethodConfig, 'OPENAI_MODEL', None))
                    )
                    MethodConfig.NEO4J_DRIVER = (
                        flask_app.config.get('FLGL_TESTMETHOD_NEO4J_DRIVER')
                        or flask_app.config.get('NEO4J_DRIVER')
                    )
                    MethodConfig.CHROMA_CLIENT = flask_app.config.get('CHROMA_CLIENT')
                    MethodConfig.CHROMA_COLLECTION_OBJ = flask_app.config.get('CHROMA_COLLECTION_OBJ')
                    MethodConfig.MINIO_CLIENT = flask_app.config.get('MINIO_CLIENT')
                    _bge = (getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_MODEL_PATH', None) or '').strip()
                    MethodConfig.BGE_MODEL_PATH = os.path.abspath(_bge) if _bge else None
                    MethodConfig.BGE_LOCAL_FILES_ONLY = bool(getattr(MainConfig, 'FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY', True))
                    MethodConfig.TESTMETHOD_RETRIEVER = None

                    from GetTestMethod.pipeline import run_extract_only, run_recall_only

                    _method_ocr_tmp = os.path.join(_backend_dir, "output_json", "ocr_tmp")
                    extract_result = run_extract_only(
                        file_path=filepath,
                        source_file_id=source_file_id,
                        user_id=user_id,
                        output_root=os.path.join(tmp_dir, 'output'),
                        ocr_output_dir=_method_ocr_tmp,
                    )
                    extraction_data = extract_result.get('extraction_data', {}) or {}
                    methods = list(extraction_data.get('methods') or [])
                    source_name = str((extraction_data.get('source_file') or {}).get('file_name') or original_filename)
                    document_dir = os.path.dirname(extract_result.get('extraction_checkpoint_path') or '') or ''
                    ctx["source_name"] = source_name
                    ctx["document_dir"] = document_dir

                    recall_result = None
                    checkpoint_path = extract_result.get('extraction_checkpoint_path')
                    if checkpoint_path and not skip_recall:
                        try:
                            recall_result = run_recall_only(
                                extraction_checkpoint_path=checkpoint_path,
                                source_file_id=source_file_id,
                                user_id=user_id,
                                output_root=os.path.join(tmp_dir, 'output'),
                                topk_config={
                                    'equipment': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_EQUIPMENT', 3),
                                    'environment': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_ENVIRONMENT', 3),
                                    'test_project': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT', 2),
                                    'indicator': _flgl_cfg_int(MainConfig, 'FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR', 2),
                                },
                                save_method_json=False,
                            )
                        except Exception as recall_e:
                            logger.warning("[method-extract-auto/stream] 召回失败: %s", recall_e)

                    enriched_from_recall = []
                    if recall_result and isinstance(recall_result.get('result'), dict):
                        enriched_from_recall = recall_result.get('result', {}).get('methods') or []

                    from .method_section_enrich import generate_method_description_llm, label_from_source_filename

                    method_label = label_from_source_filename(source_name)
                    parsed_path = extract_result.get('parsed_document_path') or ''
                    parsed_doc: dict = {}
                    if parsed_path and os.path.isfile(parsed_path):
                        try:
                            with open(parsed_path, "r", encoding="utf-8") as pf:
                                parsed_doc = json.load(pf)
                        except (OSError, json.JSONDecodeError):
                            parsed_doc = {}
                    trace_full_text, trace_all_tables, trace_all_figures = _trace_context_from_parsed(extraction_data, parsed_doc)
                    gallery_html = _ocr_figure_gallery_html_from_parsed_doc(parsed_path, worker_draft_id, user_id)

                    for index, method in enumerate(methods):
                        if not isinstance(method, dict):
                            continue
                        graph_matches = {}
                        if index < len(enriched_from_recall) and isinstance(enriched_from_recall[index], dict):
                            gm = enriched_from_recall[index].get('graph_matches')
                            if isinstance(gm, dict):
                                graph_matches = gm
                        method_with_gm = dict(method)
                        method_with_gm["graph_matches"] = graph_matches
                        method_with_gm["extract_source"] = "pipeline"
                        if graph_matches.get("equipment"):
                            method_with_gm["equipment_candidates"] = [
                                {
                                    "node_id": str(e.get("id") or ""),
                                    "node_name": str(e.get("name") or ""),
                                    "score": float(e.get("score") or 0.5),
                                }
                                for e in graph_matches.get("equipment") or []
                                if isinstance(e, dict) and e.get("id")
                            ]
                        current_name = str(method.get('name') or '').strip()
                        synr_traced = _apply_synr_document_trace(
                            _method_parts_to_synr(method, gallery_html if index == 0 else ''),
                            full_text=trace_full_text,
                            all_tables=trace_all_tables,
                            all_figures=trace_all_figures,
                        )
                        method_with_gm["synr"] = synr_traced
                        desc = str(method.get('description') or method.get('summary') or '').strip()
                        if not desc:
                            desc = generate_method_description_llm(
                                current_name,
                                content_html=synr_traced,
                                source_name=source_name,
                            )
                        method_with_gm["description"] = desc
                        method_with_gm["type"] = method.get('type', '')
                        panel = _normalize_method_for_panel(
                            method_with_gm,
                            source_name=source_name,
                            draft_method_id=worker_draft_id,
                            phase="extract",
                            method_index=index + 1,
                            job_id=job_id,
                            document_dir=document_dir,
                        )
                        panel["label"] = method_label
                        on_event("method_ready", {
                            "job_id": job_id,
                            "method_index": index + 1,
                            "method": panel,
                        })

                    on_event("done", {
                        "job_id": job_id,
                        "methods_count": len(methods),
                        "document_dir": document_dir,
                        "draft_method_id": worker_draft_id,
                        "source_name": source_name,
                        "extract_engine": "pipeline",
                    })
        except Exception as exc:
            logger.error("[method-extract-auto/stream] 失败: %s", exc)
            on_event("error", {"job_id": job_id, "message": str(exc)})
        finally:
            if saved_backend_config is not None:
                sys.modules['backend.config'] = saved_backend_config
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            event_queue.put(None)

    thread = threading.Thread(target=run_extract_worker, daemon=True)
    thread.start()

    def generate():
        yield ": FLGL-method-stream\n\n"
        yield ":" + (" " * 2048) + "\n\n"
        while True:
            item = event_queue.get()
            if item is None:
                break
            event_type, evt_data = item
            yield _sse_pack_flgl(event_type, {**evt_data, "job_id": job_id})

    return Response(
        generate(),
        mimetype="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _stream_normalize_method_ready(
    event_type: str,
    evt_data: dict,
    ctx: Dict[str, str],
    draft_method_id: str,
    job_id: str,
    fallback_source_name: str,
) -> dict:
    """word_llm on_event 回调：将 method_ready 中的 raw method 转为 panel 结构。"""
    if event_type != "method_ready":
        if event_type == "job_started":
            ctx["document_dir"] = str(evt_data.get("document_dir") or "")
        if event_type == "done":
            ctx["source_name"] = str(evt_data.get("source_name") or ctx.get("source_name") or fallback_source_name)
            ctx["document_dir"] = str(evt_data.get("document_dir") or ctx.get("document_dir") or "")
        return evt_data
    raw = evt_data.get("method")
    method_raw = raw if isinstance(raw, dict) else {}
    mi = int(evt_data.get("method_index") or method_raw.get("method_index") or 0)
    source_name = ctx.get("source_name") or fallback_source_name
    document_dir = ctx.get("document_dir") or str(evt_data.get("document_dir") or "")
    has_eq_recs = bool(method_raw.get("equipment_recommendations"))
    panel = _normalize_method_for_panel(
        method_raw,
        source_name=source_name,
        draft_method_id=draft_method_id,
        phase="scoped" if has_eq_recs else "extract",
        method_index=mi,
        job_id=job_id,
        document_dir=document_dir,
    )
    return {**evt_data, "method": panel, "method_index": mi, "job_id": job_id}


from .guided_method_extract import register_guided_extract_routes

register_guided_extract_routes(flgl_method_bp)
