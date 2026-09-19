"""
MXGL 模型管理蓝图
管理AI模型配置，记录修改历史
"""
import json
import os
import logging
import re
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify
from config import Config
from utils.operation_log import log_user_action

logger = logging.getLogger(__name__)

mxgl_bp = Blueprint('mxgl', __name__, url_prefix='/api/xtgl/mxgl')

# ANZHUANGBAO 目录路径
ANZHUANGBAO_DIR = Path(Config.ANZHUANGBAO_DIR)


def _ensure_mxgl_token_columns():
    """为已存在的 mxgl_models 表补充 context_window / max_output_tokens（新库可在 CREATE 中已包含）。"""
    if not getattr(Config, "MYSQL_POOL", None):
        return
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mxgl_models'
                      AND COLUMN_NAME = 'context_window'
                """)
                if cursor.fetchone()[0] == 0:
                    cursor.execute(
                        "ALTER TABLE mxgl_models ADD COLUMN context_window INT NOT NULL DEFAULT 8192 "
                        "COMMENT '总上下文token近似上限'"
                    )
                cursor.execute("""
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mxgl_models'
                      AND COLUMN_NAME = 'max_output_tokens'
                """)
                if cursor.fetchone()[0] == 0:
                    cursor.execute(
                        "ALTER TABLE mxgl_models ADD COLUMN max_output_tokens INT NOT NULL DEFAULT 4096 "
                        "COMMENT '单次输出token上限'"
                    )
                conn.commit()
    except Exception as e:
        logger.warning("MXGL token columns ensure: %s", e)


# ==================== 数据库初始化 ====================

def _init_model_tables():
    """初始化模型管理相关表"""
    if not Config.MYSQL_POOL:
        return False
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mxgl_models (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        model_key VARCHAR(100) NOT NULL UNIQUE COMMENT '模型标识：llm/embedding/vector_retrieval/cross_encoder',
                        model_name VARCHAR(200) NOT NULL COMMENT '模型名称',
                        model_type VARCHAR(50) NOT NULL COMMENT '模型类型：llm/embedding/reranker',
                        model_path VARCHAR(500) COMMENT '本地路径',
                        api_url VARCHAR(500) COMMENT 'API地址',
                        api_key VARCHAR(200) COMMENT 'API密钥',
                        description TEXT COMMENT '描述',
                        is_local BOOLEAN DEFAULT TRUE COMMENT '是否本地模型',
                        is_active BOOLEAN DEFAULT TRUE COMMENT '是否启用',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_model_type (model_type),
                        INDEX idx_is_active (is_active)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mxgl_change_logs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        model_key VARCHAR(100) NOT NULL COMMENT '模型标识',
                        model_name VARCHAR(200) COMMENT '模型名称',
                        change_type VARCHAR(50) NOT NULL COMMENT '变更类型：create/update/delete',
                        old_value JSON COMMENT '旧值',
                        new_value JSON COMMENT '新值',
                        user_id VARCHAR(50) DEFAULT 'admin' COMMENT '操作用户ID',
                        user_name VARCHAR(100) DEFAULT '系统管理员' COMMENT '操作用户名',
                        change_note TEXT COMMENT '变更说明',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_model_key (model_key),
                        INDEX idx_created_at (created_at),
                        INDEX idx_user_id (user_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mxgl_user_preferences (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(50) NOT NULL COMMENT '用户ID',
                        model_key VARCHAR(100) NOT NULL COMMENT '模型标识',
                        model_name VARCHAR(200) COMMENT '模型名称',
                        model_path VARCHAR(500) COMMENT '本地路径',
                        api_url VARCHAR(500) COMMENT 'API地址',
                        api_key VARCHAR(200) COMMENT 'API密钥',
                        is_active BOOLEAN DEFAULT TRUE COMMENT '是否启用',
                        scope VARCHAR(20) DEFAULT 'user' COMMENT '作用域：user/personal/global',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE INDEX idx_user_model (user_id, model_key),
                        INDEX idx_user_id (user_id),
                        INDEX idx_scope (scope)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                conn.commit()
                logger.info("模型管理表初始化完成")
                return True
    except Exception as e:
        logger.error(f"初始化模型管理表失败: {e}")
        return False
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                # 模型配置表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mxgl_models (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        model_key VARCHAR(100) NOT NULL UNIQUE COMMENT '模型标识：llm/embedding/vector_retrieval/cross_encoder',
                        model_name VARCHAR(200) NOT NULL COMMENT '模型名称',
                        model_type VARCHAR(50) NOT NULL COMMENT '模型类型：llm/embedding/reranker',
                        model_path VARCHAR(500) COMMENT '本地路径',
                        api_url VARCHAR(500) COMMENT 'API地址',
                        api_key VARCHAR(200) COMMENT 'API密钥',
                        description TEXT COMMENT '描述',
                        is_local BOOLEAN DEFAULT TRUE COMMENT '是否本地模型',
                        is_active BOOLEAN DEFAULT TRUE COMMENT '是否启用',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_model_type (model_type),
                        INDEX idx_is_active (is_active)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                # 模型修改记录表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS mxgl_change_logs (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        model_key VARCHAR(100) NOT NULL COMMENT '模型标识',
                        model_name VARCHAR(200) COMMENT '模型名称',
                        change_type VARCHAR(50) NOT NULL COMMENT '变更类型：create/update/delete',
                        old_value JSON COMMENT '旧值',
                        new_value JSON COMMENT '新值',
                        user_id VARCHAR(50) DEFAULT 'admin' COMMENT '操作用户ID',
                        user_name VARCHAR(100) DEFAULT '系统管理员' COMMENT '操作用户名',
                        change_note TEXT COMMENT '变更说明',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_model_key (model_key),
                        INDEX idx_created_at (created_at),
                        INDEX idx_user_id (user_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                
                conn.commit()
                logger.info("模型管理表初始化完成")
                return True
    except Exception as e:
        logger.error(f"初始化模型管理表失败: {e}")
        return False


def _log_change(model_key: str, model_name: str, change_type: str, 
                old_value: dict = None, new_value: dict = None, 
                user_id: str = 'admin', user_name: str = '系统管理员', 
                change_note: str = ''):
    """记录模型变更日志"""
    if not Config.MYSQL_POOL:
        return
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO mxgl_change_logs 
                    (model_key, model_name, change_type, old_value, new_value, user_id, user_name, change_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    model_key, model_name, change_type,
                    json.dumps(old_value, ensure_ascii=False) if old_value else None,
                    json.dumps(new_value, ensure_ascii=False) if new_value else None,
                    user_id, user_name, change_note
                ))
                conn.commit()
    except Exception as e:
        logger.error(f"记录变更日志失败: {e}")


# ==================== 本地模型扫描 ====================

def _scan_local_models():
    """扫描ANZHUANGBAO目录下的本地模型"""
    models = []
    
    if not ANZHUANGBAO_DIR.exists():
        logger.warning(f"ANZHUANGBAO目录不存在: {ANZHUANGBAO_DIR}")
        return models
    
    try:
        for item in ANZHUANGBAO_DIR.iterdir():
            if item.is_dir():
                model_info = {
                    "name": item.name,
                    "path": str(item),
                    "type": "unknown",
                    "size": sum(f.stat().st_size for f in item.rglob('*') if f.is_file()),
                    "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat()
                }
                
                # 根据目录名判断模型类型
                name_lower = item.name.lower()
                if 'minilm' in name_lower or 'embedding' in name_lower:
                    model_info["type"] = "embedding"
                elif 'ms-marco' in name_lower or 'cross-encoder' in name_lower:
                    model_info["type"] = "reranker"
                elif 'sentence' in name_lower:
                    model_info["type"] = "embedding"
                else:
                    model_info["type"] = "unknown"
                
                models.append(model_info)
        
        return sorted(models, key=lambda x: x["name"])
    except Exception as e:
        logger.error(f"扫描本地模型失败: {e}")
        return []


# ==================== API 接口 ====================

@mxgl_bp.route('/models', methods=['GET'])
def get_models():
    """获取所有模型配置（数据库优先，兼容.env兜底）"""
    _init_model_tables()
    _ensure_mxgl_token_columns()

    try:
        db_models = []
        if Config.MYSQL_POOL:
            try:
                with Config.MYSQL_POOL.connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            SELECT model_key, model_name, model_type, model_path, api_url, api_key,
                                   description, is_local, is_active, created_at, updated_at,
                                   context_window, max_output_tokens
                            FROM mxgl_models
                            ORDER BY model_type ASC, updated_at DESC
                        """)
                        for row in cursor.fetchall():
                            db_models.append({
                                "model_key": row[0],
                                "model_name": row[1],
                                "model_type": row[2],
                                "model_path": row[3],
                                "api_url": row[4],
                                "api_key": row[5],
                                "description": row[6],
                                "is_local": bool(row[7]),
                                "is_active": bool(row[8]),
                                "created_at": str(row[9]) if row[9] else "",
                                "updated_at": str(row[10]) if row[10] else "",
                                "context_window": int(row[11]) if len(row) > 11 and row[11] is not None else 8192,
                                "max_output_tokens": int(row[12]) if len(row) > 12 and row[12] is not None else 4096,
                            })
            except Exception as e:
                logger.warning(f"读取数据库模型配置失败: {e}")

        if db_models:
            return jsonify({"success": True, "data": db_models})

        # 数据库无配置时，返回.env兜底
        fallback = [
            {
                "model_key": "llm_default",
                "model_name": os.environ.get("OPENAI_MODEL", ""),
                "model_type": "llm",
                "api_url": os.environ.get("OPENAI_BASE_URL", ""),
                "api_key": os.environ.get("OPENAI_API_KEY", ""),
                "description": "默认LLM（来自环境变量）",
                "is_local": False,
                "is_active": True,
                "context_window": 8192,
                "max_output_tokens": 4096,
            },
            {
                "model_key": "embedding",
                "model_name": "paraphrase-multilingual-MiniLM-L12-v2",
                "model_type": "embedding",
                "model_path": os.environ.get("LOCAL_EMBEDDING_MODEL_PATH", ""),
                "description": "本地Embedding向量模型",
                "is_local": True,
                "is_active": True
            },
            {
                "model_key": "minilm",
                "model_name": "all-MiniLM-L6-v2",
                "model_type": "embedding",
                "model_path": os.environ.get("LOCAL_MINILM_MODEL_PATH", ""),
                "description": "轻量级Embedding模型",
                "is_local": True,
                "is_active": True
            },
            {
                "model_key": "cross_encoder",
                "model_name": "ms-marco-MiniLM-L6-v2",
                "model_type": "reranker",
                "model_path": os.environ.get("LOCAL_CROSSENCODER_MODEL_PATH", ""),
                "description": "Cross-Encoder重排序模型",
                "is_local": True,
                "is_active": True
            }
        ]
        return jsonify({"success": True, "data": fallback})
    except Exception as e:
        logger.error(f"获取模型配置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _normalize_model_key(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")


@mxgl_bp.route('/models', methods=['POST'])
def create_model():
    """新增模型配置（支持多个 LLM）"""
    _init_model_tables()
    _ensure_mxgl_token_columns()
    data = request.get_json(silent=True) or {}
    model_key = _normalize_model_key(data.get("model_key") or data.get("model_name"))
    if not model_key:
        return jsonify({"success": False, "error": "model_key不能为空"}), 400

    model_name = (data.get("model_name") or "").strip()
    model_type = (data.get("model_type") or "llm").strip().lower()
    if not model_name:
        return jsonify({"success": False, "error": "model_name不能为空"}), 400
    if model_type not in ("llm", "embedding", "reranker"):
        return jsonify({"success": False, "error": "model_type仅支持 llm/embedding/reranker"}), 400

    try:
        ctx_w = int(data.get("context_window") or 8192)
        max_out = int(data.get("max_output_tokens") or 4096)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "context_window / max_output_tokens 须为整数"}), 400
    if model_type == "llm":
        if ctx_w < 1024 or ctx_w > 2000000:
            return jsonify({"success": False, "error": "context_window 建议 1024～2000000"}), 400
        if max_out < 128 or max_out >= ctx_w:
            return jsonify({"success": False, "error": "max_output_tokens 须小于 context_window 且 >=128"}), 400

    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500

    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO mxgl_models (model_key, model_name, model_type, model_path, api_url, api_key, description,
                        context_window, max_output_tokens, is_local, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    model_key,
                    model_name,
                    model_type,
                    data.get("model_path", ""),
                    data.get("api_url", ""),
                    data.get("api_key", ""),
                    data.get("description", ""),
                    ctx_w if model_type == "llm" else 8192,
                    max_out if model_type == "llm" else 4096,
                    bool(data.get("is_local", False)),
                    bool(data.get("is_active", True)),
                ))
                conn.commit()

        _log_change(
            model_key, model_name, "create", None, data,
            data.get("user_id", "admin"), data.get("user_name", "系统管理员"),
            data.get("change_note", "新增模型配置")
        )
        return jsonify({"success": True, "message": "模型新增成功", "data": {"model_key": model_key}})
    except Exception as e:
        logger.error(f"新增模型失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mxgl_bp.route('/local-models', methods=['GET'])
def get_local_models():
    """获取本地模型列表（扫描ANZHUANGBAO目录）"""
    models = _scan_local_models()
    return jsonify({"success": True, "data": models})


@mxgl_bp.route('/models/<model_key>', methods=['PUT'])
def update_model(model_key: str):
    """更新模型配置"""
    _init_model_tables()
    _ensure_mxgl_token_columns()

    data = request.get_json()
    user_id = data.get('user_id', 'admin')
    user_name = data.get('user_name', '系统管理员')
    change_note = data.get('change_note', '')
    
    # 获取旧值
    old_value = None
    if Config.MYSQL_POOL:
        try:
            with Config.MYSQL_POOL.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT model_name, api_url, api_key, description, is_active, context_window, max_output_tokens "
                        "FROM mxgl_models WHERE model_key = %s",
                        (model_key,),
                    )
                    row = cursor.fetchone()
                    if row:
                        old_value = {
                            "model_name": row[0],
                            "api_url": row[1],
                            "api_key": row[2],
                            "description": row[3],
                            "is_active": bool(row[4]),
                            "context_window": int(row[5]) if row[5] is not None else 8192,
                            "max_output_tokens": int(row[6]) if row[6] is not None else 4096,
                        }
        except Exception as e:
            logger.warning(f"读取旧值失败: {e}")
    
    try:
        ctx_w_u = int(data.get("context_window") or (old_value or {}).get("context_window") or 8192)
        max_out_u = int(data.get("max_output_tokens") or (old_value or {}).get("max_output_tokens") or 4096)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "context_window / max_output_tokens 须为整数"}), 400
    mt = (data.get("model_type") or "llm").strip().lower()
    if mt == "llm" and (max_out_u >= ctx_w_u or max_out_u < 128 or ctx_w_u < 1024):
        return jsonify({"success": False, "error": "LLM: 请检查 context_window 与 max_output_tokens 范围"}), 400

    # 新值
    new_value = {
        "model_name": data.get("model_name", ""),
        "api_url": data.get("api_url", ""),
        "api_key": data.get("api_key", ""),
        "description": data.get("description", ""),
        "is_active": data.get("is_active", True),
        "context_window": ctx_w_u,
        "max_output_tokens": max_out_u,
    }
    
    # 保存到数据库
    if Config.MYSQL_POOL:
        try:
            with Config.MYSQL_POOL.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO mxgl_models (model_key, model_name, model_type, api_url, api_key, description,
                            context_window, max_output_tokens, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            model_name = VALUES(model_name),
                            api_url = VALUES(api_url),
                            api_key = VALUES(api_key),
                            description = VALUES(description),
                            context_window = VALUES(context_window),
                            max_output_tokens = VALUES(max_output_tokens),
                            is_active = VALUES(is_active),
                            updated_at = CURRENT_TIMESTAMP
                    """, (
                        model_key,
                        new_value["model_name"],
                        data.get("model_type", "llm"),
                        new_value["api_url"],
                        new_value["api_key"],
                        new_value["description"],
                        ctx_w_u,
                        max_out_u,
                        new_value["is_active"],
                    ))
                    conn.commit()
            
            # 记录变更日志
            change_type = "create" if not old_value else "update"
            _log_change(
                model_key, new_value["model_name"], change_type,
                old_value, new_value, user_id, user_name, change_note
            )

            log_user_action(f"模型管理(MXGL)-更新模型配置 key={model_key} type={change_type}")
            return jsonify({"success": True, "message": "模型配置更新成功"})
        except Exception as e:
            logger.error(f"更新模型配置失败: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    else:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500


@mxgl_bp.route('/models/<model_key>', methods=['DELETE'])
def delete_model(model_key: str):
    """删除模型配置"""
    _init_model_tables()
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500

    try:
        old_value = None
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT model_name, model_type, model_path, api_url, description, is_local, is_active
                    FROM mxgl_models WHERE model_key = %s
                """, (model_key,))
                row = cursor.fetchone()
                if not row:
                    return jsonify({"success": False, "error": "模型不存在"}), 404
                old_value = {
                    "model_name": row[0],
                    "model_type": row[1],
                    "model_path": row[2],
                    "api_url": row[3],
                    "description": row[4],
                    "is_local": bool(row[5]),
                    "is_active": bool(row[6]),
                }
                cursor.execute("DELETE FROM mxgl_models WHERE model_key = %s", (model_key,))
                conn.commit()

        _log_change(model_key, old_value.get("model_name", ""), "delete", old_value, None, "admin", "系统管理员", "删除模型")
        return jsonify({"success": True, "message": "模型删除成功"})
    except Exception as e:
        logger.error(f"删除模型失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def get_llm_models(active_only: bool = True):
    """获取可用 LLM 模型列表"""
    _init_model_tables()
    items = []
    if Config.MYSQL_POOL:
        try:
            with Config.MYSQL_POOL.connection() as conn:
                with conn.cursor() as cursor:
                    sql = """
                        SELECT model_key, model_name, api_url, api_key, is_local, is_active, description,
                               context_window, max_output_tokens
                        FROM mxgl_models
                        WHERE model_type = 'llm'
                    """
                    if active_only:
                        sql += " AND is_active = TRUE"
                    sql += " ORDER BY updated_at DESC"
                    cursor.execute(sql)
                    for row in cursor.fetchall():
                        items.append({
                            "model_key": row[0],
                            "model_name": row[1],
                            "api_url": row[2] or "",
                            "api_key": row[3] or "",
                            "is_local": bool(row[4]),
                            "is_active": bool(row[5]),
                            "description": row[6] or "",
                            "context_window": int(row[7]) if len(row) > 7 and row[7] is not None else 8192,
                            "max_output_tokens": int(row[8]) if len(row) > 8 and row[8] is not None else 4096,
                            "source": "db",
                        })
        except Exception as e:
            logger.warning(f"获取LLM列表失败: {e}")

    if not items:
        items.append({
            "model_key": "llm_default",
            "model_name": Config.OPENAI_MODEL,
            "api_url": Config.OPENAI_BASE_URL,
            "api_key": Config.OPENAI_API_KEY,
            "is_local": False,
            "is_active": True,
            "description": "默认LLM（环境变量）",
            "context_window": int(getattr(Config, "MAX_CONTEXT_TOKENS", 8192) or 8192),
            "max_output_tokens": 4096,
            "source": "env",
        })
    return items


def resolve_llm_model(model_key: str = "", user_id: str = "") -> dict:
    """按选择的 model_key 解析 LLM 配置，不存在则回退默认配置"""
    selected = (model_key or "").strip()
    llm_items = get_llm_models(active_only=True)
    if selected:
        for item in llm_items:
            if item.get("model_key") == selected:
                return item
        logger.warning(
            "mxgl: llmModelKey=%s 未在已启用 LLM 列表中命中，将使用列表首项或环境变量",
            selected,
        )
    # 未选择或 key 无效：用列表第一项（数据库/env 兜底），不再用 legacy 的 model_key='llm' 单行覆盖，避免与下拉切换打架
    if llm_items:
        return llm_items[0]
    return {
        "model_key": "llm_default",
        "model_name": Config.OPENAI_MODEL,
        "api_url": Config.OPENAI_BASE_URL,
        "api_key": Config.OPENAI_API_KEY,
        "is_local": False,
        "is_active": True,
        "description": "默认LLM（环境变量）",
        "context_window": 8192,
        "max_output_tokens": 4096,
        "source": "env",
    }


@mxgl_bp.route('/llm-models', methods=['GET'])
def get_llm_model_list():
    """返回可选择的 LLM 列表"""
    active_only = request.args.get("active_only", "1").strip() not in ("0", "false", "False")
    return jsonify({"success": True, "data": get_llm_models(active_only=active_only)})


@mxgl_bp.route('/models/<model_key>/sync-env', methods=['POST'])
def sync_to_env(model_key: str):
    """同步模型配置到.env文件"""
    data = request.get_json()
    
    # 读取当前.env文件
    env_path = Path('.env')
    if not env_path.exists():
        return jsonify({"success": False, "error": ".env文件不存在"}), 404
    
    try:
        env_content = env_path.read_text(encoding='utf-8')
        lines = env_content.split('\n')
        
        # 根据model_key确定要更新的环境变量
        llm_update = {
            "OPENAI_API_KEY": data.get("api_key", ""),
            "OPENAI_BASE_URL": data.get("api_url", ""),
            "OPENAI_MODEL": data.get("model_name", "")
        }
        env_mapping = {
            "llm": llm_update,
            "llm_default": llm_update,
            "embedding": {
                "LOCAL_EMBEDDING_MODEL_PATH": data.get("model_path", "")
            },
            "minilm": {
                "LOCAL_MINILM_MODEL_PATH": data.get("model_path", "")
            },
            "cross_encoder": {
                "LOCAL_CROSSENCODER_MODEL_PATH": data.get("model_path", "")
            }
        }
        
        updates = env_mapping.get(model_key, {})
        if not updates and (data.get("model_type") == "llm" or str(model_key).startswith("llm_")):
            updates = llm_update
        if not updates:
            return jsonify({"success": False, "error": "未知的模型类型"}), 400
        
        # 更新环境变量
        new_lines = []
        updated_keys = set()
        
        for line in lines:
            updated = False
            for key, value in updates.items():
                if line.strip().startswith(f"{key}="):
                    new_lines.append(f"{key}={value}")
                    updated_keys.add(key)
                    updated = True
                    break
            if not updated:
                new_lines.append(line)
        
        # 添加缺失的键
        for key, value in updates.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}")
        
        # 写入文件
        env_path.write_text('\n'.join(new_lines), encoding='utf-8')
        
        # 记录变更日志
        _log_change(
            model_key, data.get("model_name", ""),
            "env_sync", None, updates,
            data.get("user_id", "admin"), data.get("user_name", "系统管理员"),
            "同步到.env文件"
        )

        log_user_action(f"模型管理(MXGL)-同步环境变量 key={model_key} keys={list(updated_keys)}")
        return jsonify({"success": True, "message": "已同步到.env文件", "updated_keys": list(updated_keys)})
    except Exception as e:
        logger.error(f"同步到.env文件失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mxgl_bp.route('/change-logs', methods=['GET'])
def get_change_logs():
    """获取模型修改记录"""
    _init_model_tables()
    
    model_key = request.args.get('model_key', '')
    limit = int(request.args.get('limit', 50))
    page = int(request.args.get('page', 1))
    offset = (page - 1) * limit
    
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                # 查询总数
                if model_key:
                    cursor.execute("SELECT COUNT(*) FROM mxgl_change_logs WHERE model_key = %s", (model_key,))
                else:
                    cursor.execute("SELECT COUNT(*) FROM mxgl_change_logs")
                total = cursor.fetchone()[0]
                
                # 查询列表
                if model_key:
                    cursor.execute("""
                        SELECT id, model_key, model_name, change_type, old_value, new_value,
                               user_id, user_name, change_note, created_at
                        FROM mxgl_change_logs
                        WHERE model_key = %s
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                    """, (model_key, limit, offset))
                else:
                    cursor.execute("""
                        SELECT id, model_key, model_name, change_type, old_value, new_value,
                               user_id, user_name, change_note, created_at
                        FROM mxgl_change_logs
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                    """, (limit, offset))
                
                rows = cursor.fetchall()
                logs = []
                for r in rows:
                    logs.append({
                        "id": r[0],
                        "model_key": r[1],
                        "model_name": r[2],
                        "change_type": r[3],
                        "old_value": json.loads(r[4]) if r[4] else None,
                        "new_value": json.loads(r[5]) if r[5] else None,
                        "user_id": r[6],
                        "user_name": r[7],
                        "change_note": r[8],
                        "created_at": str(r[9]) if r[9] else ""
                    })
        
        return jsonify({
            "success": True,
            "data": logs,
            "total": total,
            "page": page,
            "page_size": limit
        })
    except Exception as e:
        logger.error(f"获取修改记录失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mxgl_bp.route('/stats', methods=['GET'])
def get_model_stats():
    """获取模型统计信息"""
    _init_model_tables()
    
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                # 统计模型数量
                cursor.execute("SELECT COUNT(*) FROM mxgl_models")
                model_count = cursor.fetchone()[0]
                
                # 统计修改记录数量
                cursor.execute("SELECT COUNT(*) FROM mxgl_change_logs")
                log_count = cursor.fetchone()[0]
                
                # 最近修改记录
                cursor.execute("""
                    SELECT model_key, model_name, change_type, user_name, created_at
                    FROM mxgl_change_logs
                    ORDER BY created_at DESC
                    LIMIT 5
                """)
                recent_logs = [
                    {"model_key": r[0], "model_name": r[1], "change_type": r[2], 
                     "user_name": r[3], "created_at": str(r[4]) if r[4] else ""}
                    for r in cursor.fetchall()
                ]
        
        return jsonify({
            "success": True,
            "data": {
                "model_count": model_count,
                "log_count": log_count,
                "recent_logs": recent_logs
            }
        })
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# 初始化表
_init_model_tables()


def get_model_config(user_id: str = None, model_key: str = 'llm') -> dict:
    """获取有效的模型配置（用户偏好优先，否则使用全局配置）
    
    Args:
        user_id: 用户ID，如果为None则返回全局配置
        model_key: 模型标识，默认'llm'
    
    Returns:
        包含 model_name, api_url, api_key, is_local 等配置的字典
    """
    if not user_id or not Config.MYSQL_POOL:
        return {
            "model_name": Config.OPENAI_MODEL,
            "api_url": Config.OPENAI_BASE_URL,
            "api_key": Config.OPENAI_API_KEY,
            "is_local": False,
            "source": "env"
        }
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT model_name, model_path, api_url, api_key, is_active, scope
                    FROM mxgl_user_preferences 
                    WHERE user_id = %s AND model_key = %s AND is_active = TRUE
                """, (user_id, model_key))
                row = cursor.fetchone()
                
                if row:
                    return {
                        "model_name": row[0] or "",
                        "model_path": row[1] or "",
                        "api_url": row[2] or "",
                        "api_key": row[3] or "",
                        "is_active": bool(row[4]),
                        "scope": row[5],
                        "source": "user_preference"
                    }
                
                cursor.execute("""
                    SELECT model_name, model_path, api_url, api_key, is_local, is_active
                    FROM mxgl_models 
                    WHERE model_key = %s AND is_active = TRUE
                """, (model_key,))
                row = cursor.fetchone()
                
                if row:
                    return {
                        "model_name": row[0] or "",
                        "model_path": row[1] or "",
                        "api_url": row[2] or "",
                        "api_key": row[3] or "",
                        "is_local": bool(row[4]),
                        "is_active": bool(row[5]),
                        "source": "global_db"
                    }
                
                return {
                    "model_name": Config.OPENAI_MODEL,
                    "api_url": Config.OPENAI_BASE_URL,
                    "api_key": Config.OPENAI_API_KEY,
                    "is_local": False,
                    "source": "env"
                }
    except Exception as e:
        logger.warning(f"获取模型配置失败，使用全局配置: {e}")
        return {
            "model_name": Config.OPENAI_MODEL,
            "api_url": Config.OPENAI_BASE_URL,
            "api_key": Config.OPENAI_API_KEY,
            "is_local": False,
            "source": "env"
        }


@mxgl_bp.route('/user-preferences', methods=['GET'])
def get_user_preferences():
    """获取用户模型偏好设置"""
    user_id = request.args.get('user_id', '')
    model_key = request.args.get('model_key', '')
    
    if not user_id:
        return jsonify({"success": False, "error": "user_id required"}), 400
    
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                if model_key:
                    cursor.execute("""
                        SELECT user_id, model_key, model_name, model_path, api_url, api_key, is_active, scope
                        FROM mxgl_user_preferences 
                        WHERE user_id = %s AND model_key = %s
                    """, (user_id, model_key))
                    row = cursor.fetchone()
                    if row:
                        return jsonify({
                            "success": True,
                            "data": {
                                "user_id": row[0],
                                "model_key": row[1],
                                "model_name": row[2],
                                "model_path": row[3],
                                "api_url": row[4],
                                "api_key": row[5],
                                "is_active": bool(row[6]),
                                "scope": row[7]
                            }
                        })
                    return jsonify({"success": True, "data": None})
                else:
                    cursor.execute("""
                        SELECT user_id, model_key, model_name, model_path, api_url, api_key, is_active, scope
                        FROM mxgl_user_preferences 
                        WHERE user_id = %s
                    """, (user_id,))
                    rows = cursor.fetchall()
                    result = []
                    for r in rows:
                        result.append({
                            "user_id": r[0],
                            "model_key": r[1],
                            "model_name": r[2],
                            "model_path": r[3],
                            "api_url": r[4],
                            "api_key": r[5],
                            "is_active": bool(r[6]),
                            "scope": r[7]
                        })
                    return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"获取用户偏好失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mxgl_bp.route('/user-preferences', methods=['PUT'])
def update_user_preferences():
    """更新用户模型偏好设置"""
    data = request.get_json()
    user_id = data.get('user_id', '')
    model_key = data.get('model_key', '')
    model_name = data.get('model_name', '')
    model_path = data.get('model_path', '')
    api_url = data.get('api_url', '')
    api_key = data.get('api_key', '')
    scope = data.get('scope', 'user')
    is_active = data.get('is_active', True)
    
    if not user_id or not model_key:
        return jsonify({"success": False, "error": "user_id and model_key required"}), 400
    
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO mxgl_user_preferences 
                    (user_id, model_key, model_name, model_path, api_url, api_key, is_active, scope)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        model_name = VALUES(model_name),
                        model_path = VALUES(model_path),
                        api_url = VALUES(api_url),
                        api_key = VALUES(api_key),
                        is_active = VALUES(is_active),
                        scope = VALUES(scope),
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, model_key, model_name, model_path, api_url, api_key, is_active, scope))
                conn.commit()
                
                _log_change(
                    f"{model_key}_user", model_name, "user_update",
                    None, {"user_id": user_id, "scope": scope},
                    user_id, data.get('user_name', '用户'),
                    f"更新用户模型偏好: {model_key}"
                )

        log_user_action(f"模型管理(MXGL)-更新用户模型偏好 user={user_id} key={model_key}")
        return jsonify({"success": True, "message": "用户偏好设置已更新"})
    except Exception as e:
        logger.error(f"更新用户偏好失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mxgl_bp.route('/user-preferences', methods=['DELETE'])
def delete_user_preferences():
    """删除用户模型偏好设置（恢复使用全局配置）"""
    data = request.get_json()
    user_id = data.get('user_id', '')
    model_key = data.get('model_key', '')
    
    if not user_id or not model_key:
        return jsonify({"success": False, "error": "user_id and model_key required"}), 400
    
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    DELETE FROM mxgl_user_preferences 
                    WHERE user_id = %s AND model_key = %s
                """, (user_id, model_key))
                conn.commit()

        log_user_action(f"模型管理(MXGL)-删除用户模型偏好 user={user_id} key={model_key}")
        return jsonify({"success": True, "message": "用户偏好已删除，将使用全局配置"})
    except Exception as e:
        logger.error(f"删除用户偏好失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mxgl_bp.route('/effective-config', methods=['GET'])
def get_effective_config():
    """获取有效的模型配置（用户偏好优先，否则使用全局配置）"""
    user_id = request.args.get('user_id', '')
    model_key = request.args.get('model_key', 'llm')
    
    if not user_id:
        return jsonify({"success": False, "error": "user_id required"}), 400
    
    if not Config.MYSQL_POOL:
        return jsonify({"success": False, "error": "MySQL未连接"}), 500
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT model_name, model_path, api_url, api_key, is_active, scope
                    FROM mxgl_user_preferences 
                    WHERE user_id = %s AND model_key = %s AND is_active = TRUE
                """, (user_id, model_key))
                row = cursor.fetchone()
                
                if row:
                    return jsonify({
                        "success": True,
                        "data": {
                            "model_name": row[0],
                            "model_path": row[1],
                            "api_url": row[2],
                            "api_key": row[3],
                            "is_active": bool(row[4]),
                            "scope": row[5],
                            "source": "user_preference"
                        }
                    })
                
                cursor.execute("""
                    SELECT model_key, model_name, api_url, api_key
                    FROM mxgl_models 
                    WHERE model_key = %s AND is_active = TRUE
                """, (model_key,))
                row = cursor.fetchone()
                
                if row:
                    return jsonify({
                        "success": True,
                        "data": {
                            "model_key": row[0],
                            "model_name": row[1],
                            "api_url": row[2],
                            "api_key": row[3],
                            "source": "global_config"
                        }
                    })
                
                return jsonify({
                    "success": True,
                    "data": {
                        "model_name": Config.OPENAI_MODEL,
                        "api_url": Config.OPENAI_BASE_URL,
                        "api_key": Config.OPENAI_API_KEY,
                        "source": "env_config"
                    }
                })
    except Exception as e:
        logger.error(f"获取有效配置失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
