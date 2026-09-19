"""Office 文档转 PDF（LibreOffice headless），供文件管理预览使用。"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

from config import Config

logger = logging.getLogger(__name__)

# 仅 PPT 类走后端 LibreOffice 转 PDF；Word/Excel 由前端本地预览
PPT_TO_PDF_EXT = frozenset({".ppt", ".pptx", ".dps"})

WORD_NATIVE_EXT = frozenset({".doc", ".docx", ".wps"})
EXCEL_NATIVE_EXT = frozenset({".xls", ".xlsx", ".xlsm", ".et"})

_CONVERT_LOCK = __import__("threading").Lock()
_SOFFICE_PATH_CACHE: Optional[str] = None


def is_ppt_convertible(object_name: str) -> bool:
    ext = os.path.splitext(object_name)[1].lower()
    return ext in PPT_TO_PDF_EXT


def is_word_native_preview(object_name: str) -> bool:
    ext = os.path.splitext(object_name)[1].lower()
    return ext in WORD_NATIVE_EXT


def is_excel_native_preview(object_name: str) -> bool:
    ext = os.path.splitext(object_name)[1].lower()
    return ext in EXCEL_NATIVE_EXT


def is_office_convertible(object_name: str) -> bool:
    """兼容旧名：仅 PPT 需要转 PDF。"""
    return is_ppt_convertible(object_name)


def _cache_dir() -> str:
    d = os.path.join(Config.OUTPUT_DIR, "office_pdf_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_paths(object_name: str) -> Tuple[str, str]:
    h = hashlib.md5(object_name.encode("utf-8")).hexdigest()
    base = os.path.join(_cache_dir(), h)
    return f"{base}.pdf", f"{base}.json"


def _windows_soffice_candidates() -> List[str]:
    patterns = [
        r"C:\Program Files\LibreOffice*\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice*\program\soffice.exe",
        r"D:\Program Files\LibreOffice*\program\soffice.exe",
        r"D:\LibreOffice*\program\soffice.exe",
    ]
    found: List[str] = []
    for pat in patterns:
        found.extend(glob.glob(pat))
    # 版本号高的目录优先（LibreOffice 24 等）
    return sorted({os.path.normpath(p) for p in found if os.path.isfile(p)}, reverse=True)


def resolve_soffice_path() -> Optional[str]:
    """定位 LibreOffice 的 soffice 可执行文件（供后端转 PDF，与前端 react-doc-viewer 无关）。"""
    env = (os.environ.get("LIBREOFFICE_PATH") or getattr(Config, "LIBREOFFICE_PATH", "") or "").strip()
    if env:
        if os.path.isfile(env):
            return env
        logger.warning("LIBREOFFICE_PATH 已设置但文件不存在: %s", env)

    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return found

    if sys.platform == "win32":
        for candidate in _windows_soffice_candidates():
            return candidate
        for fixed in (
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ):
            if os.path.isfile(fixed):
                return fixed

    return None


def get_soffice_path() -> Optional[str]:
    global _SOFFICE_PATH_CACHE
    if _SOFFICE_PATH_CACHE is None:
        _SOFFICE_PATH_CACHE = resolve_soffice_path()
        if _SOFFICE_PATH_CACHE:
            logger.info("Office 转 PDF 使用 LibreOffice: %s", _SOFFICE_PATH_CACHE)
        else:
            logger.warning(
                "未检测到 LibreOffice（soffice）。Word/Excel/PPT 预览需后端先转 PDF；"
                "请在运行 Flask 的机器上安装 LibreOffice，或在 .env 设置 LIBREOFFICE_PATH"
            )
    return _SOFFICE_PATH_CACHE


_MAGICK_PATH_CACHE: Optional[str] = None


def _windows_magick_candidates() -> List[str]:
    patterns = [
        r"C:\Program Files\ImageMagick*\magick.exe",
        r"C:\Program Files (x86)\ImageMagick*\magick.exe",
        r"D:\Program Files\ImageMagick*\magick.exe",
    ]
    found: List[str] = []
    for pat in patterns:
        found.extend(glob.glob(pat))
    return sorted({os.path.normpath(p) for p in found if os.path.isfile(p)}, reverse=True)


def resolve_magick_path() -> Optional[str]:
    """定位 ImageMagick magick.exe（EMF/WMF 转 PNG；Windows 安装后常不在 PATH）。"""
    env = (
        os.environ.get("MAGICK_PATH")
        or os.environ.get("MAGICK_HOME")
        or getattr(Config, "MAGICK_PATH", "")
        or ""
    ).strip()
    if env:
        if os.path.isfile(env):
            return env
        candidate = os.path.join(env, "magick.exe")
        if os.path.isfile(candidate):
            return candidate
        logger.warning("MAGICK_PATH 已设置但 magick 不存在: %s", env)

    for name in ("magick", "magick.exe"):
        found = shutil.which(name)
        if found and os.path.isfile(found) and "imagemagick" in found.lower().replace("\\", "/"):
            return found
        if found and os.path.isfile(found) and name.startswith("magick"):
            return found

    if sys.platform == "win32":
        for candidate in _windows_magick_candidates():
            return candidate

    found = shutil.which("magick") or shutil.which("magick.exe")
    if found and os.path.isfile(found):
        return found

    return None


def get_magick_path() -> Optional[str]:
    global _MAGICK_PATH_CACHE
    if _MAGICK_PATH_CACHE is None:
        _MAGICK_PATH_CACHE = resolve_magick_path()
        if _MAGICK_PATH_CACHE:
            logger.info("EMF/WMF 转 PNG 使用 ImageMagick: %s", _MAGICK_PATH_CACHE)
        else:
            logger.warning(
                "未检测到 ImageMagick（magick.exe）。Word Visio/EMF 图需 ImageMagick 或 LibreOffice；"
                "请在 .env 设置 MAGICK_PATH，或安装到 Program Files\\ImageMagick*"
            )
    return _MAGICK_PATH_CACHE


def office_convert_status() -> dict:
    path = get_soffice_path()
    magick = get_magick_path()
    return {
        "available": bool(path),
        "soffice_path": path or "",
        "magick_available": bool(magick),
        "magick_path": magick or "",
        "platform": sys.platform,
        "env_LIBREOFFICE_PATH": (os.environ.get("LIBREOFFICE_PATH") or "").strip(),
        "env_MAGICK_PATH": (os.environ.get("MAGICK_PATH") or "").strip(),
        "hint": (
            "react-doc-viewer 本身不需要 LibreOffice；"
            "当前项目仅对 PPT 采用「后端 LibreOffice 转 PDF」；Word/Excel 由前端本地预览。"
            if not path
            else "转换服务就绪"
        ),
    }


def convert_office_to_pdf(input_path: str, out_dir: Optional[str] = None) -> str:
    """本地 Office 文件（doc/docx/wps）转 PDF，供 MinerU / parser 等后端使用。"""
    if not input_path or not os.path.isfile(input_path):
        raise FileNotFoundError(input_path or "office file")
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".pdf":
        return os.path.abspath(input_path)
    if ext not in WORD_NATIVE_EXT:
        raise ValueError(f"不支持的 Office 类型: {ext}")
    out_dir = out_dir or tempfile.mkdtemp(prefix="office_pdf_out_")
    os.makedirs(out_dir, exist_ok=True)
    return _run_soffice_convert(input_path, out_dir)


# 兼容旧 import 名
convert_local_office_to_pdf = convert_office_to_pdf


def _run_soffice_convert(input_path: str, out_dir: str) -> str:
    soffice = get_soffice_path()
    if not soffice:
        raise RuntimeError(
            "未找到 LibreOffice（soffice.exe）。"
            "react-doc-viewer 组件不需要安装 LibreOffice，但本项目 PPT 预览"
            "需在后端先把文件转成 PDF（Word/Excel 为前端本地预览）。"
            "请在运行 Flask 后端的机器安装 LibreOffice，并在项目根目录 .env 中设置："
            "LIBREOFFICE_PATH=C:\\Program Files\\LibreOffice\\program\\soffice.exe"
            "（路径按实际安装位置修改），然后重启后端。"
        )
    cmd = [
        soffice,
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        "pdf",
        "--outdir",
        out_dir,
        input_path,
    ]
    logger.info("office_to_pdf: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("OFFICE_PDF_CONVERT_TIMEOUT", "180")),
        cwd=out_dir,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"LibreOffice 转换失败: {err[:500]}")
    base = os.path.splitext(os.path.basename(input_path))[0]
    pdf_path = os.path.join(out_dir, f"{base}.pdf")
    if not os.path.isfile(pdf_path):
        raise RuntimeError("LibreOffice 未生成 PDF 文件")
    return pdf_path


def _read_cache_meta(meta_path: str) -> Optional[dict]:
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_valid(meta_path: str, pdf_path: str, etag: str, size: int) -> bool:
    if not os.path.isfile(pdf_path) or not os.path.isfile(meta_path):
        return False
    meta = _read_cache_meta(meta_path)
    if not meta:
        return False
    return meta.get("etag") == etag and meta.get("size") == size


def get_or_create_preview_pdf(
    minio_client,
    bucket: str,
    object_name: str,
    etag: str = "",
    size: int = 0,
) -> str:
    """返回可用于 inline 预览的本地 PDF 路径（带缓存）。"""
    pdf_cache, meta_cache = _cache_paths(object_name)
    if _cache_valid(meta_cache, pdf_cache, etag, size):
        return pdf_cache

    with _CONVERT_LOCK:
        if _cache_valid(meta_cache, pdf_cache, etag, size):
            return pdf_cache

        ext = os.path.splitext(object_name)[1].lower()
        if ext == ".pdf":
            raise ValueError("origin file is already pdf")

        tmp_dir = tempfile.mkdtemp(prefix="wjgl_office_src_")
        try:
            local_name = f"origin{ext}"
            local_path = os.path.join(tmp_dir, local_name)
            minio_client.fget_object(bucket, object_name, local_path)

            conv_dir = tempfile.mkdtemp(prefix="wjgl_office_out_")
            try:
                produced = _run_soffice_convert(local_path, conv_dir)
                shutil.copy2(produced, pdf_cache)
            finally:
                shutil.rmtree(conv_dir, ignore_errors=True)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        with open(meta_cache, "w", encoding="utf-8") as f:
            json.dump(
                {"object_name": object_name, "etag": etag, "size": size},
                f,
                ensure_ascii=False,
            )
        return pdf_cache
