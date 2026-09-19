#!/usr/bin/env bash
# 修复 WSL agent 环境中 mineru-api 启动失败：
#   AttributeError: _ARRAY_API not found  (numpy 2.x + onnxruntime 不兼容)
#   import cv2 失败                         (numpy/opencv ABI 不匹配)
#
# 用法（WSL 内）:
#   bash /mnt/f/antd-demo1/scripts/mineru/wsl/fix_mineru_numpy_stack.sh
#
# 离线: 默认使用独立小包目录（不必复制整个 pip_wheels_linux）
#   bash scripts/mineru/wsl/fix_mineru_numpy_stack.sh
# 或指定:
#   WHEELS_FIX_DIR=/mnt/f/antd-demo1/0616/packages/pip_wheels_mineru_fix_cp310 \
#   bash scripts/mineru/wsl/fix_mineru_numpy_stack.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/wsl_wheel_utils.sh"

CONDA_ENV="${CONDA_ENV:-agent}"
WHEELS_FIX_DIR="${WHEELS_FIX_DIR:-$PROJECT_ROOT/0616/packages/pip_wheels_mineru_fix_cp310}"
WHEELS_DIR="${WHEELS_DIR:-}"
OFFLINE="${OFFLINE:-}"

if [[ -d "$WHEELS_FIX_DIR" ]] || [[ -n "$WHEELS_DIR" && -d "$WHEELS_DIR" ]]; then
  OFFLINE=1
fi

source_conda() {
  if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
    return 0
  fi
  if [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
    return 0
  fi
  return 1
}

if ! source_conda; then
  echo "ERROR: 未找到 conda，请先安装 Miniconda"
  exit 1
fi
conda activate "$CONDA_ENV"

echo "========================================"
echo " Fix MinerU numpy/onnx/opencv stack"
echo " Env: $CONDA_ENV  Python: $(python --version 2>&1)"
echo " Offline: ${OFFLINE:-0}  FIX: ${WHEELS_FIX_DIR:-none}  EXTRA: ${WHEELS_DIR:-none}"
echo "========================================"

export WHEELS_FIX_DIR
fix_numpy_onnx_opencv_stack "${WHEELS_DIR:-}" "${OFFLINE:-0}"

echo ""
echo "==> verify imports"
python - <<'PY'
import sys
import numpy as np
print("numpy", np.__version__)
import onnxruntime as ort
print("onnxruntime", ort.__version__)
import cv2
print("opencv", cv2.__version__)
from magika import Magika
print("magika OK")
from pptx import Presentation
print("pptx OK")
import mineru
print("mineru OK")
print("ALL_OK")
PY

echo ""
echo "==> mineru-api"
mineru-api --help | head -5 || true
echo ""
echo "Done. 重启: .\\scripts\\mineru\\start_mineru_wsl.ps1 -Foreground"
