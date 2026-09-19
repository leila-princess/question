import logging
import pymysql

logger = logging.getLogger(__name__)


class MySQLUpdateManager:
    def __init__(self, conn_params):
        self.conn_params = conn_params

    def _get_conn(self):
        return pymysql.connect(**self.conn_params)

    def update_record(self, table_name, record_id, updates, id_column="id"):
        try:
            conn = self._get_conn()
            set_clauses = ", ".join([f"`{k}` = %s" for k in updates.keys()])
            query = f"UPDATE `{table_name}` SET {set_clauses} WHERE `{id_column}` = %s"
            params = list(updates.values()) + [record_id]
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                conn.commit()
                affected = cursor.rowcount
            if affected > 0:
                return {"success": True, "data": {"affected": affected}, "message": f"Record {record_id} updated"}
            return {"success": False, "error": f"Record {record_id} not found"}
        except Exception as e:
            logger.error(f"Failed to update record: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def batch_update(self, table_name, records, id_column="id"):
        try:
            conn = self._get_conn()
            updated = 0
            for record in records:
                record_id = record.pop(id_column, None)
                if record_id is None or not record:
                    continue
                set_clauses = ", ".join([f"`{k}` = %s" for k in record.keys()])
                query = f"UPDATE `{table_name}` SET {set_clauses} WHERE `{id_column}` = %s"
                params = list(record.values()) + [record_id]
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    updated += cursor.rowcount
            conn.commit()
            return {"success": True, "data": {"updated": updated}, "message": f"Updated {updated} records"}
        except Exception as e:
            logger.error(f"Failed to batch update: {e}")
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def update_by_condition(self, table_name, conditions, updates):
        try:
            conn = self._get_conn()
            set_clauses = ", ".join([f"`{k}` = %s" for k in updates.keys()])
            where_parts = " AND ".join([f"`{k}` = %s" for k in conditions.keys()])
            query = f"UPDATE `{table_name}` SET {set_clauses} WHERE {where_parts}"
            params = list(updates.values()) + list(conditions.values())
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                conn.commit()
                affected = cursor.rowcount
            return {"success": True, "data": {"affected": affected}, "message": f"Updated {affected} records"}
        except Exception as e:
            logger.error(f"Failed to update by condition: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
