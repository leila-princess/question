#!/usr/bin/env bash
# Run MinerU API inside WSL. Called by start_mineru_wsl.ps1 or manually:
#   bash /mnt/f/antd-demo1/scripts/mineru/wsl/mineru_api_wsl.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
CONDA_ENV="${CONDA_ENV:-agent}"
HOST="${MINERU_API_HOST:-127.0.0.1}"
PORT="${MINERU_API_PORT:-8001}"
PROFILE="${MINERU_WSL_PROFILE:-pipeline-wsl}"

win_to_wsl_path() {
  local p="$1"
  p="${p//\\//}"
  if [[ "$p" =~ ^([A-Za-z]):/(.*)$ ]]; then
    local drive="${BASH_REMATCH[1],,}"
    local rest="${BASH_REMATCH[2]}"
    echo "/mnt/$drive/$rest"
    return
  fi
  echo "$p"
}

# Activate conda
if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [[ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/anaconda3/etc/profile.d/conda.sh"
fi
conda activate "$CONDA_ENV"

export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-0}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-0}"
export MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-local}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export TORCH_CUDA_ALLOC_CONF="${TORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

PIPE_DIR="$PROJECT_ROOT/models/mineru/pipeline"
VLM_DIR="$PROJECT_ROOT/models/mineru/vlm"
WSL_JSON="$PROJECT_ROOT/mineru.wsl.json"

mkdir -p "$(dirname "$WSL_JSON")"
cat > "$WSL_JSON" <<EOF
{
  "models-dir": {
    "pipeline": "${PIPE_DIR}",
    "vlm": "${VLM_DIR}"
  }
}
EOF
export MINERU_TOOLS_CONFIG_JSON="$WSL_JSON"

case "$PROFILE" in
  rtx5090-wsl|hybrid)
    export MINERU_BACKEND="${MINERU_BACKEND:-hybrid-auto-engine}"
    export MINERU_EFFORT="${MINERU_EFFORT:-medium}"
    export MINERU_LMDEPLOY_BACKEND="${MINERU_LMDEPLOY_BACKEND:-pytorch}"
    export MINERU_LMDEPLOY_DEVICE="${MINERU_LMDEPLOY_DEVICE:-cuda}"
    ENABLE_VLM_PRELOAD="${ENABLE_VLM_PRELOAD:-true}"
    ;;
  pipeline-wsl|pipeline|*)
    export MINERU_BACKEND="${MINERU_BACKEND:-pipeline}"
    ENABLE_VLM_PRELOAD="${ENABLE_VLM_PRELOAD:-false}"
    ;;
esac

export MINERU_LANG="${MINERU_LANG:-ch}"
export MINERU_METHOD="${MINERU_METHOD:-auto}"
export MINERU_FORMULA="${MINERU_FORMULA:-true}"
export MINERU_TABLE="${MINERU_TABLE:-true}"
export MINERU_TIMEOUT="${MINERU_TIMEOUT:-3600}"

echo "========================================"
echo " MinerU API (WSL / $PROFILE)"
echo " Project : $PROJECT_ROOT"
echo " Config  : $WSL_JSON"
echo " Backend : $MINERU_BACKEND"
echo " URL     : http://${HOST}:${PORT}"
echo "========================================"

if [[ ! -f "$PIPE_DIR/models/MFR/unimernet_hf_small_2503/model.safetensors" ]]; then
  echo "ERROR: pipeline model missing under $PIPE_DIR"
  exit 1
fi

# hybrid / VLM preload 依赖 lmdeploy；离线 fix 小包未装时自动回退 pipeline
if ! python -c "import lmdeploy" 2>/dev/null; then
  if [[ "$MINERU_BACKEND" == "hybrid-auto-engine" ]] || [[ "$ENABLE_VLM_PRELOAD" == "true" ]]; then
    echo "WARN: lmdeploy not installed -> MINERU_BACKEND=pipeline (no VLM preload)"
    echo "      Install: bash scripts/mineru/wsl/setup_mineru_wsl.sh"
    export MINERU_BACKEND=pipeline
    ENABLE_VLM_PRELOAD=false
  fi
fi

if [[ "$ENABLE_VLM_PRELOAD" == "true" ]] && [[ ! -f "$VLM_DIR/config.json" ]]; then
  echo "WARN: VLM model missing at $VLM_DIR -> pipeline, no preload"
  export MINERU_BACKEND=pipeline
  ENABLE_VLM_PRELOAD=false
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true
else
  echo "WARN: nvidia-smi unavailable in WSL"
fi

python -c "import torch; print('torch cuda:', torch.cuda.is_available())" || true

ARGS=(--host "$HOST" --port "$PORT")
if [[ "$ENABLE_VLM_PRELOAD" == "true" ]]; then
  ARGS+=(--enable-vlm-preload true)
fi

exec mineru-api "${ARGS[@]}"
