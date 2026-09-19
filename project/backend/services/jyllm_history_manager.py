"""
JYLLM历史记录管理模块
提供完整的方案历史保存、查询和恢复功能
"""
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)


def _serialize_messages_for_db(value) -> Optional[str]:
    """messages 列存 LONGTEXT（JSON 字符串）；避免直接传 list/dict 导致驱动或 MySQL 异常。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


class JYLLMHistoryManager:
    """JYLLM辅助设计历史记录管理器"""
    
    def __init__(self):
        self.mysql_pool = getattr(Config, 'MYSQL_POOL', None)
    
    def _ensure_tables(self):
        """确保数据库表存在"""
        if not self.mysql_pool:
            logger.error("MySQL pool not available")
            return False
        
        try:
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    # 读取并执行SQL文件
                    import os
                    _base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                    _candidates = [
                        os.path.join(_base, "Database", "MySQL", "jyllm_history_schema.sql"),
                        os.path.join(_base, "database", "mysql", "jyllm_history_schema.sql"),
                    ]
                    sql_path = next((p for p in _candidates if os.path.exists(p)), None)
                    if sql_path:
                        with open(sql_path, 'r', encoding='utf-8') as f:
                            sql_content = f.read()
                            # 分割并执行每个CREATE TABLE语句
                            statements = sql_content.split(';')
                            for stmt in statements:
                                stmt = stmt.strip()
                                if stmt and 'CREATE TABLE' in stmt.upper():
                                    try:
                                        cursor.execute(stmt)
                                    except Exception as e:
                                        if 'already exists' not in str(e).lower():
                                            logger.warning(f"Table creation warning: {e}")
                        conn.commit()
                        logger.info("JYLLM history tables ensured")
                        return True
                    logger.warning(
                        "JYLLM schema SQL not found (tried backend/Database/MySQL and backend/database/mysql)"
                    )
        except Exception as e:
            logger.error(f"Failed to ensure tables: {e}")
        return False

    def _migrate_scheme_history_columns(self, cursor) -> None:
        """老库表已存在但缺列时补齐（CREATE IF NOT EXISTS 不会更新旧表结构）。"""
        def add_col(sql: str, label: str) -> None:
            try:
                cursor.execute(sql)
            except Exception as e:
                err = str(e).lower()
                if "duplicate" not in err and "1060" not in err and "already exists" not in err:
                    logger.warning("migrate jyllm_scheme_history %s: %s", label, e)

        add_col(
            """
                ALTER TABLE jyllm_scheme_history
                ADD COLUMN current_step VARCHAR(50) DEFAULT 'device'
                COMMENT '当前步骤'
            """,
            "current_step",
        )
        add_col(
            """
                ALTER TABLE jyllm_scheme_history
                ADD COLUMN messages LONGTEXT NULL
                COMMENT '对话历史（JSON）'
            """,
            "messages",
        )
        add_col(
            """
                ALTER TABLE jyllm_scheme_history
                ADD COLUMN planner_prompts JSON NULL
                COMMENT '大纲提示词列表'
            """,
            "planner_prompts",
        )
        add_col(
            """
                ALTER TABLE jyllm_scheme_history
                ADD COLUMN selected_equipment_keys JSON NULL
                COMMENT '选择的设备ID列表'
            """,
            "selected_equipment_keys",
        )
        add_col(
            """
                ALTER TABLE jyllm_scheme_history
                ADD COLUMN page_type VARCHAR(20) DEFAULT 'jyllm'
                COMMENT '页面类型'
            """,
            "page_type",
        )
        add_col(
            """
                ALTER TABLE jyllm_scheme_history
                ADD COLUMN overall_score INT NULL
                COMMENT '用户整体评分'
            """,
            "overall_score",
        )
        add_col(
            """
                ALTER TABLE jyllm_scheme_history
                ADD COLUMN overall_comment TEXT NULL
                COMMENT '用户整体评价'
            """,
            "overall_comment",
        )
    
    def save_scheme_history(self, history_data: Dict[str, Any]) -> bool:
        if not self.mysql_pool:
            logger.error("MySQL pool not available")
            return False
        
        try:
            self._ensure_tables()
            
            with self.mysql_pool.connection() as conn:
                try:
                    with conn.cursor() as cursor:
                        self._migrate_scheme_history_columns(cursor)
                        conn.commit()

                    tid = history_data.get("task_id")
                    uid = history_data.get("user_id", "admin")
                    with conn.cursor() as cursor:
                        # 仅替换主表行；章节仍由 save_chapters 单独删写，避免仅更新元数据时清空章节
                        cursor.execute(
                            "DELETE FROM jyllm_scheme_history WHERE task_id = %s AND user_id = %s",
                            (tid, uid),
                        )
                        sql = """
                        INSERT INTO jyllm_scheme_history (
                            task_id, user_id, scheme_name, scheme_description,
                            selected_equipment, selected_environments, selected_projects,
                            selected_indicators, selected_methods, selected_files,
                            environment_config, template_id, template_name,
                            generated_content, word_count, generation_status,
                            current_step, messages, planner_prompts, selected_equipment_keys,
                            page_type, overall_score, overall_comment
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """
                        cursor.execute(
                            sql,
                            (
                                tid,
                                uid,
                                history_data.get("scheme_name"),
                                history_data.get("scheme_description"),
                                json.dumps(
                                    history_data.get("selected_equipment", []), ensure_ascii=False
                                ),
                                json.dumps(
                                    history_data.get("selected_environments", []), ensure_ascii=False
                                ),
                                json.dumps(
                                    history_data.get("selected_projects", []), ensure_ascii=False
                                ),
                                json.dumps(
                                    history_data.get("selected_indicators", []), ensure_ascii=False
                                ),
                                json.dumps(
                                    history_data.get("selected_methods", []), ensure_ascii=False
                                ),
                                json.dumps(
                                    history_data.get("selected_files", []), ensure_ascii=False
                                ),
                                history_data.get("environment_config"),
                                history_data.get("template_id"),
                                history_data.get("template_name"),
                                history_data.get("generated_content"),
                                history_data.get("word_count", 0),
                                history_data.get("generation_status", "generating"),
                                history_data.get("current_step", "device"),
                                _serialize_messages_for_db(history_data.get("messages")),
                                json.dumps(
                                    history_data.get("planner_prompts", []), ensure_ascii=False
                                )
                                if history_data.get("planner_prompts")
                                else None,
                                json.dumps(
                                    history_data.get("selected_equipment_keys", []), ensure_ascii=False
                                )
                                if history_data.get("selected_equipment_keys")
                                else None,
                                history_data.get("page_type", "jyllm"),
                                history_data.get("overall_score"),
                                history_data.get("overall_comment"),
                            ),
                        )
                    conn.commit()
                    sn = history_data.get("scheme_name") or ""
                    wc = history_data.get("word_count", 0)
                    logger.info(
                        "[MySQL] jyllm_scheme_history INSERT ok | task_id=%s user_id=%s scheme_name=%r word_count=%s",
                        tid,
                        uid,
                        sn,
                        wc,
                    )
                    return True
                except Exception:
                    conn.rollback()
                    raise

        except Exception as e:
            logger.error("[MySQL] Failed to save scheme history: %s", e)
            return False
    
    def save_chapters(self, task_id: str, chapters: List[Dict[str, Any]]) -> bool:
        """保存章节详情"""
        if not self.mysql_pool:
            return False
        
        try:
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    # 删除旧记录
                    cursor.execute(
                        "DELETE FROM jyllm_chapters WHERE task_id = %s",
                        (task_id,)
                    )
                    
                    # 插入新记录
                    sql = """
                    INSERT INTO jyllm_chapters (
                        task_id, chapter_index, chapter_title,
                        writer_prompt, reviewer_prompt, content,
                        thinking, keywords, search_context,
                        auto_score, user_score, user_comment, review_status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    for chapter in chapters:
                        cursor.execute(sql, (
                            task_id,
                            chapter.get('chapter_index'),
                            chapter.get('chapter_title'),
                            chapter.get('writer_prompt'),
                            chapter.get('reviewer_prompt'),
                            chapter.get('content'),
                            chapter.get('thinking'),
                            chapter.get('keywords'),
                            chapter.get('search_context'),
                            chapter.get('auto_score'),
                            chapter.get('user_score'),
                            chapter.get('user_comment'),
                            chapter.get('review_status', 'pending')
                        ))
                    
                    conn.commit()
                    logger.info(
                        "[MySQL] jyllm_chapters INSERT ok | task_id=%s rows=%d",
                        task_id,
                        len(chapters),
                    )
                    return True
                    
        except Exception as e:
            logger.error("[MySQL] Failed to save chapters: %s", e)
            return False
    
    def save_agent_interaction(self, interaction_data: Dict[str, Any]) -> bool:
        """保存智能体交互记录"""
        if not self.mysql_pool:
            return False
        
        try:
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    sql = """
                    INSERT INTO jyllm_agent_interactions (
                        task_id, agent_type, interaction_type,
                        chapter_index, chapter_title,
                        input_content, output_content, thinking_content,
                        tool_name, tool_params, tool_result,
                        score, is_pass, review_comments,
                        tokens_input, tokens_output, duration_ms
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    cursor.execute(sql, (
                        interaction_data.get('task_id'),
                        interaction_data.get('agent_type'),
                        interaction_data.get('interaction_type'),
                        interaction_data.get('chapter_index'),
                        interaction_data.get('chapter_title'),
                        interaction_data.get('input_content'),
                        interaction_data.get('output_content'),
                        interaction_data.get('thinking_content'),
                        interaction_data.get('tool_name'),
                        json.dumps(interaction_data.get('tool_params', {}), ensure_ascii=False),
                        interaction_data.get('tool_result'),
                        interaction_data.get('score'),
                        interaction_data.get('is_pass'),
                        interaction_data.get('review_comments'),
                        interaction_data.get('tokens_input'),
                        interaction_data.get('tokens_output'),
                        interaction_data.get('duration_ms')
                    ))
                    
                    conn.commit()
                    return True
                    
        except Exception as e:
            logger.error(f"Failed to save agent interaction: {e}")
            return False
    
    def save_user_action(self, task_id: str, user_id: str, action_type: str, action_data: Dict[str, Any]) -> bool:
        """保存用户操作日志"""
        if not self.mysql_pool:
            return False
        
        try:
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    sql = """
                    INSERT INTO jyllm_user_actions (task_id, user_id, action_type, action_data)
                    VALUES (%s, %s, %s, %s)
                    """
                    
                    cursor.execute(sql, (
                        task_id, user_id, action_type,
                        json.dumps(action_data, ensure_ascii=False)
                    ))
                    
                    conn.commit()
                    return True
                    
        except Exception as e:
            logger.error(f"Failed to save user action: {e}")
            return False
    
    def get_user_history_list(self, user_id: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        """获取用户历史记录列表"""
        if not self.mysql_pool:
            return {"success": False, "error": "Database not available"}
        
        try:
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    # 查询总数
                    cursor.execute(
                        "SELECT COUNT(*) FROM jyllm_scheme_history WHERE user_id = %s",
                        (user_id,)
                    )
                    total = cursor.fetchone()[0]
                    
                    # 查询列表
                    offset = (page - 1) * page_size
                    cursor.execute("""
                        SELECT 
                            task_id, scheme_name, generation_status,
                            created_at, completed_at, word_count
                        FROM jyllm_scheme_history
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s
                    """, (user_id, page_size, offset))
                    
                    rows = cursor.fetchall()
                    items = []
                    for row in rows:
                        items.append({
                            'task_id': row[0],
                            'scheme_name': row[1],
                            'generation_status': row[2],
                            'created_at': row[3].isoformat() if row[3] else None,
                            'completed_at': row[4].isoformat() if row[4] else None,
                            'word_count': row[5]
                        })
                    
                    return {
                        "success": True,
                        "items": items,
                        "total": total,
                        "page": page,
                        "page_size": page_size
                    }
                    
        except Exception as e:
            logger.error(f"Failed to get history list: {e}")
            return {"success": False, "error": str(e)}
    
    def get_scheme_detail(self, task_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        if not self.mysql_pool:
            return None
        
        try:
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT * FROM jyllm_scheme_history
                        WHERE task_id = %s AND user_id = %s
                    """, (task_id, user_id))
                    
                    row = cursor.fetchone()
                    if not row:
                        return None
                    
                    columns = [desc[0] for desc in cursor.description]
                    history_data = dict(zip(columns, row))
                    
                    for field in ['selected_equipment', 'selected_environments', 'selected_projects', 
                                  'selected_indicators', 'selected_methods', 'selected_files',
                                  'planner_prompts', 'selected_equipment_keys']:
                        if history_data.get(field):
                            try:
                                history_data[field] = json.loads(history_data[field])
                            except:
                                history_data[field] = []
                    
                    if history_data.get('messages'):
                        try:
                            history_data['messages'] = json.loads(history_data['messages'])
                        except:
                            history_data['messages'] = []
                    
                    cursor.execute("""
                        SELECT * FROM jyllm_chapters
                        WHERE task_id = %s
                        ORDER BY chapter_index
                    """, (task_id,))
                    
                    chapter_rows = cursor.fetchall()
                    chapters = []
                    if chapter_rows:
                        chapter_columns = [desc[0] for desc in cursor.description]
                        for chapter_row in chapter_rows:
                            chapters.append(dict(zip(chapter_columns, chapter_row)))
                    
                    history_data['chapters'] = chapters
                    
                    cursor.execute("""
                        SELECT * FROM jyllm_agent_interactions
                        WHERE task_id = %s
                        ORDER BY created_at
                    """, (task_id,))
                    
                    interaction_rows = cursor.fetchall()
                    interactions = []
                    if interaction_rows:
                        interaction_columns = [desc[0] for desc in cursor.description]
                        for interaction_row in interaction_rows:
                            interactions.append(dict(zip(interaction_columns, interaction_row)))
                    
                    history_data['agent_interactions'] = interactions

                    need_score = history_data.get("overall_score") is None
                    need_comment = not (history_data.get("overall_comment") or "").strip()
                    if need_score or need_comment:
                        cursor.execute(
                            """
                            SELECT action_data FROM jyllm_user_actions
                            WHERE task_id = %s AND action_type = 'feedback_save'
                            ORDER BY created_at DESC
                            LIMIT 20
                            """,
                            (task_id,),
                        )
                        action_rows = cursor.fetchall()
                        for action_row in action_rows:
                            if not action_row or not action_row[0]:
                                continue
                            try:
                                action_data = json.loads(action_row[0]) if isinstance(action_row[0], str) else action_row[0]
                            except Exception:
                                continue
                            if action_data.get("chapter_index") != -1:
                                continue
                            if need_score and action_data.get("score") is not None:
                                history_data["overall_score"] = action_data.get("score")
                                need_score = False
                            if need_comment and (action_data.get("comment") or "").strip():
                                history_data["overall_comment"] = action_data.get("comment")
                                need_comment = False
                            if not need_score and not need_comment:
                                break
                    
                    return history_data
                    
        except Exception as e:
            logger.error(f"Failed to get scheme detail: {e}")
            return None
    
    def delete_scheme_history(self, task_id: str, user_id: str) -> bool:
        """删除历史记录"""
        if not self.mysql_pool:
            return False
        
        try:
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    # 删除关联表数据
                    cursor.execute("DELETE FROM jyllm_chapters WHERE task_id = %s", (task_id,))
                    cursor.execute("DELETE FROM jyllm_agent_interactions WHERE task_id = %s", (task_id,))
                    cursor.execute("DELETE FROM jyllm_user_actions WHERE task_id = %s", (task_id,))
                    cursor.execute(
                        "DELETE FROM jyllm_scheme_history WHERE task_id = %s AND user_id = %s",
                        (task_id, user_id)
                    )
                    
                    conn.commit()
                    logger.info(f"Scheme history deleted: {task_id}")
                    return True
                    
        except Exception as e:
            logger.error(f"Failed to delete scheme history: {e}")
            return False

    def update_overall_feedback(
        self,
        task_id: str,
        user_id: str,
        score: Optional[int],
        comment: Optional[str],
    ) -> bool:
        """更新方案主表中的整体评价（保存评价按钮）。"""
        if not self.mysql_pool:
            return False
        try:
            self._ensure_tables()
            with self.mysql_pool.connection() as conn:
                with conn.cursor() as cursor:
                    self._migrate_scheme_history_columns(cursor)
                    conn.commit()
                    cursor.execute(
                        """
                        UPDATE jyllm_scheme_history
                        SET overall_score = %s, overall_comment = %s, updated_at = NOW()
                        WHERE task_id = %s AND user_id = %s
                        """,
                        (score, comment or "", task_id, user_id),
                    )
                    updated = cursor.rowcount > 0
                conn.commit()
                return updated
        except Exception as e:
            logger.error("Failed to update overall feedback: %s", e)
            return False


# 单例模式
_history_manager = None

def get_history_manager() -> JYLLMHistoryManager:
    """获取历史记录管理器实例"""
    global _history_manager
    if _history_manager is None:
        _history_manager = JYLLMHistoryManager()
    return _history_manager
