import json                                 # JSON序列化模块（虽然本文件未直接使用，但可能被其他模块调用）
import logging                              # 日志记录模块
import pymysql                              # MySQL数据库驱动
from flask import Blueprint, request, jsonify # Flask组件：Blueprint路由蓝图、request请求对象、jsonify JSON响应
from config import Config                    # 配置类（包含数据库连接、MinIO客户端等配置）

logger = logging.getLogger(__name__)          # 创建当前模块的日志记录器
sy_bp = Blueprint('sy', __name__, url_prefix='/api/sy')  # 创建名为'sy'的Flask蓝图，API路由前缀为'/api/sy'


def _get_neo4j_driver():
    return Config.NEO4J_DRIVER              # 获取Neo4j驱动的辅助函数（目前未被使用，可能是预留）


# 知识库 MinIO 一级目录与 7 类规范文件的对应关系（展示顺序固定为下列 7 种）
TYPE_ORDER = (
    "法规标准",
    "规范性文件",
    "指导性文件",
    "试验方案",
    "试验大纲",
    "试验报告",
    "专项评估报告",
)

# MinIO 路径首段（中文/英文/简称） -> 上述 7 类之一；未命中则不计入统计
TYPE_MAP = {
    "法规标准": "法规标准",
    "规范性文件": "规范性文件",
    "指导性文件": "指导性文件",
    "试验方案": "试验方案",
    "试验大纲": "试验大纲",
    "试验报告": "试验报告",
    "专项评估报告": "专项评估报告",
    "Standard": "法规标准",
    "Regulation": "法规标准",
    "Normative": "规范性文件",
    "Guidance": "指导性文件",
    "Scheme": "试验方案",
    "Outline": "试验大纲",
    "Report": "试验报告",
    "Assessment": "专项评估报告",
    "Template": "指导性文件",
    "标准": "法规标准",
    "规范": "规范性文件",
    "指导": "指导性文件",
    "方案": "试验方案",
    "大纲": "试验大纲",
    "报告": "试验报告",
    "评估": "专项评估报告",
}

TYPE_COLOR_MAP = {
    "法规标准": "#1d4ed8",
    "规范性文件": "#7c3aed",
    "指导性文件": "#0d9488",
    "试验方案": "#059669",
    "试验大纲": "#d97706",
    "试验报告": "#dc2626",
    "专项评估报告": "#9333ea",
}


def _canonical_file_type(segment: str):
    if not segment:
        return None
    key = segment.strip()
    mapped = TYPE_MAP.get(key)
    if mapped and mapped in TYPE_ORDER:
        return mapped
    return None


@sy_bp.route('/file_stats', methods=['GET'])    # 路由：GET /api/sy/file_stats，获取文件统计
def get_file_stats():
    if not Config.MINIO_CLIENT:                # 检查MinIO客户端是否初始化
        return jsonify({"success": False, "error": "MinIO未连接"})  # 未初始化则返回错误
    
    try:
        bucket_name = Config.MINIO_BUCKET_NAME  # 从配置获取MinIO存储桶名称
        
        folder_counts = {t: set() for t in TYPE_ORDER}
        
        objects = Config.MINIO_CLIENT.list_objects(bucket_name, recursive=True)  # 递归列出MinIO存储桶中所有文件对象
        
        for obj in objects:               # 循环统计每个分类的文件夹数量
            object_name = obj.object_name  # 获取文件完整路径，如 "法规标准/xxx/doc.pdf"
            parts = object_name.split('/')  # 按"/"分割路径
            
            if len(parts) >= 2:         # 确保至少有分类和文件夹两级
                category = parts[0]      # 取第一级目录作为分类
                folder_name = parts[1]   # 取第二级目录作为文件夹名
                
                display_type = _canonical_file_type(category)
                if not display_type:
                    continue
                
                folder_counts[display_type].add(folder_name)
        
        stats = []
        for display_type in TYPE_ORDER:
            folder_count = len(folder_counts[display_type])
            color = TYPE_COLOR_MAP.get(display_type, "#3b82f6")
            stats.append({
                "type": display_type,
                "count": folder_count,
                "color": color,
            })
            
        return jsonify({"success": True, "data": stats})  # 返回成功的JSON响应，包含统计数据
    except Exception as e:                # 异常捕获
        logger.error(f"获取文件统计失败: {e}")  # 记录错误日志
        return jsonify({"success": False, "error": str(e)}), 500  # 返回500服务器错误


@sy_bp.route('/recent_schemes', methods=['GET'])  # 路由：GET /api/sy/recent_schemes，获取最近方案
def get_recent_schemes():
    """获取最近方案（分组返回：基于规则和基于LLM）"""  # 函数文档字符串
    user_id = request.args.get('user_id', 'admin')  # 获取user_id参数，默认为'admin'
    limit = int(request.args.get('limit', 2))  # 获取limit参数，默认为3
    try:
        if not Config.MYSQL_POOL:  # 检查MySQL连接
            return jsonify({"success": False, "error": "MySQL未初始化"})
        
        with Config.MYSQL_POOL.connection() as conn:  # 从连接池获取数据库连接
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:  # 创建字典类型游标
                # 基于规则的方案：独立表 jygz_history（与 jyllm_scheme_history 字段风格一致）
                jygz_rows = []
                try:
                    cursor.execute("""
                        SELECT task_id, scheme_name, scheme_description, generation_status, word_count,
                               created_at, updated_at
                        FROM jygz_history
                        WHERE user_id = %s
                        ORDER BY updated_at DESC
                        LIMIT %s
                    """, (user_id, limit))
                    jygz_rows = cursor.fetchall()
                except Exception as e:
                    # 表尚未创建时回退到旧数据（jyllm 表 + page_type）
                    logger.warning(f"读取 jygz_history 失败，尝试 jyllm_scheme_history: {e}")
                    cursor.execute("""
                        SELECT task_id, scheme_name, scheme_description, generation_status, word_count,
                               created_at, updated_at, page_type
                        FROM jyllm_scheme_history
                        WHERE user_id = %s AND page_type = 'jygz'
                        ORDER BY updated_at DESC
                        LIMIT %s
                    """, (user_id, limit))
                    jygz_rows = cursor.fetchall()
                
                # 查询基于LLM的方案（jyllm）
                cursor.execute("""
                    SELECT task_id, scheme_name, scheme_description, generation_status, word_count,
                           created_at, updated_at, page_type
                    FROM jyllm_scheme_history
                    WHERE user_id = %s AND page_type = 'jyllm'
                    ORDER BY updated_at DESC
                    LIMIT %s
                """, (user_id, limit))  # 查询page_type='jyllm'（基于LLM）的方案历史，按updated_at降序
                jyllm_rows = cursor.fetchall()  # 获取查询结果
        
        def format_schemes(rows):            # 内部函数：将数据库行格式化为前端需要的JSON结构
            schemes = []
            for r in rows:
                desc = r.get("scheme_description") or ""
                if isinstance(desc, str):
                    desc = desc.strip()
                schemes.append({
                    "task_id": r.get("task_id", ""),
                    "name": r.get("scheme_name", "未命名方案"),
                    "description": desc if desc else "",
                    "status": r.get("generation_status", "unknown"),
                    "progress": 100 if r.get("generation_status") in ("completed", "complete") else 0,
                    "word_count": r.get("word_count", 0),
                    "created_at": str(r.get("created_at", "")) if r.get("created_at") else "",  # 转换为字符串
                    "updated_at": str(r.get("updated_at", "")) if r.get("updated_at") else ""
                })
            return schemes
        
        return jsonify({                 # 返回成功响应
            "success": True, 
            "data": {
                "jygz": format_schemes(jygz_rows),   # 基于规则
                "jyllm": format_schemes(jyllm_rows)  # 基于LLM
            }
        })
    except Exception as e:            # 异常捕获
        logger.error(f"获取最近方案失败: {e}")  # 记录错误日志
        return jsonify({"success": False, "error": str(e)}), 500  # 返回500服务器错误


@sy_bp.route('/problems', methods=['GET'])  # 路由：GET /api/sy/problems，获取问题列表
def get_problems():
    limit = int(request.args.get('limit', 10))  # 获取limit参数，默认10条
    try:
        if not Config.MYSQL_POOL:  # 检查MySQL连接
            return jsonify({"success": False, "error": "MySQL未初始化"})
        
        with Config.MYSQL_POOL.connection() as conn:  # 从连接池获取数据库连接
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:  # 创建字典类型游标
                cursor.execute("""
                    SELECT id, title, content, user_id, user_name, created_at, 
                           replied, reply_content, reply_user_id, reply_user_name, reply_at
                    FROM problems
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))  # 查询problems表，按创建时间降序
                rows = cursor.fetchall()  # 获取查询结果
        
        problems = []                   # 初始化问题列表
        for r in rows:                 # 遍历查询结果
            problems.append({         # 格式化每条记录
                "id": r.get("id"),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "user_id": r.get("user_id", ""),
                "user_name": r.get("user_name", ""),
                "created_at": str(r.get("created_at", "")) if r.get("created_at") else "",  # 转换为字符串
                "replied": bool(r.get("replied", False)),  # 转换为布尔值
                "reply_content": r.get("reply_content", ""),
                "reply_user_id": r.get("reply_user_id", ""),
                "reply_user_name": r.get("reply_user_name", ""),
                "reply_at": str(r.get("reply_at", "")) if r.get("reply_at") else ""
            })
        
        return jsonify({"success": True, "data": problems})  # 返回成功响应
    except Exception as e:            # 异常捕获
        logger.error(f"获取问题列表失败: {e}")  # 记录错误日志
        return jsonify({"success": False, "error": str(e)}), 500  # 返回500服务器错误