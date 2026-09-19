#!/usr/bin/env bash
# Download Linux cp310 wheels for offline WSL MinerU (run in WSL on ONLINE machine)
# Usage:
#   wsl bash /mnt/f/antd-demo1/scripts/mineru/wsl/download_wsl_wheels.sh
#
# Uses pip cross-platform download flags so python3.10 does NOT need to be installed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WHEELS_OUT="${WHEELS_OUT:-$PROJECT_ROOT/0616/packages/pip_wheels_linux}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
TORCH_VER="${TORCH_VER:-2.9.1}"
TORCHVISION_VER="${TORCHVISION_VER:-0.24.1}"
PY_VER="${PY_VER:-3.10}"
PLATFORM="${PLATFORM:-manylinux2014_x86_64}"
ABI="${ABI:-cp310}"

mkdir -p "$WHEELS_OUT"
export TMPDIR="${TMPDIR:-$WHEELS_OUT/.pip_tmp}"
mkdir -p "$TMPDIR"
REQ_FILE="$WHEELS_OUT/wsl_requirements.txt"

cat > "$REQ_FILE" <<'EOF'
mineru[all]==3.3.1
lmdeploy==0.0.14
triton
huggingface-hub>=0.34,<1.0
sniffio
exceptiongroup
EOF

pick_python() {
  for bin in python3 python; do
    if command -v "$bin" >/dev/null 2>&1; then
      echo "$bin"
      return 0
    fi
  done
  echo "ERROR: python3 required. In WSL run: sudo apt install -y python3 python3-pip" >&2
  exit 1
}

PY="$(pick_python)"
PIP_DL=(
  "$PY" -m pip download --no-cache-dir -d "$WHEELS_OUT"
  --platform "$PLATFORM" --python-version "$PY_VER" --abi "$ABI"
  --only-binary=:all:
)
PIP_DL_PYPI=(
  "$PY" -m pip download --no-cache-dir -d "$WHEELS_OUT"
  --platform "$PLATFORM" --python-version "$PY_VER"
  --only-binary=:all:
)

echo "========================================"
echo " Download Linux wheels (cp310) for WSL"
echo " Output   : $WHEELS_OUT"
echo " pip from : $($PY --version)"
echo " target   : $PLATFORM / py$PY_VER / $ABI"
echo " Torch    : $TORCH_VER + cu128"
echo " TMPDIR   : $TMPDIR"
echo "========================================"

df -h "$WHEELS_OUT" /tmp 2>/dev/null || true

avail_kb=$(df -Pk "$WHEELS_OUT" 2>/dev/null | awk 'NR==2 {print $4}')
if [[ -n "${avail_kb:-}" && "$avail_kb" -lt 12582912 ]]; then
  echo "ERROR: need >=12GB free on $(df -Pk "$WHEELS_OUT" | awk 'NR==2 {print $6}'), have $(df -h "$WHEELS_OUT" | awk 'NR==2 {print $4}')" >&2
  echo "Free space: rm -rf ~/.cache/pip /tmp/pip-* $WHEELS_OUT/.pip_tmp" >&2
  exit 1
fi

"$PY" -m pip install -U pip wheel 2>/dev/null || true

echo "==> torch cu128"
if ! "${PIP_DL[@]}" "torch==${TORCH_VER}" "torchvision==${TORCHVISION_VER}" \
  --index-url "$TORCH_INDEX" 2>/dev/null; then
  echo "WARN: cross-platform torch download failed, retry with native pip..."
  "$PY" -m pip download --no-cache-dir -d "$WHEELS_OUT" \
    "torch==${TORCH_VER}" "torchvision==${TORCHVISION_VER}" \
    --index-url "$TORCH_INDEX" \
    --python-version "$PY_VER" --only-binary=:all:
fi

echo "==> mineru wheel (no deps; mineru[all] pulls obsolete pyarrow<3.1)"
"${PIP_DL_PYPI[@]}" --no-deps "mineru==3.3.1" || true

echo "==> mineru + lmdeploy + deps (PyPI)"
if ! "${PIP_DL_PYPI[@]}" -r "$REQ_FILE" 2>/dev/null; then
  echo "WARN: cross-platform PyPI download failed, retry with abi3-friendly flags..."
  if ! "${PIP_DL_PYPI[@]}" -r "$REQ_FILE"; then
    echo "WARN: still failing; retry per-package without platform lock..."
    while IFS= read -r req; do
      [[ -z "$req" || "$req" =~ ^# ]] && continue
      if [[ "$req" == mineru* ]]; then
        "${PIP_DL_PYPI[@]}" --no-deps "mineru==3.3.1" || true
        continue
      fi
      "${PIP_DL_PYPI[@]}" "$req" || "$PY" -m pip download --no-cache-dir -d "$WHEELS_OUT" "$req" || true
    done < "$REQ_FILE"
  fi
fi

COUNT=$(find "$WHEELS_OUT" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l)
SIZE=$(du -sh "$WHEELS_OUT" 2>/dev/null | cut -f1)
echo ""
echo "Done: $COUNT wheels, total $SIZE"
echo "Verify cp310 torch:"
ls "$WHEELS_OUT"/torch-*cp310*.whl 2>/dev/null || ls "$WHEELS_OUT"/torch-*.whl | head -3
echo ""
echo "Copy to new PC: 0616/packages/pip_wheels_linux"
