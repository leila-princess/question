-- JYLLM 辅助设计历史记录数据库设计
-- 用于保存完整的试验方案生成过程和结果

-- 1. 主表：方案历史记录
CREATE TABLE IF NOT EXISTS jyllm_scheme_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    task_id VARCHAR(100) NOT NULL COMMENT '任务唯一标识',
    user_id VARCHAR(64) NOT NULL COMMENT '用户ID',
    
    -- 基本信息
    scheme_name VARCHAR(200) COMMENT '方案名称',
    scheme_description TEXT COMMENT '方案描述',
    
    -- 用户选择配置（JSON存储）
    selected_equipment JSON COMMENT '选择的设备列表 [{id, name, type}]',
    selected_environments JSON COMMENT '选择的环境列表 [{value, label}]',
    selected_projects JSON COMMENT '选择的项目列表 [{id, name}]',
    selected_indicators JSON COMMENT '选择的指标列表 [{id, name}]',
    selected_methods JSON COMMENT '选择的方法列表 [{id, name}]',
    selected_files JSON COMMENT '上传的参考文件列表 [{filename, minio_id}]',
    environment_config TEXT COMMENT '环境详细配置',
    
    -- 模板信息
    template_id VARCHAR(64) COMMENT '使用的模板ID',
    template_name VARCHAR(200) COMMENT '模板名称',
    
    -- 生成结果
    generated_content LONGTEXT COMMENT '最终生成的完整方案内容（Markdown）',
    word_count INT DEFAULT 0 COMMENT '方案字数统计',
    generation_status VARCHAR(20) COMMENT '生成状态：generating/complete/failed',
    
    -- 前端状态恢复字段
    current_step VARCHAR(50) DEFAULT 'device' COMMENT '当前步骤：device/environment/project/indicator/method/planner_thinking/planner_review/generating/reviewing/complete',
    messages LONGTEXT COMMENT '对话历史（JSON格式，包含所有消息）',
    planner_prompts JSON COMMENT '大纲提示词列表',
    selected_equipment_keys JSON COMMENT '选择的设备ID列表（用于恢复设备树选择）',
    page_type VARCHAR(20) DEFAULT 'jyllm' COMMENT '页面类型：jygz（基于规则）/jyllm（基于LLM）',
    overall_score INT NULL COMMENT '用户整体评分',
    overall_comment TEXT NULL COMMENT '用户整体评价',
    
    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    completed_at TIMESTAMP NULL COMMENT '完成时间',
    
    -- 索引
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
    
    -- 交互信息
    agent_type VARCHAR(20) NOT NULL COMMENT '智能体类型：planner/writer/reviewer/librarian',
    interaction_type VARCHAR(20) NOT NULL COMMENT '交互类型：input/output/thinking/tool_call',
    
    -- 章节信息（writer和reviewer用）
    chapter_index INT COMMENT '章节索引',
    chapter_title VARCHAR(200) COMMENT '章节标题',
    
    -- 输入输出内容
    input_content LONGTEXT COMMENT '输入提示词/上下文',
    output_content LONGTEXT COMMENT '输出内容',
    thinking_content LONGTEXT COMMENT '思考过程',
    
    -- 工具调用记录
    tool_name VARCHAR(50) COMMENT '调用的工具名称',
    tool_params JSON COMMENT '工具参数',
    tool_result TEXT COMMENT '工具返回结果',
    
    -- 评分和评审信息
    score INT COMMENT '评分（0-100）',
    is_pass BOOLEAN COMMENT '是否通过评审',
    review_comments TEXT COMMENT '评审意见',
    
    -- 性能指标
    tokens_input INT COMMENT '输入token数',
    tokens_output INT COMMENT '输出token数',
    duration_ms INT COMMENT '执行耗时（毫秒）',
    
    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    
    -- 索引
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
    
    -- 章节信息
    chapter_index INT NOT NULL COMMENT '章节索引（从1开始）',
    chapter_title VARCHAR(200) NOT NULL COMMENT '章节标题',
    
    -- 提示词
    writer_prompt LONGTEXT COMMENT '写作提示词',
    reviewer_prompt LONGTEXT COMMENT '评审提示词',
    
    -- 生成内容
    content LONGTEXT COMMENT '章节内容（Markdown）',
    thinking TEXT COMMENT '写作思考过程',
    keywords VARCHAR(500) COMMENT '检索关键词',
    search_context TEXT COMMENT '检索上下文',
    
    -- 评审结果
    auto_score INT COMMENT '自动评审分数',
    user_score INT COMMENT '用户评分',
    user_comment TEXT COMMENT '用户评论',
    review_status VARCHAR(20) COMMENT '评审状态：pending/completed/rewriting',
    
    -- 重写记录
    rewrite_count INT DEFAULT 0 COMMENT '重写次数',
    
    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    
    -- 索引
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
    
    -- 操作信息
    action_type VARCHAR(50) NOT NULL COMMENT '操作类型：select_device/select_env/select_project/select_indicator/select_method/confirm_prompts/start_generation/submit_feedback/rewrite/save/export',
    action_data JSON COMMENT '操作数据（存储选择的具体内容）',
    
    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    
    -- 索引
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


-- 插入测试数据
INSERT INTO jyllm_scheme_history (
    task_id, user_id, scheme_name, scheme_description,
    selected_equipment, selected_environments, selected_projects,
    selected_indicators, selected_methods, generated_content,
    word_count, generation_status
) VALUES (
    'jyllm_1234567890',
    'user_001',
    '无人机环境试验方案',
    '针对无人机A型在复杂电磁环境下的试验方案',
    '[{"id": "drone-001", "name": "无人机A型", "type": "drone"}]',
    '[{"value": "high_temperature", "label": "复杂电磁环境"}]',
    '[{"id": "d-h-1", "name": "高温启动测试"}]',
    '[{"id": "d-1", "name": "飞行性能指标"}]',
    '[{"id": "method-d-1", "name": "飞行性能测试法"}]',
    '# 无人机A型环境试验方案\n\n## 1. 范围\n本方案规定了...',
    3500,
    'complete'
);

-- 测试查询
SELECT * FROM jyllm_scheme_history WHERE user_id = 'user_001' ORDER BY created_at DESC LIMIT 10;
SELECT * FROM jyllm_scheme_overview WHERE user_id = 'user_001';

-- 添加索引优化查询性能
CREATE INDEX idx_jyllm_scheme_history_user_status ON jyllm_scheme_history(user_id, generation_status);
CREATE INDEX idx_jyllm_agent_interactions_task_agent ON jyllm_agent_interactions(task_id, agent_type);
CREATE INDEX idx_jyllm_chapters_task_status ON jyllm_chapters(task_id, review_status);

-- 添加数据清理存储过程
DELIMITER //
CREATE PROCEDURE cleanup_old_history(IN days_to_keep INT)
BEGIN
  DELETE FROM jyllm_user_actions 
  WHERE created_at < DATE_SUB(NOW(), INTERVAL days_to_keep DAY);
  
  DELETE ai FROM jyllm_agent_interactions ai
  LEFT JOIN jyllm_scheme_history sh ON ai.task_id = sh.task_id
  WHERE sh.task_id IS NULL;
END //
DELIMITER ;
