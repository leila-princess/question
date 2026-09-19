# MinerU 部署指南 — 本机开发（CPU / 弱 GPU）

面向日常开发机：**交互快 + 显存友好 + 解析稳定**。

## 1. 前置条件

| 项目 | 建议 |
|------|------|
| GPU | 可选；无独显或显存 < 8GB 时用 `pipeline` |
| Conda | `agent` 环境，已安装 `mineru[all]` |
| 模型 | 本地 `models/mineru/` + `mineru.json` |

升级至 MinerU 3.3+（可选，本机 pipeline 不强制）：

```powershell
.\scripts\mineru\upgrade_mineru.ps1
```

## 2. 一键应用本机 profile

```powershell
cd F:\antd-demo1
.\scripts\mineru\apply_mineru_profile.ps1 -Profile local
```

等价于写入 `.env` 的核心项：

| 变量 | 值 | 说明 |
|------|-----|------|
| `MINERU_BACKEND` | `pipeline` | CPU 友好，显存占用低 |
| `WJGL_UPLOAD_PREVIEW` | `fitz` | 上传后 1–3 秒可见草稿 |
| `MINERU_METHOD` | `txt` | 电子版国标跳过 OCR |
| `MINERU_PARSE_SERIAL` | `1` | Flask 侧串行，避免 CPU 过载 |
| `MINERU_API_URL` | `http://127.0.0.1:8001` | 必须配合常驻 API |

## 3. 启动 MinerU API

```powershell
.\scripts\mineru\start_mineru_local.ps1
```

保持窗口常开。另开终端启动 Flask：

```powershell
cd backend
conda activate agent
python app.py
```

或使用 `start-all.ps1`（默认 `$MineruProfile = "local"`）。

## 4. 验证速度

```powershell
python EFHR/scripts/test_mineru_speed.py "path\to\GBT+38924.6-2020.pdf" --twice
```

| 阶段 | 预期 |
|------|------|
| fitz 上传预览 | 1–3 秒 |
| 首次 MinerU（模型加载） | 2–5 分钟 |
| 第 2 份 PDF 起 | 30 秒–2 分钟 |

## 5. 质量 vs 速度

| 场景 | 推荐 |
|------|------|
| 日常国标方法提取 | `pipeline` + `txt` + fitz 预览 |
| 表格较多、本机有 8GB+ 显存 | 可试 `MINERU_BACKEND=hybrid-auto-engine`，OOM 则改回 pipeline |
| 含复杂插图 | 建议用 5090 机 `effort=high` |

## 6. 故障排查

| 现象 | 处理 |
|------|------|
| 每次解析 5–20 分钟 | 未开 `mineru-api` 或窗口已关；确认 `MINERU_API_URL` |
| 预览是纯文本、表格差 | 等后台 MinerU 完成；或临时设 `WJGL_UPLOAD_PREVIEW=mineru` |
| API 连不上 | 端口 8001 是否被占用；重启 `start_mineru_local.ps1` |
| `detect_pdf_parse_method` 报错 | 重启 Flask，确认 `mineru_adapter.py` 已更新 |

## 7. 相关文件

- 环境模板：[`config/mineru/local.env`](../config/mineru/local.env)
- 启动脚本：[`scripts/mineru/start_mineru_local.ps1`](../scripts/mineru/start_mineru_local.ps1)
- 5090 新机：[`docs/MINERU_RTX5090_DEPLOY.md`](MINERU_RTX5090_DEPLOY.md)
