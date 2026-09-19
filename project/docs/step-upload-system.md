# 分步骤文件上传系统 - 实现文档

## 1. 系统概述

将文件上传过程分解为3个独立的API步骤，避免单个请求超时，并提供实时的进度反馈。

## 2. 系统架构

### 2.1 上传流程

```
用户选择文件 → 步骤1 API → 步骤2 API → 步骤3 API → 完成
                ↓            ↓            ↓
           文件数据库    向量数据库    图数据库
           (MinIO+     (Chroma)    (Neo4j)
            Neo4j)
```

### 2.2 3个步骤说明

#### 步骤1：保存到文件数据库
**API**: `POST /api/fzsj/wjgl/upload-step1`

功能：
- 保存文件到MinIO对象存储
- 创建Neo4j File节点（基础信息）
- 提取文档元数据（页数、图片、表格等）
- 上传图片到MinIO
- 返回文件ID和预览信息

返回数据：
```json
{
  "success": true,
  "step": 1,
  "session_id": "upload_abc123...",
  "file_id": "WJ_gb0001",
  "doc_id": "gb0001",
  "file_info": {
    "filename": "标准文件.pdf",
    "title": "试验标准",
    "pages": 25,
    "tables": 3,
    "figures": 5
  },
  "has_images": true,
  "image_preview_urls": ["minio://..."],
  "image_count": 5
}
```

#### 步骤2：保存到向量数据库
**API**: `POST /api/fzsj/wjgl/upload-step2/{session_id}`

功能：
- 将文档内容同步到Chroma向量数据库
- 文本分块和向量化
- 存储元数据（页数、分块ID等）

返回数据：
```json
{
  "success": true,
  "step": 2,
  "chroma_id": "gb0001",
  "chroma_chunks": 15,
  "chroma_metadata": {
    "doc_id": "gb0001",
    "chunks": 15,
    "pages": 25,
    "text_length": 12500
  }
}
```

#### 步骤3：保存到图数据库
**API**: `POST /api/fzsj/wjgl/upload-step3/{session_id}`

功能：
- 更新Neo4j节点属性（添加Chroma信息）
- 创建文件与其他节点的关联
- 清理临时文件
- 返回完整节点信息

返回数据：
```json
{
  "success": true,
  "step": 3,
  "neo4j_node": {
    "id": "WJ_gb0001",
    "name": "试验标准",
    "type": "国家标准",
    "minio_id": "gb0001",
    "chroma_id": "gb0001",
    "chroma_chunks": 15,
    "has_images": true
  }
}
```

## 3. API端点列表

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/fzsj/wjgl/upload-step1` | POST | 步骤1：保存到文件数据库 |
| `/api/fzsj/wjgl/upload-step2/{session_id}` | POST | 步骤2：保存到向量数据库 |
| `/api/fzsj/wjgl/upload-step3/{session_id}` | POST | 步骤3：保存到图数据库 |
| `/api/fzsj/wjgl/upload-status/{session_id}` | GET | 查询上传状态 |
| `/api/fzsj/wjgl/upload-cancel/{session_id}` | POST | 取消上传 |

## 4. 前端组件

### 4.1 StepUploadModal 组件

位置：`fronted/components/StepUploadModal.tsx`

功能：
- 显示3个步骤的进度条
- 实时展示每个步骤的详细数据
- 支持重试和取消
- 显示图片预览
- 展示Neo4j节点属性

使用示例：
```tsx
import StepUploadModal from '@/components/StepUploadModal';

const [uploadModalOpen, setUploadModalOpen] = useState(false);
const [file, setFile] = useState<File | null>(null);
const [uploadData, setUploadData] = useState({
  category: '国家标准',
  title: '',
  abstract: '',
  keywords: [],
  ics: '',
  ccs: ''
});

// 在组件中使用
<StepUploadModal
  open={uploadModalOpen}
  onClose={() => setUploadModalOpen(false)}
  file={file}
  uploadData={uploadData}
  userId="user_001"
  onSuccess={(fileInfo) => {
    message.success('上传成功！');
    console.log(fileInfo);
  }}
/>
```

### 4.2 演示页面

位置：`fronted/app/FZSJ/upload-demo/page.tsx`

访问地址：`http://localhost:3000/FZSJ/upload-demo`

## 5. 数据库变更

无需数据库迁移，新系统使用：
- 内存session存储（生产环境建议使用Redis）
- 现有的MinIO、Chroma、Neo4j连接

## 6. 优势

### 6.1 解决超时问题
- 每个步骤独立执行，避免单个请求超时
- 步骤之间可以中断和恢复
- 用户可以实时看到进展

### 6.2 更好的用户体验
- 实时显示每个数据库的保存结果
- 展示文件元数据（页数、图片等）
- 图片预览功能
- 详细的错误提示

### 6.3 容错能力
- 每个步骤可以独立重试
- session机制支持断点续传
- 取消上传时自动清理资源

## 7. 使用说明

### 7.1 后端启动
```bash
cd backend
python app.py
```

### 7.2 前端测试
1. 访问 `http://localhost:3000/FZSJ/upload-demo`
2. 选择文件并填写表单
3. 点击"开始上传"
4. 观察3个步骤的进度

### 7.3 迁移现有代码
将原有的上传调用替换为：
```typescript
// 旧代码（同步上传，可能超时）
const response = await fetch('/api/fzsj/wjgl/upload', {...});

// 新代码（分步骤上传，使用Modal）
setUploadModalOpen(true);
```

## 8. 注意事项

1. **Session过期**：session数据保存在内存中，重启后端服务后会丢失。生产环境建议使用Redis。

2. **临时文件清理**：步骤3完成后会自动清理临时文件。如果步骤3失败，需要手动清理。

3. **并发限制**：建议在前端控制同时上传的文件数量，避免后端资源耗尽。

4. **错误处理**：每个步骤都有独立的错误处理，失败后可以在Modal中点击"重试"。

## 9. 性能优化建议

1. **Redis替换内存存储**：生产环境使用Redis存储session数据
2. **异步处理**：考虑使用Celery将Chroma同步改为后台任务
3. **进度推送**：使用WebSocket推送实时进度到前端
4. **断点续传**：大文件支持分片上传和断点续传

## 10. 文件清单

### 后端文件
1. `backend/blueprints/ZSGL/WJGL/step_upload_routes.py` - 分步骤上传API
2. `backend/app.py` - 注册新蓝图

### 前端文件
1. `fronted/components/StepUploadModal.tsx` - 分步骤上传Modal组件
2. `fronted/app/FZSJ/upload-demo/page.tsx` - 演示页面

## 11. 测试建议

1. 测试小文件上传（< 1MB）
2. 测试中等文件上传（10-50MB）
3. 测试大文件上传（> 100MB）
4. 测试包含大量图片的PDF
5. 测试网络不稳定时的重试功能
6. 测试取消上传功能

---

如有问题，请检查日志输出或联系开发团队。
