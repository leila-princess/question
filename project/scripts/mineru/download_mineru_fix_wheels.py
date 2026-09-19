#!/usr/bin/env python3
"""Download MinerU WSL fix wheels (Linux cp310 small pack)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "0616" / "packages" / "pip_wheels_mineru_fix_cp310"
PLATFORM = "manylinux2014_x86_64"
PY_VER = "3.10"
ABI = "cp310"
INDEX = "https://pypi.org/simple"

# --no-deps + platform lock (linux cp310 binary)
BINARY_NO_DEPS = [
    "numpy==1.26.4",
    "onnxruntime==1.16.3",
    "opencv-python-headless==4.11.0.86",
    "lxml>=4.9,<7",
]

# pure python or universal wheels
# pure python: --no-deps to avoid pulling Windows transitive wheels on download host
PURE_NODEPS = [
    "magika==0.6.3",
    "pypptx-with-oxml==1.0.3",
    "python-pptx",
    "python-docx",
    "openpyxl",
    "mammoth",
    "XlsxWriter",
    "typing_extensions",
    "pypdf",
    "reportlab",
    "pdftext",
    "json-repair",
    "fastapi",
    "uvicorn",
    "python-multipart",
    "coloredlogs",
    "humanfriendly",
    "flatbuffers",
    "packaging",
    "protobuf",
    "sympy",
    "mpmath",
    "click",
    "pylatexenc==2.10",
]

PURE = []

# may need platform-specific wheel
BINARY_WITH_DEPS = [
    "pypdfium2>=4.30.0",
]


def run(cmd: list[str]) -> int:
    print(">", " ".join(cmd))
    return subprocess.call(cmd)


def download(req: str, *, nodeps: bool = False, platform: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "download", "--no-cache-dir",
        "-d", str(OUT), "--index-url", INDEX, req,
    ]
    if nodeps:
        cmd.append("--no-deps")
    if platform:
        cmd.extend([
            "--platform", PLATFORM,
            "--python-version", PY_VER,
            "--abi", ABI,
            "--only-binary=:all:",
        ])
    if run(cmd) != 0 and platform:
        run([sys.executable, "-m", "pip", "download", "--no-cache-dir", "-d", str(OUT),
             "--index-url", INDEX, "--no-deps", req])


def clean() -> int:
    removed = 0
    for whl in OUT.glob("*.whl"):
        name = whl.name
        if re.search(r"win_amd64|win32|macosx", name):
            whl.unlink()
            removed += 1
            print("removed non-linux:", name)
        elif name.startswith("numpy-2."):
            whl.unlink()
            removed += 1
            print("removed numpy 2.x:", name)
    return removed


def main() -> int:
    print("OUT:", OUT)
    for req in BINARY_NO_DEPS:
        download(req, nodeps=True, platform=True)
    for req in PURE_NODEPS:
        download(req, nodeps=True, platform=False)
    for req in PURE:
        download(req, nodeps=False, platform=False)
    for req in BINARY_WITH_DEPS:
        download(req, nodeps=False, platform=True)

    removed = clean()
    whls = sorted(p.name for p in OUT.glob("*.whl"))
    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform": PLATFORM,
        "python": PY_VER,
        "abi": ABI,
        "wheel_count": len(whls),
        "wheels": whls,
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nDone: {len(whls)} wheels (removed {removed})")
    checks = [
        "numpy-1.*manylinux*.whl",
        "onnxruntime*manylinux*.whl",
        "opencv_python_headless*manylinux*.whl",
        "pypptx_with_oxml*.whl",
        "magika*.whl",
    ]
    for pat in checks:
        hit = next(OUT.glob(pat.replace("*", "*")), None)
        # glob pattern fix
        import fnmatch
        hit = next((w for w in whls if fnmatch.fnmatch(w, pat)), None)
        print(" OK", hit if hit else f"MISSING {pat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
