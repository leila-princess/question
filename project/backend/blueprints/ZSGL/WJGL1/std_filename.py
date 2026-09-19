"""从文件名解析标准号/名称（GBT+38924.2-2020.pdf 等）。"""
from __future__ import annotations

import os
import re
from typing import Dict


def parse_std_metadata_from_filename(filename: str) -> Dict[str, str]:
    stem = os.path.splitext(filename or "")[0].strip()
    if not stem:
        return {"std_code": "", "std_name": "", "doc_id": ""}

    compact = re.sub(r"[+\s_]+", " ", stem).strip()
    compact = re.sub(r"\s+", " ", compact)

    m = re.match(
        r"^(?:(?P<prefix>GB[/\s]?T|GBT|GJB|HB|YY|JB|QC|DL|TJ)\s*)?"
        r"(?P<num>\d+(?:\.\d+)*\s*-\s*\d{4})"
        r"(?:\s+(?P<name>.+))?$",
        compact,
        re.I,
    )
    if m:
        prefix_raw = (m.group("prefix") or "GBT").upper().replace(" ", "")
        if prefix_raw in ("GBT", "GB/T", "GBT"):
            std_prefix = "GB/T"
        elif prefix_raw.startswith("GB"):
            std_prefix = "GB/T"
        else:
            std_prefix = prefix_raw
        num = re.sub(r"\s+", "", m.group("num") or "")
        std_code = f"{std_prefix} {num}"
        std_name = (m.group("name") or "").strip()
        doc_id = re.sub(r"[^\w\.\-]+", "_", std_code).strip("_")
        return {"std_code": std_code, "std_name": std_name, "doc_id": doc_id or stem}

    m2 = re.match(r"^(?P<code>[A-Z]{2,}[\s/\-]?\d[\w\.\-]*)(?:[_\s]+(?P<name>.+))?$", compact, re.I)
    if m2:
        std_code = re.sub(r"\s+", " ", m2.group("code").strip())
        std_name = (m2.group("name") or "").strip()
        doc_id = re.sub(r"[^\w\.\-]+", "_", std_code).strip("_")
        return {"std_code": std_code, "std_name": std_name, "doc_id": doc_id or stem}

    return {"std_code": "", "std_name": stem, "doc_id": re.sub(r"[^\w\.\-]+", "_", stem)[:64]}
