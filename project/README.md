This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## 需求文档

### 开发语言列表
| 开发语言 | 描述 | 备注（版本要求） |
|---------|------|-----------------|
| Python | 后端开发语言，用于Flask应用、数据处理、知识图谱构建等 | 3.9.13 - 3.10+ |
| TypeScript | 前端开发语言，用于Next.js应用 | ^5.0 |
| JavaScript | 前端脚本语言 | - |
| HTML/CSS | 前端样式语言 | - |

### 依赖软件表格
| 依赖软件 | 描述 | 备注 |
|---------|------|------|
| Node.js | JavaScript运行时环境 | v22.17.0 |
| Neo4j | 图数据库，用于知识图谱存储 | - |
| Flask | Python Web框架 | - |
| Next.js | React框架 | 15.4.1 |
| Ant Design | UI组件库 | ^6.3.1 |

### 软硬件环境部署应用的适应性需求要求表
| 序号 | 适应性需求名称/标识 | 具体要求 | 优先级 | 备注 |
|------|-------------------|----------|--------|------|
| 1 | 操作系统适应性 | 支持Windows/Linux/MacOS | 高 | 开发环境为Windows，部署需跨平台 |
| 2 | 浏览器兼容性 | 支持主流浏览器（Chrome, Firefox, Safari, Edge） | 高 | 前端基于Next.js构建 |
| 3 | 内存要求 | 至少4GB内存 | 中 | 数据库和应用同时运行 |
| 4 | 存储空间 | 至少10GB可用空间 | 中 | 用于存储文档和知识图谱数据 |
| 5 | 网络要求 | 支持HTTP/HTTPS协议 | 高 | 前后端分离架构 |

### 软件需求表
| 序号 | 软件项名称 | 软件版本 | 备注 |
|------|-----------|----------|------|
| 1 | Python | 3.9.13+ | 后端运行环境 |
| 2 | Node.js | v22.17.0 | 前端运行环境 |
| 3 | Neo4j | - | 图数据库 |
| 4 | Flask | - | Python Web框架 |
| 5 | Next.js | 15.4.1 | React框架 |
| 6 | Ant Design | ^6.3.1 | UI组件库 |

## 第一次下载安装部署
### 前端

安装node（v22.17.0）
在前端文件夹内/fronted，下载所有的依赖包

npm install
fronted\node_modules

运行前端（在前端文件夹内）
npm run dev

(构建+应用)
npm run build
npm run start

数组的操作/对象、函数：

### 后端
下载所有的依赖包
这里面直接就先运行，缺什么下载什么
先python app.py(我的python环境是3.9.13，但是用3.10以上的好像也行，你先用你的试试)
缺什么包就Pip install xxx
大概就几个，挺快的
最后包都安装完就python app.py'

### 数据库
我的IP：192.168.15.123
neo4j：启动：在bin目录下：neo4j.bat console
数据库地址：http://192.168.15.123:7474

MinIO:启动：.\minio.exe server D:\MinIO\data --console-address ":9001"

Chroma启动：chroma run --path ./chroma_data --port 8000


## git
git add .
git commit -m "xxx"
git pull origin main
git push origin main


## 进入虚拟环境
conda activate agent


## 模型文件
多语言语义搜索：paraphrase-multilingual-MiniLM-L12-v2
C:\Users\hyx\.cache\huggingface\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2

## 安装包：
pip install paddlepaddle==2.6.2 paddleocr==2.7.3

