import logging

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"标准", "方案", "模板", "规范", "其他"}


class MinIOSearchManager:
    def __init__(self, client, bucket_name):
        self.client = client
        self.bucket_name = bucket_name

    def search_by_keyword(self, keyword):
        try:
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            matches = []
            for obj in objects:
                if keyword.lower() in obj.object_name.lower():
                    stat = self.client.stat_object(self.bucket_name, obj.object_name)
                    meta = {}
                    if stat.metadata:
                        for k, v in stat.metadata.items():
                            meta[k] = v
                    matches.append({
                        "object_name": obj.object_name,
                        "size": obj.size,
                        "last_modified": str(obj.last_modified),
                        "metadata": meta,
                    })
            return {
                "success": True,
                "data": matches,
                "count": len(matches),
                "message": f"Found {len(matches)} files matching '{keyword}'"
            }
        except Exception as e:
            logger.error(f"Failed to search keyword: {e}")
            return {"success": False, "error": str(e)}

    def search_by_doc_id(self, doc_id):
        try:
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            matches = []
            for obj in objects:
                stat = self.client.stat_object(self.bucket_name, obj.object_name)
                meta = {}
                if stat.metadata:
                    for k, v in stat.metadata.items():
                        meta[k] = v
                if meta.get("x-amz-meta-doc-id") == doc_id:
                    matches.append({
                        "object_name": obj.object_name,
                        "size": obj.size,
                        "last_modified": str(obj.last_modified),
                        "metadata": meta,
                    })
            return {
                "success": True,
                "data": matches,
                "count": len(matches),
                "message": f"Found {len(matches)} files for doc_id {doc_id}"
            }
        except Exception as e:
            logger.error(f"Failed to search by doc_id: {e}")
            return {"success": False, "error": str(e)}

    def search_by_category(self, category):
        if category not in VALID_CATEGORIES:
            return {"success": False, "error": f"Invalid category: {category}. Must be one of {VALID_CATEGORIES}"}
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=f"{category}/", recursive=True)
            files = []
            for obj in objects:
                files.append({
                    "object_name": obj.object_name,
                    "size": obj.size,
                    "last_modified": str(obj.last_modified),
                    "etag": obj.etag,
                })
            return {
                "success": True,
                "data": files,
                "count": len(files),
                "message": f"Found {len(files)} files in category {category}"
            }
        except Exception as e:
            logger.error(f"Failed to search by category: {e}")
            return {"success": False, "error": str(e)}

    def search_by_type(self, file_type):
        try:
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            matches = []
            for obj in objects:
                if obj.object_name.lower().endswith(f".{file_type.lower()}"):
                    stat = self.client.stat_object(self.bucket_name, obj.object_name)
                    meta = {}
                    if stat.metadata:
                        for k, v in stat.metadata.items():
                            meta[k] = v
                    matches.append({
                        "object_name": obj.object_name,
                        "size": obj.size,
                        "last_modified": str(obj.last_modified),
                        "metadata": meta,
                    })
            return {
                "success": True,
                "data": matches,
                "count": len(matches),
                "message": f"Found {len(matches)} files of type {file_type}"
            }
        except Exception as e:
            logger.error(f"Failed to search by type: {e}")
            return {"success": False, "error": str(e)}

    def search_by_metadata_field(self, meta_key, meta_value):
        try:
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            matches = []
            lookup_key = meta_key if meta_key.startswith("x-amz-meta-") else f"x-amz-meta-{meta_key}"
            for obj in objects:
                stat = self.client.stat_object(self.bucket_name, obj.object_name)
                meta = {}
                if stat.metadata:
                    for k, v in stat.metadata.items():
                        meta[k] = v
                if meta.get(lookup_key) == meta_value:
                    matches.append({
                        "object_name": obj.object_name,
                        "size": obj.size,
                        "last_modified": str(obj.last_modified),
                        "metadata": meta,
                    })
            return {
                "success": True,
                "data": matches,
                "count": len(matches),
                "message": f"Found {len(matches)} files with {meta_key}={meta_value}"
            }
        except Exception as e:
            logger.error(f"Failed to search by metadata: {e}")
            return {"success": False, "error": str(e)}
