import logging

logger = logging.getLogger(__name__)


class ChromaDeleteManager:
    def __init__(self, client, collection):
        self.client = client
        self.collection = collection

    def delete_document(self, doc_id):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"doc_id": doc_id})
            ids = results.get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
            return {"success": True, "data": {"deleted": len(ids)}, "message": f"Deleted {len(ids)} chunks for document {doc_id}"}
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")
            return {"success": False, "error": str(e)}

    def delete_chunk(self, chunk_id):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            self.collection.delete(ids=[chunk_id])
            return {"success": True, "message": f"Chunk {chunk_id} deleted"}
        except Exception as e:
            logger.error(f"Failed to delete chunk {chunk_id}: {e}")
            return {"success": False, "error": str(e)}

    def delete_by_type(self, doc_type):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"type": doc_type})
            ids = results.get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
            return {"success": True, "data": {"deleted": len(ids)}, "message": f"Deleted {len(ids)} documents of type {doc_type}"}
        except Exception as e:
            logger.error(f"Failed to delete type {doc_type}: {e}")
            return {"success": False, "error": str(e)}

    def delete_by_filter(self, where_filter):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where=where_filter)
            ids = results.get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
            return {"success": True, "data": {"deleted": len(ids)}, "message": f"Deleted {len(ids)} matching documents"}
        except Exception as e:
            logger.error(f"Failed to delete by filter: {e}")
            return {"success": False, "error": str(e)}
