import logging
import pymysql
from datetime import datetime

logger = logging.getLogger(__name__)


class MySQLInputManager:
    def __init__(self, conn_params):
        self.conn_params = conn_params

    def _get_conn(self):
        return pymysql.connect(**self.conn_params)

    def insert_record(self, table_name, data):
        try:
            conn = self._get_conn()
            columns = ", ".join([f"`{k}`" for k in data.keys()])
            placeholders = ", ".join(["%s"] * len(data))
            query = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
            with conn.cursor() as cursor:
                cursor.execute(query, list(data.values()))
                conn.commit()
                last_id = cursor.lastrowid
            return {"success": True, "data": {"id": last_id}, "message": f"Record inserted into {table_name}"}
        except Exception as e:
            logger.error(f"Failed to insert record: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def batch_insert(self, table_name, records):
        try:
            if not records:
                return {"success": False, "error": "No records to insert"}
            conn = self._get_conn()
            columns = ", ".join([f"`{k}`" for k in records[0].keys()])
            placeholders = ", ".join(["%s"] * len(records[0]))
            query = f"INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})"
            values = [list(r.values()) for r in records]
            with conn.cursor() as cursor:
                cursor.executemany(query, values)
                conn.commit()
            return {"success": True, "data": {"inserted": len(records)}, "message": f"Batch inserted {len(records)} records"}
        except Exception as e:
            logger.error(f"Failed to batch insert: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def insert_or_update(self, table_name, data, unique_keys):
        try:
            conn = self._get_conn()
            columns = ", ".join([f"`{k}`" for k in data.keys()])
            placeholders = ", ".join(["%s"] * len(data))
            update_clause = ", ".join([f"`{k}` = VALUES(`{k}`)" for k in data.keys() if k not in unique_keys])
            query = f"""
                INSERT INTO `{table_name}` ({columns}) VALUES ({placeholders})
                ON DUPLICATE KEY UPDATE {update_clause}
            """
            with conn.cursor() as cursor:
                cursor.execute(query, list(data.values()))
                conn.commit()
            return {"success": True, "message": f"Record inserted or updated in {table_name}"}
        except Exception as e:
            logger.error(f"Failed to insert or update: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def query(self, table_name, conditions=None, columns=None, order_by=None, limit=None, offset=None):
        try:
            conn = self._get_conn()
            col_str = ", ".join([f"`{c}`" for c in columns]) if columns else "*"
            query = f"SELECT {col_str} FROM `{table_name}`"
            params = []
            if conditions:
                where_parts = []
                for k, v in conditions.items():
                    if isinstance(v, (list, tuple)):
                        placeholders = ", ".join(["%s"] * len(v))
                        where_parts.append(f"`{k}` IN ({placeholders})")
                        params.extend(v)
                    else:
                        where_parts.append(f"`{k}` = %s")
                        params.append(v)
                query += " WHERE " + " AND ".join(where_parts)
            if order_by:
                query += f" ORDER BY {order_by}"
            if limit:
                query += f" LIMIT {limit}"
            if offset:
                query += f" OFFSET {offset}"
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall()
            return {"success": True, "data": rows, "count": len(rows), "message": f"Retrieved {len(rows)} records"}
        except Exception as e:
            logger.error(f"Failed to query: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def query_by_id(self, table_name, record_id, id_column="id"):
        return self.query(table_name, conditions={id_column: record_id})

    def count(self, table_name, conditions=None):
        try:
            conn = self._get_conn()
            query = f"SELECT COUNT(*) AS cnt FROM `{table_name}`"
            params = []
            if conditions:
                where_parts = []
                for k, v in conditions.items():
                    where_parts.append(f"`{k}` = %s")
                    params.append(v)
                query += " WHERE " + " AND ".join(where_parts)
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                result = cursor.fetchone()
            return {"success": True, "data": {"count": result[0]}, "message": f"Count: {result[0]}"}
        except Exception as e:
            logger.error(f"Failed to count: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def execute_raw(self, query, params=None):
        try:
            conn = self._get_conn()
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(query, params or ())
                if query.strip().upper().startswith("SELECT"):
                    rows = cursor.fetchall()
                    conn.commit()
                    return {"success": True, "data": rows, "count": len(rows), "message": "Query executed"}
                else:
                    conn.commit()
                    return {"success": True, "data": {"affected": cursor.rowcount}, "message": "Query executed"}
        except Exception as e:
            logger.error(f"Failed to execute raw query: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
