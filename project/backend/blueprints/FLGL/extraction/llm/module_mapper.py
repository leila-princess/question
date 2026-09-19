"""语义模块 → blocks + EQUIPMENT inline 融合。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..shared.line_index import LineIndex
from ..shared.materializer import slice_modules
from ..shared.schemas import EquipmentDisplayMode, MethodBlocks, ParseBundle, SemanticModule


def _equipment_inline_block(text: str, *, mode: EquipmentDisplayMode) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if mode == "separate_reference":
        return f"【试验仪器设备】\n{t}"
    return f"【使用设备】{t}"


def modules_to_blocks(
    bundle: ParseBundle,
    modules: List[SemanticModule],
    *,
    equipment_display_mode: EquipmentDisplayMode = "inline",
) -> MethodBlocks:
    idx = LineIndex.build(str(bundle.get("plain_text") or ""))
    mod_dicts = [dict(m) for m in modules]
    sliced = slice_modules(bundle, mod_dicts)

    sytj_parts: List[str] = []
    sygc_parts: List[str] = []
    jgpd_parts: List[str] = []
    equipment_hints: List[Dict[str, Any]] = []

    for m in modules:
        t = str(m.get("type") or "").upper()
        text = idx.slice_lines(int(m.get("start_line") or 0), int(m.get("end_line") or 0)).strip()
        if not text:
            continue
        ic = str(m.get("inline_criteria") or "").strip()
        if t == "CONDITION":
            sytj_parts.append(text)
        elif t == "METHOD":
            sygc_parts.append(text)
            if ic:
                jgpd_parts.append(ic)
        elif t == "CRITERIA":
            jgpd_parts.append(text)
        elif t == "EQUIPMENT":
            equipment_hints.append({
                "text": text,
                "source_lines": [m.get("start_line"), m.get("end_line")],
                "module_id": m.get("module_id"),
            })
            inline = _equipment_inline_block(text, mode=equipment_display_mode)
            if sytj_parts:
                sytj_parts.append(inline)
            elif sygc_parts:
                sygc_parts.insert(0, inline)
            else:
                sytj_parts.append(inline)

    # 汇总 sliced 兜底
    if sliced.get("CONDITION") and not sytj_parts:
        sytj_parts.append(sliced["CONDITION"])
    if sliced.get("METHOD") and not sygc_parts:
        sygc_parts.append(sliced["METHOD"])
    if sliced.get("CRITERIA") and not jgpd_parts:
        jgpd_parts.append(sliced["CRITERIA"])

    return MethodBlocks(
        sytj="\n\n".join(sytj_parts),
        sygc="\n\n".join(sygc_parts),
        jgpd="\n\n".join(jgpd_parts),
        equipment_hints=equipment_hints,
    )
