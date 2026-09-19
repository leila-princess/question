"""复合问句 / 多意图检测 → ZNWD6 composite template_id。"""

from __future__ import annotations

import re
from typing import Optional


def _norm(q: str) -> str:
    return re.sub(r"\s+", "", (q or "").strip())


def detect_composite_template(question: str) -> Optional[str]:
    q = (question or "").strip()
    n = _norm(q)
    if not q:
        return None

    if re.search(r"(试验项目和考核指标|试验项目.*考核指标|项目.*考核指标|项目与指标|项目和指标)", n):
        return "composite_project_and_indicator"
    if re.search(r"(考核指标和试验方法|指标和试验方法|指标与试验方法|指标.*试验方法)", n):
        return "composite_indicator_then_method"
    if re.search(
        r"(请先.+试验项目.+方法|先查询.+试验项目.+方法|先告诉我.+试验项目.+方法|"
        r"试验项目和方法|测试项目和方法|项目和方法)",
        n,
    ):
        return "composite_project_then_method"
    if re.search(
        r".+环境下?有哪些试验项目.+用什么方法|"
        r".+环境环境下有哪些试验项目.+这些项目用什么方法|"
        r".+在.+环境.+有哪些试验项目.+方法|"
        r".+有哪些试验项目.+什么试验方法",
        n,
    ):
        return "composite_project_env_method"
    if re.search(r"做.+试验时的环境条件|推荐的环境设置", n):
        return "composite_env_settings"
    if re.search(r"有哪些试验项目.+什么试验方法|试验项目.+对应.*方法", n):
        return "composite_project_then_method"
    return None
