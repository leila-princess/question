# 文件管理模块 — 前端说明 (WJGL Frontend)

## 技术栈

| 包名 | 版本 | 用途 |
|------|------|------|
| React | 18+ | UI 框架 |
| Next.js | 15.4.1 | 应用框架 |
| Ant Design | ^6.3.1 | UI 组件库 |
| dayjs | 1.x | 日期处理 |
| @ant-design/icons | 5.x | 图标库 |
| TypeScript | ^5.0 | 类型系统 |

## 页面架构

```
WJGLPage (page.tsx)
├── Tabs — 8 个文件分类标签页
├── 左侧分类树 (仅法规标准显示)
├── 右侧文件列表 (Table)
│   ├── 筛选栏：文件名关键词、上传日期起止、上传按钮
│   └── 数据列：文件名 | 标准号(法规标准) | 摘要 | 上传用户 | 上传时间 | 操作
├── 上传弹窗 (Modal)
│   ├── Upload.Dragger — 拖拽上传
│   ├── 元数据编辑表格：文件名、标准号/名称/ICS/CCS(法规标准)、关键词、摘要、关联、向量库同步
│   └── 自动解析按钮 — 调用 LLM 提取元数据
├── 关联配置弹窗 (Modal)
│   └── 动态表格：节点类型选择 → 节点多选
└── 编辑弹窗 (Modal)
    ├── 所有类型：文件名、摘要
    └── 法规标准额外：标准号、文档ID(只读)、CCS
```

## 状态管理

页面使用 React `useState` 管理以下状态：

| 状态 | 类型 | 用途 |
|------|------|------|
| `activeTab` | string | 当前选中的分类标签 |
| `files` | MinIOFile[] | 文件列表数据 |
| `treeData` | DataNode[] | 左侧分类树数据 |
| `filters` | {keyword, dateFrom, dateTo} | 筛选条件 |
| `uploadFileList` | UploadFileEntry[] | 上传队列 |
| `editingFile` | MinIOFile \| null | 正在编辑的文件 |

## 核心流程

### 文件上传流程
1. 拖拽/选择文件 → `handleFileSelect` 创建 UploadFileEntry
2. 点击"自动解析" → `handleAutoParse` 调用 `/api/wjgl/auto-parse`
3. LLM 返回元数据（摘要、关键词、ICS、CCS、标准号/名称）→ 填充编辑表格
4. 用户可手动修改元数据
5. 配置关联节点（可选）→ 打开关联弹窗，选择 Neo4j 节点类型和具体节点
6. 点击"确认上传" → `handleUpload` 调用 `/api/wjgl/upload`
7. 后端返回成功 → 刷新列表和分类树

### 文件列表流程
1. 切换标签页 → `fetchFiles` 调用 `/api/wjgl/list?category=xxx`
2. 筛选条件变化 → 重新请求（关键词、日期范围）
3. 日期展示：后端返回 ISO 格式 → 前端 `dayjs().format('YYYY-MM-DD')`

### 编辑流程
1. 点击编辑 → `handleEdit` 填入表单
2. 修改后保存 → `handleEditSave` 调用 `/api/wjgl/update`
3. 成功 → 刷新列表

### 删除流程
1. 点击删除 → Popconfirm 确认
2. `handleDelete` 调用 `/api/wjgl/delete`
3. 成功 → 刷新列表和分类树

## 分类标签

| 前端 key | 显示名称 | 后端 category | 特殊字段 |
|----------|----------|---------------|----------|
| standard | 法规标准 | 法规标准 | 标准号、ICS、CCS、关联节点 |
| normative | 规范性文件 | 规范性文件 | — |
| guidance | 指导性文件 | 指导性文件 | — |
| scheme | 试验方案 | 试验方案 | — |
| outline | 试验大纲 | 试验大纲 | — |
| report | 试验报告 | 试验报告 | — |
| assessment | 专项评估报告 | 专项评估报告 | — |
| other | 其他 | 其他 | — |

## API 调用

| 端点 | 方法 | 参数 | 用途 |
|------|------|------|------|
| `/api/wjgl/list` | GET | category, keyword, upload_time_from, upload_time_to | 获取文件列表 |
| `/api/wjgl/upload` | POST | FormData (files + metadata) | 上传文件 |
| `/api/wjgl/auto-parse` | POST | file | LLM 自动解析元数据 |
| `/api/wjgl/delete` | POST | {object_name, doc_id} | 删除文件 |
| `/api/wjgl/update` | POST | {object_name, doc_id, updates} | 更新元数据 |
| `/api/wjgl/ccs-tree` | GET | category | 获取 CCS 分类树 |
| `/api/wjgl/label-tree` | GET | category | 获取标签分类树 |
| `/api/wjgl/neo4j-nodes` | GET | node_type | 获取 Neo4j 节点列表 |
