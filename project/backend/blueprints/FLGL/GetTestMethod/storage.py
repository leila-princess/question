import json
import os
import re
from typing import Any, Dict, List


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: str) -> str:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def sanitize_name(name: str) -> str:
    name = str(name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name or "unnamed"


def build_document_output_dir(output_root: str, file_name: str) -> str:
    stem = os.path.splitext(os.path.basename(file_name))[0]
    return ensure_dir(os.path.join(output_root, sanitize_name(stem)))


def save_parsed_document(parsed_doc: Dict[str, Any], document_dir: str) -> str:
    return save_json(parsed_doc, os.path.join(document_dir, "parsed_document.json"))


def save_extraction_checkpoint(extraction_data: Dict[str, Any], document_dir: str) -> str:
    return save_json(extraction_data, os.path.join(document_dir, "extraction_checkpoint.json"))


def save_recall_checkpoint(recall_data: Dict[str, Any], document_dir: str) -> str:
    return save_json(recall_data, os.path.join(document_dir, "recall_checkpoint.json"))


def save_manifest(manifest: Dict[str, Any], document_dir: str) -> str:
    return save_json(manifest, os.path.join(document_dir, "manifest.json"))


def save_method_payloads(payloads: List[Dict[str, Any]], document_dir: str) -> List[str]:
    paths: List[str] = []
    for idx, payload in enumerate(payloads, start=1):
        paths.append(save_single_method_payload(payload, document_dir, idx))
    return paths


def save_single_method_payload(
    payload: Dict[str, Any],
    document_dir: str,
    method_index: int,
) -> str:
    """写入单条 method_NNN_*.json（增量落盘）。"""
    method_node = payload.get("method_node", {}) or {}
    name = sanitize_name(method_node.get("Name", f"method_{method_index:03d}"))
    file_name = f"method_{method_index:03d}_{name}.json"
    path = os.path.join(document_dir, file_name)
    payload["_output_file"] = file_name
    save_json(payload, path)
    return path
