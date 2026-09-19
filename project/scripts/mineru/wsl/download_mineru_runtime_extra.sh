#!/usr/bin/env bash
# 下载 MinerU WSL 修复包 → 独立小目录（不必复制整个 pip_wheels_linux）
#
# 有网 WSL:
#   bash scripts/mineru/wsl/download_mineru_runtime_extra.sh
#
# Windows 有网（等价）:
#   .\scripts\mineru\download_mineru_fix_wheels.ps1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
W="${WHEELS_OUT:-$PROJECT_ROOT/0616/packages/pip_wheels_mineru_fix_cp310}"
PY_VER="${PY_VER:-3.10}"
PLATFORM="${PLATFORM:-manylinux2014_x86_64}"
ABI="${ABI:-cp310}"

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
mkdir -p "$W"

PIP_DL=(
  "$PY" -m pip download --no-cache-dir -d "$W"
  --platform "$PLATFORM" --python-version "$PY_VER" --abi "$ABI"
  --only-binary=:all:
)

echo "==> mineru fix wheels (Linux cp310) -> $W"

CORE=(
  "numpy==1.26.4"
  "onnxruntime==1.16.3"
  "opencv-python-headless==4.11.0.86"
  "opencv-python==4.11.0.86"
  "magika==0.6.3"
  "pylatexenc==2.10"
)
EXTRA=(coloredlogs humanfriendly flatbuffers packaging protobuf sympy mpmath)
OFFICE=(
  "pypptx-with-oxml==1.0.3"
  "python-pptx"
  "python-docx"
  "openpyxl"
  "mammoth"
  "XlsxWriter"
  "lxml>=4.9,<7"
  "typing_extensions"
  "pypdf"
  "pypdfium2"
  "reportlab"
  "pdftext"
  "json-repair"
  "fastapi"
  "uvicorn"
  "python-multipart"
)

download_req() {
  local req="$1"
  local nodeps="${2:-0}"
  echo "  $req"
  if [[ "$nodeps" == "1" ]]; then
    if ! "$PY" -m pip download --no-cache-dir -d "$W" --no-deps \
      --index-url https://pypi.org/simple \
      --platform "$PLATFORM" --python-version "$PY_VER" --abi "$ABI" \
      --only-binary=:all: "$req" 2>/dev/null; then
      "$PY" -m pip download --no-cache-dir -d "$W" --no-deps \
        --index-url https://pypi.org/simple "$req" || true
    fi
    return 0
  fi
  if ! "${PIP_DL[@]}" --index-url https://pypi.org/simple "$req"; then
    "$PY" -m pip download --no-cache-dir -d "$W" --index-url https://pypi.org/simple "$req" || true
  fi
}

for req in "${CORE[@]}"; do
  case "$req" in
    numpy=*|onnxruntime=*|opencv-python*)
      download_req "$req" 1 ;;
    *)
      download_req "$req" 0 ;;
  esac
done
for req in "${EXTRA[@]}"; do download_req "$req" 0 || true; done
for req in "${OFFICE[@]}"; do
  case "$req" in
    lxml*) download_req "$req" 1 ;;
    *) download_req "$req" 0 ;;
  esac
done

# 去掉误下的 win/mac wheel 及 numpy 2.x
removed=0
for whl in "$W"/*.whl; do
  [[ -f "$whl" ]] || continue
  base="$(basename "$whl")"
  if [[ "$base" == *win_* ]] || [[ "$base" == *macosx_* ]]; then
    rm -f "$whl"
    removed=$((removed + 1))
    echo "  removed non-linux: $base"
    continue
  fi
  if [[ "$base" == numpy-2.* ]]; then
    rm -f "$whl"
    removed=$((removed + 1))
    echo "  removed numpy 2.x: $base"
  fi
done

count=$(find "$W" -maxdepth 1 -name '*.whl' | wc -l)
echo "==> total $count wheels (removed non-linux: $removed)"

{
  echo "{"
  echo "  \"platform\": \"$PLATFORM\","
  echo "  \"python\": \"$PY_VER\","
  echo "  \"abi\": \"$ABI\","
  echo "  \"wheel_count\": $count,"
  echo "  \"wheels\": ["
  first=1
  for whl in "$W"/*.whl; do
    [[ -f "$whl" ]] || continue
    [[ $first -eq 1 ]] && first=0 || echo ","
    printf '    "%s"' "$(basename "$whl")"
  done
  echo ""
  echo "  ]"
  echo "}"
} > "$W/manifest.json"

echo ""
echo "Downloaded key wheels:"
ls "$W"/numpy-1.*cp310*manylinux*.whl 2>/dev/null || echo "  WARN: numpy 1.x linux wheel missing"
ls "$W"/onnxruntime*cp310*manylinux*.whl 2>/dev/null || echo "  WARN: onnxruntime linux wheel missing"
ls "$W"/opencv_python*cp310*manylinux*.whl 2>/dev/null || echo "  WARN: opencv linux wheel missing"
ls "$W"/magika* 2>/dev/null || echo "  WARN: magika wheel missing"
ls "$W"/pypptx_with_oxml* "$W"/python_pptx* 2>/dev/null || echo "  WARN: pptx wheel missing"
echo ""
echo "拷到新机: 仅复制 pip_wheels_mineru_fix_cp310/"
echo "WSL 安装: bash scripts/mineru/wsl/fix_mineru_numpy_stack.sh"
