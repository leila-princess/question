"""ZNWD6 运行时配置（环境变量，与 ZNWD4/5 隔离）。"""

from __future__ import annotations

import os

# 路由修复版本：部署后 GET /api/znwd6/index/status 可核对 routing_fix_rev
ROUTING_FIX_REV = "20260702-equip-indicator-guard"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def grounding_score_threshold() -> float:
    raw = _env("ZNWD6_GROUNDING_SCORE_THRESHOLD", "0.35")
    try:
        return max(0.05, min(0.95, float(raw)))
    except ValueError:
        return 0.35


def grounding_max_per_span() -> int:
    raw = _env("ZNWD6_GROUNDING_MAX_PER_SPAN", "16")
    try:
        return max(3, min(32, int(raw)))
    except ValueError:
        return 16


def zvec_data_path() -> str:
    return _env("ZNWD6_ZVEC_DATA_PATH", _env("ZVEC_DATA_PATH", ""))


def use_znwd5_zvec() -> bool:
    return _env("ZNWD6_USE_ZNWD5_ZVEC", "1").lower() not in ("0", "false", "no")


def ontology_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return _env("ZNWD6_ONTOLOGY_DIR", os.path.join(here, "ontology"))
