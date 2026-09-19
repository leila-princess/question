#!/usr/bin/env bash
# Offline install wheels (deduped by package, highest version wins).
# Usage:
#   export WHEELS_DIR=/mnt/d/SVFAXTYX_0429_v1.0/0616/pip_wheels_linux
#   bash scripts/mineru/wsl/install_wheels_bulk_offline.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/wsl_wheel_utils.sh"

WHEELS_DIR="${WHEELS_DIR:-/mnt/f/antd-demo1/0616/packages/pip_wheels_linux}"
CONDA_ENV="${CONDA_ENV:-agent}"

source_conda() {
  if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
    # shellcheck disable=SC1091
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
    return 0
  fi
  return 1
}

if source_conda; then
  conda activate "$CONDA_ENV"
else
  echo "ERROR: 未找到 ~/miniconda3，请先跑 setup_mineru_wsl.sh"
  exit 1
fi

if [[ ! -d "$WHEELS_DIR" ]]; then
  echo "ERROR: WHEELS_DIR not found: $WHEELS_DIR"
  exit 1
fi

whl_count=$(find "$WHEELS_DIR" -maxdepth 1 -name '*.whl' | wc -l)
echo "Python: $(python --version 2>&1)"
echo "WHEELS_DIR: $WHEELS_DIR ($whl_count whl files)"

export PIP_NO_INDEX=1
export PIP_FIND_LINKS="$WHEELS_DIR"

bulk_install_deduped_wheels "$WHEELS_DIR"
fix_torch_cuda_stack "$WHEELS_DIR"
install_mineru_runtime_from_wheels "$WHEELS_DIR"
fix_numpy_onnx_opencv_stack "$WHEELS_DIR" 1

echo "==> verify"
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
python -c "import triton; print('triton', triton.__version__)"
python -c "import lmdeploy; print('lmdeploy', lmdeploy.__version__)"
python -c "import mineru; print('mineru OK')"
