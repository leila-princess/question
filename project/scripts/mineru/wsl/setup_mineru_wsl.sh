#!/usr/bin/env bash
# Offline / online MinerU setup inside WSL (Ubuntu)
#
# Online:
#   wsl bash /mnt/f/antd-demo1/scripts/mineru/wsl/setup_mineru_wsl.sh
#
# Offline:
#   WHEELS_DIR=/mnt/f/antd-demo1/0616/packages/pip_wheels_linux \
#   MINICONDA_SH=/mnt/f/antd-demo1/0616/packages/Miniconda3-py310_24.11.1-0-Linux-x86_64.sh \
#   wsl bash /mnt/f/antd-demo1/scripts/mineru/wsl/setup_mineru_wsl.sh
#
# NOTE: Windows conda env "agent" CANNOT be used in WSL (different OS binaries).

set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/mnt/f/antd-demo1}"
CONDA_ENV="${CONDA_ENV:-agent}"
PYTHON_VER="${PYTHON_VER:-3.10}"
WHEELS_DIR="${WHEELS_DIR:-}"
MINICONDA_SH="${MINICONDA_SH:-}"
OFFLINE="${OFFLINE:-}"

if [[ -n "$WHEELS_DIR" ]]; then
  OFFLINE=1
fi

auto_miniconda_sh() {
  local dir="${1:-}"
  local f
  if [[ -z "$dir" || ! -d "$dir" ]]; then
    return 1
  fi
  for f in \
    "$dir"/Miniconda3-py310_*-Linux-x86_64.sh \
    "$dir"/Miniconda3-latest-Linux-x86_64.sh; do
    if [[ -f "$f" ]]; then
      echo "$f"
      return 0
    fi
  done
  return 1
}

if [[ -z "$MINICONDA_SH" && -n "$WHEELS_DIR" ]]; then
  MINICONDA_SH="$(auto_miniconda_sh "$(dirname "$WHEELS_DIR")" || true)"
fi

echo "========================================"
echo " MinerU WSL setup"
echo " Project : $PROJECT_ROOT"
echo " Env     : $CONDA_ENV (python $PYTHON_VER)"
echo " Offline : ${OFFLINE:-0}  WHEELS: ${WHEELS_DIR:-none}"
echo " Miniconda: ${MINICONDA_SH:-auto/installed}"
echo "========================================"
echo "NOTE: Windows agent conda 不能给 WSL 用，需 WSL 内单独装 Linux 版 Python 包。"
echo ""

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "WARN: nvidia-smi not found in WSL (GPU 加速可能不可用)"
else
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
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

install_miniconda() {
  if command -v conda >/dev/null 2>&1 || source_conda; then
    return 0
  fi
  if [[ -n "$MINICONDA_SH" && -f "$MINICONDA_SH" ]]; then
    echo "==> install Miniconda from $MINICONDA_SH"
    bash "$MINICONDA_SH" -b -p "$HOME/miniconda3"
    source_conda
    return 0
  fi
  if [[ "$OFFLINE" == "1" ]]; then
    echo "ERROR: offline 模式需要其一:"
    echo "  1) WSL 内已安装 conda/miniconda（~/miniconda3）"
    echo "  2) 设置 MINICONDA_SH 为安装包完整路径，例如:"
    echo "     /mnt/f/antd-demo1/0616/packages/Miniconda3-py310_24.11.1-0-Linux-x86_64.sh"
    echo "  3) 把安装包放在 pip_wheels_linux 同级目录（0616/packages/）"
  else
    echo "ERROR: 未找到 Miniconda。有网时下载:"
    echo "  bash scripts/mineru/wsl/download_miniconda.sh"
  fi
  exit 1
}

py_major_minor() {
  python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

env_exists() {
  conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"
}

ensure_conda_env() {
  if ! source_conda; then
    echo "ERROR: conda not found after install_miniconda"
    exit 1
  fi

  if env_exists; then
    conda activate "$CONDA_ENV"
    ver="$(py_major_minor)"
    echo "==> using existing env $CONDA_ENV (python $ver)"
    if [[ "$ver" != "$PYTHON_VER" ]]; then
      echo "ERROR: env $CONDA_ENV is python $ver, need $PYTHON_VER (cp310 wheels)."
      echo "  删除后重装: conda env remove -n $CONDA_ENV -y"
      echo "  或 WSL 联网: conda create -n $CONDA_ENV python=$PYTHON_VER -y"
      exit 1
    fi
    return 0
  fi

  base_ver="$(conda run -n base python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  echo "==> base env python $base_ver"

  if [[ "$OFFLINE" == "1" ]]; then
    if [[ "$base_ver" == "$PYTHON_VER" ]]; then
      echo "==> offline: clone base -> $CONDA_ENV (无需联网)"
      conda create -n "$CONDA_ENV" --clone base -y
      conda activate "$CONDA_ENV"
      return 0
    fi
    echo "ERROR: offline 无法创建 python $PYTHON_VER 的 $CONDA_ENV 环境。"
    echo "  当前 Miniconda base 是 python $base_ver（多为 latest 安装包）。"
    echo "  离线 wheel 是 cp310，必须用 Python $PYTHON_VER。"
    echo ""
    echo "  方案 A（推荐，完全离线）:"
    echo "    1) 有网机下载 Python 3.10 版 Miniconda:"
    echo "       bash scripts/mineru/wsl/download_miniconda.sh"
    echo "    2) 拷 Miniconda3-py310_*.sh 到新机"
    echo "    3) 删除旧 miniconda 后重装:"
    echo "       rm -rf ~/miniconda3"
    echo "       MINICONDA_SH=.../Miniconda3-py310_*.sh bash setup_mineru_wsl.sh"
    echo ""
    echo "  方案 B（WSL 可联网一次）:"
    echo "    source ~/miniconda3/etc/profile.d/conda.sh"
    echo "    conda create -n $CONDA_ENV python=$PYTHON_VER -y"
    echo "    然后重新运行本脚本"
    exit 1
  fi

  echo "==> online: conda create -n $CONDA_ENV python=$PYTHON_VER"
  conda create -n "$CONDA_ENV" "python=${PYTHON_VER}" -y
  conda activate "$CONDA_ENV"
}

install_miniconda
ensure_conda_env

if [[ "$OFFLINE" == "1" ]]; then
  if [[ ! -d "$WHEELS_DIR" ]]; then
    echo "ERROR: WHEELS_DIR not found: $WHEELS_DIR"
    exit 1
  fi
  export PIP_NO_INDEX=1
  export PIP_FIND_LINKS="$WHEELS_DIR"
  echo "==> offline: skip pip upgrade (conda 自带 pip，避免联网)"
  echo "==> offline pip from $WHEELS_DIR"

  offline_pip() {
    python -m pip install --no-index --find-links="$WHEELS_DIR" "$@"
  }

  # shellcheck disable=SC1091
  source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/wsl_wheel_utils.sh"

  bulk_offline_wheels() {
    bulk_install_deduped_wheels "$WHEELS_DIR"
    fix_torch_cuda_stack "$WHEELS_DIR"
  }

  whl_n=$(find "$WHEELS_DIR" -maxdepth 1 -name '*.whl' | wc -l)
  echo "==> wheel count: $whl_n"
  if [[ "$whl_n" -lt 100 ]]; then
    echo "WARN: wheel 数量偏少，请确认 pip_wheels_linux 拷完整"
  fi

  # 离线一律 bulk，不再走 pip 依赖解析（必失败或漏装）
  bulk_offline_wheels
  install_mineru_runtime_from_wheels "$WHEELS_DIR"
  fix_numpy_onnx_opencv_stack "$WHEELS_DIR" 1
else
  python -m pip install -U pip setuptools wheel
  echo "==> online pip"
  if ! python -m pip install "torch==${TORCH_VER:-2.9.1}" "torchvision==${TORCHVISION_VER:-0.24.1}" \
    --index-url https://download.pytorch.org/whl/cu128; then
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
  fi
  python -m pip install -U "mineru[all]==3.3.1" lmdeploy triton
  fix_numpy_onnx_opencv_stack "" 0
fi

echo "==> verify"
python -c "import sys; print('python', sys.version)"
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
if ! python -c "import triton, lmdeploy, mineru; print('triton/lmdeploy/mineru OK')"; then
  echo "ERROR: lmdeploy/mineru 未装好。离线请执行:"
  echo "  bash scripts/mineru/wsl/install_wheels_bulk_offline.sh"
  exit 1
fi
mineru --version || true

PIPE="$PROJECT_ROOT/models/mineru/pipeline/models/MFR/unimernet_hf_small_2503/model.safetensors"
VLM_CFG="$PROJECT_ROOT/models/mineru/vlm/config.json"
[[ -f "$PIPE" ]] && echo "OK pipeline" || echo "MISSING pipeline: $PIPE"
[[ -f "$VLM_CFG" ]] && echo "OK vlm" || echo "WARN vlm missing: $VLM_CFG"

echo ""
echo "Done. Windows: .\\scripts\\mineru\\start_mineru_wsl.ps1"
