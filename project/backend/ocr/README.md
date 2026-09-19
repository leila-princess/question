# backend/ocr — 独立 Paddle 子进程 OCR

主进程通过 `ocr_subprocess.py` 调用独立 Python 环境（默认 conda 名 `paddle_env`）执行 `ocr_worker.py`，避免与主环境 PyTorch / NumPy 等冲突。

## 设备选择（默认 CPU）

| 优先级（高 → 低） | 说明 |
|-------------------|------|
| 代码里传入的 `use_gpu=True/False` | 显式指定时覆盖下面所有 |
| 环境变量 `OCR_USE_GPU` | `1` / `true` / `yes` 为 GPU，`0` / `false` / `no` 为 CPU；未设置则看配置文件 |
| `ocr_config.json` 中 `use_gpu` | 仓库默认 `false`；在 GPU 机器上改为 `true` 并装好 GPU 版 Paddle |

可选：`ocr_config.json` 里设置 `"cuda_visible_devices": "0"`，子进程会设置 `CUDA_VISIBLE_DEVICES`（多卡时指定物理卡）。也可在系统/服务环境变量里全局设置 `CUDA_VISIBLE_DEVICES`。

## 换电脑 / 不同 GPU

- Paddle **GPU 包与机器上的 CUDA 大版本绑定**（例如 `post117` 对应 CUDA 11.7）。换机器后请在该机安装 **与显卡驱动匹配的 CUDA 对应的 wheel**，不要假定 `post117` 在所有机器可用。
- 流程建议：在新机安装 NVIDIA 驱动 → 按 [飞桨安装页](https://www.paddlepaddle.org.cn/install/quick) 选择 OS、CUDA 版本，得到正确的 `pip install paddlepaddle-gpu==...` 命令 → 再 `pip install paddleocr==2.7.3` 等（与 `requirements-paddle-env-*.txt` 一致）。
- `paddleocr` 模型缓存在用户目录 `.paddleocr`，可复制到新机同路径以减少下载。

## 安装示例（conda 环境名 paddle_env）

**CPU（默认）**

```bash
conda activate paddle_env
pip install -r backend/ocr/requirements-paddle-env-cpu.txt -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
```

**GPU（CUDA 11.7 + 官方 cu117 源，与你提供的命令一致）**

```bash
conda activate paddle_env
pip install -r backend/ocr/requirements-paddle-env-gpu-cu117.txt
pip install paddlepaddle-gpu==2.6.2.post117 -i https://www.paddlepaddle.org.cn/packages/stable/cu117/
```

然后编辑 `backend/ocr/ocr_config.json`：

```json
{
  "use_gpu": true,
  "cuda_visible_devices": "0"
}
```

## 解释器路径

- Windows：`CONDA_BASE`（默认 `C:\Users\hyx\anaconda3`）+ `envs\paddle_env\python.exe`
- Linux：`/opt/conda/envs/paddle_env/bin/python`（可用环境变量 `CONDA_BASE`、`PADDLE_ENV_NAME` 覆盖）

## 与 PaddleOCR 版本

当前子进程内推荐 **paddleocr==2.7.3**，与 **paddlepaddle / paddlepaddle-gpu 2.6.2** 配套使用；升级大版本前请在样例 PDF 上回归测试。
