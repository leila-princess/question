"""LLM 提取：分散/整合/单一组织形态判断。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import (
    _heuristic_organization_mode,
    _normalize_organization_mode,
)


def test_normalize_organization_mode_aliases():
    assert _normalize_organization_mode("dispersed") == "dispersed"
    assert _normalize_organization_mode("scattered") == "dispersed"
    assert _normalize_organization_mode("integrated") == "integrated"
    assert _normalize_organization_mode("single_method") == "single_method"


def test_heuristic_dispersed_when_multiple_projects_have_triple():
    text = """3.1 高温试验
试验条件
环境要求A
试验过程
步骤A
结果评定
合格

3.2 低温试验
试验条件
环境要求B
试验过程
步骤B
结果评定
合格
"""
    parsed = {
        "sections": [
            {
                "section_id": "S1",
                "title": "3.1 高温试验",
                "level": 2,
                "start_offset": 0,
                "end_offset": 40,
                "text": "试验条件\n环境要求A\n试验过程\n步骤A\n结果评定\n合格",
            },
            {
                "section_id": "S2",
                "title": "3.2 低温试验",
                "level": 2,
                "start_offset": 40,
                "end_offset": len(text),
                "text": "试验条件\n环境要求B\n试验过程\n步骤B\n结果评定\n合格",
            },
        ],
    }
    assert _heuristic_organization_mode(parsed, text) == "dispersed"


def test_heuristic_integrated_when_shared_sections():
    text = """试验目的
说明

试验方法
（1）项目A
步骤A
（2）项目B
步骤B

试验条件
统一条件

结果评定
统一评定
"""
    parsed = {"sections": [{"section_id": "S1", "title": "试验方法", "level": 1, "text": text}]}
    assert _heuristic_organization_mode(parsed, text) == "integrated"
