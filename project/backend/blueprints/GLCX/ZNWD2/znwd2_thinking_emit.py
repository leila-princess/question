"""ZNWD2 思考区 SSE 里程碑。"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .config import instant_thinking_enabled as _instant_enabled


def instant_thinking_enabled() -> bool:
    return _instant_enabled()


def emit_milestone(text: str, *, kind: str = "system") -> Tuple[str, Dict[str, Any]]:
    line = (text or "").strip()
    if not line:
        return ("thinking", {"content": "", "kind": kind})
    if not line.endswith("\n"):
        line += "\n"
    return ("thinking", {"content": line, "kind": kind})


def sanitize_thinking_for_sse(text: str) -> str:
    return (text or "").strip()
