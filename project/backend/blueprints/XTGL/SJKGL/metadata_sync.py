"""从 MySQL / Neo4j / MinIO / Chroma 实际结构同步元数据目录表。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from config import Config

logger = logging.getLogger(__name__)

_MYSQL_SYSTEM_DBS = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
_NEO4J_UK_OLD = "uk_label_prop"
_NEO4J_UK_NEW = "uk_label_type_name_prop"


def ensure_neo4j_label_unique_index(cursor) -> None:
    """旧库唯一键仅 (label_name, property_name)，与 node/relation 并存冲突，需迁移。"""
    try:
        cursor.execute(
            """
            DELETE t1 FROM neo4j_label_properties t1
            INNER JOIN neo4j_label_properties t2
            ON t1.label_type = t2.label_type
               AND t1.label_name = t2.label_name
               AND t1.property_name = t2.property_name
               AND t1.id > t2.id
            """
        )
        cursor.execute("SHOW INDEX FROM neo4j_label_properties")
        key_names = {row[2] for row in cursor.fetchall()}

        if _NEO4J_UK_OLD in key_names:
            cursor.execute(f"ALTER TABLE neo4j_label_properties DROP INDEX {_NEO4J_UK_OLD}")
            logger.info("已移除 neo4j_label_properties 旧唯一键 %s", _NEO4J_UK_OLD)
            key_names.discard(_NEO4J_UK_OLD)

        if _NEO4J_UK_NEW not in key_names:
            cursor.execute(
                f"""
                ALTER TABLE neo4j_label_properties
                ADD UNIQUE KEY {_NEO4J_UK_NEW} (label_type, label_name, property_name)
                """
            )
            logger.info("已添加 neo4j_label_properties 唯一键 %s", _NEO4J_UK_NEW)
    except Exception as exc:
        logger.warning("neo4j_label_properties 唯一键迁移未完成: %s", exc)


def _neo4j_prop_type(value: Any) -> str:
    if value is None:
        return "STRING"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, (list, tuple)):
        return "LIST"
    return "STRING"


def _minio_tag_type(value: Any) -> str:
    if value is None:
        return "STRING"
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return "BOOLEAN"
    try:
        int(text)
        return "INTEGER"
    except ValueError:
        pass
    try:
        float(text)
        return "FLOAT"
    except ValueError:
        pass
    return "STRING"


def _normalize_minio_tag_key(key: str) -> str:
    for prefix in ("x-amz-meta-", "X-Amz-Meta-"):
        if key.startswith(prefix):
            return key[len(prefix) :]
    return key


def sync_mysql_metadata(cursor) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATA_TYPE,
               IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT, COLUMN_COMMENT
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """
    )
    live_rows = cursor.fetchall()

    cursor.execute(
        """
        SELECT id, database_name, table_name, column_name, description, status
        FROM mysql_table_columns
        """
    )
    existing = {
        (r[1], r[2], r[3]): {"id": r[0], "description": r[4] or "", "status": r[5] or "正常"}
        for r in cursor.fetchall()
    }

    live_keys: Set[Tuple[str, str, str]] = set()
    added = updated = 0

    for row in live_rows:
        db_name, table_name, column_name, column_type, data_type, is_nullable, column_key, column_default, column_comment = row
        key = (db_name, table_name, column_name)
        live_keys.add(key)
        nullable = str(is_nullable).upper() == "YES"
        data_type_norm = (data_type or "").strip() or (
            str(column_type).split("(")[0].strip() if column_type else ""
        )
        desc_from_db = (column_comment or "").strip()

        if key in existing:
            old = existing[key]
            description = old["description"] or desc_from_db
            cursor.execute(
                """
                UPDATE mysql_table_columns
                SET column_type = %s, data_type = %s, is_nullable = %s,
                    column_key = %s, column_default = %s, description = %s
                WHERE id = %s
                """,
                (
                    column_type,
                    data_type_norm,
                    nullable,
                    column_key or "",
                    column_default,
                    description,
                    old["id"],
                ),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO mysql_table_columns
                (database_name, table_name, column_name, column_type, data_type,
                 is_nullable, column_key, column_default, description, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, '正常')
                """,
                (
                    db_name,
                    table_name,
                    column_name,
                    column_type,
                    data_type_norm,
                    nullable,
                    column_key or "",
                    column_default,
                    desc_from_db,
                ),
            )
            added += 1

    removed = 0
    for key, old in existing.items():
        if key not in live_keys:
            cursor.execute("DELETE FROM mysql_table_columns WHERE id = %s", (old["id"],))
            removed += 1

    return {"added": added, "updated": updated, "removed": removed, "total_live": len(live_keys)}


def _neo4j_label_props(session, label: str, *, is_relation: bool) -> Dict[str, str]:
    safe_label = label.replace("`", "``")
    if is_relation:
        cypher = f"""
            MATCH ()-[r:`{safe_label}`]->()
            WITH r LIMIT 500
            UNWIND keys(r) AS prop
            RETURN DISTINCT prop
        """
        sample_cypher = f"""
            MATCH ()-[r:`{safe_label}`]->()
            WHERE r[$prop] IS NOT NULL
            RETURN r[$prop] AS v LIMIT 1
        """
    else:
        cypher = f"""
            MATCH (n:`{safe_label}`)
            WITH n LIMIT 500
            UNWIND keys(n) AS prop
            RETURN DISTINCT prop
        """
        sample_cypher = f"""
            MATCH (n:`{safe_label}`)
            WHERE n[$prop] IS NOT NULL
            RETURN n[$prop] AS v LIMIT 1
        """

    props: Dict[str, str] = {}
    result = session.run(cypher)
    for record in result:
        prop = record["prop"]
        if not prop:
            continue
        try:
            sample = session.run(sample_cypher, prop=prop).single()
            props[prop] = _neo4j_prop_type(sample["v"] if sample else None)
        except Exception:
            props[prop] = "STRING"
    return props


def sync_neo4j_metadata(cursor) -> Dict[str, Any]:
    if not Config.NEO4J_DRIVER:
        return {"skipped": True, "reason": "Neo4j未连接"}

    ensure_neo4j_label_unique_index(cursor)

    cursor.execute(
        """
        SELECT id, label_type, label_name, property_name,
               display_name, description, is_required, is_indexed, is_unique, status
        FROM neo4j_label_properties
        """
    )
    existing: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in cursor.fetchall():
        key = (r[1], r[2], r[3])
        if key in existing:
            continue
        existing[key] = {
            "id": r[0],
            "display_name": r[4] or "",
            "description": r[5] or "",
            "is_required": bool(r[6]),
            "is_indexed": bool(r[7]),
            "is_unique": bool(r[8]),
            "status": r[9] or "正常",
        }

    live_keys: Set[Tuple[str, str, str]] = set()
    added = updated = 0
    live_labels: List[Tuple[str, str]] = []

    with Config.NEO4J_DRIVER.session() as session:
        node_labels = [r["label"] for r in session.run("CALL db.labels() YIELD label RETURN label")]
        rel_labels = [
            r["relationshipType"]
            for r in session.run(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
            )
        ]

        live_labels = [("node", lb) for lb in node_labels] + [("relation", lb) for lb in rel_labels]

        for label_type, label_name in live_labels:
            is_relation = label_type == "relation"
            try:
                props = _neo4j_label_props(session, label_name, is_relation=is_relation)
            except Exception as exc:
                logger.warning("Neo4j 标签 %s(%s) 属性扫描失败: %s", label_type, label_name, exc)
                props = {"Id": "STRING"} if not is_relation else {}

            if not is_relation and "Id" not in props:
                props["Id"] = "STRING"

            for prop_name, prop_type in props.items():
                key = (label_type, label_name, prop_name)
                live_keys.add(key)
                is_required = prop_name == "Id"

                if key in existing:
                    old = existing[key]
                    cursor.execute(
                        """
                        UPDATE neo4j_label_properties
                        SET property_type = %s
                        WHERE id = %s
                        """,
                        (prop_type, old["id"]),
                    )
                    updated += 1
                else:
                    cursor.execute(
                        """
                        INSERT INTO neo4j_label_properties
                        (label_type, label_name, property_name, property_type,
                         display_name, description, is_required, is_indexed, is_unique, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, FALSE, '正常')
                        ON DUPLICATE KEY UPDATE property_type = VALUES(property_type)
                        """,
                        (
                            label_type,
                            label_name,
                            prop_name,
                            prop_type,
                            prop_name,
                            "",
                            is_required,
                        ),
                    )
                    if cursor.rowcount == 1:
                        added += 1
                    else:
                        updated += 1

    removed = 0
    for key, old in existing.items():
        if key not in live_keys:
            cursor.execute("DELETE FROM neo4j_label_properties WHERE id = %s", (old["id"],))
            removed += 1

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "labels": len(live_labels),
    }


def sync_minio_metadata(cursor, *, max_objects_per_bucket: int = 500) -> Dict[str, Any]:
    if not Config.MINIO_CLIENT:
        return {"skipped": True, "reason": "MinIO未连接"}

    cursor.execute(
        """
        SELECT id, bucket_name, tag_key, display_name, description, is_required, status
        FROM minio_tag_keys
        """
    )
    existing = {
        (r[1], r[2]): {
            "id": r[0],
            "display_name": r[3] or "",
            "description": r[4] or "",
            "is_required": bool(r[5]),
            "status": r[6] or "正常",
        }
        for r in cursor.fetchall()
    }

    live_keys: Set[Tuple[str, str]] = set()
    added = updated = 0
    bucket_names: List[str] = []

    try:
        bucket_names = [b.name for b in Config.MINIO_CLIENT.list_buckets()]
    except Exception as exc:
        logger.warning("MinIO list_buckets 失败，回退默认 bucket: %s", exc)
        bucket_names = [Config.MINIO_BUCKET_NAME]

    for bucket_name in bucket_names:
        tag_types: Dict[str, str] = {}
        scanned = 0
        try:
            for obj in Config.MINIO_CLIENT.list_objects(bucket_name, recursive=True):
                if scanned >= max_objects_per_bucket:
                    break
                scanned += 1
                try:
                    stat = Config.MINIO_CLIENT.stat_object(bucket_name, obj.object_name)
                except Exception:
                    continue
                if not stat.metadata:
                    continue
                for raw_key, raw_val in stat.metadata.items():
                    tag_key = _normalize_minio_tag_key(raw_key)
                    if not tag_key:
                        continue
                    tag_types[tag_key] = _minio_tag_type(raw_val)
        except Exception as exc:
            logger.warning("MinIO bucket %s 扫描失败: %s", bucket_name, exc)
            continue

        for tag_key, tag_type in tag_types.items():
            key = (bucket_name, tag_key)
            live_keys.add(key)
            if key in existing:
                old = existing[key]
                cursor.execute(
                    "UPDATE minio_tag_keys SET tag_type = %s WHERE id = %s",
                    (tag_type, old["id"]),
                )
                updated += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO minio_tag_keys
                    (bucket_name, tag_key, tag_type, display_name, description, is_required, status)
                    VALUES (%s, %s, %s, %s, '', FALSE, '正常')
                    """,
                    (bucket_name, tag_key, tag_type, tag_key),
                )
                added += 1

    removed = 0
    for key, old in existing.items():
        if key not in live_keys:
            cursor.execute("DELETE FROM minio_tag_keys WHERE id = %s", (old["id"],))
            removed += 1

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "buckets": len(bucket_names),
    }


def sync_chroma_metadata(cursor, *, sample_limit: int = 300) -> Dict[str, Any]:
    collections: List[Any] = []
    if Config.CHROMA_CLIENT:
        try:
            collections = list(Config.CHROMA_CLIENT.list_collections())
        except Exception as exc:
            logger.warning("Chroma list_collections 失败: %s", exc)
    elif Config.CHROMA_COLLECTION_OBJ:
        collections = [Config.CHROMA_COLLECTION_OBJ]

    if not collections:
        return {"skipped": True, "reason": "Chroma未连接或无集合"}

    cursor.execute(
        """
        SELECT id, collection_name, field_name, display_name, description,
               is_required, default_value, status
        FROM chroma_collection_metadata
        """
    )
    existing = {
        (r[1], r[2]): {
            "id": r[0],
            "display_name": r[3] or "",
            "description": r[4] or "",
            "is_required": bool(r[5]),
            "default_value": r[6] or "",
            "status": r[7] or "正常",
        }
        for r in cursor.fetchall()
    }

    live_keys: Set[Tuple[str, str]] = set()
    added = updated = 0

    for coll in collections:
        collection_name = getattr(coll, "name", None) or Config.CHROMA_COLLECTION
        field_types: Dict[str, str] = {}
        try:
            sample = coll.get(limit=sample_limit, include=["metadatas"])
            for meta in sample.get("metadatas") or []:
                if not meta:
                    continue
                for field_name, val in meta.items():
                    field_types[field_name] = _minio_tag_type(val)
        except Exception as exc:
            logger.warning("Chroma 集合 %s 采样失败: %s", collection_name, exc)
            continue

        for field_name, field_type in field_types.items():
            key = (collection_name, field_name)
            live_keys.add(key)
            if key in existing:
                cursor.execute(
                    "UPDATE chroma_collection_metadata SET field_type = %s WHERE id = %s",
                    (field_type, existing[key]["id"]),
                )
                updated += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO chroma_collection_metadata
                    (collection_name, field_name, field_type, display_name,
                     description, is_required, default_value, status)
                    VALUES (%s, %s, %s, %s, '', FALSE, '', '正常')
                    """,
                    (collection_name, field_name, field_type, field_name),
                )
                added += 1

    removed = 0
    for key, old in existing.items():
        if key not in live_keys:
            cursor.execute("DELETE FROM chroma_collection_metadata WHERE id = %s", (old["id"],))
            removed += 1

    return {"added": added, "updated": updated, "removed": removed, "collections": len(collections)}


def run_metadata_sync(
    targets: Optional[List[str]] = None,
    *,
    init_tables_fn,
) -> Dict[str, Any]:
    if not Config.MYSQL_POOL:
        return {"success": False, "error": "MySQL未连接"}

    if not init_tables_fn():
        return {"success": False, "error": "元数据表初始化失败"}

    allowed = {"mysql", "neo4j", "minio", "chroma"}
    selected = {t.lower() for t in (targets or list(allowed))} & allowed
    if not selected:
        selected = allowed

    summary: Dict[str, Any] = {}
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                if "mysql" in selected:
                    summary["mysql"] = sync_mysql_metadata(cursor)
                if "neo4j" in selected:
                    summary["neo4j"] = sync_neo4j_metadata(cursor)
                if "minio" in selected:
                    summary["minio"] = sync_minio_metadata(cursor)
                if "chroma" in selected:
                    summary["chroma"] = sync_chroma_metadata(cursor)
                conn.commit()
        return {"success": True, "data": summary}
    except Exception as exc:
        logger.exception("元数据同步失败")
        return {"success": False, "error": str(exc), "data": summary}
