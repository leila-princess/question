"""加载 Ontology Pack（YAML），与具体业务数据解耦。"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List

import yaml

from .config import ontology_dir


def _read_yaml(name: str) -> Dict[str, Any]:
    path = os.path.join(ontology_dir(), name)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_ontology() -> Dict[str, Any]:
    nodes = _read_yaml("nodes.yaml")
    intents = _read_yaml("intents.yaml")
    templates = _read_yaml("templates.yaml")
    return {
        "node_labels": list(nodes.get("node_labels") or []),
        "slot_keys": dict(nodes.get("slot_keys") or {}),
        "intents": list(intents.get("intents") or []),
        "templates": dict(templates.get("templates") or {}),
    }


@lru_cache(maxsize=1)
def load_guided_intents() -> Dict[str, Any]:
    return _read_yaml("guided_intents.yaml")


def slot_key_for_node_type(node_type: str) -> str:
    return str(load_ontology()["slot_keys"].get(node_type) or "focus")


def get_template(template_id: str) -> Dict[str, Any]:
    return dict(load_ontology()["templates"].get(template_id) or {})


def list_template_ids() -> List[str]:
    return list(load_ontology()["templates"].keys())
