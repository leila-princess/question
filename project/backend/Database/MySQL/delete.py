import logging
import pymysql

logger = logging.getLogger(__name__)


class MySQLDeleteManager:
    def __init__(self, conn_params):
        self.conn_params = conn_params

    def _get_conn(self):
        return pymysql.connect(**self.conn_params)

    def delete_record(self, table_name, record_id, id_column="id"):
        try:
            conn = self._get_conn()
            query = f"DELETE FROM `{table_name}` WHERE `{id_column}` = %s"
            with conn.cursor() as cursor:
                cursor.execute(query, (record_id,))
                conn.commit()
                affected = cursor.rowcount
            if affected > 0:
                return {"success": True, "data": {"deleted": affected}, "message": f"Record {record_id} deleted"}
            return {"success": False, "error": f"Record {record_id} not found"}
        except Exception as e:
            logger.error(f"Failed to delete record: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def delete_by_condition(self, table_name, conditions):
        try:
            conn = self._get_conn()
            where_parts = " AND ".join([f"`{k}` = %s" for k in conditions.keys()])
            query = f"DELETE FROM `{table_name}` WHERE {where_parts}"
            params = list(conditions.values())
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                conn.commit()
                affected = cursor.rowcount
            return {"success": True, "data": {"deleted": affected}, "message": f"Deleted {affected} records"}
        except Exception as e:
            logger.error(f"Failed to delete by condition: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def delete_by_ids(self, table_name, ids, id_column="id"):
        try:
            if not ids:
                return {"success": False, "error": "No IDs provided"}
            conn = self._get_conn()
            placeholders = ", ".join(["%s"] * len(ids))
            query = f"DELETE FROM `{table_name}` WHERE `{id_column}` IN ({placeholders})"
            with conn.cursor() as cursor:
                cursor.execute(query, ids)
                conn.commit()
                affected = cursor.rowcount
            return {"success": True, "data": {"deleted": affected}, "message": f"Deleted {affected} records"}
        except Exception as e:
            logger.error(f"Failed to delete by IDs: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
