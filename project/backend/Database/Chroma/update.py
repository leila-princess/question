import logging

logger = logging.getLogger(__name__)


class ChromaUpdateManager:
    def __init__(self, client, collection):
        self.client = client
        self.collection = collection

    def update_metadata(self, chunk_id, metadata_updates):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            self.collection.update(
                ids=[chunk_id],
                metadatas=[metadata_updates],
            )
            return {"success": True, "message": f"Metadata updated for {chunk_id}"}
        except Exception as e:
            logger.error(f"Failed to update metadata: {e}")
            return {"success": False, "error": str(e)}

    def update_document(self, chunk_id, new_text, metadata=None):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            params = {"ids": [chunk_id], "documents": [new_text]}
            if metadata:
                params["metadatas"] = [metadata]
            self.collection.update(**params)
            return {"success": True, "message": f"Document updated for {chunk_id}"}
        except Exception as e:
            logger.error(f"Failed to update document: {e}")
            return {"success": False, "error": str(e)}

    def batch_update_metadata(self, doc_id, metadata_updates):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"doc_id": doc_id})
            ids = results.get("ids", [])
            if not ids:
                return {"success": False, "error": f"No chunks found for doc {doc_id}"}
            metadatas = []
            for i in range(len(ids)):
                meta = results.get("metadatas", [{}])[i].copy()
                meta.update(metadata_updates)
                metadatas.append(meta)
            self.collection.update(ids=ids, metadatas=metadatas)
            return {"success": True, "data": {"updated": len(ids)}, "message": f"Updated metadata for {len(ids)} chunks of {doc_id}"}
        except Exception as e:
            logger.error(f"Failed to batch update metadata: {e}")
            return {"success": False, "error": str(e)}

    def update_chunk_text(self, chunk_id, new_text):
        return self.update_document(chunk_id, new_text)
