CREATE TABLE IF NOT EXISTS `createfa_history` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `task_id` VARCHAR(100) NOT NULL COMMENT '任务ID',
  `agent_name` VARCHAR(50) NOT NULL COMMENT '智能体名称 (planner, writer, reviewer)',
  `role` VARCHAR(20) NOT NULL COMMENT '角色 (user, assistant)',
  `content` LONGTEXT COMMENT '内容',
  `metadata` JSON COMMENT '附加信息 (如章节索引、评分等)',
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  INDEX `idx_task_id` (`task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='辅助设计智能问答历史记录表';
