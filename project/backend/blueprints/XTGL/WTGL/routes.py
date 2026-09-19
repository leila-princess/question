import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
from config import Config
from utils.operation_log import log_user_action

logger = logging.getLogger(__name__)
wtgl_bp = Blueprint('wtgl', __name__, url_prefix='/api/xtgl/wtgl')


def _init_problems_table():
    if not Config.MYSQL_POOL:
        return
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS problems (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        content TEXT,
                        user_id VARCHAR(50),
                        user_name VARCHAR(100),
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        replied TINYINT(1) DEFAULT 0,
                        reply_content TEXT,
                        reply_user_id VARCHAR(50),
                        reply_user_name VARCHAR(100),
                        reply_at DATETIME
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.commit()
                logger.info("问题表初始化成功")
    except Exception as e:
        logger.error(f"初始化问题表失败: {e}")


@wtgl_bp.route('/problems', methods=['GET'])
def list_problems():
    _init_problems_table()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT id, title, content, user_id, user_name, created_at,
                           replied, reply_content, reply_user_id, reply_user_name, reply_at
                    FROM problems ORDER BY created_at DESC
                """)
                rows = cursor.fetchall()
        return jsonify({
            "success": True,
            "data": [
                {
                    "id": r[0],
                    "title": r[1],
                    "content": r[2],
                    "user_id": r[3],
                    "user_name": r[4],
                    "created_at": str(r[5]) if r[5] else None,
                    "replied": bool(r[6]),
                    "reply_content": r[7],
                    "reply_user_id": r[8],
                    "reply_user_name": r[9],
                    "reply_at": str(r[10]) if r[10] else None
                }
                for r in rows
            ]
        })
    except Exception as e:
        logger.error(f"获取问题列表失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@wtgl_bp.route('/problems', methods=['POST'])
def create_problem():
    _init_problems_table()
    data = request.get_json()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO problems (title, content, user_id, user_name, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (data.get('title'), data.get('content'),
                     data.get('user_id'), data.get('user_name'), datetime.now())
                )
                conn.commit()
        log_user_action(f"问题管理-新增问题 title={str(data.get('title', ''))[:80]}")
        return jsonify({"success": True, "message": "问题创建成功"})
    except Exception as e:
        logger.error(f"创建问题失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@wtgl_bp.route('/problems/<int:problem_id>', methods=['PUT'])
def update_problem(problem_id):
    data = request.get_json()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE problems SET title=%s, content=%s, user_id=%s, user_name=%s WHERE id=%s",
                    (data.get('title'), data.get('content'),
                     data.get('user_id'), data.get('user_name'), problem_id)
                )
                conn.commit()
        log_user_action(f"问题管理-修改问题 id={problem_id}")
        return jsonify({"success": True, "message": "问题更新成功"})
    except Exception as e:
        logger.error(f"更新问题失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@wtgl_bp.route('/problems/<int:problem_id>', methods=['DELETE'])
def delete_problem(problem_id):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM problems WHERE id=%s", (problem_id,))
                conn.commit()
        log_user_action(f"问题管理-删除问题 id={problem_id}")
        return jsonify({"success": True, "message": "问题删除成功"})
    except Exception as e:
        logger.error(f"删除问题失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@wtgl_bp.route('/problems/<int:problem_id>/reply', methods=['POST'])
def reply_problem(problem_id):
    data = request.get_json()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """UPDATE problems 
                       SET replied=1, reply_content=%s, reply_user_id=%s, 
                           reply_user_name=%s, reply_at=%s 
                       WHERE id=%s""",
                    (data.get('reply_content'), data.get('reply_user_id'),
                     data.get('reply_user_name'), datetime.now(), problem_id)
                )
                conn.commit()
        log_user_action(f"问题管理-回复问题 id={problem_id}")
        return jsonify({"success": True, "message": "回复成功"})
    except Exception as e:
        logger.error(f"回复问题失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



_init_problems_table()
