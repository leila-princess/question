"""ZNWD2 配置（环境变量）。"""

from __future__ import annotations

import os


def instant_thinking_enabled() -> bool:
    raw = (os.environ.get("ZNWD2_INSTANT_THINKING") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def intent_model(default: str = "") -> str:
    env = (os.environ.get("ZNWD2_INTENT_MODEL") or "").strip()
    return env or (default or "").strip()


def default_query_limit() -> int:
    try:
        return max(1, min(int(os.environ.get("ZNWD2_QUERY_LIMIT", "200")), 500))
    except (TypeError, ValueError):
        return 200


def intent_temperature() -> float:
    try:
        return float(os.environ.get("ZNWD2_INTENT_TEMPERATURE", "0.1"))
    except (TypeError, ValueError):
        return 0.1
