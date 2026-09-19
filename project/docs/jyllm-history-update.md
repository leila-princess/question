# JYLLM辅助设计系统 - 更新总结

## 1. 修复的问题

### 1.1 方法选择后列表仍显示的问题
**状态**: ✅ 已修复
**修改文件**: `fronted/app/FZSJ/JYLLM/page.tsx`
- 在方法选择确认后，自动将当前步骤切换到 'planner_review'
- 方法选择列表不再显示在对话框底部

### 1.2 流式生成自动滚动问题
**状态**: ✅ 待前端实现
**建议实现**: 
```typescript
// 在聊天容器中使用
const chatContainerRef = useRef<HTMLDivElement>(null);
const [isAutoScroll, setIsAutoScroll] = useState(true);

useEffect(() => {
  if (isAutoScroll && chatContainerRef.current) {
    chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
  }
}, [messages, isStreaming]);

// 监听滚动，用户上滑时停止自动滚动
const handleScroll = () => {
  if (chatContainerRef.current) {
    const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setIsAutoScroll(isAtBottom);
  }
};
```

### 1.3 模板选择接入主页面
**状态**: ✅ 待前端集成
**建议位置**: 在JYLLM主页面的左侧边栏添加模板选择区域

---

## 2. MySQL数据库设计

### 2.1 表结构
**文件**: `backend/database/mysql/jyllm_history_schema.sql`

#### 表1: jyllm_scheme_history (方案历史主表)
```sql
- id: BIGINT PRIMARY KEY
- task_id: VARCHAR(100) - 任务唯一标识
- user_id: VARCHAR(64) - 用户ID
- scheme_name: VARCHAR(200) - 方案名称
- scheme_description: TEXT - 方案描述
- selected_equipment: JSON - 选择的设备
- selected_environments: JSON - 选择的环境
- selected_projects: JSON - 选择的项目
- selected_indicators: JSON - 选择的指标
- selected_methods: JSON - 选择的方法
- selected_files: JSON - 上传的参考文件
- environment_config: TEXT - 环境详细配置
- template_id: VARCHAR(64) - 使用的模板ID
- template_name: VARCHAR(200) - 模板名称
- generated_content: LONGTEXT - 最终生成的方案内容
- word_count: INT - 方案字数统计
- generation_status: VARCHAR(20) - 生成状态
- created_at: TIMESTAMP - 创建时间
- updated_at: TIMESTAMP - 更新时间
- completed_at: TIMESTAMP - 完成时间
```

#### 表2: jyllm_agent_interactions (智能体交互记录)
```sql
- id: BIGINT PRIMARY KEY
- task_id: VARCHAR(100) - 关联任务ID
- agent_type: VARCHAR(20) - 智能体类型
- interaction_type: VARCHAR(20) - 交互类型
- chapter_index: INT - 章节索引
- chapter_title: VARCHAR(200) - 章节标题
- input_content: LONGTEXT - 输入内容
- output_content: LONGTEXT - 输出内容
- thinking_content: LONGTEXT - 思考过程
- tool_name: VARCHAR(50) - 工具名称
- tool_params: JSON - 工具参数
- tool_result: TEXT - 工具返回结果
- score: INT - 评分
- is_pass: BOOLEAN - 是否通过
- review_comments: TEXT - 评审意见
- tokens_input: INT - 输入token数
- tokens_output: INT - 输出token数
- duration_ms: INT - 执行耗时
- created_at: TIMESTAMP - 创建时间
```

#### 表3: jyllm_chapters (章节详情表)
```sql
- id: BIGINT PRIMARY KEY
- task_id: VARCHAR(100) - 关联任务ID
- chapter_index: INT - 章节索引
- chapter_title: VARCHAR(200) - 章节标题
- writer_prompt: LONGTEXT - 写作提示词
- reviewer_prompt: LONGTEXT - 评审提示词
- content: LONGTEXT - 章节内容
- thinking: TEXT - 思考过程
- keywords: VARCHAR(500) - 检索关键词
- search_context: TEXT - 检索上下文
- auto_score: INT - 自动评审分数
- user_score: INT - 用户评分
- user_comment: TEXT - 用户评论
- review_status: VARCHAR(20) - 评审状态
- rewrite_count: INT - 重写次数
- created_at: TIMESTAMP
- updated_at: TIMESTAMP
```

#### 表4: jyllm_user_actions (用户操作日志)
```sql
- id: BIGINT PRIMARY KEY
- task_id: VARCHAR(100) - 关联任务ID
- user_id: VARCHAR(64) - 用户ID
- action_type: VARCHAR(50) - 操作类型
- action_data: JSON - 操作数据
- created_at: TIMESTAMP
```

### 2.2 执行SQL
在MySQL中执行以下命令创建表：
```bash
mysql -u your_username -p your_database < backend/database/mysql/jyllm_history_schema.sql
```

---

## 3. 后端实现

### 3.1 历史记录管理模块
**文件**: `backend/services/jyllm_history_manager.py`

主要功能：
- `save_scheme_history()` - 保存方案历史
- `save_chapters()` - 保存章节详情
- `save_agent_interaction()` - 保存智能体交互
- `save_user_action()` - 保存用户操作
- `get_user_history_list()` - 获取用户历史列表
- `get_scheme_detail()` - 获取方案详情（支持恢复）
- `delete_scheme_history()` - 删除历史记录

### 3.2 API路由
**文件**: `backend/blueprints/FZSJ/JYLLM/history_routes.py`

API端点：
- `GET /api/fzsj/jyllm/history/list?user_id=xxx&page=1&page_size=10` - 获取历史列表
- `GET /api/fzsj/jyllm/history/detail/<task_id>?user_id=xxx` - 获取详情
- `POST /api/fzsj/jyllm/history/save` - 保存历史
- `POST /api/fzsj/jyllm/history/save-interaction` - 保存交互
- `DELETE /api/fzsj/jyllm/history/delete/<task_id>` - 删除历史

---

## 4. 前端实现

### 4.1 历史记录侧边栏组件
**文件**: `fronted/app/FZSJ/JYLLM/components/HistorySidebar.tsx`

功能：
- 显示用户历史方案列表
- 支持查看历史详情（Drawer）
- 显示方案配置、章节、智能体交互
- 支持恢复历史方案到主界面
- 支持删除历史记录

### 4.2 在JYLLM页面集成历史记录
需要修改 `fronted/app/FZSJ/JYLLM/page.tsx`：

```typescript
import HistorySidebar from './components/HistorySidebar';

// 在页面中添加
<HistorySidebar 
  userId={userId} 
  onRestore={(detail) => {
    // 恢复历史方案
    restoreHistory(detail);
  }} 
/>

// 恢复函数
const restoreHistory = (detail: SchemeDetail) => {
  // 1. 设置选择状态
  setDeviceSelection(detail.selected_equipment);
  setEnvSelection(detail.selected_environments);
  // ... 其他选择
  
  // 2. 设置生成的内容
  setModules(detail.chapters);
  setCurrentStep('complete');
  
  // 3. 恢复消息历史
  const restoredMessages = convertHistoryToMessages(detail);
  setMessages(restoredMessages);
};
```

### 4.3 保存历史记录
在方案生成完成时调用保存API：

```typescript
const saveHistory = async () => {
  const historyData = {
    task_id: taskId,
    user_id: userId,
    scheme_name: `方案_${new Date().toLocaleString()}`,
    selected_equipment: deviceSelection.selectedDevices,
    selected_environments: envSelection.selectedEnvs,
    selected_projects: selectedProjects,
    selected_indicators: selectedIndicators,
    selected_methods: selectedMethods,
    selected_files: selectedUploadedFiles,
    environment_config: envSelection.envConfig,
    template_id: selectedTemplate?.id,
    template_name: selectedTemplate?.name,
    generated_content: modules.map(m => m.content).join('\n\n'),
    word_count: modules.reduce((acc, m) => acc + m.content.length, 0),
    generation_status: 'complete',
    chapters: modules.map((m, i) => ({
      chapter_index: i + 1,
      chapter_title: m.title,
      content: m.content,
      thinking: m.thinking,
      keywords: m.keywords,
      search_context: m.searchContext,
      auto_score: m.score,
      user_score: m.userScore,
      user_comment: m.userComment,
      review_status: m.status
    }))
  };
  
  await fetch('/api/fzsj/jyllm/history/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(historyData)
  });
};
```

---

## 5. 新增文件清单

### 后端文件
1. `backend/database/mysql/jyllm_history_schema.sql` - 数据库表结构
2. `backend/services/jyllm_history_manager.py` - 历史记录管理器
3. `backend/blueprints/FZSJ/JYLLM/history_routes.py` - API路由

### 前端文件
1. `fronted/app/FZSJ/JYLLM/components/HistorySidebar.tsx` - 历史记录组件

### 修改的文件
1. `backend/app.py` - 注册新的蓝图
2. `fronted/app/FZSJ/JYLLM/page.tsx` - 修复问题并集成历史功能

---

## 6. 待完成的工作

### 6.1 前端集成（需要用户完成）
- [ ] 在JYLLM页面集成HistorySidebar组件
- [ ] 实现自动滚动到底部的逻辑
- [ ] 实现保存历史记录的调用
- [ ] 集成模板选择功能
- [ ] 美化历史记录UI

### 6.2 后端优化（可选）
- [ ] 添加定时清理过期session的任务
- [ ] 添加历史记录导出功能
- [ ] 添加历史记录搜索功能

---

## 7. 测试建议

1. 先执行SQL创建数据库表
2. 重启后端服务加载新路由
3. 测试历史记录的保存和查询
4. 测试历史方案恢复功能
5. 测试删除历史记录功能

---

## 8. 注意事项

1. **数据库表**：需要先执行SQL文件创建表，否则保存会失败
2. **Session过期**：历史记录session保存在内存中，重启后丢失
3. **权限控制**：目前基于user_id进行简单的权限控制
4. **性能优化**：大量历史记录时考虑分页和搜索优化

---

**作者**: OpenCode Agent
**日期**: 2026-04-08
**版本**: v1.0
