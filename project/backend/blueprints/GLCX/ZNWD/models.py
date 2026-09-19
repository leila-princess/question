import datetime
import os
import sys
import json
import uuid
import logging
import pymysql
from contextlib import contextmanager
from typing import Union, List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from config import Config

logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': Config.DB_HOST,
    'port': Config.DB_PORT,
    'user': Config.DB_USER,
    'password': Config.DB_PASSWORD,
    'database': Config.DB_NAME,
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}


@contextmanager
def get_db():
    conn = pymysql.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    conn = pymysql.connect(
        host=DB_CONFIG['host'], port=DB_CONFIG['port'],
        user=DB_CONFIG['user'], password=DB_CONFIG['password'],
        charset='utf8mb4'
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_CONFIG['database']}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.commit()
    finally:
        conn.close()

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    session_id VARCHAR(50) NOT NULL,
                    title TEXT,
                    messages JSON NOT NULL,
                    message_count INT DEFAULT 0,
                    user_id VARCHAR(50),
                    user_name VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS user_interest_equipments (
                    user_id VARCHAR(64) NOT NULL,
                    equipment_id VARCHAR(64) NOT NULL,
                    PRIMARY KEY (user_id, equipment_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                '''
            )
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS user_interest_equipment_types (
                    user_id VARCHAR(64) NOT NULL,
                    equipment_type_id VARCHAR(64) NOT NULL,
                    PRIMARY KEY (user_id, equipment_type_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                '''
            )
            cur.execute(
                '''
                CREATE TABLE IF NOT EXISTS user_interest_environments (
                    user_id VARCHAR(64) NOT NULL,
                    environment_id VARCHAR(64) NOT NULL,
                    PRIMARY KEY (user_id, environment_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                '''
            )
        conn.commit()
    finally:
        conn.close()
    _migrate_old_data()


def _migrate_old_data():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'chat_history'")
            if cur.fetchone():
                cur.execute("SHOW COLUMNS FROM chat_history LIKE 'session_id'")
                if not cur.fetchone():
                    cur.execute('''
                        ALTER TABLE chat_history 
                        ADD COLUMN session_id VARCHAR(50) AFTER id,
                        ADD COLUMN title TEXT AFTER session_id,
                        ADD COLUMN messages JSON AFTER title,
                        ADD COLUMN message_count INT DEFAULT 0 AFTER messages,
                        ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at
                    ''')
                    cur.execute('''
                        UPDATE chat_history 
                        SET session_id = UUID(),
                            title = LEFT(question, 30),
                            messages = JSON_ARRAY(
                                JSON_OBJECT('role', 'user', 'content', question),
                                JSON_OBJECT('role', 'ai', 'content', answer, 'thinkingSteps', COALESCE(thinking_steps, '[]'))
                            ),
                            message_count = 2
                        WHERE session_id IS NULL
                    ''')
                    cur.execute('ALTER TABLE chat_history DROP COLUMN question, DROP COLUMN answer, DROP COLUMN thinking_steps')

                cur.execute("SHOW COLUMNS FROM chat_history LIKE 'user_id'")
                if not cur.fetchone():
                    cur.execute('ALTER TABLE chat_history ADD COLUMN user_id VARCHAR(50) AFTER message_count')
                    cur.execute('ALTER TABLE chat_history ADD COLUMN user_name VARCHAR(100) AFTER user_id')

                cur.execute("SHOW INDEX FROM chat_history WHERE Key_name = 'uk_session_id'")
                if not cur.fetchone():
                    cur.execute('ALTER TABLE chat_history ADD UNIQUE KEY uk_session_id (session_id)')


def json_serializer(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    try:
        from neo4j.time import DateTime
        if isinstance(obj, DateTime):
            return obj.to_native().isoformat()
    except ImportError:
        pass
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def save_conversation(session_id: str, messages: list, title: str = None, user_id: str = None, user_name: str = None) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''INSERT INTO chat_history (session_id, title, messages, message_count, user_id, user_name) 
                   VALUES (%s, %s, %s, %s, %s, %s) 
                   ON DUPLICATE KEY UPDATE 
                   title = IFNULL(title, VALUES(title)),
                   messages = VALUES(messages),
                   message_count = VALUES(message_count),
                   user_id = IFNULL(user_id, VALUES(user_id)),
                   user_name = IFNULL(user_name, VALUES(user_name)),
                   updated_at = CURRENT_TIMESTAMP''',
                (session_id, title, json.dumps(messages, ensure_ascii=False, default=json_serializer), len(messages), user_id, user_name)
            )
            return cur.lastrowid if cur.lastrowid else _get_id_by_session(session_id)


def _get_id_by_session(session_id: str) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM chat_history WHERE session_id = %s', (session_id,))
            row = cur.fetchone()
            return row['id'] if row else 0


def get_history_list(page: int = 1, page_size: int = 20) -> dict:
    offset = (page - 1) * page_size
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) as cnt FROM chat_history')
            total = cur.fetchone()['cnt']

            cur.execute(
                'SELECT id, session_id, title, message_count, created_at, updated_at FROM chat_history ORDER BY updated_at DESC LIMIT %s OFFSET %s',
                (page_size, offset)
            )
            rows = cur.fetchall()

    items = []
    for row in rows:
        title = row['title'] or '新对话'
        items.append({
            'id': row['id'],
            'sessionId': row['session_id'],
            'title': title[:30] + ('...' if len(title) > 30 else ''),
            'messageCount': row['message_count'],
            'createdAt': row['created_at'].strftime('%Y-%m-%d %H:%M:%S') if row['created_at'] else '',
            'updatedAt': row['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if row['updated_at'] else ''
        })

    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}


def get_history_detail(history_id: int) -> Union[dict, None]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM chat_history WHERE id = %s', (history_id,))
            row = cur.fetchone()

    if not row:
        return None

    messages = row['messages']
    if isinstance(messages, str):
        messages = json.loads(messages) if messages else []

    return {
        'id': row['id'],
        'sessionId': row['session_id'],
        'title': row['title'],
        'messages': messages,
        'messageCount': row['message_count'],
        'createdAt': row['created_at'].strftime('%Y-%m-%d %H:%M:%S') if row['created_at'] else '',
        'updatedAt': row['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if row['updated_at'] else ''
    }


def get_history_by_session(session_id: str) -> Union[dict, None]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM chat_history WHERE session_id = %s', (session_id,))
            row = cur.fetchone()

    if not row:
        return None

    messages = row['messages']
    if isinstance(messages, str):
        messages = json.loads(messages) if messages else []

    return {
        'id': row['id'],
        'sessionId': row['session_id'],
        'title': row['title'],
        'messages': messages,
        'messageCount': row['message_count'],
        'createdAt': row['created_at'].strftime('%Y-%m-%d %H:%M:%S') if row['created_at'] else '',
        'updatedAt': row['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if row['updated_at'] else ''
    }


def delete_history(history_id: int) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            affected = cur.execute('DELETE FROM chat_history WHERE id = %s', (history_id,))
            return affected > 0


def clear_all_history() -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            return cur.execute('DELETE FROM chat_history')


def save_conversation_mysql(session_id: str, summary: str, last_exchange: dict, title: str = None, user_id: str = None, user_name: str = None) -> int:
    msgs = []
    if last_exchange and last_exchange.get("user"):
        msgs.append({"role": "user", "content": last_exchange["user"]})
    if last_exchange and last_exchange.get("assistant"):
        msgs.append({"role": "assistant", "content": last_exchange["assistant"]})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''INSERT INTO chat_history (session_id, title, messages, message_count, user_id, user_name)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                   title = IFNULL(title, VALUES(title)),
                   messages = VALUES(messages),
                   message_count = VALUES(message_count),
                   user_id = IFNULL(user_id, VALUES(user_id)),
                   user_name = IFNULL(user_name, VALUES(user_name)),
                   updated_at = CURRENT_TIMESTAMP''',
                (session_id, title or 'Saved Session', json.dumps(msgs, ensure_ascii=False, default=json_serializer), len(msgs), user_id, user_name)
            )
            return cur.lastrowid if cur.lastrowid else _get_id_by_session(session_id)


def get_znwd_user_interest(user_id: str) -> Dict[str, Any]:
    """读取用户兴趣：一级设备类型 equipmentTypeIds、环境 environmentIds。"""
    uid = (user_id or "").strip() or "anonymous"
    equipment_type_ids: List[str] = []
    environment_ids: List[str] = []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT equipment_type_id FROM user_interest_equipment_types WHERE user_id = %s ORDER BY equipment_type_id",
                    (uid,),
                )
                for r in cur.fetchall() or []:
                    tid = str(r.get("equipment_type_id") or "").strip()
                    if tid:
                        equipment_type_ids.append(tid)
                if not equipment_type_ids:
                    cur.execute(
                        "SELECT equipment_id FROM user_interest_equipments WHERE user_id = %s ORDER BY equipment_id",
                        (uid,),
                    )
                    for r in cur.fetchall() or []:
                        tid = str(r.get("equipment_id") or "").strip()
                        if tid:
                            equipment_type_ids.append(tid)
                cur.execute(
                    "SELECT environment_id FROM user_interest_environments WHERE user_id = %s ORDER BY environment_id",
                    (uid,),
                )
                for r in cur.fetchall() or []:
                    eid = str(r.get("environment_id") or "").strip()
                    if eid:
                        environment_ids.append(eid)
    except Exception as ex:
        logger.warning("get_znwd_user_interest failed: %s", ex)
    return {
        "equipmentTypeIds": equipment_type_ids,
        "equipmentIds": equipment_type_ids,
        "environmentIds": environment_ids,
    }


def save_znwd_user_interest(
    user_id: str,
    environment_ids: List[Any],
    equipment_type_ids: Optional[List[Any]] = None,
    equipment_ids: Optional[List[Any]] = None,
) -> None:
    uid = (user_id or "").strip() or "anonymous"
    et_ids = equipment_type_ids if equipment_type_ids is not None else equipment_ids
    et_ids = [str(x).strip() for x in (et_ids or []) if str(x).strip()][:200]
    env_ids = [str(x).strip() for x in (environment_ids or []) if str(x).strip()][:200]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_interest_equipment_types WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM user_interest_equipments WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM user_interest_environments WHERE user_id = %s", (uid,))
            for tid in et_ids:
                cur.execute(
                    "INSERT INTO user_interest_equipment_types (user_id, equipment_type_id) VALUES (%s, %s)",
                    (uid, tid),
                )
            for eid in env_ids:
                cur.execute(
                    "INSERT INTO user_interest_environments (user_id, environment_id) VALUES (%s, %s)",
                    (uid, eid),
                )


try:
    init_db()
    logger.info("ZNWD MySQL 表结构检查完成 (host=%s db=%s)", DB_CONFIG["host"], DB_CONFIG["database"])
except Exception as e:
    logger.warning(
        "ZNWD 数据库初始化失败（与 OPENAI_API_KEY 无关）：%s。"
        "智能问答历史等依赖 MySQL；文件管理/自动解析等可不依赖此项。"
        "请在项目根目录 .env 配置 DB_HOST、DB_USER、DB_PASSWORD、DB_NAME 并确认 MySQL 已启动。",
        e,
    )
