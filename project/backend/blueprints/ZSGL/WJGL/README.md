# 文件管理模块 (WJGL)

## 技术栈

| 包名 | 版本 | 用途 |
|------|------|------|
| Flask | 3.x | Web 框架 |
| minio | 7.x | MinIO 对象存储客户端 |
| neo4j | 5.x | Neo4j 图数据库驱动 |
| chromadb | 0.4+ | Chroma 向量数据库客户端 |
| PyMuPDF (fitz) | 1.27+ | PDF 文本提取和页面转图片 |
| python-docx | 1.x | Word 文档解析 |
| Pillow (PIL) | 10.x | 图片处理 |
| openai | 1.x | LLM API 客户端 (DashScope qwen-plus) |
| sentence-transformers | 2.x | 本地 Embedding 模型 |
| python-dotenv | 1.x | 环境变量加载 |

### 独立 OCR 环境 (paddle_env)

| 包名 | 版本 | 用途 |
|------|------|------|
| paddlepaddle | 2.6.2 | 深度学习框架 |
| paddleocr | 2.7.3 | OCR 文字识别 |
| PyMuPDF | 1.27+ | PDF 转图片 |
| opencv-python | 4.x | 图片处理 |
| numpy | 1.x | 数值计算 |

## 文件结构

```
backend/blueprints/ZSGL/WJGL/
├── routes.py                 # 主路由：上传、列表、删除、更新、树形分类
├── step_upload_routes.py     # 分步骤上传路由（可选）
├── pdf_ocr_extractor.py      # PDF OCR 提取器（子进程委托给 paddle_env）
├── word_agent.py             # Word 文档知识图谱提取器
└── README.md                 # 本说明文档

backend/ocr/
├── __init__.py               # OCR 包入口
├── ocr_subprocess.py         # 子进程封装：主环境调用 paddle_env
└── ocr_worker.py             # OCR 工作脚本：在 paddle_env 中运行

backend/Process_data/
└── extractor.py              # 文档提取器：PyMuPDF 文本 + 子进程 OCR 图片

backend/Database/
├── minIo/main.py             # MinIO 上传、元数据管理
└── Chroma/main.py            # Chroma 向量数据库处理
```

## 核心功能

### 1. 文件上传 (POST /api/wjgl/upload)

**流程**：
```
前端上传 → MinIO 存储原始文件 → Neo4j 创建 File 节点 → 入队异步任务 → 立即返回前端
                                                                    ↓
                                                    后台线程处理：
                                                    1. DocumentExtractor.extract()
                                                       - PDF: PyMuPDF 提取文本 + 子进程 PPStructure 提取图片
                                                       - Word: python-docx 提取文本和图片
                                                    2. 图片上传到 MinIO
                                                    3. Chroma 向量入库 (如果开启)
                                                    4. 更新 Neo4j (Chroma_id, Chroma_chunks, Image_minIO_id)
                                                    5. 创建文件关联关系
```

**关键设计**：
- **异步处理**：使用 `queue.Queue` + 后台线程，上传不阻塞
- **OCR 隔离**：PaddleOCR/PPStructure 在独立 `paddle_env` 环境运行，通过 `subprocess.run()` 调用，避免依赖冲突
- **图片提取**：使用 PPStructure 版面分析识别 figure 区域，5 层过滤规则避免水印/图标：
  1. 面积 < 页面 0.1% → 过滤
  2. 靠近边缘(<5%) 且面积 < 80% → 过滤
  3. 宽高比 >5 或 <0.2 → 过滤
  4. 灰度标准差 <10 → 过滤（纯色装饰）

### 2. 自动解析 (POST /api/wjgl/auto-parse)

调用 LLM (qwen-plus) 从文档内容提取元数据：
- 摘要 (abstract)
- 关键词 (keywords)
- ICS 国际分类号
- CCS 中国分类号
- 标准号 (std_code)
- 标准名称 (std_name)

### 3. 文件列表 (GET /api/wjgl/list)

从 MinIO 读取文件元数据，支持筛选：
- `category` — 文件分类（必须）
- `keyword` — 文件名模糊匹配
- `upload_time_from` / `upload_time_to` — 日期范围 (YYYY-MM-DD)

### 4. 分类树 (GET /api/wjgl/ccs-tree, /api/wjgl/label-tree)

- `ccs-tree` — 按 CCS 分类组织（仅法规标准）
- `label-tree` — 按标签分类组织（其他类型）

### 5. 更新/删除 (POST /api/wjgl/update, /api/wjgl/delete)

- **更新**：同步更新 MinIO 元数据 + Neo4j 节点属性
- **删除**：删除 MinIO 文件 + 图片 + Chroma 向量 + Neo4j 节点

## 数据存储

### MinIO 存储结构
```
{category}/{doc_id}/origin.{ext}          # 原始文件
{category}/{doc_id}/images/{filename}     # 提取的图片
```

### MinIO 元数据 (x-amz-meta-)
| 字段 | 说明 |
|------|------|
| doc-id | 文档编号 (如 fgbz0004) |
| title | 文件名/标准名称 |
| category | 文件分类 |
| file-type | 文件扩展名 |
| upload-time | 上传时间 (ISO 8601) |
| upload-userid | 上传用户 |
| file-ics | ICS 分类号 |
| file-ccs | CCS 分类号 |
| std-code | 标准号 |
| std-name | 标准名称 |
| abstract | 摘要 |
| keywords | 关键词 |

### Neo4j File 节点属性
| 属性 | 说明 |
|------|------|
| Id | WJ_{doc_id} |
| Type | 文件分类 |
| Name | 文件名/标准名称 |
| Std_Code | 标准号 |
| ICS / CCS | 分类号 |
| Abstract | 摘要 |
| Chroma_id | Chroma 文档 ID |
| Chroma_chunks | 分块数量 |
| Image_minIO_id | 图片路径 JSON 数组 |
| Status | 启用/禁用 |
| Create_time | 创建时间 |

### 文件编号规则
| 分类 | 前缀 | 示例 |
|------|------|------|
| 法规标准 | fgbz | fgbz0001 |
| 规范性文件 | gzfwj | gzfwj0001 |
| 指导性文件 | zdzxwj | zdzxwj0001 |
| 试验方案 | syfa | syfa0001 |
| 试验大纲 | sydg | sydg0001 |
| 试验报告 | sybg | sybg0001 |
| 专项评估报告 | zxpgbg | zxpgbg0001 |
| 其他 | qt | qt0001 |

## 子进程 OCR 架构

```
主环境 (Python 3.13)                    paddle_env (Python 3.9/3.10)
┌─────────────────────┐                 ┌──────────────────────────┐
│ DocumentExtractor   │                 │ ocr_worker.py            │
│  ├─ PyMuPDF 文本    │  subprocess     │  ├─ PaddleOCR 文字识别   │
│  └─ run_extract_   ─┼────────────────┤  ├─ PPStructure 版面分析 │
│     images_only()   │  JSON 通信      │  └─ 5 层图片过滤         │
│                     │                 │                          │
│ ocr_subprocess.py   │                 │ ocr_worker.py            │
│  ├─ _run_ocr_worker │                 │  ├─ ocr_pdf              │
│  ├─ run_ocr_pdf     │                 │  ├─ extract_images_only  │
│  └─ run_ocr_ics_ccs │                 │  └─ ocr_ics_ccs          │
└─────────────────────┘                 └──────────────────────────┘
```

**子进程管理**：
- 创建：`subprocess.run()` 启动 paddle_env 的 Python 解释器
- 通信：临时 JSON 文件传递命令参数，stdout 返回 JSON 结果
- 销毁：`subprocess.run()` 同步等待完成，子进程自然退出；临时文件在 `finally` 中清理
- 超时：默认 600 秒，超时自动终止
- 路径：`C:\Users\hyx\anaconda3\envs\paddle_env\python.exe`
