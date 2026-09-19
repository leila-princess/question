import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


class MinIOUpdateManager:
    def __init__(self, client, bucket_name):
        self.client = client
        self.bucket_name = bucket_name

    def update_file_metadata(self, object_name, metadata_updates: Dict[str, Any]):
        try:
            stat = self.client.stat_object(self.bucket_name, object_name)
            existing_meta = {}
            if stat.metadata:
                for k, v in stat.metadata.items():
                    existing_meta[k] = v

            for k, v in metadata_updates.items():
                key = k if k.startswith("x-amz-meta-") else f"x-amz-meta-{k}"
                existing_meta[key] = str(v)

            import tempfile
            import os as _os
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
                self.client.fget_object(self.bucket_name, object_name, tmp_path)
                self.client.fput_object(
                    self.bucket_name, object_name, tmp_path,
                    metadata=existing_meta
                )
                _os.unlink(tmp_path)

            return {"success": True, "message": f"Metadata updated for {object_name}"}
        except Exception as e:
            logger.error(f"Failed to update metadata: {e}")
            return {"success": False, "error": str(e)}

    def update_chroma_id(self, object_name, chroma_id):
        return self.update_file_metadata(object_name, {"chroma-id": chroma_id})

    def update_neo4j_id(self, object_name, neo4j_id):
        return self.update_file_metadata(object_name, {"neo4j-id": neo4j_id})

    def update_description(self, object_name, description):
        return self.update_file_metadata(object_name, {"description": description})

    def update_image_metadata(self, object_name, page_number=None, image_type=None, description=None):
        updates = {}
        if page_number is not None:
            updates["page-number"] = str(page_number)
        if image_type is not None:
            updates["image-type"] = image_type
        if description is not None:
            updates["description"] = description
        if updates:
            return self.update_file_metadata(object_name, updates)
        return {"success": False, "error": "No metadata fields provided"}

    def add_custom_metadata(self, object_name, key, value):
        return self.update_file_metadata(object_name, {key: value})
