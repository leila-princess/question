"""KSHZS 可视化展示蓝图"""
import logging
from flask import Blueprint, jsonify, request
from config import Config
from datetime import datetime
from blueprints.GLCX.JQCX.routes import (
    get_equipment_tree as jq_equipment_tree,
    get_projects as jq_projects,
    get_indicators as jq_indicators,
)

logger = logging.getLogger(__name__)
kshzs_bp = Blueprint('kshzs', __name__, url_prefix='/api/kshzs')

NODE_TYPE_MAP = {
    "Equipment": "设备",
    "EquipmentType": "设备类型",
    "Environment": "环境",
    "TestProject": "测试项目",
    "Indicator": "指标",
    "TestMethod": "测试方法",
    "File": "文件",
}

REL_TYPE_MAP = {
    "BELONGS_TO": "属于",
    "CON": "关联",
    "HAS": "包含",
    "HAS_PART": "包含",
    "BELONGS_TO": "属于",
    "DEFINES": "定义",
    "PUBLISHED_BY": "发布",
    "CITES": "引用",
    "USES_METHOD": "使用方法",
    "UNDER_CONDITION": "条件",
    "REQUIRES_DEVICE": "需要设备",
    "CONTAINS_TABLE": "包含表格",
}


def get_date_filter():
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')
    
    if start_date and end_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d')
            return start, end
        except:
            pass
    return None, None


@kshzs_bp.route('/neo4j-stats', methods=['GET'])
def neo4j_stats():
    if not Config.NEO4J_DRIVER:
        return jsonify([])
    try:
        start_date, end_date = get_date_filter()
        
        with Config.NEO4J_DRIVER.session() as session:
            if start_date and end_date:
                result = session.run("""
                    MATCH (n)
                    WHERE n.Create_time IS NOT NULL
                    WITH labels(n) AS lbls, count(n) AS cnt, collect(n.Create_time) AS times
                    UNWIND lbls AS lbl
                    UNWIND times AS t
                    WHERE t >= $start AND t <= $end
                    RETURN lbl AS type, count(*) AS count
                    ORDER BY count DESC
                """, start=start_date.isoformat(), end=end_date.isoformat())
            else:
                result = session.run("""
                    MATCH (n)
                    WITH labels(n) AS lbls, count(n) AS cnt
                    UNWIND lbls AS lbl
                    RETURN lbl AS type, cnt AS count
                    ORDER BY count DESC
                """)
            data = []
            for r in result:
                node_type = r["type"]
                chinese_type = NODE_TYPE_MAP.get(node_type, node_type)
                data.append({
                    "type": chinese_type,
                    "count": r["count"],
                    "originalType": node_type
                })
        return jsonify(data)
    except Exception as e:
        logger.error(f"Neo4j stats error: {e}")
        return jsonify([])


@kshzs_bp.route('/get_equipment_tree', methods=['GET'])
def get_equipment_tree():
    return jq_equipment_tree()


@kshzs_bp.route('/get_projects', methods=['GET'])
def get_projects():
    return jq_projects()


@kshzs_bp.route('/get_indicators', methods=['GET'])
def get_indicators():
    return jq_indicators()


@kshzs_bp.route('/file-stats', methods=['GET'])
def file_stats():
    if not Config.NEO4J_DRIVER:
        return jsonify([])
    try:
        start_date, end_date = get_date_filter()
        
        with Config.NEO4J_DRIVER.session() as session:
            if start_date and end_date:
                result = session.run("""
                    MATCH (f:File)
                    WHERE f.Create_time IS NOT None
                    AND f.Create_time >= $start AND f.Create_time <= $end
                    RETURN f.Type AS category, count(f) AS count
                    ORDER BY count DESC
                """, start=start_date.isoformat(), end=end_date.isoformat())
            else:
                result = session.run("""
                    MATCH (f:File)
                    RETURN f.Type AS category, count(f) AS count
                    ORDER BY count DESC
                """)
            data = []
            for r in result:
                cat = r["category"] or "未分类"
                chinese_cat = NODE_TYPE_MAP.get(cat, cat)
                data.append({
                    "category": chinese_cat,
                    "count": r["count"],
                    "originalCategory": r["category"]
                })
        return jsonify(data)
    except Exception as e:
        logger.error(f"File stats error: {e}")
        return jsonify([])


@kshzs_bp.route('/chroma-stats', methods=['GET'])
def chroma_stats():
    if not Config.CHROMA_COLLECTION_OBJ:
        return jsonify({"total": 0, "count": 0})
    try:
        count = Config.CHROMA_COLLECTION_OBJ.count()
        return jsonify({"total": count, "count": count})
    except Exception as e:
        logger.error(f"Chroma stats error: {e}")
        return jsonify({"total": 0, "count": 0})


@kshzs_bp.route('/mysql-stats', methods=['GET'])
def mysql_stats():
    if not Config.MYSQL_POOL:
        return jsonify({"total": 0, "count": 0, "tables": []})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = [r[0] for r in cursor.fetchall()]
                total = 0
                table_counts = []
                for tbl in tables:
                    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
                    cnt = cursor.fetchone()[0]
                    total += cnt
                    table_counts.append({"table": tbl, "count": cnt})
        return jsonify({"total": total, "count": total, "tables": table_counts})
    except Exception as e:
        logger.error(f"MySQL stats error: {e}")
        return jsonify({"total": 0, "count": 0, "tables": []})


@kshzs_bp.route('/minio-stats', methods=['GET'])
def minio_stats():
    if not Config.MINIO_CLIENT:
        return jsonify({"total": 0, "buckets": []})
    try:
        client = Config.MINIO_CLIENT
        bucket_name = Config.MINIO_BUCKET_NAME
        
        objects = client.list_objects(bucket_name, recursive=True)
        total_size = 0
        file_count = 0
        
        for obj in objects:
            total_size += obj.size
            file_count += 1
        
        return jsonify({
            "total": file_count,
            "count": file_count,
            "totalSize": total_size,
            "bucket": bucket_name
        })
    except Exception as e:
        logger.error(f"MinIO stats error: {e}")
        return jsonify({"total": 0, "count": 0, "totalSize": 0})


@kshzs_bp.route('/relation-stats', methods=['GET'])
def relation_stats():
    if not Config.NEO4J_DRIVER:
        return jsonify([])
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) AS relType, count(*) AS count
                ORDER BY count DESC
                LIMIT 20
            """)
            data = []
            for r in result:
                rel_type = r["relType"]
                chinese_type = REL_TYPE_MAP.get(rel_type, rel_type)
                data.append({
                    "type": chinese_type,
                    "count": r["count"],
                    "originalType": rel_type
                })
        return jsonify(data)
    except Exception as e:
        logger.error(f"Relation stats error: {e}")
        return jsonify([])


@kshzs_bp.route('/file-trend', methods=['GET'])
def file_trend():
    if not Config.NEO4J_DRIVER:
        return jsonify([])
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run("""
                MATCH (f:File)
                WHERE f.Create_time IS NOT NULL
                WITH substring(f.Create_time, 0, 7) AS month, count(f) AS count
                RETURN month AS month, count AS count
                ORDER BY month ASC
            """)
            data = [{"month": r["month"], "count": r["count"]} for r in result]
        return jsonify(data)
    except Exception as e:
        logger.error(f"File trend error: {e}")
        return jsonify([])


@kshzs_bp.route('/file-type-stats', methods=['GET'])
def file_type_stats():
    if not Config.NEO4J_DRIVER:
        return jsonify([])
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run("""
                MATCH (f:File)
                RETURN f.File_type AS fileType, count(f) AS count
                ORDER BY count DESC
            """)
            data = []
            for r in result:
                ft = r["fileType"] or "未知"
                data.append({"type": ft, "count": r["count"]})
        return jsonify(data)
    except Exception as e:
        logger.error(f"File type stats error: {e}")
        return jsonify([])


@kshzs_bp.route('/method-stats', methods=['GET'])
def method_stats():
    if not Config.NEO4J_DRIVER:
        return jsonify({"total": 0, "bySource": [], "topMethods": []})
    try:
        with Config.NEO4J_DRIVER.session() as session:
            total_result = session.run("MATCH (m:TestMethod) RETURN count(m) AS total")
            total = total_result.single()["total"]

            source_result = session.run("""
                MATCH (m:TestMethod)
                RETURN m.Source_name AS source, count(m) AS count
                ORDER BY count DESC
                LIMIT 10
            """)
            by_source = [{"source": r["source"] or "未知来源", "count": r["count"]} for r in source_result]

            top_result = session.run("""
                MATCH (m:TestMethod)
                OPTIONAL MATCH (m)-[r]->()
                RETURN m.Name AS name, count(r) AS relCount
                ORDER BY relCount DESC
                LIMIT 10
            """)
            top_methods = [{"name": r["name"] or "未命名", "relCount": r["relCount"]} for r in top_result]

        return jsonify({"total": total, "bySource": by_source, "topMethods": top_methods})
    except Exception as e:
        logger.error(f"Method stats error: {e}")
        return jsonify({"total": 0, "bySource": [], "topMethods": []})


@kshzs_bp.route('/overview', methods=['GET'])
def overview():
    result = {
        "neo4jNodes": 0,
        "neo4jRelations": 0,
        "files": 0,
        "chromaVectors": 0,
        "mysqlRows": 0,
        "minioSize": 0,
        "methods": 0,
    }
    try:
        if Config.NEO4J_DRIVER:
            with Config.NEO4J_DRIVER.session() as session:
                r = session.run("MATCH (n) RETURN count(n) AS c").single()
                result["neo4jNodes"] = r["c"]
                r = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()
                result["neo4jRelations"] = r["c"]
                r = session.run("MATCH (f:File) RETURN count(f) AS c").single()
                result["files"] = r["c"]
                r = session.run("MATCH (m:TestMethod) RETURN count(m) AS c").single()
                result["methods"] = r["c"]
    except Exception as e:
        logger.error(f"Overview Neo4j error: {e}")

    try:
        if Config.CHROMA_COLLECTION_OBJ:
            result["chromaVectors"] = Config.CHROMA_COLLECTION_OBJ.count()
    except Exception as e:
        logger.error(f"Overview Chroma error: {e}")

    try:
        if Config.MYSQL_POOL:
            with Config.MYSQL_POOL.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SHOW TABLES")
                    tables = [r[0] for r in cursor.fetchall()]
                    total = 0
                    for tbl in tables:
                        cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
                        total += cursor.fetchone()[0]
                    result["mysqlRows"] = total
    except Exception as e:
        logger.error(f"Overview MySQL error: {e}")

    try:
        if Config.MINIO_CLIENT:
            objects = Config.MINIO_CLIENT.list_objects(Config.MINIO_BUCKET_NAME, recursive=True)
            total_size = 0
            for obj in objects:
                total_size += obj.size
            result["minioSize"] = total_size
    except Exception as e:
        logger.error(f"Overview MinIO error: {e}")

    return jsonify(result)
