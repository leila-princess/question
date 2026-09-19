#!/usr/bin/env bash
set -euo pipefail

OUT="${WHEELS_OUT:-/mnt/f/antd-demo1/0616/packages/pip_wheels_linux}"
PY="${PY:-python3}"
PLATFORM="${PLATFORM:-manylinux2014_x86_64}"
PY_VER="${PY_VER:-3.10}"

mkdir -p "$OUT"
export TMPDIR="${TMPDIR:-$OUT/.pip_tmp}"
mkdir -p "$TMPDIR"

PIP=(
  "$PY" -m pip download --no-cache-dir -d "$OUT"
  --platform "$PLATFORM" --python-version "$PY_VER"
  --only-binary=:all:
)

echo "==> mineru wheel (no deps)"
"${PIP[@]}" --no-deps "mineru==3.3.1"

echo "==> mineru-vl-utils"
"${PIP[@]}" "mineru-vl-utils>=1.0.5" || true

echo "==> pyarrow for cp310 (mineru needs >=3.0,<3.1)"
"${PIP[@]}" "pyarrow>=3.0,<3.1" || \
  "$PY" -m pip download --no-cache-dir -d "$OUT" \
    "pyarrow>=3.0,<3.1" --python-version "$PY_VER" --only-binary=:all: || true

ls -la "$OUT"/mineru*.whl 2>/dev/null || { echo "ERROR: mineru wheel missing"; exit 1; }
echo "Done."
