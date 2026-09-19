"""国军标/国标试验标准 — 语义树模板与规则抽取。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 典型 GJB / GB/T 38924 / 150 系列语义树（供 LLM 与规则 fallback 共用）
GJB_GBT_SEMANTIC_TEMPLATE = """
## 国军标/国标试验标准语义树模板

根节点 ST_ROOT = **试验对象/装备**（非标准号），例如：
- 「民用轻小型无人机系统环境试验方法 第4部分：温度和高度试验」→ 根：**民用轻小型无人机系统** 或 **无人机**
- 「汽车最低稳定车速试验方法」→ 根：**汽车**

一级子节点（按语义角色，非逐条目录复制）：
1. **ST_GENERAL 通用** — 范围、规范性引用、术语和定义、符号
2. **ST_TEST 试验主题** — 从标准名/part 提取，如「低温试验」「温度冲击」「振动」；38924 每一 part 通常一个主题
   - ST_TEST_COND — 试验条件、严酷等级、预处理、试件状态
   - ST_TEST_PROC — 试验程序、试验步骤、操作要求
   - ST_TEST_EVAL — 结果判定、合格判据、数据处理
3. **ST_APPENDIX 附录** — 资料性/规范性附录

示例（无人机 + 低温）：
```
无人机 [ST_ROOT, graph_link_hint=无人机]
├── 通用要求 [ST_GENERAL] ← 范围、引用、术语
├── 低温试验 [ST_TEST]
│   ├── 试验条件 [ST_TEST_COND]
│   ├── 试验过程 [ST_TEST_PROC]
│   └── 结果评定 [ST_TEST_EVAL]
└── 附录 [ST_APPENDIX]
```
"""

_ROLE_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("general", re.compile(r"范围|规范性引用|引用标准|术语|定义|符号|缩略语|前言|引言|一般要求", re.I)),
    ("appendix", re.compile(r"附录|附\s*录", re.I)),
    ("cond", re.compile(r"试验条件|严酷等级|预处理|试件|状态|条件|温度.*高度|高度.*温度", re.I)),
    ("proc", re.compile(r"试验步骤|试验程序|程序|步骤|操作方法|实施|进行", re.I)),
    ("eval", re.compile(r"结果|判定|评定|判据|合格|数据处理|报告", re.I)),
    ("method", re.compile(r"试验方法|测试方法|检验方法|方法$", re.I)),
]

_TEST_TOPIC_RE = re.compile(
    r"(低温|高温|温度冲击|湿热|振动|冲击|盐雾|砂尘|淋雨|霉菌|高度|气压|太阳辐射|"
    r"加速度|风压|浸渍|加速度|可靠性|稳定性|最低稳定车速|道路试验)[试验方法]*"
)

_EQUIPMENT_RE = re.compile(
    r"([\u4e00-\u9fff]{2,20}(?:无人机|飞机|汽车|车辆|导弹|雷达|舰船|卫星|系统|装备|地面站|控制器))"
)


def infer_equipment_name(std_name: str, abstract: str = "") -> str:
    text = f"{std_name} {abstract}"
    m = _EQUIPMENT_RE.search(text)
    if m:
        return m.group(1)
    # GB/T 38924 系列
    if "无人机" in text:
        return "民用轻小型无人机系统" if "轻小型" in text or "38924" in text else "无人机"
    if "汽车" in text or "车辆" in text or "motor" in text.lower():
        return "汽车"
    # 去掉标准号后的名称主体
    cleaned = re.sub(r"GB[/／\s]*T?\s*[\d\.]+(?:\-\u2014\d)*", "", std_name)
    cleaned = re.sub(r"GJB\s*[\d\.]+(?:\-\u2014\d)*", "", cleaned)
    cleaned = re.sub(r"第[一二三四五六七八九十\d]+部分[：:]", "", cleaned)
    cleaned = re.sub(r"[\d\.]+(?:\-\u2014)[\d\.]+", "", cleaned).strip(" \u2014-：:")
    return cleaned[:40] if cleaned else std_name or "文档"


def infer_test_topic(std_name: str, sections: List[Dict[str, Any]]) -> str:
    part = re.search(r"第[一二三四五六七八九十\d]+部分[：:\s]*([^\r\n]+)", std_name)
    if part:
        topic = part.group(1).strip()
        if topic and len(topic) <= 20:
            return topic if "试验" in topic else topic + "试验"
    m = _TEST_TOPIC_RE.search(std_name)
    if m:
        g = m.group(1)
        return g + ("试验" if "试验" not in g else "")
    for s in sections:
        title = s.get("title") or ""
        tm = _TEST_TOPIC_RE.search(title)
        if tm:
            g = tm.group(1)
            return g + ("试验" if "试验" not in g else "")
    return "试验方法"


def _classify_section(title: str) -> str:
    t = (title or "").strip()
    if not t or t in ("全文",):
        return "skip"
    for role, pat in _ROLE_PATTERNS:
        if pat.search(t):
            return role
    if re.match(r"^[１２３４５６７８９０\d]+[\s\.．]", t):
        return "numbered"
    return "other"


def _make_node(nid: str, title: str, section_ids: List[str], children: Optional[List] = None) -> Dict[str, Any]:
    return {
        "node_id": nid,
        "title": title,
        "summary": "",
        "section_ids": section_ids,
        "children": children or [],
    }


def build_rule_based_semantic_tree(
    sections: List[Dict[str, Any]],
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """规则版语义树 — 即时可用，结构符合国军标/国标试验标准。"""
    std_name = meta.get("std_name") or meta.get("title") or "文档"
    equipment = infer_equipment_name(std_name, meta.get("abstract") or "")
    test_topic = infer_test_topic(std_name, sections)

    buckets: Dict[str, List[str]] = {
        "general": [], "cond": [], "proc": [], "eval": [],
        "method": [], "appendix": [], "other": [],
    }
    sid_map = {}
    for s in sections:
        sid = s.get("section_id")
        title = s.get("title") or ""
        if not sid:
            continue
        sid_map[sid] = s
        role = _classify_section(title)
        if role == "skip":
            continue
        if role == "numbered":
            buckets["other"].append(sid)
        elif role in buckets:
            buckets[role].append(sid)
        else:
            buckets["other"].append(sid)

    children: List[Dict[str, Any]] = []

    if buckets["general"]:
        children.append(_make_node("ST_GENERAL", "通用要求", buckets["general"]))

    test_children: List[Dict[str, Any]] = []
    if buckets["cond"]:
        test_children.append(_make_node("ST_TEST_COND", "试验条件", buckets["cond"]))
    if buckets["proc"]:
        test_children.append(_make_node("ST_TEST_PROC", "试验过程", buckets["proc"]))
    if buckets["eval"]:
        test_children.append(_make_node("ST_TEST_EVAL", "结果评定", buckets["eval"]))
    if buckets["method"] and not test_children:
        test_children.append(_make_node("ST_TEST_PROC", "试验方法", buckets["method"]))

    test_sids = buckets["cond"] + buckets["proc"] + buckets["eval"] + buckets["method"] + buckets["other"]
    if test_children:
        children.append(_make_node("ST_TEST", test_topic, test_sids[:10], test_children))
    elif test_sids:
        children.append(_make_node("ST_TEST", test_topic, test_sids))

    if buckets["appendix"]:
        children.append(_make_node("ST_APPENDIX", "附录", buckets["appendix"]))

    # 若规则分桶失败，按一级标题聚类（level<=1）
    if not children:
        top_sections = [s for s in sections if int(s.get("level") or 99) <= 1 and s.get("section_id")]
        if not top_sections:
            top_sections = sections[:20]
        for i, s in enumerate(top_sections):
            sid = s.get("section_id")
            if sid:
                children.append(_make_node(f"ST_{i + 1:03d}", s.get("title") or f"章节{i + 1}", [sid]))

    root_sids = [s.get("section_id") for s in sections[:3] if s.get("section_id")]

    root = {
        "node_id": "ST_ROOT",
        "title": equipment,
        "summary": meta.get("abstract") or std_name,
        "section_ids": root_sids,
        "graph_link_hint": equipment,
        "children": children,
    }
    return root
