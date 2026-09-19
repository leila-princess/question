"""LLM v2 提取主流水线。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..config import get_extractor_version
from ..shared.formatter import blocks_to_synr_html, completeness_check
from ..shared.output import generate_method_description, normalize_method_artifact, recall_equipment_for_method
from ..shared.parse_bundle import attach_bundle_to_context, bundle_from_extract_context
from ..shared.parser_facade import parse_document_file
from ..shared.schemas import MethodArtifact, ParseBundle, SemanticModule
from .anatomist import AnatomistError, analyze_chunk
from .chunker import format_chunk_input, plan_anatomy_chunks
from .clusterer import cluster_modules_llm
from .module_mapper import modules_to_blocks

logger = logging.getLogger(__name__)

OnEvent = Callable[[str, Dict[str, Any]], None]


class LLMExtractError(Exception):
    pass


def _emit(on_event: Optional[OnEvent], event_type: str, data: Dict[str, Any]) -> None:
    if on_event:
        on_event(event_type, data)


def run_llm_extract_v2_stream(
    file_path: str,
    *,
    source_file_id: str = "",
    user_id: str = "",
    output_root: str = "",
    skip_recall: bool = False,
    job_id: str = "",
    on_event: Optional[OnEvent] = None,
    equipment_display_mode: str = "inline",
    neo4j_driver=None,
    std_name: str = "",
    std_code: str = "",
) -> Dict[str, Any]:
    """v2 解剖师流水线；yield 通过 on_event 回调。"""
    from ..shared.llm_budget import ensure_llm_context_config

    ensure_llm_context_config()
    ext = os.path.splitext(file_path)[1].lower()
    ctx = parse_document_file(file_path, ext=ext)
    bundle: ParseBundle = ctx.get("parse_bundle") or bundle_from_extract_context(ctx, file_name=os.path.basename(file_path))
    from ..shared.line_index import LineIndex

    line_index = LineIndex.build(bundle.get("plain_text") or "")
    document_dir = ""
    if output_root:
        stem = os.path.splitext(os.path.basename(file_path))[0]
        document_dir = os.path.join(output_root, stem)
        os.makedirs(document_dir, exist_ok=True)
        with open(os.path.join(document_dir, "parse_bundle_v2.json"), "w", encoding="utf-8") as f:
            json.dump(dict(bundle), f, ensure_ascii=False, indent=2)

    _emit(on_event, "parse_ready", {"job_id": job_id, "document_dir": document_dir, "text_len": len(bundle.get("plain_text") or "")})
    _emit(on_event, "job_started", {"job_id": job_id, "document_dir": document_dir, "extract_engine": "llm_v2", "extract_version": "v2"})
    _emit(on_event, "anatomy_started", {"job_id": job_id, "chunk_count": 0})

    chunks = plan_anatomy_chunks(line_index)
    all_modules: List[SemanticModule] = []
    id_offset = 0
    for chunk in chunks:
        numbered = format_chunk_input(line_index, chunk)
        try:
            mods = analyze_chunk(
                line_index,
                numbered,
                chunk_index=chunk.chunk_index,
                content_start_line=chunk.content_start_line,
                id_offset=id_offset,
            )
        except AnatomistError as e:
            raise LLMExtractError(str(e)) from e
        all_modules.extend(mods)
        id_offset = len(all_modules)
        _emit(on_event, "anatomy_chunk_done", {
            "job_id": job_id,
            "chunk_index": chunk.chunk_index,
            "module_count": len(mods),
            "line_range": [chunk.content_start_line, chunk.content_end_line],
        })

    if document_dir:
        with open(os.path.join(document_dir, "anatomy_modules.json"), "w", encoding="utf-8") as f:
            json.dump(all_modules, f, ensure_ascii=False, indent=2)

    _emit(on_event, "cluster_started", {"job_id": job_id, "module_count": len(all_modules)})
    groups, cluster_mode = cluster_modules_llm(all_modules)
    _emit(on_event, "cluster_done", {
        "job_id": job_id,
        "cluster_mode": cluster_mode,
        "method_names": [g.get("group_name") for g in groups],
    })

    if document_dir:
        with open(os.path.join(document_dir, "method_groups.json"), "w", encoding="utf-8") as f:
            json.dump({"groups": groups, "cluster_mode": cluster_mode}, f, ensure_ascii=False, indent=2)

    mod_by_id = {m["module_id"]: m for m in all_modules}
    methods_out: List[Dict[str, Any]] = []
    method_index = 0

    for group in groups:
        method_index += 1
        mids = group.get("module_ids") or []
        group_mods = [mod_by_id[mid] for mid in mids if mid in mod_by_id]
        if not group_mods:
            continue
        blocks = modules_to_blocks(
            bundle,
            group_mods,
            equipment_display_mode=equipment_display_mode,  # type: ignore[arg-type]
        )
        block_dict = {
            "sytj": blocks.get("sytj") or "",
            "sygc": blocks.get("sygc") or "",
            "jgpd": blocks.get("jgpd") or "",
        }
        comp = completeness_check(block_dict)
        synr = blocks_to_synr_html(block_dict, extract_source="llm_v2", use_markdown=False)
        method_name = str(group.get("group_name") or f"方法{method_index}")
        plain_for_desc = "\n".join(block_dict.values())

        rec_nodes, rec_rels, eq_recs = [], [], []
        if not skip_recall:
            eq_text = "\n".join(
                h.get("text", "") for h in (blocks.get("equipment_hints") or [])
            )
            recall_text = f"{plain_for_desc}\n{eq_text}".strip()
            try:
                rec_nodes, rec_rels, eq_recs = recall_equipment_for_method(
                    method_name,
                    recall_text,
                    std_name=std_name,
                    std_code=std_code,
                    neo4j_driver=neo4j_driver,
                )
            except Exception as e:
                logger.warning("[llm_v2] recall failed: %s", e)

        description = generate_method_description(method_name, content_plain=plain_for_desc[:3200])

        artifact: MethodArtifact = {
            "name": method_name,
            "synr": synr,
            "sytj": block_dict["sytj"],
            "sygc": block_dict["sygc"],
            "jgpd": block_dict["jgpd"],
            "description": description,
            "blocks": blocks,
            "modules": group_mods,
            "missing_status": comp["missing_status"],
            "missing_blocks": comp["missing_blocks"],
            "extract_version": "v2",
            "cluster_mode": cluster_mode,
            "equipment_display_mode": equipment_display_mode,  # type: ignore[typeddict-item]
            "recommended_nodes": rec_nodes,
            "recommended_relations": rec_rels,
            "equipment_recommendations": eq_recs,
            "extract_source": "llm_v2_anatomist",
            "document_dir": document_dir,
        }
        methods_out.append(dict(artifact))

        if document_dir:
            fn = os.path.join(document_dir, f"method_v2_{method_index:03d}.json")
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(artifact, f, ensure_ascii=False, indent=2)

        _emit(on_event, "method_ready", {
            "method_index": method_index,
            "method": artifact,
            "missing_status": comp["missing_status"],
            "missing_blocks": comp["missing_blocks"],
            "extract_version": "v2",
            "cluster_mode": cluster_mode,
        })

    _emit(on_event, "done", {
        "job_id": job_id,
        "document_dir": document_dir,
        "methods_count": len(methods_out),
        "extract_version": "v2",
    })

    return {
        "document_dir": document_dir,
        "methods": methods_out,
        "modules": all_modules,
        "groups": groups,
        "extract_version": "v2",
    }


def should_use_v2() -> bool:
    return get_extractor_version() == "v2"
