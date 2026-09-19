import logging

logger = logging.getLogger(__name__)


class ChromaClearManager:
    def __init__(self, client, collection):
        self.client = client
        self.collection = collection

    def clear_all(self):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            count = self.collection.count()
            ids = self.collection.get().get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
            return {"success": True, "message": f"Cleared {count} documents"}
        except Exception as e:
            logger.error(f"Failed to clear Chroma: {e}")
            return {"success": False, "error": str(e)}

    def clear_by_doc_id(self, doc_id):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"doc_id": doc_id})
            ids = results.get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
            return {"success": True, "data": {"deleted": len(ids)}, "message": f"Cleared {len(ids)} chunks for doc {doc_id}"}
        except Exception as e:
            logger.error(f"Failed to clear doc {doc_id}: {e}")
            return {"success": False, "error": str(e)}

    def clear_by_type(self, doc_type):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"type": doc_type})
            ids = results.get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
            return {"success": True, "data": {"deleted": len(ids)}, "message": f"Cleared {len(ids)} documents of type {doc_type}"}
        except Exception as e:
            logger.error(f"Failed to clear type {doc_type}: {e}")
            return {"success": False, "error": str(e)}
