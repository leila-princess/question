"""
JYLLM 辅助设计历史记录数据库建表脚本
直接使用 config.py 中的 MySQL 配置创建表
"""
import sys
import os

# 添加backend目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config
import pymysql
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# SQL 建表语句
CREATE_TABLES_SQL = """
-- JYLLM 辅助设计历史记录数据库设计
-- 用于保存完整的试验方案生成过程和结果

-- 1. 主表：方案历史记录
CREATE TABLE IF NOT EXISTS jyllm_scheme_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    task_id VARCHAR(100) NOT NULL COMMENT '任务唯一标识',
    user_id VARCHAR(64) NOT NULL COMMENT '用户ID',
    
    scheme_name VARCHAR(200) COMMENT '方案名称',
    scheme_description TEXT COMMENT '方案描述',
    
    selected_equipment JSON COMMENT '选择的设备列表',
    selected_environments JSON COMMENT '选择的环境列表',
    selected_projects JSON COMMENT '选择的项目列表',
    selected_indicators JSON COMMENT '选择的指标列表',
    selected_methods JSON COMMENT '选择的方法列表',
    selected_files JSON COMMENT '上传的参考文件列表',
    environment_config TEXT COMMENT '环境详细配置',
    
    template_id VARCHAR(64) COMMENT '使用的模板ID',
    template_name VARCHAR(200) COMMENT '模板名称',
    
    generated_content LONGTEXT COMMENT '最终生成的完整方案内容',
    word_count INT DEFAULT 0 COMMENT '方案字数统计',
    generation_status VARCHAR(20) COMMENT '生成状态：generating/complete/failed',
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    completed_at TIMESTAMP NULL COMMENT '完成时间',
    
    INDEX idx_task_id (task_id),
    INDEX idx_user_id (user_id),
    INDEX idx_created_at (created_at),
    INDEX idx_status (generation_status),
    FULLTEXT INDEX ft_content (generated_content) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='JYLLM 辅助设计方案历史记录主表';


-- 2. 智能体交互记录表
CREATE TABLE IF NOT EXISTS jyllm_agent_interactions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    task_id VARCHAR(100) NOT NULL COMMENT '关联的任务ID',
    
    agent_type VARCHAR(20) NOT NULL COMMENT '智能体类型：planner/writer/reviewer/librarian',
    interaction_type VARCHAR(20) NOT NULL COMMENT '交互类型：input/output/thinking/tool_call',
    
    chapter_index INT COMMENT '章节索引',
    chapter_title VARCHAR(200) COMMENT '章节标题',
    
    input_content LONGTEXT COMMENT '输入提示词/上下文',
    output_content LONGTEXT COMMENT '输出内容',
    thinking_content LONGTEXT COMMENT '思考过程',
    
    tool_name VARCHAR(50) COMMENT '调用的工具名称',
    tool_params JSON COMMENT '工具参数',
    tool_result TEXT COMMENT '工具返回结果',
    
    score INT COMMENT '评分（0-100）',
    is_pass BOOLEAN COMMENT '是否通过评审',
    review_comments TEXT COMMENT '评审意见',
    
    tokens_input INT COMMENT '输入token数',
    tokens_output INT COMMENT '输出token数',
    duration_ms INT COMMENT '执行耗时（毫秒）',
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    
    INDEX idx_task_id (task_id),
    INDEX idx_agent_type (agent_type),
    INDEX idx_interaction_type (interaction_type),
    INDEX idx_chapter (task_id, chapter_index),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='智能体交互详细记录';


-- 3. 生成章节详情表
CREATE TABLE IF NOT EXISTS jyllm_chapters (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    task_id VARCHAR(100) NOT NULL COMMENT '关联的任务ID',
    
    chapter_index INT NOT NULL COMMENT '章节索引（从1开始）',
    chapter_title VARCHAR(200) NOT NULL COMMENT '章节标题',
    
    writer_prompt LONGTEXT COMMENT '写作提示词',
    reviewer_prompt LONGTEXT COMMENT '评审提示词',
    
    content LONGTEXT COMMENT '章节内容（Markdown）',
    thinking TEXT COMMENT '写作思考过程',
    keywords VARCHAR(500) COMMENT '检索关键词',
    search_context TEXT COMMENT '检索上下文',
    
    auto_score INT COMMENT '自动评审分数',
    user_score INT COMMENT '用户评分',
    user_comment TEXT COMMENT '用户评论',
    review_status VARCHAR(20) COMMENT '评审状态：pending/completed/rewriting',
    
    rewrite_count INT DEFAULT 0 COMMENT '重写次数',
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    
    INDEX idx_task_id (task_id),
    INDEX idx_chapter_index (task_id, chapter_index),
    UNIQUE KEY uk_task_chapter (task_id, chapter_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='方案章节详情';


-- 4. 用户操作日志表
CREATE TABLE IF NOT EXISTS jyllm_user_actions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    task_id VARCHAR(100) NOT NULL COMMENT '关联的任务ID',
    user_id VARCHAR(64) NOT NULL COMMENT '用户ID',
    
    action_type VARCHAR(50) NOT NULL COMMENT '操作类型',
    action_data JSON COMMENT '操作数据（存储选择的具体内容）',
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    
    INDEX idx_task_id (task_id),
    INDEX idx_user_id (user_id),
    INDEX idx_action_type (action_type),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='用户操作日志';


-- 5. 视图：方案概览
CREATE OR REPLACE VIEW jyllm_scheme_overview AS
SELECT 
    h.id,
    h.task_id,
    h.user_id,
    h.scheme_name,
    h.generation_status,
    h.created_at,
    h.completed_at,
    h.word_count,
    COUNT(DISTINCT c.chapter_index) as chapter_count,
    AVG(c.auto_score) as avg_auto_score,
    AVG(c.user_score) as avg_user_score
FROM jyllm_scheme_history h
LEFT JOIN jyllm_chapters c ON h.task_id = c.task_id
GROUP BY h.id, h.task_id, h.user_id, h.scheme_name, h.generation_status, h.created_at, h.completed_at, h.word_count;
"""


def create_tables():
    """使用 config.py 中的配置创建数据库表"""
    logger.info("=" * 60)
    logger.info("开始创建 JYLLM 历史记录数据库表")
    logger.info("=" * 60)
    
    # 获取配置
    host = Config.DB_HOST
    port = Config.DB_PORT
    user = Config.DB_USER
    password = Config.DB_PASSWORD
    database = Config.DB_NAME
    
    logger.info(f"数据库配置: host={host}, port={port}, database={database}, user={user}")
    
    try:
        # 连接MySQL（先不指定数据库，检查/创建数据库）
        logger.info("正在连接MySQL服务器...")
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            charset='utf8mb4'
        )
        
        with conn.cursor() as cursor:
            # 检查数据库是否存在，不存在则创建
            logger.info(f"检查数据库 {database} 是否存在...")
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            logger.info(f"数据库 {database} 已准备就绪")
            
            # 切换到目标数据库
            cursor.execute(f"USE {database}")
            logger.info(f"已切换到数据库 {database}")
        
        conn.close()
        logger.info("数据库连接成功！")
        
        # 重新连接到指定数据库创建表
        logger.info("正在创建数据表...")
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset='utf8mb4'
        )
        
        with conn.cursor() as cursor:
            # 分割SQL语句并逐个执行
            statements = CREATE_TABLES_SQL.split(';')
            
            success_count = 0
            skip_count = 0
            
            for stmt in statements:
                stmt = stmt.strip()
                if not stmt or stmt.startswith('--') or stmt.startswith('/*'):
                    continue
                
                try:
                    cursor.execute(stmt)
                    conn.commit()
                    
                    # 提取表名或视图名
                    if 'CREATE TABLE' in stmt.upper():
                        table_name = stmt.split('CREATE TABLE IF NOT EXISTS')[1].split('(')[0].strip()
                        logger.info(f"表已创建/已存在: {table_name}")
                        success_count += 1
                    elif 'CREATE VIEW' in stmt.upper():
                        view_name = stmt.split('CREATE OR REPLACE VIEW')[1].split('AS')[0].strip()
                        logger.info(f"视图已创建/已更新: {view_name}")
                        success_count += 1
                        
                except pymysql.err.OperationalError as e:
                    if 'already exists' in str(e).lower():
                        skip_count += 1
                    else:
                        logger.warning(f"执行SQL警告: {e}")
                        conn.rollback()
                except Exception as e:
                    logger.warning(f"执行SQL警告: {e}")
                    conn.rollback()
            
            logger.info(f"SQL执行完成: {success_count} 个成功, {skip_count} 个已存在跳过")
        
        conn.close()
        
        # 验证表是否创建成功
        logger.info("正在验证表结构...")
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset='utf8mb4'
        )
        
        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'jyllm_%'")
            tables = cursor.fetchall()
            
            logger.info("=" * 60)
            logger.info("已创建的JYLLM相关表:")
            for table in tables:
                logger.info(f"  - {table[0]}")
            
            # 检查视图
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'VIEW' AND Tables_in_znwd_history LIKE 'jyllm_%'")
            views = cursor.fetchall()
            
            if views:
                logger.info("已创建的视图:")
                for view in views:
                    logger.info(f"  - {view[0]}")
        
        conn.close()
        
        logger.info("=" * 60)
        logger.info("JYLLM 历史记录数据库表创建完成！")
        logger.info("=" * 60)
        logger.info("可用的表:")
        logger.info("  1. jyllm_scheme_history - 方案历史主表")
        logger.info("  2. jyllm_agent_interactions - 智能体交互记录")
        logger.info("  3. jyllm_chapters - 章节详情表")
        logger.info("  4. jyllm_user_actions - 用户操作日志")
        logger.info("  5. jyllm_scheme_overview - 方案概览视图")
        logger.info("=" * 60)
        
        return True
        
    except Exception as e:
        logger.error(f"创建数据库表失败: {e}")
        logger.error("请检查:")
        logger.error("  1. MySQL服务器是否启动")
        logger.error("  2. config.py中的数据库配置是否正确")
        logger.error("  3. 用户名密码是否有权限创建数据库和表")
        return False


if __name__ == '__main__':
    success = create_tables()
    sys.exit(0 if success else 1)
