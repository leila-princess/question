from typing import Any, Dict, List, Optional

from backend.config import Config

try:
    from .retriever import GraphEmbeddingRetriever
except ImportError:
    from retriever import GraphEmbeddingRetriever


DEFAULT_TOPK = {
    "equipment": 5,
    "environment": 5,
    "test_project": 2,
    "indicator": 2,
}


def normalize_topk_config(topk_config: Optional[Dict[str, Any]]) -> Dict[str, int]:
    result = dict(DEFAULT_TOPK)
    if isinstance(topk_config, dict):
        for key in DEFAULT_TOPK.keys():
            if key in topk_config:
                try:
                    value = int(topk_config[key])
                except Exception:
                    value = DEFAULT_TOPK[key]
                result[key] = max(0, value)
    return result


def get_runtime_retriever() -> GraphEmbeddingRetriever:
    retriever = getattr(Config, "TESTMETHOD_RETRIEVER", None)
    if retriever is None:
        model_name_or_path = getattr(Config, "BGE_MODEL_PATH", None) or "BAAI/bge-small-zh-v1.5"
        lfo = bool(getattr(Config, "BGE_LOCAL_FILES_ONLY", False))
        preloaded = getattr(Config, "FLGL_TESTMETHOD_BGE_MODEL", None)
        if preloaded is None:
            try:
                from blueprints.GLCX.ZNWD5.embedding_service import get_embedding_model

                preloaded = get_embedding_model()
            except Exception:
                preloaded = None
        if preloaded is not None:
            retriever = GraphEmbeddingRetriever(
                model_name_or_path=model_name_or_path,
                model=preloaded,
            )
        else:
            retriever = GraphEmbeddingRetriever(
                model_name_or_path=model_name_or_path,
                local_files_only=lfo,
            )
        Config.TESTMETHOD_RETRIEVER = retriever
    return retriever


def recall_candidates_for_methods(
    methods: List[Dict[str, Any]],
    topk_config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    neo4j_driver = getattr(Config, "NEO4J_DRIVER", None)
    normalized_topk = normalize_topk_config(topk_config)
    if neo4j_driver is None:
        return [
            {
                "candidate_id": method.get("candidate_id"),
                "graph_matches": {
                    "equipment": [],
                    "environment": [],
                    "test_project": [],
                    "indicator": [],
                },
                "topk_config": normalized_topk,
            }
            for method in methods
        ]

    retriever = get_runtime_retriever()
    retriever.build_index(neo4j_driver, force_rebuild=False)

    results = []
    for method in methods:
        matches = retriever.search_all(method_item=method, topk_config=normalized_topk)
        results.append({
            "candidate_id": method.get("candidate_id"),
            "graph_matches": matches,
            "topk_config": normalized_topk,
        })
    return results


def recall_candidates_for_document(
    extraction_data: Dict[str, Any],
    topk_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    methods = list(extraction_data.get("methods", []) or [])
    recall_results = recall_candidates_for_methods(methods=methods, topk_config=topk_config)
    by_id = {item["candidate_id"]: item for item in recall_results}

    enriched_methods = []
    for method in methods:
        row = by_id.get(method.get("candidate_id"), {})
        enriched = dict(method)
        enriched["graph_matches"] = row.get("graph_matches", {
            "equipment": [],
            "environment": [],
            "test_project": [],
            "indicator": [],
        })
        enriched["topk_config"] = row.get("topk_config", normalize_topk_config(topk_config))
        enriched_methods.append(enriched)

    result = dict(extraction_data)
    result["methods"] = enriched_methods
    result["topk_config"] = normalize_topk_config(topk_config)
    return result
