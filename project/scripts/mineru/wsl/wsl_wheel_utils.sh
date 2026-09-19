#!/usr/bin/env bash
# Shared helpers for offline wheel install in WSL.

wheel_pkg_name() {
  basename "$1" .whl | sed -E 's/-[0-9].*//' | tr '[:upper:]' '[:lower:]' | tr '_' '-'
}

latest_wheel() {
  local dir="$1"
  local pattern="$2"
  ls "$dir"/$pattern 2>/dev/null | sort -V | tail -1
}

# 修复包独立目录（默认 pip_wheels_mineru_fix_cp310，可单独拷 U 盘）
default_mineru_fix_wheels_dir() {
  local script_dir="${1:-}"
  local root=""
  if [[ -n "$script_dir" ]]; then
    root="$(cd "$script_dir/../../.." 2>/dev/null && pwd || true)"
  fi
  if [[ -z "$root" && -n "${PROJECT_ROOT:-}" ]]; then
    root="$PROJECT_ROOT"
  fi
  if [[ -n "$root" && -d "$root/0616/packages/pip_wheels_mineru_fix_cp310" ]]; then
    echo "$root/0616/packages/pip_wheels_mineru_fix_cp310"
    return 0
  fi
  echo ""
}

# 返回要搜索 wheel 的目录列表（fix 小包优先，再 WHEELS_DIR / 大目录）
mineru_wheels_search_dirs() {
  local primary="${1:-}"
  local fix_dir="${WHEELS_FIX_DIR:-}"
  local -a dirs=()

  if [[ -n "$fix_dir" && -d "$fix_dir" ]]; then
    dirs+=("$fix_dir")
  fi
  if [[ -n "$primary" && -d "$primary" ]]; then
    dirs+=("$primary")
  fi
  if [[ ${#dirs[@]} -eq 0 ]]; then
    local auto
    auto="$(default_mineru_fix_wheels_dir "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" 2>/dev/null || true)")"
    if [[ -n "$auto" && -d "$auto" ]]; then
      dirs+=("$auto")
    fi
  fi

  local d seen=""
  for d in "${dirs[@]}"; do
    if [[ " $seen " != *" $d "* ]]; then
      echo "$d"
      seen="$seen $d"
    fi
  done
}

_pick_linux_cp310_wheel_in_dirs() {
  local pattern="$1"
  shift
  local dir whl candidate=""
  for dir in "$@"; do
    [[ -d "$dir" ]] || continue
    for whl in "$dir"/$pattern; do
      [[ -f "$whl" ]] || continue
      if [[ "$whl" == *win_* ]] || [[ "$whl" == *macosx_* ]]; then
        continue
      fi
      if [[ "$whl" == *manylinux* ]] || [[ "$whl" == *linux_* ]] || [[ "$whl" == *cp310* ]] || [[ "$whl" == *py3-none-any* ]]; then
        candidate="$(
          printf '%s\n' "${candidate:+$candidate$'\n'}$whl" | sort -V | tail -1
        )"
      fi
    done
    if [[ -n "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
    whl="$(latest_wheel "$dir" "$pattern")"
    if [[ -n "$whl" && -f "$whl" ]]; then
      if [[ "$whl" != *win_* && "$whl" != *macosx_* ]]; then
        echo "$whl"
        return 0
      fi
    fi
  done
  return 1
}

bulk_install_deduped_wheels() {
  local wheels_dir="$1"
  local whl pkg
  declare -A latest=()

  for whl in "$wheels_dir"/*.whl; do
    [[ -f "$whl" ]] || continue
    pkg="$(wheel_pkg_name "$whl")"
    if [[ -z "${latest[$pkg]:-}" ]]; then
      latest[$pkg]="$whl"
    else
      latest[$pkg]="$(
        printf '%s\n' "${latest[$pkg]}" "$whl" | sort -V | tail -1
      )"
    fi
  done

  local count=0 fail=0
  for pkg in "${!latest[@]}"; do
    whl="${latest[$pkg]}"
    if python -m pip install --no-index --find-links="$wheels_dir" --no-deps "$whl" >/dev/null 2>&1; then
      count=$((count + 1))
    else
      fail=$((fail + 1))
      echo "WARN: skip $(basename "$whl")"
    fi
  done
  echo "==> deduped bulk: $count packages ($fail failed)"
}

fix_torch_cuda_stack() {
  local wheels_dir="$1"
  local whl pkg
  declare -A nvidia_latest=()

  echo "==> fix torch/cuda stack (triton 最高版 + nvidia-cu12 + torch)"

  whl="$(latest_wheel "$wheels_dir" "triton-*.whl")"
  if [[ -n "$whl" ]]; then
    echo "  triton: $(basename "$whl")"
    python -m pip install --no-index --find-links="$wheels_dir" --no-deps "$whl"
  fi

  for pattern in "torch-*cp310*.whl" "torchvision-*cp310*.whl"; do
    whl="$(latest_wheel "$wheels_dir" "$pattern")"
    if [[ -n "$whl" ]]; then
      echo "  $(wheel_pkg_name "$whl"): $(basename "$whl")"
      python -m pip install --no-index --find-links="$wheels_dir" --no-deps "$whl"
    fi
  done

  for whl in "$wheels_dir"/nvidia_*.whl; do
    [[ -f "$whl" ]] || continue
    pkg="$(wheel_pkg_name "$whl")"
    if [[ -z "${nvidia_latest[$pkg]:-}" ]]; then
      nvidia_latest[$pkg]="$whl"
    else
      nvidia_latest[$pkg]="$(
        printf '%s\n' "${nvidia_latest[$pkg]}" "$whl" | sort -V | tail -1
      )"
    fi
  done

  for pkg in "${!nvidia_latest[@]}"; do
    whl="${nvidia_latest[$pkg]}"
    echo "  $pkg: $(basename "$whl")"
    python -m pip install --no-index --find-links="$wheels_dir" --no-deps "$whl"
  done

  for pattern in "lmdeploy-*.whl" "mineru-*.whl"; do
    whl="$(latest_wheel "$wheels_dir" "$pattern")"
    if [[ -n "$whl" ]]; then
      echo "  $(wheel_pkg_name "$whl"): $(basename "$whl")"
      python -m pip install --no-index --find-links="$wheels_dir" --no-deps "$whl"
    fi
  done
}

# mineru-api 直接依赖（bulk --no-deps 不会自动装上）
install_mineru_runtime_from_wheels() {
  local wheels_dir="$1"
  local pkg whl
  local -a pkgs=(
    pypdf pypdfium2 reportlab pdftext json-repair beautifulsoup4 soupsieve
    magika python-docx openpyxl mammoth pylatexenc lxml fast-langdetect
    openai modelscope loguru click tqdm requests httpx pillow opencv-python
    huggingface-hub mineru-vl-utils python-multipart fastapi uvicorn
  )
  local missing=()

  echo "==> mineru-api runtime deps from wheels"
  for pkg in "${pkgs[@]}"; do
    whl="$(latest_wheel "$wheels_dir" "${pkg}*.whl")"
    if [[ -z "$whl" ]]; then
      whl="$(latest_wheel "$wheels_dir" "${pkg//-/_}*.whl")"
    fi
    if [[ -n "$whl" ]]; then
      python -m pip install --no-index --find-links="$wheels_dir" --no-deps "$whl" >/dev/null 2>&1 || true
    else
      missing+=("$pkg")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "WARN: wheels 目录缺少（mineru-api 可能仍报错）:"
    printf '  - %s\n' "${missing[@]}"
    echo "  有网机补: bash scripts/mineru/wsl/download_mineru_runtime_wheels.sh"
    echo "  numpy/onnx/cv2: bash scripts/mineru/wsl/download_mineru_runtime_extra.sh"
  fi
}

# numpy 2.x 会导致 onnxruntime 报 _ARRAY_API not found；opencv 也会 ABI 失败
fix_numpy_onnx_opencv_stack() {
  local wheels_dir="${1:-}"
  local offline="${2:-0}"
  local whl
  local -a search_dirs=()

  while IFS= read -r d; do
    [[ -n "$d" ]] && search_dirs+=("$d")
  done < <(mineru_wheels_search_dirs "$wheels_dir")

  echo "==> fix numpy/onnxruntime/opencv (need numpy 1.x for onnxruntime)"
  if [[ ${#search_dirs[@]} -gt 0 ]]; then
    echo "    wheel dirs: ${search_dirs[*]}"
  fi

  _install_wheel_or_pip() {
    local pattern="$1"
    shift
    local pip_args=("$@")
    if [[ ${#search_dirs[@]} -gt 0 ]]; then
      whl="$(_pick_linux_cp310_wheel_in_dirs "$pattern" "${search_dirs[@]}")" || whl=""
      if [[ -n "$whl" && -f "$whl" ]]; then
        echo "  wheel: $(basename "$whl")"
        python -m pip install --force-reinstall --no-deps "$whl"
        return 0
      fi
    fi
    if [[ "$offline" == "1" ]]; then
      echo "WARN: offline 且 wheels 缺少 $pattern，跳过在线安装"
      return 1
    fi
    echo "  pip: ${pip_args[*]}"
    python -m pip install --force-reinstall "${pip_args[@]}"
  }

  # 1) numpy 必须 <2.0
  _install_wheel_or_pip "numpy-1.*cp310*manylinux*.whl" \
    "numpy>=1.23.5,<2.0" || true

  cur_np="$(python -c 'import numpy as np; print(np.__version__)' 2>/dev/null || echo unknown)"
  echo "  numpy now: $cur_np"
  if python -c 'import numpy as np; import sys; sys.exit(0 if np.__version__.startswith("1.") else 1)'; then
    :
  else
    echo "ERROR: numpy 仍为 2.x，请有网执行: pip install 'numpy>=1.23.5,<2.0' --force-reinstall"
    echo "  或: .\\scripts\\mineru\\download_mineru_fix_wheels.ps1"
    return 1
  fi

  # 2) onnxruntime（magika 依赖；Linux cp310 最高 manylinux wheel 常为 1.16.3）
  _install_wheel_or_pip "onnxruntime-1.*cp310*manylinux*.whl" \
    "onnxruntime>=1.16.0,<1.24.1" || true

  # 3) opencv — wheel 文件名是 cp37-abi3-manylinux，不是 cp310*
  python -m pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python 2>/dev/null || true

  _install_opencv_wheel() {
    local pattern="$1"
    local label="$2"
    whl="$(_pick_linux_cp310_wheel_in_dirs "$pattern" "${search_dirs[@]}")" || whl=""
    if [[ -n "$whl" && -f "$whl" ]]; then
      echo "  opencv ($label): $(basename "$whl")"
      python -m pip install --force-reinstall --no-deps "$whl"
      return 0
    fi
    return 1
  }

  if ! _install_opencv_wheel "opencv_python_headless-4.*manylinux*.whl" "headless"; then
    _install_opencv_wheel "opencv_python-4.*manylinux*.whl" "full" || true
  fi

  if ! python -c "import cv2" 2>/dev/null; then
    echo "  cv2 import failed, try headless / online..."
    _install_opencv_wheel "opencv_python_headless-4.*manylinux*.whl" "headless-retry" || true
    if [[ "$offline" != "1" ]]; then
      python -m pip install --force-reinstall "opencv-python-headless>=4.11.0.86,<4.12" || \
        python -m pip install --force-reinstall "opencv-python>=4.11.0.86,<4.12" || true
    fi
  fi

  if ! python -c "import cv2" 2>/dev/null && command -v apt-get >/dev/null 2>&1; then
    echo "  try apt libs for opencv (libgl1)..."
    sudo apt-get update -qq && sudo apt-get install -y -qq libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 2>/dev/null || true
  fi

  # 4) magika
  whl=""
  if [[ ${#search_dirs[@]} -gt 0 ]]; then
    whl="$(_pick_linux_cp310_wheel_in_dirs "magika-0.*cp310*manylinux*.whl" "${search_dirs[@]}")" || whl=""
    if [[ -z "$whl" ]]; then
      whl="$(_pick_linux_cp310_wheel_in_dirs "magika-*.whl" "${search_dirs[@]}")" || whl=""
    fi
  fi
  if [[ -n "$whl" && -f "$whl" ]]; then
    echo "  magika wheel: $(basename "$whl")"
    python -m pip install --force-reinstall --no-deps "$whl" || true
  elif [[ "$offline" != "1" ]]; then
    python -m pip install --force-reinstall "magika>=0.6.2,<1.1.0" || true
  fi

  # 5) pylatexenc（纯 py，可选）
  _install_wheel_or_pip "pylatexenc-*.whl" "pylatexenc>=2.10,<3" || true

  fix_mineru_office_runtime_deps "$offline" "${search_dirs[@]}"
}

# mineru-api 启动还依赖 pptx / docx / xlsx 等 office 解析包
fix_mineru_office_runtime_deps() {
  local offline="${1:-0}"
  shift
  local -a search_dirs=("$@")
  local whl
  local -a pkgs=(
    pypptx-with-oxml python-pptx python-docx openpyxl mammoth
    XlsxWriter lxml typing_extensions
    pypdf pypdfium2 reportlab pdftext json-repair
    fastapi uvicorn python-multipart
  )

  echo "==> fix mineru office/api runtime (pptx docx lxml ...)"

  _install_py_wheel() {
    local pattern="$1"
    local pip_name="$2"
    whl="$(_pick_linux_cp310_wheel_in_dirs "$pattern" "${search_dirs[@]}")" || whl=""
    if [[ -n "$whl" && -f "$whl" ]]; then
      echo "  $(basename "$whl")"
      python -m pip install --force-reinstall --no-deps "$whl" >/dev/null 2>&1 || \
        python -m pip install --force-reinstall --no-deps "$whl"
      return 0
    fi
    if [[ "$offline" != "1" && -n "$pip_name" ]]; then
      echo "  pip: $pip_name"
      python -m pip install --force-reinstall "$pip_name" >/dev/null 2>&1 || true
    fi
    return 1
  }

  # pptx：mineru 用 from pptx import Presentation
  if ! python -c "from pptx import Presentation" 2>/dev/null; then
    local dir
    for dir in "${search_dirs[@]}"; do
      if [[ -d "$dir" ]] && compgen -G "$dir/pypptx_with_oxml-"*.whl >/dev/null; then
        echo "  pip offline: pypptx-with-oxml (with deps from $dir)"
        python -m pip install --force-reinstall --no-index --find-links="$dir" \
          "pypptx-with-oxml==1.0.3" && break
      fi
    done
    if ! python -c "from pptx import Presentation" 2>/dev/null; then
      _install_py_wheel "pypptx_with_oxml-*.whl" "pypptx-with-oxml==1.0.3" || true
      _install_py_wheel "python_pptx-*.whl" "python-pptx" || true
    fi
  fi

  for spec in \
    "python_docx-*.whl:python-docx" \
    "openpyxl-*.whl:openpyxl" \
    "mammoth-*.whl:mammoth" \
    "xlsxwriter-*.whl:XlsxWriter" \
    "lxml-*cp310*manylinux*.whl:lxml" \
    "pypdf-*.whl:pypdf" \
    "pypdfium2-*.whl:pypdfium2" \
    "reportlab-*.whl:reportlab" \
    "pdftext-*.whl:pdftext" \
    "json_repair-*.whl:json-repair" \
    "fastapi-*.whl:fastapi" \
    "uvicorn-*.whl:uvicorn" \
    "python_multipart-*.whl:python-multipart"
  do
    pattern="${spec%%:*}"
    pip_name="${spec#*:}"
    _install_py_wheel "$pattern" "$pip_name" || true
  done

  if ! python -c "from pptx import Presentation" 2>/dev/null; then
    echo "ERROR: pptx 仍未安装，请确认 pip_wheels_mineru_fix_cp310 含 pypptx_with_oxml 或 python_pptx"
    return 1
  fi
  echo "  pptx OK"
}
