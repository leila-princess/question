import json
import logging
import os
from flask import Blueprint, request, jsonify
from config import Config
from utils.operation_log import log_user_action

logger = logging.getLogger(__name__)
sjkgl_bp = Blueprint('sjkgl', __name__, url_prefix='/api/xtgl/sjkgl')


@sjkgl_bp.route('/db/neo4j/nodes', methods=['GET'])
def neo4j_nodes():
    if not Config.NEO4J_DRIVER:
        return jsonify({"success": False, "error": "Neo4j未连接"})
    node_type = request.args.get('type', '')
    try:
        with Config.NEO4J_DRIVER.session() as session:
            if node_type:
                result = session.run(f"MATCH (n:{node_type}) RETURN n LIMIT 100")
            else:
                result = session.run("MATCH (n) RETURN n LIMIT 100")
            nodes = []
            for r in result:
                node = dict(r["n"])
                node["_labels"] = list(r["n"].labels)
                node["_id"] = node.get("Id", "")
                nodes.append(node)
        return jsonify({"success": True, "data": nodes})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/neo4j/node-types', methods=['GET'])
def neo4j_node_types():
    if not Config.NEO4J_DRIVER:
        return jsonify({"success": False, "error": "Neo4j未连接"})
    try:
        with Config.NEO4J_DRIVER.session() as session:
            result = session.run("CALL db.labels() YIELD label RETURN label ORDER BY label")
            types = [r["label"] for r in result]
        return jsonify({"success": True, "data": types})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/neo4j/node', methods=['POST'])
def neo4j_create_node():
    data = request.get_json()
    if not Config.NEO4J_DRIVER:
        return jsonify({"success": False, "error": "Neo4j未连接"})
    node_type = data.get('type', 'Node')
    props = data.get('properties', {})
    if not props.get('Id'):
        return jsonify({"success": False, "error": "节点必须有Id属性"}), 400
    try:
        with Config.NEO4J_DRIVER.session() as session:
            set_clause = ', '.join([f"n.{k} = ${k}" for k in props.keys()])
            session.run(f"CREATE (n:{node_type} {{{set_clause}}})", props)
        log_user_action(f"数据库管理-Neo4j节点-新增 type={node_type} id={props.get('Id')}")
        return jsonify({"success": True, "message": "节点创建成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/neo4j/node', methods=['PUT'])
def neo4j_update_node():
    data = request.get_json()
    if not Config.NEO4J_DRIVER:
        return jsonify({"success": False, "error": "Neo4j未连接"})
    node_id = data.get('id')
    props = data.get('properties', {})
    if not node_id:
        return jsonify({"success": False, "error": "节点Id不能为空"}), 400
    try:
        with Config.NEO4J_DRIVER.session() as session:
            set_clause = ', '.join([f"n.{k} = ${k}" for k in props.keys()])
            result = session.run(f"MATCH (n {{Id: $node_id}}) SET {set_clause}", {"node_id": node_id, **props})
            if result.consume().counters.nodes_created == 0 and result.consume().counters.properties_set == 0:
                return jsonify({"success": False, "error": "节点不存在"})
        log_user_action(f"数据库管理-Neo4j节点-更新 id={node_id}")
        return jsonify({"success": True, "message": "节点更新成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/neo4j/node', methods=['DELETE'])
def neo4j_delete_node():
    data = request.get_json()
    if not Config.NEO4J_DRIVER:
        return jsonify({"success": False, "error": "Neo4j未连接"})
    node_id = data.get('id')
    if not node_id:
        return jsonify({"success": False, "error": "节点Id不能为空"}), 400
    try:
        with Config.NEO4J_DRIVER.session() as session:
            session.run("MATCH (n {Id: $id}) DETACH DELETE n", id=node_id)
        log_user_action(f"数据库管理-Neo4j节点-删除 id={node_id}")
        return jsonify({"success": True, "message": "节点删除成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/minio/files', methods=['GET'])
def minio_files():
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO未连接"})
    prefix = request.args.get('prefix', '')
    try:
        objects = Config.MINIO_CLIENT.list_objects(
            Config.MINIO_BUCKET_NAME, prefix=prefix, recursive=True
        )
        files = []
        for obj in objects:
            if '/images/' in obj.object_name:
                continue
            try:
                stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, obj.object_name)
                meta = {}
                if stat.metadata:
                    for k, v in stat.metadata.items():
                        meta[k] = v
                files.append({
                    "object_name": obj.object_name,
                    "size": obj.size,
                    "last_modified": str(obj.last_modified),
                    "metadata": meta,
                })
            except:
                pass
        return jsonify({"success": True, "data": files, "count": len(files)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/minio/metadata', methods=['PUT'])
def minio_update_metadata():
    data = request.get_json()
    if not Config.MINIO_CLIENT:
        return jsonify({"success": False, "error": "MinIO未连接"})
    object_name = data.get('object_name')
    updates = data.get('updates', {})
    if not object_name:
        return jsonify({"success": False, "error": "object_name不能为空"}), 400
    try:
        import tempfile
        stat = Config.MINIO_CLIENT.stat_object(Config.MINIO_BUCKET_NAME, object_name)
        existing_meta = {}
        if stat.metadata:
            for k, v in stat.metadata.items():
                existing_meta[k] = v
        for k, v in updates.items():
            key = k if k.startswith("x-amz-meta-") else f"x-amz-meta-{k}"
            existing_meta[key] = str(v)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            Config.MINIO_CLIENT.fget_object(Config.MINIO_BUCKET_NAME, object_name, tmp_path)
            Config.MINIO_CLIENT.fput_object(Config.MINIO_BUCKET_NAME, object_name, tmp_path, metadata=existing_meta)
            os.unlink(tmp_path)
        log_user_action(f"数据库管理-MinIO对象元数据-更新 object={object_name[:200]}")
        return jsonify({"success": True, "message": "元数据更新成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/chroma/stats', methods=['GET'])
def chroma_db_stats():
    if not Config.CHROMA_COLLECTION_OBJ:
        return jsonify({"success": False, "error": "Chroma未连接"})
    try:
        count = Config.CHROMA_COLLECTION_OBJ.count()
        return jsonify({"success": True, "data": {"total_chunks": count}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/chroma/metadata', methods=['GET'])
def chroma_metadata():
    if not Config.CHROMA_COLLECTION_OBJ:
        return jsonify({"success": False, "error": "Chroma未连接"})
    doc_id = request.args.get('doc_id', '')
    limit = int(request.args.get('limit', 50))
    try:
        if doc_id:
            results = Config.CHROMA_COLLECTION_OBJ.get(where={"doc_id": doc_id}, limit=limit, include=['metadatas', 'documents'])
        else:
            results = Config.CHROMA_COLLECTION_OBJ.get(limit=limit, include=['metadatas', 'documents'])
        items = []
        for i, meta in enumerate(results.get('metadatas', [])):
            items.append({
                "id": results.get('ids', [])[i] if i < len(results.get('ids', [])) else '',
                "metadata": meta or {},
                "document_preview": (results.get('documents', [])[i] or '')[:200] if i < len(results.get('documents', [])) else '',
            })
        return jsonify({"success": True, "data": items})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/mysql/tables', methods=['GET'])
def mysql_tables():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = [r[0] for r in cursor.fetchall()]
                table_info = []
                for tbl in tables:
                    cursor.execute(f"SELECT COUNT(*) FROM `{tbl}`")
                    row_count = cursor.fetchone()[0]
                    cursor.execute(f"DESCRIBE `{tbl}`")
                    columns = [{"name": r[0], "type": r[1], "nullable": r[2] == 'YES', "key": r[3], "default": r[4]} for r in cursor.fetchall()]
                    table_info.append({"name": tbl, "row_count": row_count, "columns": columns})
        return jsonify({"success": True, "data": table_info})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _init_metadata_tables():
    if not Config.MYSQL_POOL:
        return False
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mysql_table_columns (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        database_name VARCHAR(100) NOT NULL,
                        table_name VARCHAR(100) NOT NULL,
                        column_name VARCHAR(100) NOT NULL,
                        column_type VARCHAR(100) NOT NULL,
                        data_type VARCHAR(50),
                        is_nullable BOOLEAN DEFAULT TRUE,
                        column_key VARCHAR(10),
                        column_default VARCHAR(255),
                        description TEXT,
                        status VARCHAR(20) DEFAULT '正常',
                        user_id VARCHAR(50) DEFAULT 'admin',
                        user_name VARCHAR(100) DEFAULT '系统管理员',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_db_table (database_name, table_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS neo4j_label_properties (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        label_type VARCHAR(20) NOT NULL DEFAULT 'node',
                        label_name VARCHAR(100) NOT NULL,
                        property_name VARCHAR(100) NOT NULL,
                        property_type VARCHAR(50) NOT NULL,
                        display_name VARCHAR(200),
                        description TEXT,
                        is_required BOOLEAN DEFAULT FALSE,
                        is_indexed BOOLEAN DEFAULT FALSE,
                        is_unique BOOLEAN DEFAULT FALSE,
                        status VARCHAR(20) DEFAULT '正常',
                        user_id VARCHAR(50) DEFAULT 'admin',
                        user_name VARCHAR(100) DEFAULT '系统管理员',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_label_type_name (label_type, label_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chroma_collection_metadata (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        collection_name VARCHAR(200) NOT NULL,
                        field_name VARCHAR(100) NOT NULL,
                        field_type VARCHAR(50) NOT NULL,
                        display_name VARCHAR(200),
                        description TEXT,
                        is_required BOOLEAN DEFAULT FALSE,
                        default_value TEXT,
                        status VARCHAR(20) DEFAULT '正常',
                        user_id VARCHAR(50) DEFAULT 'admin',
                        user_name VARCHAR(100) DEFAULT '系统管理员',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_collection (collection_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS minio_tag_keys (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        bucket_name VARCHAR(100) NOT NULL,
                        tag_key VARCHAR(100) NOT NULL,
                        tag_type VARCHAR(50) NOT NULL,
                        display_name VARCHAR(200),
                        description TEXT,
                        is_required BOOLEAN DEFAULT FALSE,
                        status VARCHAR(20) DEFAULT '正常',
                        user_id VARCHAR(50) DEFAULT 'admin',
                        user_name VARCHAR(100) DEFAULT '系统管理员',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_bucket (bucket_name)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)

                def _ensure_column(table_name: str, column_name: str, column_def_sql: str):
                    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
                    if not cursor.fetchone():
                        cursor.execute(
                            f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_def_sql}"
                        )
                        logger.info(f"表 {table_name} 缺失列 {column_name}，已自动补齐")

                # 兼容旧版本数据库：历史表可能缺少 user_id/user_name 字段
                _ensure_column("mysql_table_columns", "user_id", "VARCHAR(50) DEFAULT 'admin'")
                _ensure_column("mysql_table_columns", "user_name", "VARCHAR(100) DEFAULT '系统管理员'")
                _ensure_column("neo4j_label_properties", "user_id", "VARCHAR(50) DEFAULT 'admin'")
                _ensure_column("neo4j_label_properties", "user_name", "VARCHAR(100) DEFAULT '系统管理员'")
                _ensure_column("chroma_collection_metadata", "user_id", "VARCHAR(50) DEFAULT 'admin'")
                _ensure_column("chroma_collection_metadata", "user_name", "VARCHAR(100) DEFAULT '系统管理员'")
                _ensure_column("minio_tag_keys", "user_id", "VARCHAR(50) DEFAULT 'admin'")
                _ensure_column("minio_tag_keys", "user_name", "VARCHAR(100) DEFAULT '系统管理员'")

                from .metadata_sync import ensure_neo4j_label_unique_index
                ensure_neo4j_label_unique_index(cursor)
                
                conn.commit()
                logger.info("数据库元数据表初始化完成")
                return True
    except Exception as e:
        logger.error(f"初始化元数据表失败: {e}")
        return False


@sjkgl_bp.route('/db/metadata/init', methods=['POST'])
def init_metadata():
    success = _init_metadata_tables()
    if success:
        log_user_action("数据库管理-元数据表-初始化")
        return jsonify({"success": True, "message": "元数据表初始化成功"})
    return jsonify({"success": False, "error": "元数据表初始化失败"}), 500


@sjkgl_bp.route('/db/metadata/mysql/tree', methods=['GET'])
def mysql_metadata_tree():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    _init_metadata_tables()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT DISTINCT database_name, table_name 
                    FROM mysql_table_columns 
                    ORDER BY database_name, table_name
                """)
                rows = cursor.fetchall()
                
                tree = {}
                for db_name, table_name in rows:
                    if db_name not in tree:
                        tree[db_name] = []
                    tree[db_name].append(table_name)
                
                result = []
                for db_name, tables in tree.items():
                    result.append({
                        "title": db_name,
                        "key": f"db_{db_name}",
                        "children": [
                            {"title": t, "key": f"table_{db_name}_{t}", "isLeaf": True}
                            for t in tables
                        ]
                    })
                
                return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"获取MySQL元数据树失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/mysql/columns', methods=['GET'])
def mysql_columns():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    db_name = request.args.get('database', '')
    table_name = request.args.get('table', '')
    
    if not db_name or not table_name:
        return jsonify({"success": False, "error": "database和table参数必填"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, column_name, column_type, data_type, is_nullable, column_key, 
                           column_default, description, status
                    FROM mysql_table_columns
                    WHERE database_name = %s AND table_name = %s
                    ORDER BY id
                """, (db_name, table_name))
                
                columns = []
                for r in cursor.fetchall():
                    columns.append({
                        "id": r[0],
                        "column_name": r[1],
                        "column_type": r[2],
                        "data_type": r[3],
                        "is_nullable": bool(r[4]),
                        "column_key": r[5],
                        "column_default": r[6],
                        "description": r[7],
                        "status": r[8]
                    })
                
                return jsonify({"success": True, "data": columns})
    except Exception as e:
        logger.error(f"获取MySQL字段元数据失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/mysql/column', methods=['POST'])
def create_mysql_column():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    db_name = data.get('database_name')
    table_name = data.get('table_name')
    column_name = data.get('column_name')
    column_type = data.get('column_type', 'VARCHAR(255)')
    is_nullable = data.get('is_nullable', True)
    column_default = data.get('column_default')
    description = data.get('description', '')
    data_type = (data.get('data_type') or '').strip() or (str(column_type).split('(')[0].strip() if column_type else '')
    # 与前端「数据类型」或 column_type 前缀一致，供列表展示
    user_id = data.get('user_id', 'admin')
    user_name = data.get('user_name', '系统管理员')
    
    if not all([db_name, table_name, column_name]):
        return jsonify({"success": False, "error": "缺少必填字段"}), 400
    
    def _id_quote(ident):
        return str(ident).replace('`', '``')
    
    def _str_quote(s):
        return str(s).replace("'", "''")
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO mysql_table_columns 
                    (database_name, table_name, column_name, column_type, data_type, is_nullable, 
                     column_default, description, status, user_id, user_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '正常', %s, %s)
                """, (db_name, table_name, column_name, column_type, data_type, is_nullable, 
                      column_default, description, user_id, user_name))
                
                try:
                    null_sql = "NULL" if is_nullable else "NOT NULL"
                    default_sql = ""
                    if column_default is not None and str(column_default).strip() != "":
                        dft = str(column_default).strip()
                        ct_up = str(column_type).upper()
                        if any(x in ct_up for x in ('INT', 'BIGINT', 'SMALLINT', 'TINYINT', 'DECIMAL', 'FLOAT', 'DOUBLE', 'BOOL', 'BIT', 'YEAR')):
                            default_sql = f" DEFAULT {dft}"
                        else:
                            default_sql = f" DEFAULT '{_str_quote(dft)}'"
                    desc_for_cmt = (data.get('column_comment') or description or '').strip()
                    comment_sql = f" COMMENT '{_str_quote(desc_for_cmt)}'" if desc_for_cmt else ""
                    alter_sql = (
                        f"ALTER TABLE `{_id_quote(db_name)}`.`{_id_quote(table_name)}` "
                        f"ADD COLUMN `{_id_quote(column_name)}` {column_type} {null_sql}{default_sql}{comment_sql}"
                    )
                    cursor.execute(alter_sql)
                except Exception as e:
                    logger.warning(f"同步修改实际表结构失败: {e}")
                
                conn.commit()
                log_user_action(f"数据库管理-MySQL元数据-新增字段 {db_name}.{table_name}.{column_name}")
                return jsonify({"success": True, "message": "字段创建成功"})
    except Exception as e:
        logger.error(f"创建MySQL字段元数据失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/mysql/column/<int:column_id>', methods=['PUT'])
def update_mysql_column(column_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT database_name, table_name, column_name, column_type
                    FROM mysql_table_columns WHERE id = %s
                """, (column_id,))
                row = cursor.fetchone()
                if not row:
                    return jsonify({"success": False, "error": "字段不存在"}), 404
                
                db_name, table_name, old_column_name, old_column_type = row
                new_column_name = data.get('column_name', old_column_name)
                new_column_type = data.get('column_type', old_column_type)
                is_nullable = data.get('is_nullable', True)
                column_default = data.get('column_default')
                new_description = data.get('description', data.get('column_comment', ''))
                new_data_type = (data.get('data_type') or '').strip() or (str(new_column_type).split('(')[0].strip() if new_column_type else '')
                
                cursor.execute("""
                    UPDATE mysql_table_columns 
                    SET column_name = %s, column_type = %s, data_type = %s, is_nullable = %s,
                        column_default = %s, description = %s
                    WHERE id = %s
                """, (new_column_name, new_column_type, new_data_type, is_nullable, 
                      column_default, new_description, column_id))
                
                try:
                    if old_column_name != new_column_name:
                        cursor.execute(f"ALTER TABLE `{db_name}`.`{table_name}` CHANGE `{old_column_name}` `{new_column_name}` {new_column_type}")
                    elif old_column_type != new_column_type:
                        cursor.execute(f"ALTER TABLE `{db_name}`.`{table_name}` MODIFY `{new_column_name}` {new_column_type}")
                except Exception as e:
                    logger.warning(f"同步修改实际表结构失败: {e}")
                
                conn.commit()
                log_user_action(f"数据库管理-MySQL元数据-更新字段 id={column_id}")
                return jsonify({"success": True, "message": "字段更新成功"})
    except Exception as e:
        logger.error(f"更新MySQL字段元数据失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/mysql/column/<int:column_id>', methods=['DELETE'])
def delete_mysql_column(column_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT database_name, table_name, column_name
                    FROM mysql_table_columns WHERE id = %s
                """, (column_id,))
                row = cursor.fetchone()
                if not row:
                    return jsonify({"success": False, "error": "字段不存在"}), 404
                
                db_name, table_name, column_name = row
                
                cursor.execute("DELETE FROM mysql_table_columns WHERE id = %s", (column_id,))
                
                try:
                    cursor.execute(f"ALTER TABLE `{db_name}`.`{table_name}` DROP COLUMN `{column_name}`")
                except Exception as e:
                    logger.warning(f"同步删除实际表字段失败: {e}")
                
                conn.commit()
                log_user_action(f"数据库管理-MySQL元数据-删除字段 id={column_id} {db_name}.{table_name}.{column_name}")
                return jsonify({"success": True, "message": "字段删除成功"})
    except Exception as e:
        logger.error(f"删除MySQL字段元数据失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/neo4j/tree', methods=['GET'])
def neo4j_metadata_tree():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    _init_metadata_tables()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT DISTINCT label_name 
                    FROM neo4j_label_properties 
                    WHERE label_type = 'node'
                    ORDER BY label_name
                """)
                mysql_node_labels = [r[0] for r in cursor.fetchall()]
                
                cursor.execute("""
                    SELECT DISTINCT label_name 
                    FROM neo4j_label_properties 
                    WHERE label_type = 'relation'
                    ORDER BY label_name
                """)
                mysql_relation_labels = [r[0] for r in cursor.fetchall()]
                
                neo4j_node_labels = []
                if Config.NEO4J_DRIVER:
                    try:
                        with Config.NEO4J_DRIVER.session() as session:
                            result = session.run("CALL db.labels() YIELD label RETURN label ORDER BY label")
                            neo4j_node_labels = [r["label"] for r in result]
                    except Exception as e:
                        logger.warning(f"从Neo4j获取节点标签失败: {e}")
                
                neo4j_relation_labels = []
                if Config.NEO4J_DRIVER:
                    try:
                        with Config.NEO4J_DRIVER.session() as session:
                            result = session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType")
                            neo4j_relation_labels = [r["relationshipType"] for r in result]
                    except Exception as e:
                        logger.warning(f"从Neo4j获取关系类型失败: {e}")
                
                node_labels = neo4j_node_labels if neo4j_node_labels else mysql_node_labels
                relation_labels = neo4j_relation_labels if neo4j_relation_labels else mysql_relation_labels
                
                result = [
                    {
                        "title": "节点",
                        "key": "neo4j_nodes",
                        "children": [
                            {"title": label, "key": f"node_{label}", "isLeaf": True}
                            for label in node_labels
                        ]
                    },
                    {
                        "title": "关系",
                        "key": "neo4j_relations",
                        "children": [
                            {"title": label, "key": f"relation_{label}", "isLeaf": True}
                            for label in relation_labels
                        ]
                    }
                ]
                
                return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"获取Neo4j元数据树失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/neo4j/properties', methods=['GET'])
def neo4j_properties():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    label_type = request.args.get('type', '')
    label_name = request.args.get('label', '')
    
    if not label_type or not label_name:
        return jsonify({"success": False, "error": "type和label参数必填"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, property_name, property_type, display_name, description, 
                           is_required, is_indexed, is_unique, status
                    FROM neo4j_label_properties
                    WHERE label_type = %s AND label_name = %s
                    ORDER BY id
                """, (label_type, label_name))
                
                properties = []
                for r in cursor.fetchall():
                    properties.append({
                        "id": r[0],
                        "property_name": r[1],
                        "property_type": r[2],
                        "display_name": r[3],
                        "description": r[4],
                        "is_required": bool(r[5]),
                        "is_indexed": bool(r[6]),
                        "is_unique": bool(r[7]),
                        "status": r[8]
                    })
                
                return jsonify({"success": True, "data": properties})
    except Exception as e:
        logger.error(f"获取Neo4j属性元数据失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/neo4j/label', methods=['POST'])
def create_neo4j_label():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    label_type = data.get('label_type')
    label_name = data.get('label_name')
    print("label_type",label_type)
    if not label_type or not label_name:
        return jsonify({"success": False, "error": "缺少必填字段"}), 400
    
    if label_type not in ['node', 'relation']:
        return jsonify({"success": False, "error": "label_type必须是node或relation"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) FROM neo4j_label_properties
                    WHERE label_type = %s AND label_name = %s
                """, (label_type, label_name))
                if cursor.fetchone()[0] > 0:
                    return jsonify({"success": False, "error": "标签已存在"}), 400
                
                cursor.execute("""
                    INSERT INTO neo4j_label_properties 
                    (label_type, label_name, property_name, property_type, is_required, description)
                    VALUES (%s, %s, 'Id', 'STRING', TRUE, '唯一标识符')
                """, (label_type, label_name))
                
                if Config.NEO4J_DRIVER:
                    try:
                        with Config.NEO4J_DRIVER.session() as neo4j_session:
                            if label_type == 'node':
                                import re
                                safe_label = re.sub(r'[^a-zA-Z_]', '_', label_name)
                                if not safe_label[0].isalpha() and not safe_label.startswith('_'):
                                    safe_label = 'L_' + safe_label
                                if safe_label != label_name:
                                    logger.warning(f"标签名已转换为安全的Neo4j标签: {label_name} -> {safe_label}")
                                neo4j_session.run(f"CREATE (n:{safe_label} {{Id: $id}})", id=f"TEMP_{safe_label}")
                    except Exception as e:
                        logger.warning(f"同步创建Neo4j标签失败: {e}")
                
                conn.commit()
                log_user_action(f"数据库管理-Neo4j元数据-新增标签 type={label_type} name={label_name}")
                return jsonify({"success": True, "message": "标签创建成功"})
    except Exception as e:
        logger.error(f"创建Neo4j标签失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/neo4j/property', methods=['POST'])
def create_neo4j_property():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    label_type = data.get('label_type', 'node')
    if label_type not in ('node', 'relation'):
        label_type = 'node'
    label_name = data.get('label_name')
    property_name = data.get('property_name')
    property_type = data.get('property_type', 'STRING')
    display_name = data.get('display_name', '')
    is_required = data.get('is_required', False)
    is_indexed = data.get('is_indexed', False)
    is_unique = data.get('is_unique', False)
    description = data.get('description', '')
    user_id = data.get('user_id', 'admin')
    user_name = data.get('user_name', '系统管理员')
    
    if not all([label_name, property_name]):
        return jsonify({"success": False, "error": "缺少必填字段"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO neo4j_label_properties 
                    (label_type, label_name, property_name, property_type, display_name, is_required, is_indexed, is_unique, description, status, user_id, user_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '正常', %s, %s)
                """, (label_type, label_name, property_name, property_type, display_name, is_required, is_indexed, is_unique, description, user_id, user_name))
                
                conn.commit()
                log_user_action(f"数据库管理-Neo4j元数据-新增属性 label={label_name} prop={property_name}")
                return jsonify({"success": True, "message": "属性创建成功"})
    except Exception as e:
        logger.error(f"创建Neo4j属性失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/neo4j/property/<int:property_id>', methods=['PUT'])
def update_neo4j_property(property_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE neo4j_label_properties 
                    SET property_name = %s, property_type = %s, display_name = %s, is_required = %s, is_indexed = %s, is_unique = %s, description = %s, status = %s
                    WHERE id = %s
                """, (data.get('property_name'), data.get('property_type'),
                      data.get('display_name', ''),
                      data.get('is_required', False), data.get('is_indexed', False), data.get('is_unique', False),
                      data.get('description', ''), data.get('status', '正常'), property_id))
                
                conn.commit()
                log_user_action(f"数据库管理-Neo4j元数据-更新属性 id={property_id}")
                return jsonify({"success": True, "message": "属性更新成功"})
    except Exception as e:
        logger.error(f"更新Neo4j属性失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/neo4j/property/<int:property_id>', methods=['DELETE'])
def delete_neo4j_property(property_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM neo4j_label_properties WHERE id = %s", (property_id,))
                conn.commit()
                log_user_action(f"数据库管理-Neo4j元数据-删除属性 id={property_id}")
                return jsonify({"success": True, "message": "属性删除成功"})
    except Exception as e:
        logger.error(f"删除Neo4j属性失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/neo4j/label/<label_type>/<label_name>', methods=['DELETE'])
def delete_neo4j_label(label_type: str, label_name: str):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    DELETE FROM neo4j_label_properties
                    WHERE label_type = %s AND label_name = %s
                """, (label_type, label_name))
                
                if Config.NEO4J_DRIVER and label_type == 'node':
                    try:
                        with Config.NEO4J_DRIVER.session() as neo4j_session:
                            neo4j_session.run(f"MATCH (n:{label_name}) DETACH DELETE n")
                    except Exception as e:
                        logger.warning(f"同步删除Neo4j节点失败: {e}")
                
                conn.commit()
                log_user_action(f"数据库管理-Neo4j元数据-删除标签 type={label_type} name={label_name}")
                return jsonify({"success": True, "message": "标签删除成功"})
    except Exception as e:
        logger.error(f"删除Neo4j标签失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/chroma/tree', methods=['GET'])
def chroma_metadata_tree():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    _init_metadata_tables()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT DISTINCT collection_name 
                    FROM chroma_collection_metadata 
                    ORDER BY collection_name
                """)
                collections = [r[0] for r in cursor.fetchall()]
                
                result = [
                    {
                        "title": coll,
                        "key": f"collection_{coll}",
                        "isLeaf": True
                    }
                    for coll in collections
                ]
                
                return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"获取Chroma元数据树失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/chroma/metadata', methods=['GET'])
def chroma_collection_metadata():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    collection_name = request.args.get('collection', '')
    
    if not collection_name:
        return jsonify({"success": False, "error": "collection参数必填"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, field_name, field_type, display_name, description, 
                           is_required, default_value, status
                    FROM chroma_collection_metadata
                    WHERE collection_name = %s
                    ORDER BY id
                """, (collection_name,))
                
                metadata = []
                for r in cursor.fetchall():
                    metadata.append({
                        "id": r[0],
                        "field_name": r[1],
                        "field_type": r[2],
                        "display_name": r[3],
                        "description": r[4],
                        "is_required": bool(r[5]),
                        "default_value": r[6],
                        "status": r[7]
                    })
                
                return jsonify({"success": True, "data": metadata})
    except Exception as e:
        logger.error(f"获取Chroma元数据失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/minio/tree', methods=['GET'])
def minio_metadata_tree():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    _init_metadata_tables()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT DISTINCT bucket_name 
                    FROM minio_tag_keys 
                    ORDER BY bucket_name
                """)
                buckets = [r[0] for r in cursor.fetchall()]
                
                result = [
                    {
                        "title": bucket,
                        "key": f"bucket_{bucket}",
                        "isLeaf": True
                    }
                    for bucket in buckets
                ]
                
                return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"获取MinIO元数据树失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/minio/metadata', methods=['GET'])
def minio_metadata():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    bucket_name = request.args.get('bucket', '')
    
    if not bucket_name:
        return jsonify({"success": False, "error": "bucket参数必填"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, tag_key, tag_type, display_name, description, 
                           is_required, status
                    FROM minio_tag_keys
                    WHERE bucket_name = %s
                    ORDER BY id
                """, (bucket_name,))
                
                metadata = []
                for r in cursor.fetchall():
                    metadata.append({
                        "id": r[0],
                        "tag_key": r[1],
                        "tag_type": r[2],
                        "display_name": r[3],
                        "description": r[4],
                        "is_required": bool(r[5]),
                        "status": r[6]
                    })
                
                return jsonify({"success": True, "data": metadata})
    except Exception as e:
        logger.error(f"获取MinIO元数据失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/chroma/field', methods=['POST'])
def create_chroma_field():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    collection_name = data.get('collection_name')
    field_name = data.get('field_name')
    field_type = data.get('field_type', 'string')
    display_name = data.get('display_name', '')
    description = data.get('description', '')
    is_required = data.get('is_required', False)
    default_value = data.get('default_value', '')
    user_id = data.get('user_id', 'admin')
    user_name = data.get('user_name', '系统管理员')
    
    if not all([collection_name, field_name]):
        return jsonify({"success": False, "error": "缺少必填字段"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO chroma_collection_metadata 
                    (collection_name, field_name, field_type, display_name, description, is_required, default_value, status, user_id, user_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, '正常', %s, %s)
                """, (collection_name, field_name, field_type, display_name, description, is_required, default_value, user_id, user_name))
                conn.commit()
                log_user_action(f"数据库管理-Chroma元数据-新增字段 collection={collection_name} field={field_name}")
                return jsonify({"success": True, "message": "字段创建成功"})
    except Exception as e:
        logger.error(f"创建Chroma字段失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/chroma/field/<int:field_id>', methods=['PUT'])
def update_chroma_field(field_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE chroma_collection_metadata 
                    SET display_name = %s, description = %s, is_required = %s, default_value = %s, status = %s
                    WHERE id = %s
                """, (data.get('display_name'), data.get('description'), 
                      data.get('is_required', False), data.get('default_value'), 
                      data.get('status', '正常'), field_id))
                conn.commit()
                log_user_action(f"数据库管理-Chroma元数据-更新字段 id={field_id}")
                return jsonify({"success": True, "message": "字段更新成功"})
    except Exception as e:
        logger.error(f"更新Chroma字段失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/chroma/field/<int:field_id>', methods=['DELETE'])
def delete_chroma_field(field_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM chroma_collection_metadata WHERE id = %s", (field_id,))
                conn.commit()
                log_user_action(f"数据库管理-Chroma元数据-删除字段 id={field_id}")
                return jsonify({"success": True, "message": "字段删除成功"})
    except Exception as e:
        logger.error(f"删除Chroma字段失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/minio/tag', methods=['POST'])
def create_minio_tag():
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    bucket_name = data.get('bucket_name')
    tag_key = data.get('tag_key')
    tag_type = data.get('tag_type', 'string')
    display_name = data.get('display_name', '')
    description = data.get('description', '')
    is_required = data.get('is_required', False)
    user_id = data.get('user_id', 'admin')
    user_name = data.get('user_name', '系统管理员')
    
    if not all([bucket_name, tag_key]):
        return jsonify({"success": False, "error": "缺少必填字段"}), 400
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO minio_tag_keys 
                    (bucket_name, tag_key, tag_type, display_name, description, is_required, status, user_id, user_name)
                    VALUES (%s, %s, %s, %s, %s, %s, '正常', %s, %s)
                """, (bucket_name, tag_key, tag_type, display_name, description, is_required, user_id, user_name))
                conn.commit()
                log_user_action(f"数据库管理-MinIO元数据-新增标签 bucket={bucket_name} key={tag_key}")
                return jsonify({"success": True, "message": "标签创建成功"})
    except Exception as e:
        logger.error(f"创建MinIO标签失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/minio/tag/<int:tag_id>', methods=['PUT'])
def update_minio_tag(tag_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    data = request.get_json()
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE minio_tag_keys 
                    SET display_name = %s, description = %s, is_required = %s, status = %s
                    WHERE id = %s
                """, (data.get('display_name'), data.get('description'), 
                      data.get('is_required', False), data.get('status', '正常'), tag_id))
                conn.commit()
                log_user_action(f"数据库管理-MinIO元数据-更新标签 id={tag_id}")
                return jsonify({"success": True, "message": "标签更新成功"})
    except Exception as e:
        logger.error(f"更新MinIO标签失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/db/metadata/minio/tag/<int:tag_id>', methods=['DELETE'])
def delete_minio_tag(tag_id: int):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM minio_tag_keys WHERE id = %s", (tag_id,))
                conn.commit()
                log_user_action(f"数据库管理-MinIO元数据-删除标签 id={tag_id}")
                return jsonify({"success": True, "message": "标签删除成功"})
    except Exception as e:
        logger.error(f"删除MinIO标签失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@sjkgl_bp.route('/sync-metadata', methods=['POST'])
def sync_metadata():
    from .metadata_sync import run_metadata_sync

    data = request.get_json(silent=True) or {}
    targets = data.get("targets")
    result = run_metadata_sync(targets, init_tables_fn=_init_metadata_tables)
    if result.get("success"):
        log_user_action(f"数据库管理-元数据同步 targets={targets or 'all'}")
        summary = result.get("data") or {}
        parts = []
        for name, stats in summary.items():
            if stats.get("skipped"):
                parts.append(f"{name}:跳过({stats.get('reason', '')})")
            else:
                parts.append(
                    f"{name}:+{stats.get('added', 0)}/~{stats.get('updated', 0)}/-{stats.get('removed', 0)}"
                )
        message = "元数据同步完成：" + "；".join(parts) if parts else "元数据同步完成"
        return jsonify({"success": True, "message": message, "data": summary})
    return jsonify({"success": False, "error": result.get("error", "同步失败"), "data": result.get("data")}), 500


_init_metadata_tables()
