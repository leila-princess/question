"""方法 SYNR 抛光：重编号、结构化、LaTeX。"""
from blueprints.FLGL.method_section_enrich import (
    format_latex_in_html_content,
    polish_method_synr_for_editor,
    polish_method_synr_structure,
)

SAMPLE_BR_MESS = (
    '<h4 class="wjgl-section-heading" data-section-no="1"><strong>1 试验目的</strong></h4>'
    "<p>（1）设备与仪器配置<br>"
    "1.试验箱:采用高低温低气压试验箱<br>"
    "（2）样品状态设置<br>"
    "1）无人机按实际飞行状态安装<br>"
    "8.2.2 试验条件<br>"
    "（1）功能与性能初检</p>"
)


def test_polish_restructures_br_mess():
    out = polish_method_synr_structure(SAMPLE_BR_MESS)
    assert "wjgl-section-heading" in out
    assert out.count("<br>") < SAMPLE_BR_MESS.count("<br>")
    assert "（1）" in out
    assert "8.2.2" not in out or "wjgl-section-heading" in out


def test_polish_latex_in_html():
    raw = "<p>公式 $E=mc^2$ 与块级 $$\\frac{a}{b}$$</p>"
    out = format_latex_in_html_content(raw)
    assert "$E=mc^2$" in out
    assert "$$" in out


def test_polish_trace_with_full_text():
    html = "<p>按8.2.1规定进行试验。</p>"
    full = "8.2.1 试验目的\n低温试验要求。\n8.2.2 试验条件\n温度-40℃。"
    out = polish_method_synr_for_editor(html, full_text=full, force_restructure=False)
    assert "溯源" in out or "8.2.1" in out
