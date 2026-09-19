"""Word LLM 计划验收：扩展名修复 + 三项校验 + 规则别名。"""
import os
import shutil
import sys
import tempfile
import uuid
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from blueprints.FLGL.method_routes import _ensure_filepath_extension, _safe_filename
from blueprints.FLGL.GetTestMethod import word_llm_upload_extract as w


def test_safe_filename_preserves_extension():
    for name in ("Matrice350试验方案.docx", "试验方案.docx", "test.docx", "docx"):
        safe = _safe_filename(name)
        ext = os.path.splitext(safe)[1].lower()
        assert ext == ".docx", f"{name!r} -> {safe!r}"


def test_ensure_filepath_extension_renames():
    tmpdir = tempfile.mkdtemp()
    try:
        bad_path = os.path.join(tmpdir, "docx")
        with open(bad_path, "wb") as f:
            f.write(b"PK")
        fixed = _ensure_filepath_extension(bad_path, ".docx")
        assert fixed.endswith(".docx")
        assert os.path.isfile(fixed)
    finally:
        shutil.rmtree(tmpdir)


def test_resolve_upload_file_with_proper_ext():
    tmpdir = tempfile.mkdtemp()
    try:
        good = os.path.join(tmpdir, f"upload_{uuid.uuid4().hex[:8]}.docx")
        with zipfile.ZipFile(good, "w") as z:
            z.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
            )
            z.writestr(
                "word/document.xml",
                '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>test</w:t></w:r></w:p></w:body></w:document>",
            )
        resolved = w.resolve_upload_file(good)
        assert os.path.isfile(resolved)
    finally:
        shutil.rmtree(tmpdir)


def test_rule_parse_trial_plan_sections():
    text = """3.1 最大起飞重量试验
试验条件
温度25度，风速不超过5m/s。
试验过程
步骤一：加载配重至最大起飞重量。
步骤二：执行起飞。
结果评定
满足设计要求即为合格。
"""
    secs = w._parse_project_sections_rule(text)
    assert secs["sytj"]["text"]
    assert secs["syff"]["text"]
    assert secs["jgpd"]["text"]
    method = w._attach_recall_alias(
        {
            "name": "最大起飞重量试验_整体试验",
            **{k: secs.get(k, {"text": ""}) for k in w.METHOD_SECTION_KEYS},
        }
    )
    assert w._method_has_core_content(method)


def test_method_has_min_content_one_slot():
    method = w._attach_recall_alias(
        {
            "name": "仅过程",
            "sytj": {"text": ""},
            "syff": {"text": "步骤一执行起飞。"},
            "jgpd": {"text": ""},
        }
    )
    assert w._method_has_min_content(method)
    slots = w._method_core_slots(method)
    assert slots["filled"] == ["syff"]
    assert "sytj" in slots["missing"]
    assert "jgpd" in slots["missing"]
    assert not slots["complete"]


def test_method_has_min_content_all_three():
    method = w._attach_recall_alias(
        {
            "name": "三项齐全",
            "sytj": {"text": "温度25度。"},
            "syff": {"text": "步骤一执行。"},
            "jgpd": {"text": "合格。"},
        }
    )
    assert w._method_has_min_content(method)
    slots = w._method_core_slots(method)
    assert slots["complete"]
    assert not slots["missing"]


def test_rule_parse_with_alias_headings():
    text = """3.2 续航试验
测试条件
环境温度25℃，湿度60%。
实施程序
按标准流程执行续航飞行。
合格判据
续航时间不低于30分钟。
"""
    secs = w._parse_project_sections_rule(text)
    assert "25" in str(secs["sytj"].get("text", ""))
    assert "续航" in str(secs["syff"].get("text", ""))
    assert "30" in str(secs["jgpd"].get("text", ""))


def test_mandatory_rule_fallback_on_hallucination():
    text = """试验条件
温度25度。
试验过程
步骤一执行。
结果评定
合格标准原文。
"""
    secs = w._parse_project_sections_rule(text)
    method = w._attach_recall_alias(
        {
            "name": "t",
            **{k: secs.get(k, {"text": ""}) for k in w.METHOD_SECTION_KEYS},
        }
    )
    method["jgpd"] = {"text": "编造的评定不在原文"}
    fixed = w._apply_mandatory_sections_from_rule(method, secs, text)
    assert "合格标准原文" in str(fixed["jgpd"].get("text", ""))


def test_rebalance_symd_condition_to_sytj_and_fill_syff():
    block = (
        "8.2.1 试验目的\n"
        "（1）设备与仪器配置\n"
        "1）试验箱:采用高低温低气压试验箱,需满足以下条件:\n"
        "温控范围:-40℃~70℃,精度±1℃;\n"
        "8.2.2 试验条件\n"
        "温控范围:-40℃~70℃,精度±1℃;\n"
        "8.2.3 试验方法\n"
        "（1）低温试验（非工作状态）\n"
        "降温:以≤1℃/min速率将试验箱温度降至-20℃;\n"
        "保温:维持6小时或至温度稳定;\n"
        "8.2.5 试验结果评判\n"
        "（1）功能复检\n"
        "重复试验前检测项目。\n"
    )
    rule_secs = w._parse_project_sections_rule(block)
    method = w._attach_recall_alias({
        "name": "温度和高度试验_低温试验（非工作状态）",
        "method_short_name": "低温试验（非工作状态）",
        "symd": {"text": "（1）设备与仪器配置\n1）试验箱:采用高低温低气压试验箱\n温控范围:-40℃~70℃"},
        "sytj": {"text": "温控范围:-40℃~70℃,精度±1℃;"},
        "syff": {"text": ""},
        "jgpd": {"text": ""},
    })
    fixed = w._rebalance_llm_method_sections(method, rule_secs, block_text=block)
    symd = str(fixed.get("symd", {}).get("text", ""))
    syff = str(fixed.get("syff", {}).get("text", ""))
    sytj = str(fixed.get("sytj", {}).get("text", ""))
    assert not symd.strip()
    assert "试验箱" in sytj or "温控" in sytj
    assert "降温" in syff
    synr = w.build_method_synr(fixed)
    assert "1 试验目的" in synr
    assert "2 试验条件" in synr
    assert "3 试验方法" in synr
    assert "4 试验数据处理" in synr
    assert "5 结果评定" in synr


def test_filter_procedural_sub_method_titles():
    raw = [
        "阶段1：试验准备",
        "阶段2：速度边界验证",
        "返程测试（逆风方向）",
        "结果计算与判定",
        "低温试验（非工作状态）",
        "喷淋试验",
    ]
    filtered = w._filter_sub_method_titles(raw)
    assert filtered == ["低温试验（非工作状态）", "喷淋试验"]
    assert w._is_procedural_sub_method_title("阶段1：试验准备")
    assert not w._is_procedural_sub_method_title("低温试验（非工作状态）")


def test_build_synr_always_five_sections():
    method = w._attach_recall_alias({
        "name": "示例",
        "symd": {"text": ""},
        "sytj": {"text": "温度25度。"},
        "syff": {"text": "执行步骤。"},
        "sjsjcl": {"text": ""},
        "jgpd": {"text": "合格。"},
    })
    synr = w.build_method_synr(method)
    for i, label in enumerate(
        ("试验目的", "试验条件", "试验方法", "试验数据处理", "结果评定"),
        start=1,
    ):
        assert f"{i} {label}" in synr


def test_slice_project_block_includes_823_syff():
    """8.2 块须含 8.2.3 试验方法，不能在 8.2.3 标题处截断。"""
    text = (
        "8.2 温度和高度试验\n"
        "8.2.1 试验目的\n"
        "（1）设备与仪器配置\n"
        "1）试验箱:采用高低温低气压试验箱\n"
        "8.2.2 试验条件\n"
        "（1）功能与性能初检\n"
        "8.2.3 试验方法试验按照低温试验、高温试验的顺序依次开展。\n"
        "（1）低温试验（非工作状态）\n"
        "降温:以≤1℃/min速率将试验箱温度降至-20℃;\n"
        "8.2.5 试验结果评判/评定\n"
        "（1）功能复检\n"
        "8.3 防水性试验\n"
    )
    block = w.slice_project_block_by_section_hint(text, "8.2")
    assert "8.2.3" in block
    assert "降温" in block
    assert "8.3" not in block
    rule = w._parse_project_sections_rule(block)
    assert len(str(rule["syff"].get("text") or "")) > 20
    subs = w._parse_sub_methods_rule(rule["syff"].get("text", ""))
    assert any("低温" in s["name"] for s in subs)
    matched = w._match_sub_method_syff("低温试验（非工作状态）", rule)
    assert "降温" in matched


def test_build_synr_from_native_html_matrice():
    import json
    import os
    doc_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "output_json",
        "word_llm_auto",
        "Matrice_350_RTK",
        "parsed_document.json",
    )
    if not os.path.isfile(doc_path):
        return
    with open(doc_path, encoding="utf-8") as f:
        parsed_doc = json.load(f)
    method = w._attach_recall_alias({
        "test_project_name": "温度和高度试验",
        "method_short_name": "低温试验（非工作状态）",
        "section_hint": "8.2",
        "symd": {"text": "fallback"},
        "sytj": {"text": ""},
        "syff": {"text": ""},
        "sjsjcl": {"text": ""},
        "jgpd": {"text": ""},
    })
    synr = w.build_method_synr_from_native_html(method, parsed_doc)
    assert "1 试验目的" in synr
    assert "2 试验条件" in synr
    assert "3 试验方法" in synr
    assert "8.2.1" not in synr
    assert "试验箱" in synr or "降温" in synr
    assert "降温" in synr or "非工作状态" in synr
    assert "fallback" not in synr
    assert "<br" not in synr.lower()


def test_polish_native_outline_synr_strips_821():
    raw = (
        '<p class="wjgl-section-heading" data-section-no="1"><strong>1 试验目的</strong></p>'
        '<p class="wjgl-section-heading" data-section-no="8.2.1"><strong>8.2.1 试验目的</strong></p>'
        '<p>（1）设备配置</p><p>1）试验箱：采用试验箱</p>'
        '<p class="wjgl-section-heading" data-section-no="2"><strong>2 试验条件</strong></p>'
        '<p>（1）初检</p>'
    )
    try:
        from blueprints.FLGL.method_section_enrich import polish_native_outline_synr
    except ImportError:
        from method_section_enrich import polish_native_outline_synr
    out = polish_native_outline_synr(raw)
    assert "8.2.1" not in out
    assert "1 试验目的" in out
    assert "2 试验条件" in out


def test_map_gb_outline_and_paren_renumber():
    try:
        from blueprints.FLGL.method_section_enrich import polish_method_synr_for_editor
    except ImportError:
        from method_section_enrich import polish_method_synr_for_editor
    sample = """8.2.1 试验目的
（1）设备与仪器配置
1）试验箱：采用高低温低气压试验箱
（1）样品状态设置
1）无人机按实际飞行状态安装
8.2.2 试验条件
（1）功能与性能初检
（1）外观与结构检查
"""
    out = polish_method_synr_for_editor(sample, force_restructure=True)
    assert "8.2.1" not in out
    assert "1 试验目的" in out
    assert "2 试验条件" in out
    assert "1 ）试验箱" not in out
    assert out.count("（1）") <= 2
    assert "（2）样品" in out or "（2）" in out


if __name__ == "__main__":
    test_safe_filename_preserves_extension()
    test_ensure_filepath_extension_renames()
    test_resolve_upload_file_with_proper_ext()
    test_rule_parse_trial_plan_sections()
    test_method_has_min_content_one_slot()
    test_method_has_min_content_all_three()
    test_rule_parse_with_alias_headings()
    test_mandatory_rule_fallback_on_hallucination()
    print("ALL TESTS PASSED")
