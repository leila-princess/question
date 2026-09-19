import os
import sys
import json
import tempfile
import subprocess
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCR_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_WORKER_PATH = os.path.join(OCR_DIR, "ocr_worker.py")
OCR_CONFIG_PATH = os.path.join(OCR_DIR, "ocr_config.json")

PADDLE_ENV_NAME = os.environ.get("PADDLE_ENV_NAME", "paddle_env")

_WINDOWS_CONDA_BASE = os.environ.get(
    "CONDA_BASE",
    r"C:\Users\hyx\anaconda3",
)

_DEFAULT_OCR_CONFIG: Dict[str, Any] = {
    "use_gpu": False,
    "cuda_visible_devices": None,
}

_ocr_config_cache: Optional[Dict[str, Any]] = None


def load_ocr_config() -> Dict[str, Any]:
    """读取 backend/ocr/ocr_config.json；缺失或解析失败则返回默认（CPU）。"""
    global _ocr_config_cache
    if _ocr_config_cache is not None:
        return _ocr_config_cache
    cfg = dict(_DEFAULT_OCR_CONFIG)
    if os.path.isfile(OCR_CONFIG_PATH):
        try:
            with open(OCR_CONFIG_PATH, "r", encoding="utf-8") as f:
                merged = {**cfg, **json.load(f)}
                cfg = merged
        except Exception as e:
            logger.warning("读取 %s 失败，使用默认 OCR 配置: %s", OCR_CONFIG_PATH, e)
    _ocr_config_cache = cfg
    return cfg


def reload_ocr_config() -> None:
    """修改 ocr_config.json 后可调用以重新加载（一般进程内不需要）。"""
    global _ocr_config_cache
    _ocr_config_cache = None


def _resolve_use_gpu(explicit: Optional[bool]) -> bool:
    """
    explicit 非 None 时以调用方为准；
    否则看环境变量 OCR_USE_GPU，再看 ocr_config.json 的 use_gpu（默认 false = CPU）。
    """
    if explicit is not None:
        return bool(explicit)
    env = os.environ.get("OCR_USE_GPU", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return bool(load_ocr_config().get("use_gpu", False))


def _subprocess_env(base: Optional[Dict[str, str]] = None, *, use_gpu: bool) -> Dict[str, str]:
    env = dict(base or os.environ)
    if use_gpu:
        dev = load_ocr_config().get("cuda_visible_devices")
        if dev is not None and str(dev).strip() != "":
            env["CUDA_VISIBLE_DEVICES"] = str(dev).strip()
    return env


def _get_python_exe() -> str:
    if sys.platform == "win32":
        return os.path.join(_WINDOWS_CONDA_BASE, "envs", PADDLE_ENV_NAME, "python.exe")
    return os.path.join("/", "opt", "conda", "envs", PADDLE_ENV_NAME, "bin", "python")


def _run_ocr_worker(action: str, **kwargs) -> Dict[str, Any]:
    if "file_path" in kwargs and not os.path.isabs(kwargs["file_path"]):
        kwargs["file_path"] = os.path.abspath(kwargs["file_path"])
    if "output_dir" in kwargs and not os.path.isabs(kwargs["output_dir"]):
        kwargs["output_dir"] = os.path.abspath(kwargs["output_dir"])

    run_timeout = int(kwargs.pop("timeout", 600))
    explicit_gpu = kwargs.pop("use_gpu", None)
    use_gpu = _resolve_use_gpu(explicit_gpu)

    command = {
        "action": action,
        "use_gpu": use_gpu,
        **kwargs,
    }

    cmd_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(command, f, ensure_ascii=False)
            cmd_path = f.name

        python_exe = _get_python_exe()
        if not os.path.exists(python_exe):
            logger.error(f"paddle_env python not found at: {python_exe}")
            return {"success": False, "error": f"paddle_env python not found: {python_exe}"}

        child_env = _subprocess_env(os.environ.copy(), use_gpu=use_gpu)

        result = subprocess.run(
            [python_exe, OCR_WORKER_PATH, cmd_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=run_timeout,
            env=child_env,
            cwd=PROJECT_ROOT,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout_tail = (result.stdout or "")[-4000:]
            logger.error(
                "OCR worker failed (exit %s): stderr=%s stdout_tail=%s",
                result.returncode,
                stderr[:2000],
                stdout_tail,
            )
            return {"success": False, "error": stderr or f"exit {result.returncode}"}

        stdout = result.stdout.strip()
        if not stdout:
            return {"success": False, "error": "OCR worker returned empty output"}

        return json.loads(stdout)

    except subprocess.TimeoutExpired:
        logger.error("OCR worker timed out")
        return {"success": False, "error": "OCR processing timed out"}
    except Exception as e:
        logger.error(f"OCR worker subprocess error: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if cmd_path:
            try:
                os.unlink(cmd_path)
            except Exception:
                pass


def run_ocr_pdf(
    file_path: str,
    original_filename: str = None,
    output_dir: str = "outputs",
    use_gpu: Optional[bool] = None,
    dpi: int = 300,
    timeout: int = 600,
    start_page: int = 1,
    end_page: Optional[int] = None,
    use_layout: Optional[bool] = None,
) -> Dict[str, Any]:
    return _run_ocr_worker(
        "ocr_pdf",
        file_path=file_path,
        original_filename=original_filename,
        output_dir=output_dir,
        use_gpu=use_gpu,
        dpi=dpi,
        timeout=timeout,
        start_page=start_page,
        end_page=end_page,
        use_layout=use_layout,
    )


def run_ocr_image(image_path: str, use_gpu: Optional[bool] = None) -> Dict[str, Any]:
    return _run_ocr_worker("ocr_image", image_path=image_path, use_gpu=use_gpu)


def run_ocr_ics_ccs(file_path: str, use_gpu: Optional[bool] = None) -> Dict[str, Any]:
    return _run_ocr_worker("ocr_ics_ccs", file_path=file_path, use_gpu=use_gpu)


def run_extract_images_only(
    file_path: str,
    output_dir: str = "outputs",
    original_filename: str = None,
    use_gpu: Optional[bool] = None,
    dpi: int = 300,
    timeout: int = 300,
) -> Dict[str, Any]:
    return _run_ocr_worker(
        "extract_images_only",
        file_path=file_path,
        output_dir=output_dir,
        original_filename=original_filename,
        use_gpu=use_gpu,
        dpi=dpi,
        timeout=timeout,
    )
