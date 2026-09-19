import logging
import pymysql

logger = logging.getLogger(__name__)


class MySQLClearManager:
    def __init__(self, conn_params):
        self.conn_params = conn_params

    def _get_conn(self):
        return pymysql.connect(**self.conn_params)

    def clear_table(self, table_name):
        try:
            conn = self._get_conn()
            with conn.cursor() as cursor:
                cursor.execute(f"DELETE FROM `{table_name}`")
                conn.commit()
            return {"success": True, "message": f"Table {table_name} cleared"}
        except Exception as e:
            logger.error(f"Failed to clear table {table_name}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def clear_all_tables(self, table_names):
        try:
            conn = self._get_conn()
            with conn.cursor() as cursor:
                for table in table_names:
                    cursor.execute(f"DELETE FROM `{table}`")
                conn.commit()
            return {"success": True, "message": f"Cleared {len(table_names)} tables"}
        except Exception as e:
            logger.error(f"Failed to clear tables: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def truncate_table(self, table_name):
        try:
            conn = self._get_conn()
            with conn.cursor() as cursor:
                cursor.execute(f"TRUNCATE TABLE `{table_name}`")
                conn.commit()
            return {"success": True, "message": f"Table {table_name} truncated"}
        except Exception as e:
            logger.error(f"Failed to truncate table {table_name}: {e}")
            return {"success": False, "error": str(e)}
        finally:
            conn.close()
