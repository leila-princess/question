# Win10 完整离线部署手册

> 更新：2026-07-07  
> 适用：全新 Win10 无网机，一键安装程序 + 环境 + Neo4j/MinIO/Redis/MySQL + 业务数据

---

## 一、开发机打包（有网）

### 前置条件

- 项目路径：`F:\antd-demo1`
- Anaconda 环境 `agent`（Python 3.10）
- Neo4j、MinIO、MySQL 已安装且数据可用
- Node.js 22+（前端 build）
- 磁盘空闲 ≥ 80GB（打包临时空间）

### 一键打包

```powershell
cd F:\antd-demo1
Set-ExecutionPolicy Bypass -Scope Process

.\scripts\deploy\pack_win10_full.ps1 `
  -ProjectRoot F:\antd-demo1 `
  -CondaBase C:\Users\hyx\anaconda3 `
  -Neo4jHome "D:\soft\Neo4j\neo4j-community-2025.06.2-windows\neo4j-community-2025.06.2" `
  -MinioExe "D:\MinIO\bin\minio.exe" `
  -MinioDataDir "D:\MinIO\data" `
  -MySqlBin "C:\Program Files\MySQL\MySQL Server 8.0\bin" `
  -MySqlPassword "your_mysql_password"
```

输出目录默认：`release\win10\SYFAXTYX_win10_YYYYMMDD\`

### 打包内容

| 目录 | 内容 |
|------|------|
| `project/` | backend + fronted + scripts + docs（**不含** DTDL/EFHR） |
| `env/` | Miniconda 安装器 + pip wheels |
| `fronted_deps/` | node_modules + `.next` 生产构建 |
| `models/` | MinerU + BGE 模型 |
| `runtime/` | Neo4j + MinIO + Redis + MySQL |
| `data/` | chroma / zvec / neo4j dump / mysql dump / minio 数据 |
| `install_and_start.ps1` | 目标机唯一入口 |

### 常用参数

| 参数 | 说明 |
|------|------|
| `-SkipBuild` | 跳过 npm build（已有 `.next` 时） |
| `-SkipWheels` | 跳过 pip download |
| `-SkipData` | 不导出数据库 |
| `-SkipRuntime` | 不收集 Neo4j/MinIO 等 |
| `-SkipMiniconda` | 不下载 Miniconda 安装器 |

### 仅导出数据库

```powershell
.\scripts\deploy\export_databases.ps1 `
  -OutputDir F:\antd-demo1\release\win10\data `
  -Neo4jHome "D:\soft\Neo4j\..." `
  -MySqlBin "C:\Program Files\MySQL\MySQL Server 8.0\bin"
```

---

## 二、拷贝到目标机

将整个 `SYFAXTYX_win10_YYYYMMDD` 文件夹复制到目标 Win10（U 盘/移动硬盘），例如：

```
D:\SYFAXTYX\
```

---

## 三、目标机安装并启动（无网）

```powershell
Set-ExecutionPolicy Bypass -Scope Process
cd D:\SYFAXTYX

.\install_and_start.ps1 -InstallRoot D:\SYFAXTYX
```

脚本自动完成：

1. 安装 Miniconda + 创建 `agent` 环境
2. 离线 pip install
3. 部署代码、前端构建、模型
4. 部署 Neo4j/MinIO/Redis/MySQL
5. 恢复数据库
6. 生成 `.env` 和 `start-all.ps1`
7. 启动全部服务并 healthcheck

### 仅启动（已安装过）

```powershell
.\install_and_start.ps1 -InstallRoot D:\SYFAXTYX -SkipInstall -StartOnly
```

或：

```powershell
D:\SYFAXTYX\project\start-all.ps1
```

---

## 四、验证

```powershell
D:\SYFAXTYX\project\scripts\deploy\healthcheck.ps1 -ProjectRoot D:\SYFAXTYX\project
```

浏览器打开：**http://127.0.0.1:3001**

| 服务 | 端口 |
|------|------|
| 前端 | 3001 |
| 后端 Flask | 5000 |
| Neo4j Bolt | 7687 |
| Neo4j 浏览器 | 7474 |
| Redis | 6379 |
| MinIO | 9000 |
| MinerU API | 8001 |

---

## 五、LLM 配置（无网必看）

无网环境无法访问 DashScope，需在安装前或安装后编辑 `project\.env`：

```ini
# 内网 vLLM 示例
OPENAI_API_KEY=EMPTY
OPENAI_BASE_URL=http://192.168.x.x:8000/v1
OPENAI_MODEL=Qwen2.5-7B-Instruct
```

若有外网 API Key，可直接填写 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。

---

## 六、故障排查

### 前端无法访问

- 确认 `fronted\.next\standalone\server.js` 存在
- 查看前端 PowerShell 窗口是否有 `npm run start` 报错
- 开发机重新打包（不要加 `-SkipBuild`）

### Neo4j 连接失败

- 等待 Neo4j 窗口显示 "Started"
- 检查 `.env` 中 `NEO4J_URI=bolt://localhost:7687`
- 若 dump 恢复失败，检查 `data\neo4j_data\` 是否存在

### MySQL 连接失败

- 确认 MySQL 窗口已启动
- 检查 `DB_HOST=localhost` `DB_PASSWORD=root`（与打包时一致）
- 手动导入：`mysql -u root -p znwd_history < data\mysql_znwd_history.sql`

### MinerU PDF 解析失败

- 确认 MinerU 窗口运行且 `curl http://127.0.0.1:8001/health` 返回 OK
- 检查 `mineru.json` 中模型路径是否指向 `{ProjectRoot}/models/mineru/`

### pip 安装失败

- 确认 `env\pip_wheels\` 非空
- 目标机 Miniconda 安装路径与 `config\install_config.json` 中 `CondaBase` 一致

---

## 七、排除说明

以下目录**不会**打入发布包：

- `DTDL/` — 实验框架
- `EFHR/` — 路由 benchmark（仅 `router_mlp.pkl` 会单独复制）
- `znwd-gitee/`、`orio-search-master/`、`moniDB/`
- 历史迁移包 `0616/` `0624/` `06242/` `0630/`

---

## 八、文件索引

| 脚本 | 用途 |
|------|------|
| `scripts/deploy/pack_win10_full.ps1` | 开发机完整打包 |
| `scripts/deploy/export_databases.ps1` | 单独导出数据库 |
| `scripts/deploy/restore_databases.ps1` | 恢复数据库 |
| `install_and_start.ps1` | 目标机安装+启动 |
| `scripts/deploy/healthcheck.ps1` | 服务健康检查 |
| `release/exclude_patterns.txt` | 打包排除规则 |
