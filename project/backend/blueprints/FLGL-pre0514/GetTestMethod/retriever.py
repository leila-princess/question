from typing import Any, Dict, List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer


GRAPH_LABELS = {
    "equipment": "Equipment",
    "environment": "Environment",
    "test_project": "TestProject",
    "indicator": "Indicator",
}


def _cosine_topk(query_vec: np.ndarray, doc_vecs: np.ndarray, top_k: int = 8) -> List[int]:
    if doc_vecs.size == 0 or top_k <= 0:
        return []
    sims = doc_vecs @ query_vec
    top_k = min(top_k, len(sims))
    idxs = np.argsort(-sims)[:top_k]
    return idxs.tolist()


class GraphEmbeddingRetriever:
    """
    图数据库节点 -> 拼文本 -> embedding -> 相似度召回
    支持每一类节点单独设置 top-k。
    """

    def __init__(
        self,
        model_name_or_path: str = "BAAI/bge-small-zh-v1.5",
        device: Optional[str] = None,
        cache_dir: Optional[str] = None,
        local_files_only: bool = False,
    ):
        self.model_name_or_path = model_name_or_path
        self.device = device
        self.cache_dir = cache_dir

        if local_files_only:
            self.model = SentenceTransformer(
                model_name_or_path,
                device=device,
                cache_folder=cache_dir,
                local_files_only=True,
            )
        else:
            self.model = SentenceTransformer(
                model_name_or_path,
                device=device,
                cache_folder=cache_dir,
            )

        self.node_store: Dict[str, List[Dict[str, Any]]] = {
            graph_label: [] for graph_label in GRAPH_LABELS.values()
        }
        self.text_store: Dict[str, List[str]] = {
            graph_label: [] for graph_label in GRAPH_LABELS.values()
        }
        self.embedding_store: Dict[str, np.ndarray] = {
            graph_label: np.zeros((0, 1), dtype=np.float32) for graph_label in GRAPH_LABELS.values()
        }

    @staticmethod
    def node_to_text(node: Dict[str, Any]) -> str:
        parts = []
        for field_name, cn_name in (
            ("name", "名称"),
            ("label", "标签"),
            ("type", "类别"),
            ("description", "描述"),
        ):
            value = str(node.get(field_name, "")).strip()
            if value:
                parts.append(f"{cn_name}：{value}")
        return "；".join(parts).strip()

    def load_nodes_from_neo4j(self, neo4j_driver, graph_label: str) -> List[Dict[str, Any]]:
        if neo4j_driver is None:
            return []

        cypher = f"""
        MATCH (n:{graph_label})
        RETURN
            COALESCE(n.Id, '') AS id,
            COALESCE(n.Name, '') AS name,
            COALESCE(n.Label, '') AS label,
            COALESCE(n.Type, '') AS type,
            COALESCE(n.Description, '') AS description
        """

        items: List[Dict[str, Any]] = []
        try:
            with neo4j_driver.session() as session:
                rows = session.run(cypher)
                for row in rows:
                    items.append(
                        {
                            "id": row["id"],
                            "name": row["name"],
                            "label": row["label"],
                            "type": row["type"],
                            "description": row["description"],
                        }
                    )
        except Exception:
            return []

        return items

    def build_index(self, neo4j_driver, force_rebuild: bool = False):
        for graph_label in GRAPH_LABELS.values():
            if self.node_store[graph_label] and not force_rebuild:
                continue

            nodes = self.load_nodes_from_neo4j(neo4j_driver, graph_label)
            texts = [self.node_to_text(node) for node in nodes]

            self.node_store[graph_label] = nodes
            self.text_store[graph_label] = texts

            if texts:
                vecs = self.model.encode(
                    texts,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                ).astype(np.float32)
            else:
                vecs = np.zeros((0, 1), dtype=np.float32)

            self.embedding_store[graph_label] = vecs

    @staticmethod
    def build_query_text(category: str, method_item: Dict[str, Any]) -> str:
        parts = []
        name = str(method_item.get("name", "")).strip()
        typ = str(method_item.get("type", "")).strip()
        desc = str(method_item.get("description", "")).strip()

        if name:
            parts.append(f"方法名称：{name}")
        if typ:
            parts.append(f"方法类型：{typ}")
        if desc:
            parts.append(f"方法摘要：{desc}")

        recall_plan = method_item.get("recall_plan", {}) or {}
        slot = recall_plan.get(category, {}) or {}
        keywords = slot.get("keywords", []) or []
        reason = str(slot.get("reason", "")).strip()

        if keywords:
            parts.append(f"{category}召回关键词：" + "、".join([str(x).strip() for x in keywords if str(x).strip()]))
        if reason:
            parts.append(f"{category}关键词说明：{reason}")

        # 适当补充子块信息
        for block_name in ("sytj", "sygc", "jgpd"):
            block = method_item.get(block_name, {}) or {}
            block_text = str(block.get("text", "")).strip()
            if block_text and block_text != "文档未明确说明":
                parts.append(f"{block_name.upper()}：{block_text[:240]}")

        return "；".join(parts).strip()

    def search(
        self,
        category: str,
        method_item: Dict[str, Any],
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        graph_label = GRAPH_LABELS.get(category)
        if graph_label is None:
            return []

        nodes = self.node_store.get(graph_label, [])
        vecs = self.embedding_store.get(graph_label)
        if not nodes or vecs is None or vecs.size == 0:
            return []

        query_text = self.build_query_text(category=category, method_item=method_item)
        if not query_text:
            return []

        qvec = self.model.encode(
            [query_text],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).astype(np.float32)[0]

        sims = vecs @ qvec
        idxs = _cosine_topk(qvec, vecs, top_k=top_k)
        return [
            {
                "id": nodes[idx]["id"],
                "name": nodes[idx]["name"],
                "label": nodes[idx]["label"],
                "type": nodes[idx]["type"],
                "description": nodes[idx]["description"],
                "score": round(float(sims[idx]), 6),
            }
            for idx in idxs
        ]

    def search_all(
        self,
        method_item: Dict[str, Any],
        topk_config: Optional[Dict[str, int]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        topk_config = topk_config or {}
        result: Dict[str, List[Dict[str, Any]]] = {}
        for category in GRAPH_LABELS.keys():
            top_k = int(topk_config.get(category, 8) or 0)
            result[category] = self.search(category=category, method_item=method_item, top_k=top_k)
        return result
