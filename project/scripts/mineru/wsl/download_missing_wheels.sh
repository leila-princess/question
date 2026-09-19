#!/usr/bin/env bash
# Download commonly missing transitive wheels for offline lmdeploy install.
# Run on ONLINE WSL: bash scripts/mineru/wsl/download_missing_wheels.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WHEELS_OUT="${WHEELS_OUT:-$PROJECT_ROOT/0616/packages/pip_wheels_linux}"
PLATFORM="${PLATFORM:-manylinux2014_x86_64}"
PY_VER="${PY_VER:-3.10}"
ABI="${ABI:-cp310}"

mkdir -p "$WHEELS_OUT"

pick_python() {
  for bin in python3 python; do
    if command -v "$bin" >/dev/null 2>&1; then
      echo "$bin"
      return 0
    fi
  done
  echo "ERROR: python3 required" >&2
  exit 1
}

PY="$(pick_python)"
PIP_DL=(
  "$PY" -m pip download --no-cache-dir -d "$WHEELS_OUT"
  --platform "$PLATFORM" --python-version "$PY_VER" --abi "$ABI"
  --only-binary=:all:
)

echo "==> extra deps (anyio / fastapi chain on py3.10)"
"${PIP_DL[@]}" sniffio exceptiongroup || true

echo "==> lmdeploy + triton full dep tree"
if ! "${PIP_DL[@]}" lmdeploy==0.0.14 triton==3.5.1; then
  "$PY" -m pip download --no-cache-dir -d "$WHEELS_OUT" \
    --platform "$PLATFORM" --python-version "$PY_VER" \
    lmdeploy==0.0.14 triton==3.5.1 || true
fi

COUNT=$(find "$WHEELS_OUT" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l)
echo "Done: $COUNT wheels in $WHEELS_OUT"
