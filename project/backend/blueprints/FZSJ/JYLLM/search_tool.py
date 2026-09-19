"""
Chroma向量数据库检索工具
支持关键词检索和向量相似度检索，供多智能体协作全过程调用
"""
import logging
from typing import List, Dict, Any, Optional
from config import Config

logger = logging.getLogger(__name__)


class ChromaSearchTool:
    """封装Chroma向量检索功能，供智能体调用"""

    def __init__(self):
        self.collection = Config.CHROMA_COLLECTION_OBJ
        self.client = Config.CHROMA_CLIENT
        if not self.collection:
            logger.warning("Chroma集合未初始化，检索功能不可用")

    def search_by_keyword(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        关键词检索（基于Chroma的query接口，内部使用embedding）

        Args:
            query: 搜索关键词/短语
            n_results: 返回结果数量
            where: 过滤条件

        Returns:
            结构化检索结果列表，每项包含content, metadata, distance
        """
        if not self.collection:
            logger.warning("Chroma集合未配置，返回空结果")
            return []

        try:
            kwargs = {"query_texts": [query], "n_results": n_results}
            if where:
                kwargs["where"] = where

            results = self.collection.query(**kwargs)

            formatted = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for i, (doc_id, doc, meta, dist) in enumerate(
                zip(ids, documents, metadatas, distances)
            ):
                formatted.append({
                    "id": doc_id,
                    "content": doc,
                    "metadata": meta or {},
                    "distance": dist,
                    "rank": i + 1,
                })

            logger.info(f"关键词检索 '{query}' 返回 {len(formatted)} 条结果")
            return formatted

        except Exception as e:
            logger.error(f"关键词检索失败 '{query}': {str(e)}")
            return []

    def search_by_vector(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        where: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """
        向量相似度检索

        Args:
            query_embedding: 查询向量
            n_results: 返回结果数量
            where: 过滤条件

        Returns:
            结构化检索结果列表
        """
        if not self.collection:
            logger.warning("Chroma集合未配置，返回空结果")
            return []

        try:
            kwargs = {"query_embeddings": [query_embedding], "n_results": n_results}
            if where:
                kwargs["where"] = where

            results = self.collection.query(**kwargs)

            formatted = []
            ids = results.get("ids", [[]])[0]
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for i, (doc_id, doc, meta, dist) in enumerate(
                zip(ids, documents, metadatas, distances)
            ):
                formatted.append({
                    "id": doc_id,
                    "content": doc,
                    "metadata": meta or {},
                    "distance": dist,
                    "rank": i + 1,
                })

            logger.info(f"向量检索返回 {len(formatted)} 条结果")
            return formatted

        except Exception as e:
            logger.error(f"向量检索失败: {str(e)}")
            return []

    def search_hybrid(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[Dict] = None
    ) -> str:
        """
        混合检索（关键词+向量），返回纯文本格式供LLM直接使用

        Args:
            query: 查询文本
            n_results: 返回结果数量
            where: 过滤条件

        Returns:
            格式化后的文本字符串
        """
        results = self.search_by_keyword(query, n_results=n_results, where=where)

        if not results:
            return "未检索到相关文档。"

        text_parts = []
        for i, r in enumerate(results, 1):
            meta_info = ""
            if r.get("metadata"):
                meta_items = [f"{k}: {v}" for k, v in r["metadata"].items() if v]
                if meta_items:
                    meta_info = f"\n   元数据: {', '.join(meta_items)}"

            text_parts.append(
                f"[参考文档{i}] (相关度: {1 - r['distance']:.2%})\n"
                f"   内容: {r['content']}{meta_info}"
            )

        return "\n\n".join(text_parts)

    def get_collection_info(self) -> Dict[str, Any]:
        """获取集合基本信息"""
        if not self.collection:
            return {"available": False, "message": "Chroma集合未配置"}

        try:
            return {
                "available": True,
                "name": self.collection.name,
                "count": self.collection.count(),
                "metadata": self.collection.metadata or {},
            }
        except Exception as e:
            return {"available": False, "error": str(e)}


# 单例
_search_tool = None


def get_search_tool() -> ChromaSearchTool:
    global _search_tool
    if _search_tool is None:
        _search_tool = ChromaSearchTool()
    return _search_tool


def match_reference_files(query: str, n_results: int = 8) -> List[Dict[str, Any]]:
    """
    综合设备、环境、项目/指标向量匹配参考文件，返回去重后的文件列表。
    """
    q = (query or "").strip()
    if not q:
        return []

    tool = get_search_tool()
    results = tool.search_by_keyword(q, n_results=max(n_results * 2, 8))
    seen: set = set()
    out: List[Dict[str, Any]] = []

    for r in results:
        meta = r.get("metadata") or {}
        doc_id = str(
            meta.get("doc_id")
            or meta.get("filename")
            or meta.get("file_id")
            or r.get("id")
            or ""
        ).strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        name = str(
            meta.get("filename")
            or meta.get("title")
            or meta.get("name")
            or meta.get("label")
            or doc_id
        ).strip()
        content = (r.get("content") or "").strip()
        dist = float(r.get("distance") or 1.0)
        out.append(
            {
                "id": doc_id,
                "name": name or doc_id,
                "snippet": content[:200] + ("…" if len(content) > 200 else ""),
                "distance": dist,
                "relevance": round(max(0.0, (1.0 - dist) * 100), 1),
            }
        )
        if len(out) >= n_results:
            break

    return out
