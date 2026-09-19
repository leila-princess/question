"""文件审核页 API — 注册到 wjgl1 blueprint。"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import fitz
from flask import jsonify, request

from config import Config
from utils.operation_log import log_user_action

from Database.minIo.main import update_object_metadata

from .document_parse_service import (
    load_full_document_html,
    load_parse_artifacts,
    merge_section_html,
    parse_local_file,
    save_parse_artifacts,
    _parse_minio_key,
)
from .graph_link_helper import build_graph_link_candidates
from .semantic_tree_service import (
    build_fallback_semantic_tree,
    build_semantic_tree,
    collect_node_section_ids,
    find_tree_node,
)

logger = logging.getLogger(__name__)

_AUTO_PARSE_SYSTEM = (
    "你是文档元数据提取专家。请从文档文本中提取以下信息，返回纯JSON：\n"
    "1. abstract: 文档摘要，200字以内\n"
    "2. keywords: 3-5个关键词\n"
    "3. std_code: 标准号/文档标识，如 GB/T 4208-2017\n"
    "4. std_name: 标准名称全称（标题）\n"
    "5. publish_date: 颁布/发布日期，格式 YYYY-MM-DD，找不到则空字符串\n"
    "6. issuer: 颁布/发布单位全称，找不到则空字符串\n"
    'JSON格式：{"abstract":"","keywords":[],"std_code":"","std_name":"",'
    '"publish_date":"","issuer":""}'
)


def _parse_doc_id_param(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("WJ_"):
        s = s[4:]
    return s


def _file_node_id(doc_id: str) -> str:
    return f"WJ_{doc_id}" if doc_id else ""


def _stat_meta_to_dict(stat) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    if not stat or not stat.metadata:
        return meta
    for k, v in stat.metadata.items():
        lk = k.lower() if isinstance(k, str) else k
        if v:
            try:
                meta[lk] = unquote(v)
            except Exception:
                meta[lk] = v
        else:
            meta[lk] = v
    return meta


def _find_minio_object_by_doc_id(doc_id: str, category: str = "") -> Optional[str]:
    if not Config.MINIO_CLIENT or not doc_id:
        return None
    from .routes import TYPE_PREFIX_MAP

    prefixes = [f"{category}/"] if category else [f"{cat}/" for cat in TYPE_PREFIX_MAP.keys()]
    for prefix in prefixes:
        try:
            for obj in Config.MINIO_CLIENT.list_objects(
                Config.MINIO_BUCKET_NAME, prefix=prefix, recursive=True
            ):
                on = obj.object_name.replace("\\", "/")
                if f"/{doc_id}/origin." in on:
                    return on
        except Exception as e:
            logger.warning("list_objects failed prefix=%s: %s", prefix, e)
    return None


def _minio_meta_file_dict(meta: Dict[str, str], object_name: str, size: int = 0) -> Dict[str, Any]:
    doc_id = meta.get("x-amz-meta-doc-id", "")
    std_name = meta.get("x-amz-meta-std-name", "")
    title = meta.get("x-amz-meta-title", "") or std_name
    keywords = meta.get("x-amz-meta-keywords", "")
    kw_list = [k.strip() for k in re.split(r"[,，;；]", keywords) if k.strip()] if keywords else []
    ext = object_name.rsplit(".", 1)[-1].lower() if object_name else ""
    return {
        "object_name": object_name,
        "doc_id": doc_id,
        "file_node_id": _file_node_id(doc_id),
        "title": title,
        "std_name": std_name,
        "std_code": meta.get("x-amz-meta-std-code", ""),
        "publish_date": meta.get("x-amz-meta-publish-date", ""),
        "issuer": meta.get("x-amz-meta-issuer", ""),
        "abstract": meta.get("x-amz-meta-abstract", ""),
        "keywords": kw_list,
        "keywords_str": keywords,
        "ics": meta.get("x-amz-meta-file-ics", ""),
        "ccs": meta.get("x-amz-meta-file-ccs", ""),
        "category": meta.get("x-amz-meta-category", ""),
        "file_type": meta.get("x-amz-meta-file-type", ext),
        "file_label": meta.get("x-amz-meta-file-label", ""),
        "upload_userid": meta.get("x-amz-meta-upload-userid", ""),
        "upload_time": meta.get("x-amz-meta-upload-time", ""),
        "file_size": size or int(meta.get("x-amz-meta-file-size", "0") or 0),
        "review_status": meta.get("x-amz-meta-review-status", "draft"),
    }


def _get_neo4j_file_props(file_node_id: str) -> Dict[str, Any]:
    if not Config.NEO4J_DRIVER or not file_node_id:
        return {}
    try:
        with Config.NEO4J_DRIVER.session() as session:
            rec = session.run(
                "MATCH (f:File {Id: $id}) RETURN f AS f",
                id=file_node_id,
            ).single()
            if not rec:
                return {}
            node = dict(rec["f"])
            if node.get("Graph_link_candidates"):
                try:
                    node["graph_link_candidates"] = json.loads(node["Graph_link_candidates"])
                except Exception:
                    node["graph_link_candidates"] = {}
            return node
    except Exception as e:
        logger.warning("neo4j file props failed: %s", e)
        return {}


def _extract_text_sample_from_minio(object_name: str, max_len: int = 4000) -> str:
    if not Config.MINIO_CLIENT or not object_name:
        return ""
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, os.path.basename(object_name))
    try:
        Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, tmp_path)
        ext = os.path.splitext(tmp_path)[1].lower()
        if ext == ".pdf":
            doc = fitz.open(tmp_path)
            parts = []
            for page in doc:
                parts.append(page.get_text() or "")
                if len("".join(parts)) > max_len:
                    break
            doc.close()
            return "".join(parts)[:max_len]
        if ext in (".docx", ".doc"):
            import docx
            doc = docx.Document(tmp_path)
            return "\n".join(p.text for p in doc.paragraphs)[:max_len]
    except Exception as e:
        logger.warning("extract text sample failed: %s", e)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass
    return ""


def _llm_extract_metadata(text_sample: str, *, max_chars: int = 3500) -> Dict[str, Any]:
    empty = {
        "abstract": "", "keywords": [],
        "std_code": "", "std_name": "", "publish_date": "", "issuer": "",
    }
    if not Config.OPENAI_CLIENT or not (text_sample or "").strip():
        return empty
    try:
        resp = Config.OPENAI_CLIENT.chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _AUTO_PARSE_SYSTEM},
                {"role": "user", "content": f"请分析以下文档内容并提取元数据：\n\n{text_sample[:max_chars]}"},
            ],
            temperature=0.1,
            max_tokens=700,
        )
        content = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return empty
        parsed = json.loads(m.group())
        return {
            "abstract": parsed.get("abstract", ""),
            "keywords": parsed.get("keywords", []) or [],
            "std_code": parsed.get("std_code", ""),
            "std_name": parsed.get("std_name", ""),
            "publish_date": parsed.get("publish_date", ""),
            "issuer": parsed.get("issuer", ""),
        }
    except Exception as e:
        logger.warning("LLM metadata extract failed: %s", e)
        return empty


def _update_minio_metadata(object_name: str, updates: Dict[str, str]) -> None:
    if not Config.MINIO_CLIENT or not updates:
        return
    from Database.minIo.main import prepare_minio_metadata, to_minio_meta_value

    max_lens = {
        "abstract": 500,
        "title": 200,
        "std_name": 200,
        "std_code": 50,
        "publish_date": 32,
        "issuer": 100,
        "keywords": 100,
    }
    safe: Dict[str, str] = {}
    for field, val in updates.items():
        if val is None:
            continue
        s = str(val)
        if field in max_lens:
            s = s[: max_lens[field]]
        safe[field] = s
    if not safe:
        return

    try:
        update_object_metadata(
            Config.MINIO_CLIENT,
            Config.MINIO_BUCKET_NAME,
            object_name,
            safe,
        )
    except Exception as copy_err:
        logger.warning("copy_object metadata failed, fallback fput: %s", copy_err)
        stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
        allowed_keys = {
            'x-amz-meta-doc-id', 'x-amz-meta-title', 'x-amz-meta-category',
            'x-amz-meta-file-type', 'x-amz-meta-upload-time', 'x-amz-meta-file-size',
            'x-amz-meta-checksum', 'x-amz-meta-upload-userid', 'x-amz-meta-file-label',
            'x-amz-meta-abstract', 'x-amz-meta-keywords', 'x-amz-meta-std-code',
            'x-amz-meta-std-name', 'x-amz-meta-publish-date', 'x-amz-meta-issuer',
            'x-amz-meta-review-status', 'x-amz-meta-file-ics', 'x-amz-meta-file-ccs',
            'x-amz-meta-graph-link-status',
        }
        existing: Dict[str, str] = {}
        if stat.metadata:
            for k, v in stat.metadata.items():
                if v is None:
                    continue
                key = str(k).lower()
                if key not in allowed_keys:
                    continue
                try:
                    existing[str(k).lower()] = unquote(str(v))
                except Exception:
                    existing[str(k).lower()] = str(v)
        field_to_key = {
            "title": "x-amz-meta-title",
            "std_code": "x-amz-meta-std-code",
            "std_name": "x-amz-meta-std-name",
            "publish_date": "x-amz-meta-publish-date",
            "issuer": "x-amz-meta-issuer",
            "abstract": "x-amz-meta-abstract",
            "keywords": "x-amz-meta-keywords",
        }
        for field, val in safe.items():
            mk = field_to_key.get(field)
            if mk:
                existing[mk] = to_minio_meta_value(val, max_len=max_lens.get(field, 200))
        import tempfile as _tf
        tmp_path = os.path.join(_tf.gettempdir(), f"wjgl_meta_{os.getpid()}.bin")
        try:
            Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, tmp_path)
            Config.MINIO_CLIENT.fput_object(
                Config.MINIO_BUCKET_NAME,
                object_name,
                tmp_path,
                metadata=prepare_minio_metadata(existing),
            )
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass


def _update_neo4j_file(file_node_id: str, props: Dict[str, Any]) -> None:
    if not Config.NEO4J_DRIVER or not file_node_id:
        return
    allowed = (
        "Name", "Abstract", "Keywords", "ICS", "CCS",
        "Std_code", "Std_name", "Publish_date", "Issuer",
        "Review_status", "Graph_link_status", "Graph_link_candidates",
        "Graph_link_count", "Confirmed_links",
    )
    sets = []
    params: Dict[str, Any] = {"id": file_node_id}
    for k, v in props.items():
        if k not in allowed:
            continue
        pk = k.lower()
        sets.append(f"f.{k} = ${pk}")
        params[pk] = v
    if not sets:
        return
    cypher = "MATCH (f:File {Id: $id}) SET " + ", ".join(sets)
    try:
        with Config.NEO4J_DRIVER.session() as session:
            session.run(cypher, **params)
    except Exception as e:
        logger.error("update neo4j file failed: %s", e)


def _sections_minio_key(category: str, doc_id: str) -> str:
    return f"{category}/{doc_id}/parse/sections.json"


def _semantic_tree_minio_key(category: str, doc_id: str) -> str:
    return f"{category}/{doc_id}/parse/semantic_tree.json"


def _parse_sections_from_local_path(local_path: str, fast: bool = False) -> Dict[str, Any]:
    try:
        return parse_local_file(local_path, fast=fast)
    except Exception as e:
        logger.warning("parse sections from path failed: %s", e)
        return {"sections": [], "error": str(e)}


def _parse_sections_from_origin(origin_object: str, category: str = "", doc_id: str = "") -> Dict[str, Any]:
    if not Config.MINIO_CLIENT:
        return {"sections": []}
    cached = load_parse_artifacts(category, doc_id) if category and doc_id else None
    if cached and cached.get("sections"):
        return cached
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, os.path.basename(origin_object))
    try:
        Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, origin_object, tmp_path)
        data = parse_local_file(tmp_path)
        if category and doc_id:
            save_parse_artifacts(category, doc_id, data)
        return data
    except Exception as e:
        logger.warning("parse sections failed: %s", e)
        return {"sections": [], "error": str(e)}
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass


def _load_parsed_document(category: str, doc_id: str) -> Dict[str, Any]:
    if not Config.MINIO_CLIENT or not category or not doc_id:
        return {}
    key = f"{category}/{doc_id}/parse/parsed_document.json"
    try:
        resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, key)
        raw = json.loads(resp.read().decode("utf-8"))
        resp.close()
        resp.release_conn()
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _load_sections(category: str, doc_id: str, origin_object: str) -> Dict[str, Any]:
    cached = load_parse_artifacts(category, doc_id)
    if cached:
        return cached
    return {"sections": [], "node_slices": {}, "full_html": "", "document_blocks": []}


def _load_semantic_tree(category: str, doc_id: str, origin_object: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    if Config.MINIO_CLIENT and category and doc_id:
        key = _semantic_tree_minio_key(category, doc_id)
        try:
            resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, key)
            data = json.loads(resp.read().decode("utf-8"))
            resp.close()
            resp.release_conn()
            if data.get("root"):
                return data
        except Exception:
            pass
    parsed = _load_sections(category, doc_id, origin_object)
    return build_fallback_semantic_tree(parsed, meta)


def _resolve_graph_links_for_file(file_data: Dict[str, Any], neo: Dict[str, Any]) -> Dict[str, Any]:
    stored = neo.get("graph_link_candidates") or {}
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except Exception:
            stored = {}
    if stored.get("candidates"):
        return stored
    try:
        return build_graph_link_candidates(file_data, Config.NEO4J_DRIVER)
    except Exception as e:
        logger.warning("inline graph link candidates failed: %s", e)
        return stored or {"candidates": []}


def register_review_routes(bp):
    @bp.route("/preview-content", methods=["POST"])
    def preview_content():
        """上传前预览：高保真解析章节（不入库）。"""
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            return jsonify({"success": False, "error": "请提供 file"}), 400
        fast = request.form.get("fast") == "1"
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, uploaded.filename)
        try:
            uploaded.save(tmp_path)
            data = _parse_sections_from_local_path(tmp_path, fast=fast)
            meta: Dict[str, Any] = {}
            body_meta = request.form.get("meta")
            if body_meta:
                try:
                    meta = json.loads(body_meta)
                except Exception:
                    meta = {}
            data["semantic_tree"] = build_fallback_semantic_tree(data, meta)
            return jsonify({"success": True, "data": data})
        except Exception as e:
            logger.error("preview_content failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

    @bp.route("/preview-semantic-tree", methods=["POST"])
    def preview_semantic_tree():
        """上传前：JSON 仅 LLM 树；file 上传时 skip_llm=1 仅 fallback。"""
        body = request.get_json(silent=True)
        if body:
            parsed = {
                "sections": body.get("sections") or [],
                "section_catalogue": body.get("section_catalogue") or [],
                "full_text": body.get("full_text") or "",
            }
            meta = body.get("meta") or {}
            tree = build_semantic_tree(parsed, meta, use_llm=body.get("use_llm", True))
            return jsonify({"success": True, "data": {"semantic_tree": tree}})

        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            return jsonify({"success": False, "error": "请提供 file 或 JSON"}), 400
        body_meta = request.form.get("meta")
        meta: Dict[str, Any] = {}
        if body_meta:
            try:
                meta = json.loads(body_meta)
            except Exception:
                meta = {}
        skip_llm = request.form.get("skip_llm") == "1"
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, uploaded.filename)
        try:
            uploaded.save(tmp_path)
            parsed = _parse_sections_from_local_path(tmp_path)
            tree = build_semantic_tree(parsed, meta, use_llm=not skip_llm)
            return jsonify({
                "success": True,
                "data": {
                    "semantic_tree": tree,
                    "sections": parsed.get("sections") or [],
                    "node_slices": parsed.get("node_slices") or {},
                },
            })
        except Exception as e:
            logger.error("preview_semantic_tree failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

    @bp.route("/build-semantic-tree", methods=["POST"])
    def build_semantic_tree_api():
        body = request.get_json(silent=True) or {}
        parsed = {
            "sections": body.get("sections") or [],
            "full_text": body.get("full_text") or "",
        }
        meta = body.get("meta") or {}
        use_llm = body.get("use_llm", True)
        try:
            tree = build_semantic_tree(parsed, meta, use_llm=use_llm)
            return jsonify({"success": True, "data": {"semantic_tree": tree}})
        except Exception as e:
            logger.error("build_semantic_tree_api failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/preview-graph-links", methods=["POST"])
    def preview_graph_links():
        """上传前预览：根据元数据推荐图库关联节点。"""
        body = request.get_json(silent=True) or {}
        doc = {
            "std_name": body.get("std_name") or body.get("title") or "",
            "std_code": body.get("std_code") or "",
            "abstract": body.get("abstract") or "",
            "keywords": body.get("keywords") or [],
            "title": body.get("title") or "",
        }
        try:
            result = build_graph_link_candidates(doc, Config.NEO4J_DRIVER)
            return jsonify({"success": True, "data": result})
        except Exception as e:
            logger.error("preview_graph_links failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/auto-parse-text", methods=["POST"])
    def auto_parse_text():
        """用已提取正文（如 MinerU Markdown）补全/修正元数据。"""
        from .llm_diagnostics import wjgl_llm_status

        try:
            body = request.get_json(silent=True) or {}
            text = (body.get("text") or body.get("markdown") or "").strip()
            warnings: list[str] = []
            if not Config.OPENAI_CLIENT:
                warnings.append(wjgl_llm_status()["hint"])
                return jsonify({
                    "success": True,
                    "data": {
                        "abstract": body.get("abstract") or "",
                        "keywords": body.get("keywords") or [],
                        "std_code": body.get("std_code") or "",
                        "std_name": body.get("std_name") or "",
                        "publish_date": body.get("publish_date") or "",
                        "issuer": body.get("issuer") or "",
                        "llm_used": False,
                        "warnings": warnings,
                    },
                })

            if not text:
                warnings.append("正文为空，无法调用 LLM。")
                return jsonify({
                    "success": True,
                    "data": {
                        "abstract": body.get("abstract") or "",
                        "keywords": body.get("keywords") or [],
                        "std_code": body.get("std_code") or "",
                        "std_name": body.get("std_name") or "",
                        "publish_date": body.get("publish_date") or "",
                        "issuer": body.get("issuer") or "",
                        "llm_used": False,
                        "warnings": warnings,
                    },
                })

            try:
                llm = _llm_extract_metadata(text, max_chars=8000)
            except Exception as e:
                logger.warning("auto_parse_text llm failed: %s", e)
                warnings.append(f"LLM 调用失败: {e}")
                llm = {}
            merged = {
                "abstract": llm.get("abstract") or body.get("abstract") or "",
                "keywords": llm.get("keywords") or body.get("keywords") or [],
                "std_code": llm.get("std_code") or body.get("std_code") or "",
                "std_name": llm.get("std_name") or body.get("std_name") or "",
                "publish_date": llm.get("publish_date") or body.get("publish_date") or "",
                "issuer": llm.get("issuer") or body.get("issuer") or "",
                "llm_used": bool(llm),
                "warnings": warnings,
            }
            if not merged["llm_used"] and not warnings:
                warnings.append("LLM 未返回有效元数据，请检查 OPENAI_MODEL 是否与 vLLM 一致。")
                merged["warnings"] = warnings
            return jsonify({"success": True, "data": merged})
        except Exception as e:
            logger.exception("auto_parse_text failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/review", methods=["GET"])
    def get_file_review(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        category = (request.args.get("category") or "").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
            meta = _stat_meta_to_dict(stat)
            file_data = _minio_meta_file_dict(meta, object_name, stat.size)
            neo = _get_neo4j_file_props(_file_node_id(doc_id))
            for nk, fk in (
                ("Std_name", "std_name"), ("Std_code", "std_code"),
                ("Publish_date", "publish_date"), ("Issuer", "issuer"),
                ("Abstract", "abstract"), ("Review_status", "review_status"),
            ):
                nv = neo.get(nk)
                if nv:
                    file_data[fk] = nv
            graph_links = _resolve_graph_links_for_file(file_data, neo)
            parsed_doc = _load_parsed_document(category, doc_id)
            document_toc: list = []
            try:
                from .document_parse_service import load_saved_toc

                document_toc = load_saved_toc(category, doc_id)
            except Exception:
                document_toc = []
            if not document_toc and neo.get("Document_toc"):
                try:
                    raw = json.loads(str(neo.get("Document_toc") or ""))
                    if isinstance(raw, list):
                        document_toc = raw
                except Exception:
                    document_toc = []
            content_ready = False
            content_source = "none"
            try:
                from .document_parse_service import load_preview_document_content, load_parse_artifacts

                preview = load_preview_document_content(category, doc_id)
                if preview and ((preview.get("full_html") or "").strip() or (preview.get("full_markdown") or "").strip()):
                    content_ready = True
                    content_source = str(preview.get("content_source") or "preview")
                else:
                    full = load_parse_artifacts(category, doc_id)
                    if full and ((full.get("full_html") or "").strip() or (full.get("full_markdown") or "").strip()):
                        content_ready = True
                        content_source = "parse_full"
            except Exception:
                content_ready = False
            return jsonify({
                "success": True,
                "data": {
                    "file": file_data,
                    "document_toc": document_toc,
                    "document_content_ready": content_ready,
                    "content_source": content_source,
                    "parse_status": neo.get("Parse_status") or "pending",
                    "graph_link_status": neo.get("Graph_link_status") or "none",
                    "graph_links": graph_links,
                    "parsed_document": parsed_doc,
                },
            })
        except Exception as e:
            logger.error("get_file_review failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/metadata", methods=["PATCH"])
    def patch_file_metadata(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        body = request.get_json(silent=True) or {}
        category = (body.get("category") or request.args.get("category") or "").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404

        keywords = body.get("keywords")
        kw_str = ""
        if isinstance(keywords, list):
            kw_str = ",".join(str(k) for k in keywords)
        elif keywords is not None:
            kw_str = str(keywords)

        updates = {}
        for field in ('title', 'std_code', 'std_name', 'publish_date', 'issuer', 'abstract', 'keywords'):
            if field in body:
                val = kw_str if field == 'keywords' else body.get(field)
                updates[field] = val if val is not None else ''

        document_toc = body.get("document_toc")
        saved_toc: list = []
        if isinstance(document_toc, list) and document_toc:
            try:
                from .document_parse_service import save_document_toc

                saved_toc = save_document_toc(category, doc_id, document_toc)
            except Exception as toc_err:
                logger.warning("save document_toc failed: %s", toc_err)

        content_saved = False
        full_html = body.get("full_html")
        full_markdown = body.get("full_markdown")
        mineru_key = (body.get("mineru_key") or "").strip()
        if full_html or full_markdown or mineru_key:
            try:
                from .document_parse_service import save_document_content

                result = save_document_content(
                    category,
                    doc_id,
                    html=str(full_html or ""),
                    markdown=str(full_markdown or ""),
                    mineru_key=mineru_key,
                )
                content_saved = bool(result.get("saved"))
            except Exception as content_err:
                logger.warning("save document content failed: %s", content_err)

        try:
            _update_minio_metadata(object_name, updates)
            std_name = updates.get("std_name") or updates.get("title") or ""
            neo_props = {
                k: v for k, v in {
                    "Name": std_name or updates.get("title") or "",
                    "Abstract": (updates.get("abstract") or "")[:500],
                    "Keywords": kw_str,
                    "Std_code": updates.get("std_code", ""),
                    "Std_name": updates.get("std_name", ""),
                    "Publish_date": updates.get("publish_date", ""),
                    "Issuer": updates.get("issuer", ""),
                }.items() if v is not None
            }
            if saved_toc:
                neo_props["Document_toc"] = json.dumps(saved_toc, ensure_ascii=False)
                neo_props["Document_toc_count"] = len(saved_toc)
            if content_saved:
                neo_props["Parse_status"] = "preview_ready"
            try:
                _update_neo4j_file(_file_node_id(doc_id), neo_props)
            except Exception as neo_err:
                logger.warning("patch_file_metadata neo4j skipped: %s", neo_err)
            log_user_action(f"文件管理-更新元数据 doc_id={doc_id}")
            return jsonify({"success": True, "data": {"doc_id": doc_id}})
        except Exception as e:
            logger.error("patch_file_metadata failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/graph-link-analyze", methods=["POST"])
    def graph_link_analyze(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        body = request.get_json(silent=True) or {}
        category = (body.get("category") or "").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        file_node_id = _file_node_id(doc_id)

        try:
            _update_neo4j_file(file_node_id, {"Graph_link_status": "analyzing"})
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
            meta = _stat_meta_to_dict(stat)
            file_data = _minio_meta_file_dict(meta, object_name, stat.size)
            neo = _get_neo4j_file_props(file_node_id)
            for nk, fk in (
                ("Abstract", "abstract"), ("Std_code", "std_code"),
                ("Std_name", "std_name"), ("Publish_date", "publish_date"),
                ("Issuer", "issuer"),
            ):
                if neo.get(nk) and not file_data.get(fk):
                    file_data[fk] = neo[nk]

            need_extract = not (
                (file_data.get("abstract") or "").strip()
                and (file_data.get("std_code") or file_data.get("std_name") or "").strip()
            )
            if need_extract:
                sample = _extract_text_sample_from_minio(object_name)
                extracted = _llm_extract_metadata(sample)
                for fk, ek in (
                    ("abstract", "abstract"), ("std_code", "std_code"),
                    ("std_name", "std_name"), ("publish_date", "publish_date"),
                    ("issuer", "issuer"),
                ):
                    if extracted.get(ek) and not file_data.get(fk):
                        file_data[fk] = extracted[ek]
                if extracted.get("keywords") and not file_data.get("keywords"):
                    file_data["keywords"] = extracted["keywords"]
                patch_updates = {k: v for k, v in {
                    "abstract": file_data.get("abstract"),
                    "std_code": file_data.get("std_code"),
                    "std_name": file_data.get("std_name"),
                    "publish_date": file_data.get("publish_date"),
                    "issuer": file_data.get("issuer"),
                    "keywords": ",".join(file_data.get("keywords") or [])
                    if isinstance(file_data.get("keywords"), list)
                    else file_data.get("keywords_str", ""),
                }.items() if v}
                if patch_updates:
                    try:
                        _update_minio_metadata(object_name, patch_updates)
                    except Exception as me:
                        logger.warning("minio metadata patch skipped: %s", me)

            result = build_graph_link_candidates(file_data, Config.NEO4J_DRIVER)
            result["file_id"] = file_node_id
            result["file_type"] = file_data.get("category", "")
            result["analyzed_at"] = datetime.now(timezone.utc).isoformat()

            _update_neo4j_file(file_node_id, {
                "Graph_link_status": "done",
                "Graph_link_candidates": json.dumps(result, ensure_ascii=False),
                "Graph_link_count": len(result.get("candidates") or []),
                "Abstract": file_data.get("abstract", ""),
                "Std_code": file_data.get("std_code", ""),
                "Std_name": file_data.get("std_name", ""),
                "Publish_date": file_data.get("publish_date", ""),
                "Issuer": file_data.get("issuer", ""),
            })
            log_user_action(
                f"文件管理-本体关联分析 doc_id={doc_id} candidates={len(result.get('candidates') or [])}"
            )
            return jsonify({"success": True, "data": {"file": file_data, "graph_links": result}})
        except Exception as e:
            logger.error("graph_link_analyze failed: %s", e)
            _update_neo4j_file(file_node_id, {"Graph_link_status": "failed"})
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/graph-link-confirm", methods=["POST"])
    def graph_link_confirm(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        body = request.get_json(silent=True) or {}
        links = list(body.get("links") or [])
        equipment_tabs = body.get("equipment_tabs") or []
        environments = body.get("environments") or []

        for tab in equipment_tabs:
            eq_id = (tab.get("equipment_id") or "").strip()
            for link in tab.get("links") or []:
                nt = (link.get("node_type") or "").strip()
                nid = (link.get("node_id") or "").strip()
                if nt and nid:
                    links.append({
                        "node_type": nt,
                        "node_id": nid,
                        "rel_type": link.get("rel_type") or "BELONGS_TO",
                        "equipment_id": eq_id,
                    })
        for env in environments:
            eid = (env.get("node_id") or env.get("id") or "").strip()
            if eid:
                links.append({
                    "node_type": "Environment",
                    "node_id": eid,
                    "rel_type": "BELONGS_TO",
                })

        file_node_id = _file_node_id(doc_id)
        if not links:
            return jsonify({"success": False, "error": "请至少选择一条关联"}), 400
        from blueprints.RelFact.neo4j_relations import merge_file_relations

        link_specs: List[Dict[str, Any]] = []
        for link in links:
            nt = (link.get("node_type") or "").strip()
            nid = (link.get("node_id") or "").strip()
            if not nt or not nid:
                continue
            rel = (link.get("rel_type") or link.get("suggested_rel") or "BELONGS_TO").strip()
            props = link.get("rel_props") or link.get("props") or {}
            from_label = (link.get("from_label") or "").strip()
            to_label = (link.get("to_label") or nt).strip()

            if rel == "APPLIES_TO":
                link_specs.append({
                    "rel_type": rel,
                    "from_label": from_label or "File",
                    "from_id": file_node_id,
                    "to_label": to_label or nt,
                    "to_id": nid,
                    "props": props,
                })
            elif rel == "CONSTRAINED_BY":
                link_specs.append({
                    "rel_type": rel,
                    "from_label": from_label or "Environment",
                    "from_id": nid,
                    "to_label": to_label or "File",
                    "to_id": file_node_id,
                    "props": props,
                })
            elif rel == "GOVERNED_BY":
                link_specs.append({
                    "rel_type": rel,
                    "from_label": from_label or "TestProject",
                    "from_id": nid,
                    "to_label": to_label or "File",
                    "to_id": file_node_id,
                    "props": props,
                })
            elif rel == "DERIVED_FROM":
                link_specs.append({
                    "rel_type": rel,
                    "from_label": from_label or "TestMethod",
                    "from_id": nid,
                    "to_label": to_label or "File",
                    "to_id": file_node_id,
                    "props": props,
                })
            else:
                link_specs.append({"node_type": nt, "node_ids": [nid], "props": props})

        merge_file_relations(file_node_id, link_specs)
        _update_neo4j_file(file_node_id, {
            "Review_status": "confirmed",
            "Confirmed_links": json.dumps(links, ensure_ascii=False),
        })
        category = (body.get("category") or "").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if object_name:
            _update_minio_metadata(object_name, {"review_status": "confirmed"})
        log_user_action(f"文件管理-确认本体关联 doc_id={doc_id} count={len(links)}")
        return jsonify({"success": True, "data": {"confirmed": len(links)}})

    @bp.route("/files/<doc_id>/local-asset", methods=["GET"])
    def get_local_parse_asset(doc_id):
        """读取 MinerU 本地 parse 目录下的图片等资源。"""
        from flask import send_file
        from pdf_extract.mineru_wjgl_service import resolve_local_parse_asset

        doc_id = _parse_doc_id_param(doc_id)
        category = (request.args.get("category") or "").strip()
        rel = (request.args.get("path") or "").strip().replace("\\", "/")
        if not category or not rel:
            return jsonify({"success": False, "error": "缺少 category 或 path"}), 400
        fpath = resolve_local_parse_asset(category, doc_id, rel)
        if not fpath:
            return jsonify({"success": False, "error": "not found"}), 404
        ext = os.path.splitext(fpath.name)[1].lower()
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png" if ext == ".png" else "application/octet-stream"
        return send_file(
            str(fpath),
            mimetype=mime,
            conditional=False,
            max_age=0,
            etag=False,
            last_modified=None,
        )

    @bp.route("/files/<doc_id>/full-document", methods=["GET"])
    def get_full_document(doc_id):
        """审核页：读取上传时已解析落盘的全文 HTML（不重复 OCR）。"""
        doc_id = _parse_doc_id_param(doc_id)
        category = (request.args.get("category") or "").strip()
        if not category:
            return jsonify({"success": False, "error": "缺少 category"}), 400

        from .document_parse_service import load_preview_document_content, load_parse_artifacts

        cached = load_preview_document_content(category, doc_id)
        content_source = "preview" if cached else "none"
        if not cached:
            cached = load_parse_artifacts(category, doc_id)
            if cached:
                content_source = "parse_full" if (cached.get("sections") or []) else "preview"

        markdown = (cached.get("full_markdown") or "").strip() if cached else ""
        if not cached or (not (cached.get("full_html") or "").strip() and not markdown):
            return jsonify({
                "success": False,
                "error": "文档结构化解析未完成，请等待上传后台处理完成",
                "code": "parse_pending",
            }), 409
        fmt = request.args.get("format", "").strip().lower()
        if fmt in ("md", "markdown"):
            from pdf_extract.mineru_wjgl_service import local_doc_dir

            if not markdown:
                md_path = local_doc_dir(category, doc_id) / "document.md"
                if md_path.is_file():
                    markdown = md_path.read_text(encoding="utf-8", errors="replace").strip()
            if not markdown:
                return jsonify({"success": False, "error": "Markdown 尚未生成"}), 404
            from flask import Response
            return Response(
                markdown,
                mimetype="text/markdown; charset=utf-8",
                headers={"X-WJGL-Success": "1", "X-WJGL-Md-Bytes": str(len(markdown.encode("utf-8")))},
            )
        html = cached.get("full_html") or ""
        if request.args.get("format", "").strip().lower() == "html":
            from flask import Response
            return Response(
                html,
                mimetype="text/html; charset=utf-8",
                headers={"X-WJGL-Success": "1", "X-WJGL-Html-Bytes": str(len(html))},
            )
        return jsonify({
            "success": True,
            "data": {
                "html": cached.get("full_html") or "",
                "markdown": markdown or (cached.get("full_markdown") or ""),
                "document_blocks": cached.get("document_blocks") or [],
                "sections": cached.get("sections") or [],
                "from_cache": True,
                "content_source": content_source,
            },
        })

    @bp.route("/files/<doc_id>/parse-asset", methods=["GET"])
    def get_parse_asset(doc_id):
        """读取 parse/ 下图片等资源（figures/...）。"""
        from flask import send_file

        doc_id = _parse_doc_id_param(doc_id)
        category = (request.args.get("category") or "").strip()
        name = (request.args.get("name") or "").strip().replace("\\", "/")
        if not category or not name:
            return jsonify({"success": False, "error": "缺少 category 或 name"}), 400
        if ".." in name or name.startswith("/"):
            return jsonify({"success": False, "error": "invalid name"}), 400
        if not Config.MINIO_CLIENT:
            return jsonify({"success": False, "error": "MinIO not initialized"}), 500
        obj_key = _parse_minio_key(category, doc_id, name)
        try:
            resp = Config.MINIO_CLIENT.get_object(Config.MINIO_BUCKET_NAME, obj_key)
            blob = resp.read()
            resp.close()
            resp.release_conn()
            ext = os.path.splitext(name)[1].lower()
            if ext == ".html":
                mime = "text/html; charset=utf-8"
            elif ext == ".png":
                mime = "image/png"
            elif ext in (".jpg", ".jpeg"):
                mime = "image/jpeg"
            else:
                mime = "application/octet-stream"
            from io import BytesIO
            return send_file(BytesIO(blob), mimetype=mime)
        except Exception as e:
            logger.warning("parse-asset miss %s: %s", obj_key, e)
            return jsonify({"success": False, "error": "not found"}), 404

    @bp.route("/files/<doc_id>/content", methods=["GET"])
    def get_file_content(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        category = (request.args.get("category") or "").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        try:
            data = _load_sections(category, doc_id, object_name)
            if not (data.get("full_html") or "").strip():
                cached = load_parse_artifacts(category, doc_id)
                if cached:
                    data["full_html"] = cached.get("full_html") or ""
                    data["document_blocks"] = cached.get("document_blocks") or []
            return jsonify({"success": True, "data": data})
        except Exception as e:
            logger.error("get_file_content failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/content", methods=["PATCH"])
    def patch_file_content(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        body = request.get_json(silent=True) or {}
        category = (body.get("category") or request.args.get("category") or "").strip()
        sections = body.get("sections")
        if not isinstance(sections, list):
            return jsonify({"success": False, "error": "sections 必须为数组"}), 400
        if not Config.MINIO_CLIENT:
            return jsonify({"success": False, "error": "MinIO not initialized"}), 500
        key = _sections_minio_key(category, doc_id)
        payload = json.dumps({"sections": sections}, ensure_ascii=False).encode("utf-8")
        from io import BytesIO
        Config.MINIO_CLIENT.put_object(
            Config.MINIO_BUCKET_NAME,
            key,
            BytesIO(payload),
            length=len(payload),
            content_type="application/json",
        )
        log_user_action(f"文件管理-更新章节内容 doc_id={doc_id} sections={len(sections)}")
        return jsonify({"success": True, "data": {"section_count": len(sections)}})

    @bp.route("/files/<doc_id>/semantic-tree", methods=["GET"])
    def get_semantic_tree(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        category = (request.args.get("category") or "").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
            meta = _minio_meta_file_dict(_stat_meta_to_dict(stat), object_name, stat.size)
            tree = _load_semantic_tree(category, doc_id, object_name, meta)
            parsed = _load_sections(category, doc_id, object_name)
            parsed_doc = _load_parsed_document(category, doc_id)
            return jsonify({
                "success": True,
                "data": {
                    "semantic_tree": tree,
                    "sections": parsed.get("sections") or [],
                    "node_slices": parsed.get("node_slices") or {},
                    "parsed_document": parsed_doc,
                },
            })
        except Exception as e:
            logger.error("get_semantic_tree failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/semantic-tree/rebuild", methods=["POST"])
    def rebuild_semantic_tree(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        body = request.get_json(silent=True) or {}
        category = (body.get("category") or request.args.get("category") or "").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
            meta = _minio_meta_file_dict(_stat_meta_to_dict(stat), object_name, stat.size)
            meta.update({k: body.get(k) for k in ("std_name", "std_code", "abstract", "title") if body.get(k)})
            parsed = _parse_sections_from_origin(object_name, category, doc_id)
            tree = build_semantic_tree(parsed, meta)
            if Config.MINIO_CLIENT:
                from io import BytesIO
                payload = json.dumps(tree, ensure_ascii=False).encode("utf-8")
                Config.MINIO_CLIENT.put_object(
                    Config.MINIO_BUCKET_NAME,
                    _semantic_tree_minio_key(category, doc_id),
                    BytesIO(payload),
                    length=len(payload),
                    content_type="application/json",
                )
            return jsonify({"success": True, "data": {"semantic_tree": tree}})
        except Exception as e:
            logger.error("rebuild_semantic_tree failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/reparse", methods=["POST"])
    def reparse_file(doc_id):
        """强制高保真重新解析（含 OCR），刷新 sections / semantic_tree。"""
        doc_id = _parse_doc_id_param(doc_id)
        body = request.get_json(silent=True) or {}
        category = (body.get("category") or request.args.get("category") or "").strip()
        force_ocr = body.get("force_ocr", True)
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        tmp_dir = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, os.path.basename(object_name))
        try:
            Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, tmp_path)
            if force_ocr:
                os.environ["FLGL_FORCE_OCR"] = "1"
            try:
                ocr_dir = os.path.join(Config.OUTPUT_DIR, "wjgl_ocr_cache", category, doc_id)
                os.makedirs(ocr_dir, exist_ok=True)
                data = parse_local_file(tmp_path, ocr_output_dir=ocr_dir)
            finally:
                os.environ.pop("FLGL_FORCE_OCR", None)
            save_parse_artifacts(category, doc_id, data)
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
            meta = _minio_meta_file_dict(_stat_meta_to_dict(stat), object_name, stat.size)
            tree = build_semantic_tree(data, meta)
            if Config.MINIO_CLIENT:
                from io import BytesIO
                payload = json.dumps(tree, ensure_ascii=False).encode("utf-8")
                Config.MINIO_CLIENT.put_object(
                    Config.MINIO_BUCKET_NAME,
                    _semantic_tree_minio_key(category, doc_id),
                    BytesIO(payload),
                    length=len(payload),
                    content_type="application/json",
                )
            log_user_action(f"文件管理-重新解析 doc_id={doc_id} ocr={force_ocr}")
            return jsonify({
                "success": True,
                "data": {
                    "sections": data.get("sections") or [],
                    "node_slices": data.get("node_slices") or {},
                    "semantic_tree": tree,
                    "parsed_document": data.get("parsed_document") or {},
                },
            })
        except Exception as e:
            logger.error("reparse_file failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

    @bp.route("/files/<doc_id>/semantic-content", methods=["GET"])
    def get_semantic_content(doc_id):
        doc_id = _parse_doc_id_param(doc_id)
        category = (request.args.get("category") or "").strip()
        node_id = (request.args.get("node_id") or "ST_ROOT").strip()
        object_name = _find_minio_object_by_doc_id(doc_id, category)
        if not object_name:
            return jsonify({"success": False, "error": "文件不存在"}), 404
        try:
            stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
            meta = _minio_meta_file_dict(_stat_meta_to_dict(stat), object_name, stat.size)
            parsed = _load_sections(category, doc_id, object_name)
            sections = parsed.get("sections") or []
            slices = parsed.get("node_slices") or {}

            node = None
            for tree_data in (
                _load_semantic_tree(category, doc_id, object_name, meta),
                build_fallback_semantic_tree(parsed, meta),
            ):
                node = find_tree_node(tree_data.get("root") or {}, node_id)
                if node:
                    break

            if not node:
                return jsonify({"success": False, "error": "节点不存在"}), 404

            sec_ids = collect_node_section_ids(node)
            html = merge_section_html(sec_ids, sections, slices)
            if not html and node.get("summary"):
                html = f"<p>{node.get('summary')}</p>"
            return jsonify({
                "success": True,
                "data": {"node_id": node_id, "title": node.get("title"), "html": html, "node": node},
            })
        except Exception as e:
            logger.error("get_semantic_content failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @bp.route("/files/<doc_id>/semantic-tree", methods=["PUT"])
    def save_semantic_tree(doc_id):
        """保存客户端生成的语义树（如 LLM 结果）到 MinIO。"""
        doc_id = _parse_doc_id_param(doc_id)
        body = request.get_json(silent=True) or {}
        category = (body.get("category") or request.args.get("category") or "").strip()
        tree = body.get("semantic_tree")
        if not tree or not tree.get("root"):
            return jsonify({"success": False, "error": "semantic_tree required"}), 400
        if not Config.MINIO_CLIENT or not category or not doc_id:
            return jsonify({"success": False, "error": "存储不可用"}), 500
        try:
            from io import BytesIO
            payload = json.dumps(tree, ensure_ascii=False).encode("utf-8")
            Config.MINIO_CLIENT.put_object(
                Config.MINIO_BUCKET_NAME,
                _semantic_tree_minio_key(category, doc_id),
                BytesIO(payload),
                length=len(payload),
                content_type="application/json",
            )
            return jsonify({"success": True})
        except Exception as e:
            logger.error("save_semantic_tree failed: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500
