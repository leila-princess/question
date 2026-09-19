#!/usr/bin/env bash
# Download Miniconda for WSL offline install.
# Prefer Python 3.10 installer (matches cp310 wheels).
set -euo pipefail

OUT_DIR="${1:-/mnt/f/antd-demo1/0616/packages}"
PY310_URL="https://repo.anaconda.com/miniconda/Miniconda3-py310_24.11.1-0-Linux-x86_64.sh"
LATEST_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
PY310_OUT="$OUT_DIR/Miniconda3-py310_24.11.1-0-Linux-x86_64.sh"
LATEST_OUT="$OUT_DIR/Miniconda3-latest-Linux-x86_64.sh"

mkdir -p "$OUT_DIR"

download_one() {
  local url="$1"
  local out="$2"
  if [[ -f "$out" ]]; then
    echo "Already exists: $out ($(du -h "$out" | cut -f1))"
    return 0
  fi
  echo "Downloading $url -> $out"
  curl -fsSL -o "$out" "$url"
  chmod +x "$out"
  ls -lh "$out"
}

echo "==> Python 3.10 Miniconda (offline WSL 推荐)"
download_one "$PY310_URL" "$PY310_OUT"

echo "==> latest Miniconda (可选，base 非 3.10，离线需联网建 agent)"
download_one "$LATEST_URL" "$LATEST_OUT"

echo ""
echo "WSL offline setup 请使用: Miniconda3-py310_24.11.1-0-Linux-x86_64.sh"
