# MinerU on WSL（RTX 5090 hybrid + VLM）

Windows 原生无法跑 `hybrid-auto-engine`（lmdeploy 缺 triton）。**WSL2 + Linux MinerU** 可正常使用 VLM。

架构：

```
Windows                          WSL (Ubuntu)
────────                         ────────────
Flask / Next.js  ──HTTP:8001──►  mineru-api (hybrid + VLM)
Neo4j / MinIO    （本机）          读 /mnt/f/.../models/mineru
```

Flask 只连 `MINERU_API_URL=http://127.0.0.1:8001`，WSL2 会自动把 Linux 端口转发到 Windows。

---

## 一、前置条件

| 项 | 要求 |
|----|------|
| WSL2 | `wsl --install -d Ubuntu` 或已有 Ubuntu |
| 显卡驱动 | Windows 侧安装 **支持 WSL 的 NVIDIA 驱动**（无需在 WSL 再装 Windows 驱动） |
| 验证 | `wsl nvidia-smi` 能显示 RTX 5090 |
| 磁盘 | 模型约 4.4GB（pipeline + vlm），WSL 环境约 5–8GB |
| 网络 | 在线：`setup_mineru_wsl.sh`；**离线**：见下文「离线 WSL 安装」 |

---

## 二、能否直接用 Windows 的 agent 环境？

**不能。** 原因：

| | Windows `agent` | WSL 需要 |
|--|-----------------|----------|
| 系统 | Windows `.exe` / `.pyd` | Linux `.so` |
| torch / triton / lmdeploy | Windows 版 wheel | **Linux 版 wheel** |
| 共用 | ❌ 不能跨 WSL 使用 | 必须在 WSL 内单独装 |

**但 Windows agent 仍有用**：不用 WSL 时，继续用 **Windows pipeline** 即可（你 0616 里已有 Windows wheels）：

```powershell
.\scripts\mineru\start_mineru_local.ps1
# .env: MINERU_BACKEND=pipeline
```

只有要 **hybrid + VLM** 时，才必须在 WSL 里装 **Linux 版** MinerU。

---

## 三、在线安装（WSL 能联网时）

PowerShell：

```powershell
wsl --install -d Ubuntu   # 若尚未安装
# 重启后
wsl bash /mnt/f/antd-demo1/scripts/mineru/wsl/setup_mineru_wsl.sh
```

脚本会：装 Miniconda → 创建 `agent` 环境 → `torch cu128` → `mineru[all]` + `lmdeploy` + `triton`。

**模型**仍用 Windows 盘上的目录（无需重复下载）：

- `F:\antd-demo1\models\mineru\pipeline\`
- `F:\antd-demo1\models\mineru\vlm\`

WSL 路径为 `/mnt/f/antd-demo1/models/mineru/...`

---

## 四、离线 WSL 安装（新机 WSL 不能联网）

### 步骤 1：在有网机器（本机 WSL 能上网）打包

```powershell
cd F:\antd-demo1
.\scripts\mineru\wsl\pack_wsl_offline.ps1
```

生成（拷 U 盘）：

- `0616\packages\pip_wheels_linux\`（Linux wheel，与 Windows 的 `pip_wheels` **不是同一套**）
- `0616\packages\Miniconda3-latest-Linux-x86_64.sh`

### 步骤 2：新机 WSL 离线安装

```powershell
wsl bash -lc "
  export WHEELS_DIR=/mnt/f/antd-demo1/0616/packages/pip_wheels_linux
  export MINICONDA_SH=/mnt/f/antd-demo1/0616/packages/Miniconda3-latest-Linux-x86_64.sh
  bash /mnt/f/antd-demo1/scripts/mineru/wsl/setup_mineru_wsl.sh
"
```

### 步骤 3：启动

```powershell
.\scripts\mineru\start_mineru_wsl.ps1
```

**模型**仍用 Windows 盘上已有的 `models\mineru\`（pipeline + vlm），WSL 通过 `/mnt/f/...` 读取，无需再下载。

### 备选：整包导出 WSL（两台都是 Windows）

在有网机装好 WSL 环境后：

```powershell
wsl --export Ubuntu D:\backup\ubuntu-mineru.tar
# 新机
wsl --import Ubuntu D:\WSL\Ubuntu D:\backup\ubuntu-mineru.tar
```

---

## 五、一键启动

### 方式 A：只启动 MinerU

```powershell
cd F:\antd-demo1
.\scripts\mineru\apply_mineru_profile.ps1 -Profile rtx5090-wsl
.\scripts\mineru\start_mineru_wsl.ps1
```

### 方式 B：start-all 全家桶

编辑 `start-all.ps1`：

```powershell
$MineruProfile = "wsl"
```

然后 `.\start-all.ps1`

---

## 六、.env 要点

```ini
MINERU_API_URL=http://127.0.0.1:8001
MINERU_BACKEND=hybrid-auto-engine
MINERU_RUNTIME=wsl
```

实际模型路径由 WSL 启动时生成的 `mineru.wsl.json`（Linux 路径）控制，**不要**在 WSL 里用 `C:\Users\...` 路径。

---

## 七、WSL 还可能遇到的问题

| 问题 | 处理 |
|------|------|
| `nvidia-smi` 在 WSL 失败 | 升级 Windows NVIDIA 驱动；BIOS 开 Resizable BAR；重启 |
| `conda env agent missing` | 重跑 `setup_mineru_wsl.sh` |
| 端口 8001 被占用 | 关 Windows 上的 mineru-api 窗口；或改 `MINERU_API_PORT` |
| 解析很慢 | 模型在 `/mnt/f/` 较慢，可把 `models/mineru` 拷到 WSL 家目录 `~/models/mineru` 并改 `mineru.wsl.json` |
| OOM | WSL 内存：`.wslconfig` 设 `memory=24GB`；或 `-Profile pipeline-wsl` |
| `huggingface-hub` 版本冲突 | WSL 内 `pip install "huggingface-hub>=0.34,<1.0"` |
| 5090 + lmdeploy 仍报错 | `export MINERU_LMDEPLOY_BACKEND=pytorch`（脚本默认已是 pytorch） |

---

## 八、仅 pipeline（不用 VLM）

```powershell
.\scripts\mineru\start_mineru_wsl.ps1 -Profile pipeline-wsl
```

---

## 九、与 Windows pipeline 对比

| | Windows pipeline | WSL hybrid |
|--|------------------|------------|
| 安装难度 | 低（0616 Windows wheels） | 中（需 Linux wheels 或 wsl export） |
| 公式/版面 | 良好 | 更好（VLM） |
| triton | 不需要 | Linux 有 |
| 离线新机 | ✅ 0616 `pip_wheels` | ✅ `pip_wheels_linux` + Miniconda `.sh` |

**结论**：

- **完全离线、不想折腾 WSL** → Windows `MINERU_BACKEND=pipeline` + `start_mineru_local.ps1`
- **要 hybrid/VLM 且新机 WSL 无网** → 有网机跑 `pack_wsl_offline.ps1`，拷 `pip_wheels_linux` 到新机再 `setup_mineru_wsl.sh`
