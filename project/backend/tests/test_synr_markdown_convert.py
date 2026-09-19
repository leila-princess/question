"""Markdown 混排 → HTML 转换测试。"""
from blueprints.FLGL.method_section_enrich import (
    _has_markdown_artifacts,
    _plain_text_to_method_html,
    polish_method_synr_structure,
)

WATERPROOF_SAMPLE = """1 试验条件
喷水角度:±60°可调摆管（覆盖无人机所有表面）;
水压范围:80-100 kPa,喷水量≥10L/min;
水温控制:25±5℃（避免冷凝影响）。
2 试验方法
## 1）参数设置如表6所示。
表 6 喷淋试验参数
序号参数参数值

水压100kPa

喷水量10L/min

持续时间10分钟（IP55等级）

喷淋角度摆管±60°覆盖无人机全表面

## 2）操作流程无人机通电运行（悬停模拟状态）;
启动摆管喷淋,计时结束后立即断电。
3 试验数据处理
（1）排水处理:
无人机置于倾斜支架（15°）,开启排水孔（如有）,自然排水≥30分钟。
4 结果评定
被试设备在各项环境试验前、后的外观和结构检查结果满足要求时为合格。
"""


def test_has_markdown_artifacts():
    assert _has_markdown_artifacts(WATERPROOF_SAMPLE)
    assert _has_markdown_artifacts("## 1）参数设置")


def test_plain_text_converts_hash_and_table():
    html = _plain_text_to_method_html(WATERPROOF_SAMPLE)
    assert "wjgl-section-heading" in html
    assert "## " not in html
    assert "<table" in html
    assert "（1）" in html
    assert "表 6" in html or "喷淋试验参数" in html


def test_polish_mixed_markdown():
    out = polish_method_synr_structure(WATERPROOF_SAMPLE)
    assert "## " not in out
    assert "<table" in out


STRUCTURED_HTML_SAMPLE = """
<h4 class="wjgl-section-heading" data-section-no="1"><strong>1 试验条件</strong></h4>
<p>试验设备与配置如表16所示。</p>
<p><strong>表 16 最大水平飞行速度试验参数</strong></p>
<table border="1"><thead><tr><th>序号</th><th>设备名称</th></tr></thead>
<tbody><tr><td>1</td><td>高精度定位系统</td></tr></tbody></table>
<h4 class="wjgl-section-heading" data-section-no="2"><strong>2 试验方法</strong></h4>
<p>试验步骤与操作如表17所示。</p>
""".strip()


def test_structured_html_not_reparsed_as_markdown():
    from blueprints.FLGL.method_section_enrich import _is_structured_synr_html

    assert _is_structured_synr_html(STRUCTURED_HTML_SAMPLE)
    assert not _has_markdown_artifacts(STRUCTURED_HTML_SAMPLE)
    out = polish_method_synr_structure(STRUCTURED_HTML_SAMPLE)
    assert out.count("<table") == 1
    assert "步骤操作数据记录" not in out
    assert "wjgl-section-heading" in out
    assert "1 试验条件" in out
