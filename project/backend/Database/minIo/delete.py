import logging

logger = logging.getLogger(__name__)


class MinIODeleteManager:
    def __init__(self, client, bucket_name):
        self.client = client
        self.bucket_name = bucket_name

    def delete_file(self, object_name):
        try:
            self.client.remove_object(self.bucket_name, object_name)
            return {"success": True, "message": f"File {object_name} deleted"}
        except Exception as e:
            logger.error(f"Failed to delete file {object_name}: {e}")
            return {"success": False, "error": str(e)}

    def delete_by_doc_id(self, doc_id):
        try:
            objects = self.client.list_objects(self.bucket_name, recursive=True)
            deleted = 0
            for obj in objects:
                stat = self.client.stat_object(self.bucket_name, obj.object_name)
                meta = stat.metadata or {}
                if meta.get("x-amz-meta-doc-id") == doc_id:
                    self.client.remove_object(self.bucket_name, obj.object_name)
                    deleted += 1
            return {"success": True, "data": {"deleted": deleted}, "message": f"Deleted {deleted} files for doc {doc_id}"}
        except Exception as e:
            logger.error(f"Failed to delete by doc_id {doc_id}: {e}")
            return {"success": False, "error": str(e)}

    def delete_category(self, category):
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=f"{category}/", recursive=True)
            deleted = 0
            for obj in objects:
                self.client.remove_object(self.bucket_name, obj.object_name)
                deleted += 1
            return {"success": True, "data": {"deleted": deleted}, "message": f"Deleted {deleted} files in category {category}"}
        except Exception as e:
            logger.error(f"Failed to delete category {category}: {e}")
            return {"success": False, "error": str(e)}

    def delete_by_prefix(self, prefix):
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=prefix, recursive=True)
            deleted = 0
            for obj in objects:
                self.client.remove_object(self.bucket_name, obj.object_name)
                deleted += 1
            return {"success": True, "data": {"deleted": deleted}, "message": f"Deleted {deleted} files under {prefix}"}
        except Exception as e:
            logger.error(f"Failed to delete prefix {prefix}: {e}")
            return {"success": False, "error": str(e)}

    def delete_folder(self, folder_path, category="其他"):
        prefix = f"{category}/{folder_path}/"
        return self.delete_by_prefix(prefix)
