from typing import Any, Dict, List, Optional


def _build_payloads(
    enriched_data: Dict[str, Any],
    source_file_id: str,
    user_id: str,
    method_id_prefix: str = "FF",
) -> List[Dict[str, Any]]:
    # 延迟导入，避免 extract 阶段提前触发 recall / torch 相关依赖
    from .assembler import build_method_payload

    payloads = []
    file_name = enriched_data.get("source_file", {}).get("file_name", "")
    terms = enriched_data.get("terms", []) or []
    document_class = enriched_data.get("document_meta", {}).get("class", "未判定")

    for idx, method_item in enumerate(enriched_data.get("methods", []) or [], start=1):
        payloads.append(
            build_method_payload(
                method_item=method_item,
                file_name=file_name,
                source_file_id=source_file_id,
                user_id=user_id,
                terms=terms,
                document_class=document_class,
                method_index=idx,
                method_id_prefix=method_id_prefix,
            )
        )
    return payloads



def run_extract_only(
    file_path: str,
    source_file_id: str,
    user_id: str,
    output_root: str = "output_json",
    max_methods: Optional[int] = None,
    ocr_output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    # 只导入抽取链路：parser / extractor / storage
    from .extractor import extract_document_methods
    from .parser import parse_standard_file
    from .storage import (
        build_document_output_dir,
        save_extraction_checkpoint,
        save_parsed_document,
    )

    import logging as _logging

    _log = _logging.getLogger(__name__)
    _log.info("[GetTestMethod][pipeline] parse_standard_file 开始: %s", file_path)
    parsed_doc = parse_standard_file(file_path=file_path, ocr_output_dir=ocr_output_dir)
    _log.info(
        "[GetTestMethod][pipeline] 解析完成: file=%s ocr_used=%s sections=%s",
        parsed_doc.get("file_name"),
        bool(parsed_doc.get("ocr_used")),
        len(parsed_doc.get("sections") or []),
    )
    _log.info("[GetTestMethod][pipeline] extract_document_methods 开始")
    extraction_data = extract_document_methods(parsed_doc=parsed_doc, max_methods=max_methods)
    _log.info(
        "[GetTestMethod][pipeline] 抽取完成: methods=%s",
        len(extraction_data.get("methods") or []),
    )

    document_dir = build_document_output_dir(output_root=output_root, file_name=parsed_doc["file_name"])
    parsed_path = save_parsed_document(parsed_doc=parsed_doc, document_dir=document_dir)
    extraction_path = save_extraction_checkpoint(extraction_data=extraction_data, document_dir=document_dir)

    return {
        "status": "paused_after_extraction",
        "document_dir": document_dir,
        "parsed_document_path": parsed_path,
        "extraction_checkpoint_path": extraction_path,
        "extraction_data": extraction_data,
    }



def run_recall_only(
    extraction_checkpoint_path: str,
    source_file_id: str,
    user_id: str,
    output_root: Optional[str] = None,
    topk_config: Optional[Dict[str, Any]] = None,
    method_id_prefix: str = "FF",
    save_method_json: bool = True,
) -> Dict[str, Any]:
    # 只导入召回链路：recall / assembler / storage
    from .assembler import build_document_manifest
    from .recall import normalize_topk_config, recall_candidates_for_document
    from .storage import (
        build_document_output_dir,
        load_json,
        save_manifest,
        save_method_payloads,
        save_recall_checkpoint,
    )

    extraction_data = load_json(extraction_checkpoint_path)
    file_name = extraction_data.get("source_file", {}).get("file_name", "document")
    import logging as _logging

    _log = _logging.getLogger(__name__)
    _log.info("[GetTestMethod][pipeline] run_recall_only 开始: %s", extraction_checkpoint_path[:200])
    document_dir = (
        build_document_output_dir(output_root=output_root or "output_json", file_name=file_name)
        if output_root else extraction_checkpoint_path.rsplit("/", 1)[0].rsplit("\\", 1)[0]
    )

    enriched_data = recall_candidates_for_document(
        extraction_data=extraction_data,
        topk_config=normalize_topk_config(topk_config),
    )
    recall_path = save_recall_checkpoint(recall_data=enriched_data, document_dir=document_dir)

    payloads = _build_payloads(
        enriched_data=enriched_data,
        source_file_id=source_file_id,
        user_id=user_id,
        method_id_prefix=method_id_prefix,
    )

    method_paths: List[str] = []
    if save_method_json:
        method_paths = save_method_payloads(payloads=payloads, document_dir=document_dir)

    manifest = build_document_manifest(extraction_data=enriched_data, payloads=payloads)
    manifest_path = save_manifest(manifest=manifest, document_dir=document_dir)

    _log.info("[GetTestMethod][pipeline] run_recall_only 完成: payloads=%s", len(payloads))

    return {
        "status": "completed",
        "document_dir": document_dir,
        "recall_checkpoint_path": recall_path,
        "manifest_path": manifest_path,
        "method_json_paths": method_paths,
        "result": enriched_data,
    }



def run_pipeline_full(
    file_path: str,
    source_file_id: str,
    user_id: str,
    output_root: str = "output_json",
    max_methods: Optional[int] = None,
    topk_config: Optional[Dict[str, Any]] = None,
    method_id_prefix: str = "FF",
) -> Dict[str, Any]:
    # 注意：如果同一进程里 Paddle 与 Torch 冲突，建议不要直接调用该函数，
    # 而是先 run_extract_only，再在新进程里 run_pipeline_resume。
    paused = run_extract_only(
        file_path=file_path,
        source_file_id=source_file_id,
        user_id=user_id,
        output_root=output_root,
        max_methods=max_methods,
    )
    return run_recall_only(
        extraction_checkpoint_path=paused["extraction_checkpoint_path"],
        source_file_id=source_file_id,
        user_id=user_id,
        output_root=output_root,
        topk_config=topk_config,
        method_id_prefix=method_id_prefix,
        save_method_json=True,
    )



def run_pipeline_interactive(
    file_path: str,
    source_file_id: str,
    user_id: str,
    output_root: str = "output_json",
    continue_after_extraction: bool = False,
    max_methods: Optional[int] = None,
    topk_config: Optional[Dict[str, Any]] = None,
    method_id_prefix: str = "FF",
) -> Dict[str, Any]:
    paused = run_extract_only(
        file_path=file_path,
        source_file_id=source_file_id,
        user_id=user_id,
        output_root=output_root,
        max_methods=max_methods,
    )
    if not continue_after_extraction:
        return paused

    return run_recall_only(
        extraction_checkpoint_path=paused["extraction_checkpoint_path"],
        source_file_id=source_file_id,
        user_id=user_id,
        output_root=output_root,
        topk_config=topk_config,
        method_id_prefix=method_id_prefix,
        save_method_json=True,
    )



def run_pipeline_resume(
    extraction_checkpoint_path: str,
    source_file_id: str,
    user_id: str,
    output_root: Optional[str] = None,
    topk_config: Optional[Dict[str, Any]] = None,
    method_id_prefix: str = "FF",
) -> Dict[str, Any]:
    return run_recall_only(
        extraction_checkpoint_path=extraction_checkpoint_path,
        source_file_id=source_file_id,
        user_id=user_id,
        output_root=output_root,
        topk_config=topk_config,
        method_id_prefix=method_id_prefix,
        save_method_json=True,
    )
