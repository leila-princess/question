-- 更新 jyllm_scheme_history 表，添加前端状态恢复字段

ALTER TABLE jyllm_scheme_history 
ADD COLUMN current_step VARCHAR(50) DEFAULT 'device' COMMENT '当前步骤：device/environment/project/indicator/method/planner_thinking/planner_review/generating/reviewing/complete',
ADD COLUMN messages LONGTEXT COMMENT '对话历史（JSON格式，包含所有消息）',
ADD COLUMN planner_prompts JSON COMMENT '大纲提示词列表',
ADD COLUMN selected_equipment_keys JSON COMMENT '选择的设备ID列表（用于恢复设备树选择）',
ADD COLUMN page_type VARCHAR(20) DEFAULT 'jyllm' COMMENT '页面类型：jygz（基于规则）/jyllm（基于LLM）';

-- 用户整体评价字段
ALTER TABLE jyllm_scheme_history
ADD COLUMN overall_score INT NULL COMMENT '用户整体评分',
ADD COLUMN overall_comment TEXT NULL COMMENT '用户整体评价';
