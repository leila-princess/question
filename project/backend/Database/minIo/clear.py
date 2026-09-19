import logging

logger = logging.getLogger(__name__)


class MinIOClearManager:
    def __init__(self, client):
        self.client = client

    def clear_bucket(self, bucket_name):
        try:
            objects = self.client.list_objects(bucket_name, recursive=True)
            deleted = 0
            for obj in objects:
                self.client.remove_object(bucket_name, obj.object_name)
                deleted += 1
            return {"success": True, "data": {"deleted": deleted}, "message": f"Cleared {deleted} objects from {bucket_name}"}
        except Exception as e:
            logger.error(f"Failed to clear bucket {bucket_name}: {e}")
            return {"success": False, "error": str(e)}

    def clear_prefix(self, bucket_name, prefix):
        try:
            objects = self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
            deleted = 0
            for obj in objects:
                self.client.remove_object(bucket_name, obj.object_name)
                deleted += 1
            return {"success": True, "data": {"deleted": deleted}, "message": f"Cleared {deleted} objects under {prefix}"}
        except Exception as e:
            logger.error(f"Failed to clear prefix {prefix}: {e}")
            return {"success": False, "error": str(e)}

    def clear_category(self, bucket_name, category):
        return self.clear_prefix(bucket_name, f"{category}/")
