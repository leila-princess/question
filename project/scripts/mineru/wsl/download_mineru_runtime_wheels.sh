#!/usr/bin/env bash
# Download mineru-api runtime wheels missing from bulk lmdeploy bundle.
# Run on ONLINE WSL:
#   bash scripts/mineru/wsl/download_mineru_runtime_wheels.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
WHEELS_OUT="${WHEELS_OUT:-$PROJECT_ROOT/0616/packages/pip_wheels_linux}"
PY_VER="${PY_VER:-3.10}"
PLATFORM="${PLATFORM:-manylinux2014_x86_64}"
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

# mineru 3.3.1 direct deps used by mineru-api (see mineru METADATA Requires-Dist)
RUNTIME_PKGS=(
  "pypdf>=5.6.0"
  "pypdfium2>=4.30.0"
  "reportlab"
  "pdftext>=0.6.3"
  "json-repair>=0.46.2"
  "beautifulsoup4>=4.13.5"
  "soupsieve"
  "magika>=0.6.2,<1.1.0"
  "python-docx>=1.2.0,<2"
  "openpyxl>=3.1.5,<4"
  "mammoth>=1.11.0,<2"
  "pylatexenc>=2.10,<3"
  "lxml>=4.0.0,<7"
  "fast-langdetect>=0.2.3,<0.3.0"
  "openai>=1.70.0,<3"
  "modelscope>=1.26.0"
)

echo "==> mineru runtime wheels -> $WHEELS_OUT"
for req in "${RUNTIME_PKGS[@]}"; do
  echo "  $req"
  "${PIP_DL[@]}" "$req" 2>/dev/null || \
    "$PY" -m pip download --no-cache-dir -d "$WHEELS_OUT" \
      --platform "$PLATFORM" --python-version "$PY_VER" "$req" || true
done

COUNT=$(find "$WHEELS_OUT" -maxdepth 1 -name 'pypdf*.whl' | wc -l)
echo "Done. pypdf wheels: $COUNT"
ls "$WHEELS_OUT"/pypdf*.whl 2>/dev/null || echo "WARN: pypdf still missing"
