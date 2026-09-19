-- 基于规则（JYGZ）方案历史，字段对齐 jyllm_scheme_history 的核心列 + 全量 config_snapshot
CREATE TABLE IF NOT EXISTS jygz_history (
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
    selected_files JSON COMMENT '参考文件列表',
    environment_config LONGTEXT COMMENT '环境详细配置（如 env_settings_map 序列化）',
    template_id VARCHAR(64) COMMENT '使用的模板ID',
    template_name VARCHAR(200) COMMENT '模板名称',
    config_snapshot LONGTEXT COMMENT '完整前端状态 JSON：config、editing_env_settings、env_settings_data',
    generated_content LONGTEXT COMMENT '最终生成的完整方案（Markdown）',
    word_count INT DEFAULT 0 COMMENT '方案字数',
    generation_status VARCHAR(20) DEFAULT 'complete' COMMENT '状态：generating/complete/failed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    completed_at TIMESTAMP NULL,
    UNIQUE KEY uk_jygz_task (task_id),
    INDEX idx_jygz_user (user_id),
    INDEX idx_jygz_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='基于规则 JYGZ 方案历史';
