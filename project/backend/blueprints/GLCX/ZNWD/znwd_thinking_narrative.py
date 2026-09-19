"""
ZNWD 思考过程 / 检索规划 — 面向用户的中文业务语义封装。

日志与执行仍可使用技术字段；前端展示与 LLM 思考区仅使用本模块生成的自然语言。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .znwd_business_types import BusinessTemplate

# 与业务模板一致的中文实体名
LABEL_ZH: Dict[str, str] = {
    "Equipment": "设备",
    "EquipmentType": "设备类型",
    "Environment": "试验环境",
    "TestProject": "测试项目",
    "Indicator": "指标",
    "TestMethod": "测试方法",
    "File": "标准/文件",
}

_TEMPLATE_TITLE: Dict[str, str] = {
    BusinessTemplate.ENUM.value: "列举库内某类对象",
    BusinessTemplate.REL_ONE_HOP.value: "查询某一对象关联的内容",
    BusinessTemplate.REL_TWO_HOP.value: "分两步查询关联内容",
    BusinessTemplate.PROJECT_TO_METHOD.value: "按测试项目名称查方法",
    BusinessTemplate.EQUIP_DISCOVERY.value: "按试验项目关键词找相关设备",
    BusinessTemplate.DOCUMENT.value: "检索标准与章节文档",
}


def _zh(label: str) -> str:
    return LABEL_ZH.get(label or "", label or "相关信息")


def _focus(slots: Dict[str, Any], program: Dict[str, Any]) -> str:
    f = str(slots.get("focus") or program.get("focus_entity") or "").strip()
    return f


def intent_summary_user(template_id: str, slots: Dict[str, Any], program: Dict[str, Any]) -> str:
    """第 1 点：对用户意图的一句话理解（无模板代号、无英文标签）。"""
    focus = _focus(slots, program)
    from_type = str(slots.get("from_type") or "").strip()
    to_type = str(slots.get("to_type") or "").strip()
    node_type = str(slots.get("node_type") or "").strip()
    path = slots.get("path") if isinstance(slots.get("path"), list) else []

    tid = template_id or ""

    if tid == BusinessTemplate.ENUM.value:
        return f"您想了解系统中有哪些{_zh(node_type)}。"

    if tid == BusinessTemplate.DOCUMENT.value:
        return "您想查阅与标准、试验过程或章节相关的文档内容。"

    if tid == BusinessTemplate.PROJECT_TO_METHOD.value:
        return f"您想查看测试项目「{focus}」采用了哪些测试方法、具体步骤或要求。"

    if tid == BusinessTemplate.EQUIP_DISCOVERY.value:
        pf = str(slots.get("project_filter") or focus).strip()
        return f"您想找出与「{pf}」这类试验项目相关联的设备有哪些。"

    if tid == BusinessTemplate.REL_ONE_HOP.value:
        ft, tt = from_type, to_type
        if ft == "Equipment" and tt == "TestProject":
            return f"您想了解设备「{focus}」关联了哪些测试项目。"
        if ft == "Environment" and tt == "TestProject":
            return f"您想了解试验环境「{focus}」下包含哪些测试项目。"
        if ft == "Equipment" and tt == "Indicator":
            return f"您想了解设备「{focus}」关联了哪些指标。"
        if ft == "EquipmentType" and tt == "Equipment":
            return f"您想了解设备类型「{focus}」下有哪些具体设备。"
        if ft == "TestProject" and tt == "TestMethod":
            return f"您想了解测试项目「{focus}」对应的测试方法。"
        if ft == "Indicator" and tt == "TestMethod":
            return f"您想了解指标「{focus}」对应的测试方法。"
        return f"您想查看「{focus}」在{_zh(ft)}与{_zh(tt)}之间的关联信息。"

    if tid == BusinessTemplate.REL_TWO_HOP.value:
        end = _zh(path[-1]["to"]) if path and isinstance(path[-1], dict) else _zh("TestMethod")
        if from_type == "Environment":
            return (
                f"您想先根据关键词「{focus}」在试验环境侧找到相关试验项目，"
                f"再查看这些项目对应的{end}。"
            )
        if from_type == "Equipment":
            return (
                f"您想先根据关键词「{focus}」在设备侧找到相关试验项目，"
                f"再查看这些项目对应的{end}。"
            )
        return f"您想分两步：先锁定「{focus}」相关的试验项目，再查询对应的{end}。"

    raw = str(program.get("intent_summary") or "").strip()
    return raw if raw and "BIZ_" not in raw else "将根据您的问题在知识图谱与文档库中检索相关信息。"


def step_display_text(
    *,
    template_id: str,
    step_index: int,
    total_steps: int,
    op_code: str,
    from_type: str = "",
    to_type: str = "",
    focus: str = "",
    path: Optional[List[Dict[str, str]]] = None,
    node_type: str = "",
    project_filter: str = "",
) -> str:
    """单步检索意图（给用户看，不含 op_code / 模板代号）。"""
    path = path or []
    focus = (focus or project_filter or "").strip()
    i, n = step_index, max(total_steps, 1)

    if op_code == "neo_list_nodes":
        return f"第{i}步：在清单库中列举所有{_zh(node_type)}。"

    if op_code == "neo_node_query":
        if node_type == "TestProject" or from_type == "TestProject":
            return f"第{i}步：先在清单库中按名称查找测试项目「{focus}」是否存在于系统中。"
        if node_type == "Environment":
            return f"第{i}步：先在清单库中按名称查找试验环境「{focus}」是否存在于系统中。"
        return f"第{i}步：先在清单库中按关键词「{focus}」定位{_zh(node_type or from_type)}。"

    if op_code == "neo_relation_query":
        if from_type == "Environment" and to_type == "TestProject":
            if i == 1 and n >= 2:
                return (
                    f"第{i}步：在清单库中查找与「{focus}」相关的试验环境，"
                    f"并列出其关联的测试项目。"
                )
            return f"第{i}步：查找试验环境「{focus}」关联的测试项目。"
        if from_type == "Equipment" and to_type == "TestProject":
            if str(project_filter):
                return f"第{i}步：查找名称中含「{project_filter}」的测试项目所关联的设备。"
            if i == 1 and n >= 2:
                return (
                    f"第{i}步：在清单库中查找与「{focus}」相关的设备，"
                    f"并列出其关联的测试项目。"
                )
            return f"第{i}步：查找设备「{focus}」关联的测试项目。"
        if from_type == "TestProject" and to_type == "TestMethod":
            if i > 1:
                return f"第{i}步：针对上一步找到的测试项目，查询其关联的测试方法。"
            return f"第{i}步：查找测试项目「{focus}」关联的测试方法。"
        if from_type == "Equipment" and to_type == "Indicator":
            return f"第{i}步：查找设备「{focus}」关联的指标。"
        if from_type == "EquipmentType" and to_type == "Equipment":
            return f"第{i}步：查找设备类型「{focus}」下的具体设备。"
        if from_type == "Indicator" and to_type == "TestMethod":
            return f"第{i}步：查找指标「{focus}」关联的测试方法。"
        return f"第{i}步：在清单库中查询「{focus}」相关的{_zh(to_type)}。"

    if op_code == "chroma_vector_search":
        return f"第{i}步：在文档库中检索与问题相关的标准与条文片段。"
    if op_code == "chroma_rerank":
        return f"第{i}步：对检索到的文档片段按相关性重新排序。"
    if op_code == "chroma_section_expand":
        return f"第{i}步：按章节扩展阅读上下文，便于完整理解试验过程。"

    return f"第{i}步：执行检索。"


def enrich_program_display(program: Dict[str, Any]) -> Dict[str, Any]:
    """为 program 与各 step 写入用户可读字段（display_text、plan_*_nl）。"""
    if not isinstance(program, dict):
        return program

    slots = program.get("_business_slots")
    if not isinstance(slots, dict):
        slots = {}

    template_id = str(program.get("_business_template") or "").strip()
    focus = _focus(slots, program)
    steps = program.get("steps") if isinstance(program.get("steps"), list) else []
    path = slots.get("path") if isinstance(slots.get("path"), list) else []

    for i, st in enumerate(steps):
        if not isinstance(st, dict):
            continue
        pr = st.get("params") if isinstance(st.get("params"), dict) else {}
        ft = str(pr.get("from_type") or slots.get("from_type") or "").strip()
        tt = str(pr.get("to_type") or slots.get("to_type") or "").strip()
        nt = str(pr.get("node_type") or slots.get("node_type") or "").strip()
        disp = step_display_text(
            template_id=template_id,
            step_index=i + 1,
            total_steps=len(steps),
            op_code=str(st.get("op_code") or ""),
            from_type=ft,
            to_type=tt,
            focus=focus,
            path=path,
            node_type=nt,
            project_filter=str(slots.get("project_filter") or ""),
        )
        st["display_text"] = disp
        # purpose 对外展示与 display_text 一致；技术说明可留在日志
        st["purpose"] = disp

    program["intent_summary_user"] = intent_summary_user(template_id, slots, program)
    program["plan_inferred_question"] = program["intent_summary_user"]
    program["plan_what_to_retrieve_nl"] = "；".join(
        str(s.get("display_text") or "").strip()
        for s in steps
        if isinstance(s, dict) and str(s.get("display_text") or "").strip()
    )
    program["plan_retrieve_order_nl"] = " → ".join(
        str(s.get("display_text") or "").strip()
        for s in steps
        if isinstance(s, dict) and str(s.get("display_text") or "").strip()
    )
    # 兼容旧字段：intent_summary 也改为用户语义（日志仍可通过 _business_template 区分）
    if template_id:
        program["intent_summary"] = program["intent_summary_user"]

    return program


def build_retrieval_planning_text(
    program: Dict[str, Any], retrieval_question: str = ""
) -> str:
    """生成前端思考区完整的【检索规划】文案。"""
    program = enrich_program_display(program)
    iq = str(program.get("plan_inferred_question") or program.get("intent_summary_user") or "").strip()
    if not iq:
        iq = (retrieval_question or "").strip()[:200] or "将根据您的问题在知识图谱与文档库中检索。"

    what = str(program.get("plan_what_to_retrieve_nl") or "").strip()
    order = str(program.get("plan_retrieve_order_nl") or "").strip()
    if not order:
        order = "按上述步骤依次在清单库与文档库中检索。"

    template_id = str(program.get("_business_template") or "").strip()
    biz_line = ""
    if template_id:
        title = _TEMPLATE_TITLE.get(template_id, "结构化检索")
        biz_line = f"（业务类型：{title}）"

    lines = [
        "【检索规划】",
        f"1. {iq}{biz_line}",
        f"2. {what or '在清单库与文档库中查找相关信息。'}",
    ]
    if order and order != what:
        lines.append(f"3. {order}")
    return "\n".join(lines)


_THINKING_RESULT_NOISE_LINE = re.compile(
    r"(未命中|无命中|检索失败|查询失败|未找到匹配|没有(?:相关)?数据|无(?:可用)?结果|"
    r"retrieval[_\s]?gap|rows\s*=\s*\d+|命中\s*\d+\s*条|返回\s*\d+\s*条|共\s*\d+\s*条|"
    r"清单库.*未|文件库.*未|工具返回.*空|evidence.*空|检索结果已|根据检索结果)",
    re.I,
)


def strip_thinking_result_noise(text: str) -> str:
    """去掉思考区中的检索结果/失败原因句，只保留简短推理。"""
    if not text or not str(text).strip():
        return text or ""
    s = str(text)
    out_lines: List[str] = []
    for line in s.splitlines():
        raw = line.strip()
        if not raw:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            continue
        if _THINKING_RESULT_NOISE_LINE.search(raw):
            continue
        if re.match(r"^步骤\s*\d+\s*[:：].*(?:返回|条记录|未找到|未命中|rows)", raw, re.I):
            continue
        out_lines.append(line.rstrip())
    joined = "\n".join(out_lines).strip()
    if not joined:
        return ""
    parts = re.split(r"(?<=[。！？])\s*", joined)
    kept = [p.strip() for p in parts if p.strip() and not _THINKING_RESULT_NOISE_LINE.search(p)]
    return "\n".join(kept) if kept else joined


def humanize_agent_thinking_text(text: str) -> str:
    """
    将 Agent <THINK> 中的工具名、图谱标签、参数键等改写为业务中文（面向用户展示）。
    """
    if not text or not str(text).strip():
        return text or ""
    if "【检索规划】" in text:
        return text

    s = str(text)

    _TOOL_ZH = {
        "TOOL_neo_relation_name_search": "在清单库中按名称查找关联关系",
        "TOOL_neo_relation_by_ids": "在清单库中按节点编号查找关联",
        "TOOL_neo_node_name_search": "在清单库中按名称查找对象",
        "TOOL_neo_list_nodes": "在清单库中列举该类对象",
        "TOOL_chroma_vector_search": "在文档库中检索相关条文",
        "TOOL_chroma_rerank": "对文档片段按相关性排序",
        "TOOL_chroma_section_expand": "按章节扩展阅读上下文",
    }
    for eng, zh in _TOOL_ZH.items():
        s = s.replace(eng, zh)

    _REL_PATH = [
        (r"EquipmentType\s*[-–—>→]+\s*Equipment", "从设备类型查到其下的具体设备"),
        (r"Equipment\s*[-–—>→]+\s*TestProject", "从设备查到相关测试项目"),
        (r"Environment\s*[-–—>→]+\s*TestProject", "从试验环境查到相关测试项目"),
        (r"TestProject\s*[-–—>→]+\s*TestMethod", "从测试项目查到测试方法"),
        (r"Indicator\s*[-–—>→]+\s*TestMethod", "从指标查到测试方法"),
        (r"Equipment\s*[-–—>→]+\s*Indicator", "从设备查到相关指标"),
    ]
    for pat, zh in _REL_PATH:
        s = re.sub(pat, zh, s, flags=re.I)

    for label, zh in LABEL_ZH.items():
        s = re.sub(rf"\b{re.escape(label)}\b", zh, s)

    s = re.sub(
        r"name_match_side\s*=\s*['\"]?(from|to)['\"]?",
        lambda m: "用起点名称匹配" if m.group(1).lower() == "from" else "用终点名称匹配",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"name_keyword\s*=\s*['\"]([^'\"]+)['\"]",
        r"关键词为「\1」",
        s,
        flags=re.I,
    )
    s = re.sub(r"from_node_label\s*=\s*\w+", "起点类型", s, flags=re.I)
    s = re.sub(r"to_node_label\s*=\s*\w+", "终点类型", s, flags=re.I)
    s = re.sub(r"relationship_type\s*=\s*\w+", "关联关系", s, flags=re.I)
    s = re.sub(r"node_label\s*=\s*\w+", "节点类型", s, flags=re.I)
    s = re.sub(r"\bBIZ_[A-Z0-9_]+\b", "", s)
    s = re.sub(r"\bop_code\s*[:=]\s*\w+", "", s, flags=re.I)
    s = re.sub(r"@TOOL", "检索", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return strip_thinking_result_noise(s.strip())


from .znwd_prompt_snippets import LLM_THINKING_LANGUAGE_RULES  # noqa: F401 re-export

__all__ = (
    "LABEL_ZH",
    "LLM_THINKING_LANGUAGE_RULES",
    "strip_thinking_result_noise",
    "build_retrieval_planning_text",
    "enrich_program_display",
    "humanize_agent_thinking_text",
    "intent_summary_user",
    "step_display_text",
)
