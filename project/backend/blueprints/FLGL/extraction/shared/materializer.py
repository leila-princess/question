"""按行号 / section 取文 — 复用手动路径能力。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .line_index import LineIndex
from .schemas import ParseBundle


def slice_lines(bundle: ParseBundle, start_line: int, end_line: int) -> str:
    idx = LineIndex.build(str(bundle.get("plain_text") or ""))
    return idx.slice_lines(start_line, end_line)


def slice_modules(bundle: ParseBundle, modules: List[Dict[str, Any]]) -> Dict[str, str]:
    """按 module type 聚合切片文本。"""
    idx = LineIndex.build(str(bundle.get("plain_text") or ""))
    buckets: Dict[str, List[str]] = {
        "METHOD": [],
        "CONDITION": [],
        "EQUIPMENT": [],
        "CRITERIA": [],
    }
    for mod in modules:
        t = str(mod.get("type") or "OTHER").upper()
        if t not in buckets:
            continue
        text = idx.slice_lines(int(mod.get("start_line") or 0), int(mod.get("end_line") or 0))
        if text.strip():
            buckets[t].append(text.strip())
    return {k: "\n\n".join(v) for k, v in buckets.items() if v}
