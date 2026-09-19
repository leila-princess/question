import json
from typing import Any, Dict, List, Optional


def build_sy(terms: List[Dict[str, str]]) -> str:
    parts = []
    for item in terms or []:
        term = str(item.get("term", "")).strip()
        definition = str(item.get("definition", "")).strip()
        if term and definition:
            parts.append(f"{term}：{definition}")
    return "；".join(parts)


def _build_subnode(
    node_kind: str,
    method_id: str,
    source_name: str,
    source_id: str,
    user_id: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "Id": f"{method_id}_{node_kind}",
        "Name": node_kind,
        "Text": str(data.get("text", "")).strip(),
        "Tables_html": list(data.get("tables_html", []) or []),
        "Formulas_latex": list(data.get("formulas_latex", []) or []),
        "Source_name": source_name,
        "Source_id": source_id,
        "Status": "启动",
        "User_id": user_id,
        "Create_time": "",
    }


def build_method_payload(
    method_item: Dict[str, Any],
    file_name: str,
    source_file_id: str,
    user_id: str,
    terms: List[Dict[str, str]],
    document_class: str,
    method_index: int,
    method_id_prefix: str = "FF",
) -> Dict[str, Any]:
    method_id = f"{method_id_prefix}_{method_index:04d}"
    recall_plan = method_item.get("recall_plan", {}) or {}

    return {
        "source_file": {
            "file_id": source_file_id,
            "file_name": file_name,
            "user_id": user_id,
        },
        "class_node": {
            "Id": f"{method_id}_CLASS",
            "Name": document_class or method_item.get("class", "未判定"),
            "Source_name": file_name,
            "Source_id": source_file_id,
            "Status": "启动",
            "User_id": user_id,
            "Create_time": "",
        },
        "terms": terms,
        "method_node": {
            "Id": method_id,
            "Candidate_id": method_item.get("candidate_id"),
            "Section_ids": list(method_item.get("section_ids", []) or []),
            "Name": method_item.get("name", ""),
            "Type": method_item.get("type", ""),
            "Label": recall_plan,
            "Description": method_item.get("description", ""),
            "SY": build_sy(terms),
            "Source_name": file_name,
            "Source_id": source_file_id,
            "Status": "启动",
            "User_id": user_id,
            "Create_time": "",
        },
        "SYTJ_node": _build_subnode(
            node_kind="SYTJ",
            method_id=method_id,
            source_name=file_name,
            source_id=source_file_id,
            user_id=user_id,
            data=method_item.get("sytj", {}) or {},
        ),
        "SYGC_node": _build_subnode(
            node_kind="SYGC",
            method_id=method_id,
            source_name=file_name,
            source_id=source_file_id,
            user_id=user_id,
            data=method_item.get("sygc", {}) or {},
        ),
        "JGPD_node": _build_subnode(
            node_kind="JGPD",
            method_id=method_id,
            source_name=file_name,
            source_id=source_file_id,
            user_id=user_id,
            data=method_item.get("jgpd", {}) or {},
        ),
        "graph_matches": method_item.get("graph_matches", {
            "equipment": [],
            "environment": [],
            "test_project": [],
            "indicator": [],
        }),
        "topk_config": method_item.get("topk_config", {}),
    }


def build_document_manifest(
    extraction_data: Dict[str, Any],
    payloads: List[Dict[str, Any]],
) -> Dict[str, Any]:
    methods = extraction_data.get("methods", []) or []
    return {
        "source_file": extraction_data.get("source_file", {}),
        "document_meta": extraction_data.get("document_meta", {}),
        "terms_count": len(extraction_data.get("terms", []) or []),
        "method_count": len(methods),
        "methods": [
            {
                "candidate_id": method.get("candidate_id"),
                "name": method.get("name"),
                "type": method.get("type"),
                "section_ids": method.get("section_ids", []),
                "output_file": payload.get("_output_file", ""),
            }
            for method, payload in zip(methods, payloads)
        ],
    }
