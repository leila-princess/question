import json                                  # JSON序列化
import logging                                 # 日志记录
import re
from datetime import datetime, timedelta       # 日期时间处理
from flask import Blueprint, request, jsonify, make_response  # Flask核心组件
from config import Config                      # 配置类
import bcrypt                                  # 密码哈希库
import jwt                                     # JWT令牌库

logger = logging.getLogger(__name__)             # 创建日志记录器
login_bp = Blueprint('login', __name__, url_prefix='/api')  # 创建登录蓝图，API前缀/api

SECRET_KEY = 'your-secret-key-here-change-in-production'  # JWT密钥，生产环境应修改


_GARBLED_NAME_RE = re.compile(r'^[\?\uFFFD\uFF1F？\s]+$')


def _resolve_display_name(name, user_id):
    """姓名乱码（常见为 ???）时回退为登录账号"""
    n = (name or '').strip()
    if not n or _GARBLED_NAME_RE.match(n):
        return user_id
    return n


def _ensure_password_column():                  # 确保sys_user表有password字段
    if not Config.MYSQL_POOL:                  # 检查MySQL连接
        return
    try:
        with Config.MYSQL_POOL.connection() as conn:  # 获取数据库连接
            with conn.cursor() as cursor:      # 创建游标
                cursor.execute("""
                    SELECT COUNT(*) FROM information_schema.columns 
                    WHERE table_schema = DATABASE() 
                    AND table_name = 'sys_user' 
                    AND column_name = 'password'
                """)                         # 查询password字段是否存在
                if cursor.fetchone()[0] == 0:  # 如果不存在
                    cursor.execute("ALTER TABLE sys_user ADD COLUMN password VARCHAR(255)")  # 添加字段
                    conn.commit()             # 提交事务
                    logger.info("password字段已添加到sys_user表")
    except Exception as e:                      # 异常处理
        logger.error(f"检查password字段失败: {e}")


def _init_admin_password():                    # 初始化admin用户的默认密码
    if not Config.MYSQL_POOL:                  # 检查MySQL连接
        return
    try:
        with Config.MYSQL_POOL.connection() as conn:  # 获取数据库连接
            with conn.cursor() as cursor:      # 创建游标
                cursor.execute("SELECT password FROM sys_user WHERE user_id = 'admin'")  # 查询admin用户
                row = cursor.fetchone()       # 获取结果
                if row and (row[0] is None or row[0] == ''):  # 如果密码为空
                    default_password = '123456'  # 默认密码123456
                    hashed = bcrypt.hashpw(default_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')  # 哈希加密
                    cursor.execute("UPDATE sys_user SET password = %s WHERE user_id = 'admin'", (hashed,))  # 更新密码
                    conn.commit()             # 提交事务
                    logger.info("admin用户密码已初始化")
    except Exception as e:                      # 异常处理
        logger.error(f"初始化admin密码失败: {e}")


_ensure_password_column()                    # 模块加载时确保password字段存在
_init_admin_password()                   # 模块加载时初始化admin密码


# 临时固定用户（数据库为空时使用）
FIXED_USERS = {
    'admin': {'password': '123456', 'name': '管理员', 'role': '系统管理员'},
}

@login_bp.route('/login', methods=['POST'])   # 登录接口
def login():
    data = request.get_json()              # 获取请求JSON数据
    username = data.get('username', '').strip()  # 获取用户名并去除空格
    password = data.get('password', '').strip()   # 获取密码并去除空格
    remember = data.get('remember', False)  # 是否记住登录（生成7天有效期token）

    if not username or not password:       # 用户名或密码为空
        return jsonify({"success": False, "error": "用户名和密码不能为空"})

    if not Config.MYSQL_POOL:              # MySQL未连接
        # 降级：匹配固定用户
        if username in FIXED_USERS and FIXED_USERS[username]['password'] == password:
            user_info = {
                "id": 1,
                "userId": username,
                "userName": FIXED_USERS[username]['name'],
                "role": FIXED_USERS[username]['role']
            }
            if remember:
                token = jwt.encode({
                    'user_id': username,
                    'exp': datetime.utcnow() + timedelta(days=7)
                }, SECRET_KEY, algorithm='HS256')
                user_info['token'] = token
            return jsonify({"success": True, "data": user_info, "message": "登录成功"})
        return jsonify({"success": False, "error": "数据库未连接"})

    try:
        with Config.MYSQL_POOL.connection() as conn:  # 获取数据库连接
            with conn.cursor() as cursor:      # 创建游标
                cursor.execute(
                    "SELECT id, user_id, name, role, password, status FROM sys_user WHERE user_id = %s",
                    (username,)
                )                         # 根据用户名查询用户
                row = cursor.fetchone()   # 获取结果

        if not row:                       # 用户不存在
            return jsonify({"success": False, "error": "用户不存在"})

        user_id_db, user_id, user_name, role, hashed_password, status = row  # 分解用户数据

        if status != 'active':            # 用户被禁用
            return jsonify({"success": False, "error": "用户已被禁用"})

        if not hashed_password:            # 密码未设置
            return jsonify({"success": False, "error": "用户未设置密码"})

        if not bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8')):  # 验证密码
            return jsonify({"success": False, "error": "密码错误"})

        user_info = {                       # 构建用户信息对象
            "id": user_id_db,             # 数据库ID
            "userId": user_id,           # 用户ID
            "userName": _resolve_display_name(user_name, user_id),
            "role": role or '试验人员'    # 角色
        }

        if remember:                      # 如果记住登录
            token = jwt.encode({          # 生成JWT令牌
                'user_id': user_id,      # 用户ID
                'exp': datetime.utcnow() + timedelta(days=7)  # 7天后过期
            }, SECRET_KEY, algorithm='HS256')  # HS256算法
            user_info['token'] = token    # 添加token到用户信息

        return jsonify({"success": True, "data": user_info, "message": "登录成功"})  # 返回成功

    except Exception as e:                # 异常处理
        logger.error(f"登录失败: {e}")  # 记录错误日志
        return jsonify({"success": False, "error": str(e)}), 500  # 返回500错误


@login_bp.route('/logout', methods=['POST'])  # 登出接口
def logout():
    return jsonify({"success": True, "message": "已登出"})  # 直接返回成功（JWT无状态��


@login_bp.route('/current-user', methods=['GET'])  # 获取当前用户接口
def get_current_user():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')  # 从Header获取token

    if not token:                         # 无token
        return jsonify({"success": False, "error": "未登录"})

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])  # 解码JWT
        user_id = payload.get('user_id')  # 获取用户ID

        if not Config.MYSQL_POOL:      # MySQL未连接
            return jsonify({"success": False, "error": "数据库未连接"})

        with Config.MYSQL_POOL.connection() as conn:  # 获取数据库连接
            with conn.cursor() as cursor:  # 创建游标
                cursor.execute(
                    "SELECT id, user_id, name, role, status FROM sys_user WHERE user_id = %s",
                    (user_id,)
                )                     # 根据token中的用户ID查询
                row = cursor.fetchone()  # 获取结果

        if not row:                     # 用户不存在
            return jsonify({"success": False, "error": "用户不存在"})

        user_id_db, user_id, user_name, role, status = row  # 分解用户数据

        if status != 'active':           # 用户被禁用
            return jsonify({"success": False, "error": "用户已被禁用"})

        return jsonify({                 # 返回用户信息
            "success": True,
            "data": {
                "id": user_id_db,
                "userId": user_id,
                "userName": user_name,
                "role": role
            }
        })

    except jwt.ExpiredSignatureError:    # Token过期
        return jsonify({"success": False, "error": "登录已过期"})
    except jwt.InvalidTokenError:        # Token无效
        return jsonify({"success": False, "error": "无效的登录凭证"})
    except Exception as e:              # 其他异常
        logger.error(f"获取当前用户失败: {e}")  # 记录错误日志
        return jsonify({"success": False, "error": str(e)}), 500  # 返回500错误
