import logging

logger = logging.getLogger(__name__)


class ChromaSearchManager:
    def __init__(self, client, collection):
        self.client = client
        self.collection = collection

    def search_by_keyword(self, keyword, n_results=20, filters=None):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            where = filters if filters else None
            results = self.collection.query(
                query_texts=[keyword],
                n_results=n_results,
                where=where,
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            ids = results.get("ids", [[]])[0]
            dists = results.get("distances", [[]])[0] if "distances" in results else []
            items = []
            for i in range(len(docs)):
                item = {
                    "id": ids[i],
                    "document": docs[i],
                    "metadata": metas[i],
                }
                if dists:
                    item["distance"] = dists[i]
                items.append(item)
            return {
                "success": True,
                "data": items,
                "count": len(items),
                "message": f"Found {len(items)} results for '{keyword}'"
            }
        except Exception as e:
            logger.error(f"Failed to search keyword: {e}")
            return {"success": False, "error": str(e)}

    def search_by_doc_id(self, doc_id):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"doc_id": doc_id})
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            chunks = []
            for i in range(len(ids)):
                chunks.append({
                    "id": ids[i],
                    "document": docs[i],
                    "metadata": metas[i],
                })
            chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
            return {
                "success": True,
                "data": chunks,
                "count": len(chunks),
                "message": f"Retrieved {len(chunks)} chunks for doc {doc_id}"
            }
        except Exception as e:
            logger.error(f"Failed to search by doc_id: {e}")
            return {"success": False, "error": str(e)}

    def search_by_type(self, doc_type, n_results=100):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"type": doc_type}, limit=n_results)
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            items = []
            for i in range(len(ids)):
                items.append({
                    "id": ids[i],
                    "document": docs[i],
                    "metadata": metas[i],
                })
            return {
                "success": True,
                "data": items,
                "count": len(items),
                "message": f"Found {len(items)} documents of type '{doc_type}'"
            }
        except Exception as e:
            logger.error(f"Failed to search by type: {e}")
            return {"success": False, "error": str(e)}

    def search_by_metadata(self, metadata_filters, n_results=100):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where=metadata_filters, limit=n_results)
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            items = []
            for i in range(len(ids)):
                items.append({
                    "id": ids[i],
                    "document": docs[i],
                    "metadata": metas[i],
                })
            return {
                "success": True,
                "data": items,
                "count": len(items),
                "message": f"Found {len(items)} matching documents"
            }
        except Exception as e:
            logger.error(f"Failed to search by metadata: {e}")
            return {"success": False, "error": str(e)}

    def search_by_neo4j_id(self, neo4j_testmethod_id):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"neo4j_testmethod_id": neo4j_testmethod_id})
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            items = []
            for i in range(len(ids)):
                items.append({
                    "id": ids[i],
                    "document": docs[i],
                    "metadata": metas[i],
                })
            return {
                "success": True,
                "data": items,
                "count": len(items),
                "message": f"Found {len(items)} chunks linked to TestMethod {neo4j_testmethod_id}"
            }
        except Exception as e:
            logger.error(f"Failed to search by neo4j_id: {e}")
            return {"success": False, "error": str(e)}
