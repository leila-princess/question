"""PaddleOCR / PPStructure 初始化兼容（2.x 与 3.x API 差异）。"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional


def _env_use_layout() -> bool:
    raw = os.environ.get("OCR_USE_LAYOUT", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    # Windows 上 PPStructure 常因 zlibwapi.dll 等原生依赖崩溃，默认纯 OCR
    return sys.platform != "win32"


def create_paddle_ocr(
    *,
    use_gpu: bool = False,
    lang: str = "ch",
    rec_batch_num: int = 4,
    **extra: Any,
):
    from paddleocr import PaddleOCR

    kwargs = {
        "use_angle_cls": True,
        "lang": lang,
        "rec_batch_num": rec_batch_num,
        **extra,
    }
    try:
        return PaddleOCR(use_gpu=use_gpu, **kwargs)
    except (TypeError, ValueError) as e:
        msg = str(e).lower()
        if "use_gpu" not in msg and "unknown argument" not in msg:
            raise

    # PaddleOCR 3.x：去掉 2.x 专有参数后重试
    for key in ("use_gpu", "rec_batch_num", "use_angle_cls"):
        kwargs.pop(key, None)
    try:
        return PaddleOCR(**kwargs)
    except (TypeError, ValueError):
        return PaddleOCR(lang=lang)


def create_pp_structure(*, use_gpu: bool = False, use_layout: Optional[bool] = None):
    if use_layout is False or (use_layout is None and not _env_use_layout()):
        return None
    try:
        from paddleocr import PPStructure
    except ImportError:
        return None
    try:
        return PPStructure(use_gpu=use_gpu)
    except (TypeError, ValueError):
        try:
            return PPStructure()
        except Exception:
            return None
