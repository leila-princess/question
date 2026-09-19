# MinerU 部署指南 — RTX 5090 / 16GB 显存

面向新机器（**无网、独立整机**）：**质量优先 + 常驻 API 加速**。

## 0. 离线部署清单（U 盘拷贝）

5090 新机无法联网时，在有网机器上准备后整体拷贝：

| 内容 | 路径 / 说明 |
|------|-------------|
| Conda 安装包 | Miniconda/Anaconda Windows 安装程序 |
| Python wheels | `pip download "mineru[all]>=3.3.1" torch` 到 `offline_wheels/` |
| 项目代码 | `backend/`、`scripts/mineru/`、`config/`、`docs/` |
| 模型目录 | `models/mineru/pipeline/`、`models/mineru/vlm/` |
| 配置 | `mineru.json`（`models-dir` 指向本机路径） |

5090 上安装步骤：

```powershell
# 1. 安装 conda + 创建环境
conda create -n agent python=3.11 -y
conda activate agent

# 2. 离线安装（将 U 盘 offline_wheels 拷到本机）
pip install --no-index --find-links D:\offline_wheels "mineru[all]>=3.3.1"

# 3. 校验版本（必须 ≥ 3.3.1 才有 effort 加速）
mineru --version
mineru --help | findstr effort

# 4. 应用 5090 profile 并启动
cd F:\antd-demo1
.\scripts\mineru\apply_mineru_profile.ps1 -Profile rtx5090
.\scripts\mineru\start_mineru_rtx5090.ps1
```

联网机器可一键升级：[`scripts/mineru/upgrade_mineru.ps1`](../scripts/mineru/upgrade_mineru.ps1)

## 1. 硬件与驱动

| 项目 | 建议 |
|------|------|
| GPU | NVIDIA RTX 5090（或同档 16GB+ 显存） |
| 驱动 | 最新 Game Ready / Studio，支持 CUDA 12.x |
| 内存 | 建议 32GB+ 系统内存（MinerU 首次加载模型占用较大） |
| 磁盘 | 模型约 4–8GB，预留 `models/mineru/` |

```powershell
nvidia-smi
```

## 2. Conda 环境与 MinerU 安装

```powershell
conda create -n agent python=3.11 -y
conda activate agent
pip install --upgrade pip uv
uv pip install -U "mineru[all]" "torch" --index-url https://download.pytorch.org/whl/cu124
```

下载模型（联网一次）：

```powershell
cd F:\antd-demo1
.\EFHR\scripts\download_mineru_models.ps1
copy mineru.json.example mineru.json
# 编辑 mineru.json 中 models-dir 路径
```

## 3. 推荐 `.env`（或 `apply_mineru_profile.ps1 -Profile rtx5090`）

```powershell
.\scripts\mineru\apply_mineru_profile.ps1 -Profile rtx5090
```

等价配置见 [`config/mineru/rtx5090.env`](../config/mineru/rtx5090.env)：

```ini
# PDF 解析
WJGL_PDF_PARSE=mineru
FLGL_PDF_BACKEND=mineru
WJGL_UPLOAD_PREVIEW=mineru
WJGL_LOCAL_PARSE_ROOT=data/wjgl_documents

# MinerU 常驻 API（必须，否则每次冷启动 1–5 分钟）
MINERU_API_URL=http://127.0.0.1:8001
MINERU_API_PORT=8001
MINERU_ENV=agent
CONDA_BASE=C:\Users\hyx\anaconda3

# 质量：GPU 混合引擎 + effort medium（3.3+）
MINERU_BACKEND=hybrid-auto-engine
MINERU_EFFORT=medium
MINERU_LANG=ch
MINERU_MODEL_SOURCE=local
MINERU_TOOLS_CONFIG_JSON=F:\antd-demo1\mineru.json

# 速度：电子版国标自动 txt；扫描件 auto/ocr
MINERU_METHOD=auto
MINERU_FORMULA=true
MINERU_TABLE=true
MINERU_IMAGE_ANALYSIS=false
MINERU_TIMEOUT=3600
MINERU_PARSE_SERIAL=0
MINERU_API_MAX_CONCURRENT_REQUESTS=2
TORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

### 显存不足时

若 `hybrid-auto-engine` OOM，改回：

```ini
MINERU_BACKEND=pipeline
```

仍保留 `MINERU_API_URL`，第二份 PDF 起仍比冷启动 CLI 快很多。

## 4. 一键启动（含 MinerU）

`start-all.ps1` 中设 `$MineruProfile = "rtx5090"` 后：

```powershell
.\start-all.ps1
```

或单独启动 MinerU（含 VLM preload，首次约 1–3 分钟）：

```powershell
.\scripts\mineru\start_mineru_rtx5090.ps1
```

启动后确认：

```powershell
curl http://127.0.0.1:8001/health
```

## 5. 质量 vs 速度对照

| 配置 | 质量 | 速度（第 2 份 PDF 起） | 显存 |
|------|------|------------------------|------|
| `pipeline` + API | 良好 | 30s–2min | 较低 |
| `hybrid-auto-engine` + API + effort=medium | **最佳平衡** | 20s–1min | ~8–14GB |
| `hybrid-auto-engine` + effort=high | 最高精度（含图表） | 1–3min | ~10–16GB |
| 无 API（CLI 冷启动） | 同左 | 5–20min/次 | 波动大 |

## 6. 前端 HTML 预览

MinerU 输出 `document.html` 后，文件预览与方法提取校对页默认 **优先展示 HTML**（表格/公式结构完整），不再用 Markdown 二次渲染覆盖。

## 7. 故障排查

| 现象 | 处理 |
|------|------|
| CUDA OOM | `MINERU_BACKEND=pipeline`；或减小 `MINERU_API_MAX_CONCURRENT_REQUESTS` |
| 无 effort 参数 | `mineru --version` 需 ≥ 3.3.1，运行 `upgrade_mineru.ps1` |
| 首次启动很慢 | 正常，`--enable-vlm-preload` 预加载 VLM，等待 health 就绪 |
| API 连不上 | 确认 `start_mineru_rtx5090.ps1` 窗口未关；端口与 `.env` 一致 |
| 预览仍是纯文本 | 设 `WJGL_UPLOAD_PREVIEW=mineru` 并重启 Flask |
| 章节切片 MinIO 失败 | 已修复中文 metadata URL 编码，重启后端 |
