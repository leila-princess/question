"""纯 LLM Demo 输出目录与运行参数。"""
import os
from pathlib import Path

from config import Config

_BACKEND = Path(__file__).resolve().parents[4]  # backend/
_DEFAULT_ROOT = Path(getattr(Config, "SCHEME_DIR", str(_BACKEND / "schemes"))) / "pure_llm"
PURE_LLM_OUTPUT_ROOT = Path(os.environ.get("JYLLM_PURE_LLM_OUTPUT", str(_DEFAULT_ROOT)))


def task_output_dir(task_id: str) -> Path:
    d = PURE_LLM_OUTPUT_ROOT / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d
