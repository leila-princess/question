import json
import logging
import re
from flask import Blueprint, request, jsonify
from config import Config
from utils.operation_log import log_user_action

logger = logging.getLogger(__name__)
yhgl_bp = Blueprint('yhgl', __name__, url_prefix='/api/xtgl/yhgl')


_GARBLED_NAME_RE = re.compile(r'^[\?\uFFFD\uFF1F？\s]+$')


def _resolve_display_name(name, user_id):
    n = (name or '').strip()
    if not n or _GARBLED_NAME_RE.match(n):
        return user_id
    return n


def _init_user_table():
    if not Config.MYSQL_POOL:
        return
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sys_user (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(50) UNIQUE NOT NULL,
                        name VARCHAR(100) NOT NULL,
                        password VARCHAR(255),
                        role VARCHAR(50) DEFAULT '试验人员',
                        department VARCHAR(100),
                        permissions JSON,
                        status VARCHAR(20) DEFAULT 'active',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                try:
                    cursor.execute("ALTER TABLE sys_user ADD COLUMN department VARCHAR(100)")
                except Exception:
                    pass
                try:
                    cursor.execute(
                        "UPDATE sys_user SET role = %s WHERE role = %s",
                        ('试验知识管理人员', '试验管理人员'),
                    )
                except Exception:
                    pass
                conn.commit()
                cursor.execute("SELECT COUNT(*) FROM sys_user WHERE user_id = 'admin'")
                if cursor.fetchone()[0] == 0:
                    import bcrypt
                    default_password = '123456'
                    hashed = bcrypt.hashpw(default_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    cursor.execute(
                        "INSERT INTO sys_user (user_id, name, password, role, department, permissions) VALUES (%s, %s, %s, %s, %s, %s)",
                        ('admin', '系统管理员', hashed, '系统管理员', '技术部', json.dumps({"all": True}))
                    )
                    conn.commit()
    except Exception as e:
        logger.error(f"Init user table error: {e}")


@yhgl_bp.route('/users', methods=['GET'])
def list_users():
    _init_user_table()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, user_id, name, role, department, permissions, status, created_at FROM sys_user ORDER BY created_at DESC")
                rows = cursor.fetchall()
        return jsonify({"success": True, "data": [
            {"id": r[0], "user_id": r[1], "name": r[2], "role": r[3], "department": r[4] or '', "permissions": json.loads(r[5]) if r[5] else {}, "status": r[6], "created_at": str(r[7])}
            for r in rows
        ]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@yhgl_bp.route('/users', methods=['POST'])
def create_user():
    _init_user_table()
    data = request.get_json()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        import bcrypt
        password = data.get('password', '123456')
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sys_user (user_id, name, password, role, department, permissions, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (data['user_id'], data['name'], hashed, data.get('role', '试验人员'),
                     data.get('department', ''), json.dumps(data.get('permissions', {})), data.get('status', 'active'))
                )
                conn.commit()
        log_user_action(f"用户管理-新增用户 user_id={data.get('user_id')}")
        return jsonify({"success": True, "message": "用户创建成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@yhgl_bp.route('/users/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    data = request.get_json()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                fields = ["name=%s", "role=%s", "department=%s", "permissions=%s", "status=%s"]
                values = [data.get('name'), data.get('role'), data.get('department', ''),
                          json.dumps(data.get('permissions', {})), data.get('status')]
                password = data.get('password')
                if password:
                    import bcrypt
                    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    fields.append("password=%s")
                    values.append(hashed)
                values.append(user_id)
                cursor.execute(
                    f"UPDATE sys_user SET {', '.join(fields)} WHERE id=%s",
                    values
                )
                conn.commit()
        log_user_action(f"用户管理-修改用户 id={user_id}")
        return jsonify({"success": True, "message": "用户更新成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@yhgl_bp.route('/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM sys_user WHERE id=%s", (user_id,))
                conn.commit()
        log_user_action(f"用户管理-删除用户 id={user_id}")
        return jsonify({"success": True, "message": "用户删除成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@yhgl_bp.route('/current-user', methods=['GET'])
def get_current_user():
    user_id = request.args.get('user_id', '')
    if not user_id:
        return jsonify({"success": False, "error": "user_id required"}), 400
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, user_id, name, role, department, permissions, status FROM sys_user WHERE user_id=%s",
                    (user_id,)
                )
                r = cursor.fetchone()
        if not r:
            return jsonify({"success": False, "error": "用户不存在"}), 404
        return jsonify({"success": True, "data": {
            "id": r[0], "user_id": r[1],
            "name": _resolve_display_name(r[2], r[1]),
            "role": r[3],
            "department": r[4] or '', "permissions": json.loads(r[5]) if r[5] else {},
            "status": r[6]
        }})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@yhgl_bp.route('/current-user', methods=['PUT'])
def update_current_user():
    data = request.get_json()
    user_id = data.get('user_id', '')
    if not user_id:
        return jsonify({"success": False, "error": "user_id required"}), 400
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"})
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                fields = []
                values = []
                if 'name' in data:
                    fields.append("name=%s")
                    values.append(data['name'])
                if 'department' in data:
                    fields.append("department=%s")
                    values.append(data['department'])
                password = data.get('password')
                if password:
                    import bcrypt
                    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    fields.append("password=%s")
                    values.append(hashed)
                if not fields:
                    return jsonify({"success": True, "message": "无需更新"})
                values.append(user_id)
                cursor.execute(
                    f"UPDATE sys_user SET {', '.join(fields)} WHERE user_id=%s",
                    values
                )
                conn.commit()
        log_user_action(f"个人信息-修改 user_id={user_id}")
        return jsonify({"success": True, "message": "更新成功"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


_init_user_table()
