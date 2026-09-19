"""extraction 包单元测试。"""
from __future__ import annotations

import re

import pytest

from blueprints.FLGL.extraction.llm.chunker import plan_anatomy_chunks
from blueprints.FLGL.extraction.llm.module_mapper import modules_to_blocks
from blueprints.FLGL.extraction.shared.formatter import (
    blocks_to_synr_html,
    completeness_check,
    plain_text_to_method_html,
)
from blueprints.FLGL.extraction.shared.line_index import LineIndex
from blueprints.FLGL.extraction.shared.parse_bundle import bundle_from_extract_context


SAMPLE_823 = """8.2.3 试验步骤
试验按照低温试验、低温低气压工作、高温试验、高温低气压工作的顺序依次开展。
（1）低温试验（非工作状态）：以≤1℃/min速率将试验箱温度降至-20℃；
（2）低温低气压工作测试：通电负载，降压至41kPa，持续2小时；
8.3.1 试验准备（防水性试验）
试验设备：XXX型淋雨试验箱，水压80--100kPa。
样品固定于试验台，倾斜15°放置。
8.3.3 试验步骤
按照喷淋试验、跌水试验的顺序开展。
喷淋时水压100kPa，持续10分钟。"""


def test_line_index_slice_and_numbered():
    idx = LineIndex.build(SAMPLE_823)
    assert idx.line_count >= 8
    assert "低温试验" in idx.slice_lines(1, 3)
    numbered = idx.format_line_numbered(1, 3)
    assert "[1]" in numbered
    assert "[3]" in numbered


def test_plan_chunks_at_section_breaks():
    idx = LineIndex.build(SAMPLE_823)
    chunks = plan_anatomy_chunks(idx, max_chars=5000)
    assert len(chunks) >= 1
    assert chunks[0].content_start_line == 1


def test_bundle_from_context():
    ctx = {"full_text": SAMPLE_823, "parse_cache": {}, "source": "test"}
    bundle = bundle_from_extract_context(ctx, file_name="t.docx")
    assert bundle["plain_text"]
    assert "8.2.3" in bundle["plain_text"]


def test_completeness_partial():
    comp = completeness_check({"sytj": "", "sygc": "步骤", "jgpd": ""})
    assert comp["missing_status"] == "partial"
    assert "sytj" in comp["missing_blocks"]
    assert "jgpd" in comp["missing_blocks"]


def test_modules_to_blocks_equipment_inline():
    bundle = bundle_from_extract_context({"full_text": SAMPLE_823, "parse_cache": {}})
    idx = LineIndex.build(bundle.get("plain_text") or "")
    equip_ln = next(i for i in range(1, idx.line_count + 1) if "淋雨试验箱" in idx.line_text(i))
    cond_ln = next(i for i in range(1, idx.line_count + 1) if "样品固定" in idx.line_text(i))
    method_start = next(i for i in range(1, idx.line_count + 1) if "按照喷淋" in idx.line_text(i))
    modules = [
        {"module_id": "m1", "type": "EQUIPMENT", "title": "淋雨箱", "start_line": equip_ln, "end_line": equip_ln, "confidence": 0.9},
        {"module_id": "m2", "type": "CONDITION", "title": "样品", "start_line": cond_ln, "end_line": cond_ln, "confidence": 0.9},
        {"module_id": "m3", "type": "METHOD", "title": "步骤", "start_line": method_start, "end_line": method_start + 1, "confidence": 0.9},
    ]
    blocks = modules_to_blocks(bundle, modules, equipment_display_mode="inline")
    assert "【使用设备】" in blocks["sytj"]
    assert "淋雨试验箱" in blocks["sytj"]
    assert blocks["sygc"]
    assert blocks.get("equipment_hints")


def test_plain_text_br_not_escaped():
    raw = "步骤一<br>步骤二"
    html = plain_text_to_method_html(raw)
    assert "&lt;br&gt;" not in html
    assert "步骤" in html


def test_formatter_chaos_newlines():
    base = "8.3.1 试验目的\n\n（1）设备\n设备列表。"
    variants = [
        base,
        base.replace("\n\n", "\n"),
        base + "  ",
        "  " + base,
        base.replace("。", "。\n"),
    ]
    for v in variants:
        html = plain_text_to_method_html(v)
        assert "8.3.1" in html or "试验目的" in html
        assert "&lt;br&gt;" not in html
