"""
ZNWD V5 轻量级意图分类（规则 + 关键词），供动态路由：简单问题直出，复杂问题走 LLM 合成。

注意：本模块不得依赖 services_v5，以避免循环导入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Intent(str, Enum):
    ENUMERATION = "ENUMERATION"
    RELATION_QUERY = "RELATION_QUERY"
    STANDARD_CHAPTER = "STANDARD_CHAPTER"
    NUMERIC = "NUMERIC"
    EXPLAIN = "EXPLAIN"
    OTHER = "OTHER"


@dataclass
class IntentResult:
    intent: Intent
    reason: str = ""


def _norm(q: str) -> str:
    return (q or "").strip()


def plain_intent_slice(raw: str) -> str:
    """
    从可能含「历史摘要 / 承前主体 / 当前问题」的检索串中取出当前用户问句，
    避免摘要里的「测试项目」等与当前「有哪些指标」跨句误触发枚举规则。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.search(r"【当前问题】\s*(.+)$", s, re.S)
    if m:
        cur = (m.group(1) or "").strip()
        if len(cur) >= 2:
            return cur
    m2 = re.search(r"补充问题：\s*(.+)$", s, re.S)
    if m2:
        cur = (m2.group(1) or "").strip()
        if len(cur) >= 2:
            return cur
    return s


def _looks_standard_chapter(q: str) -> bool:
    t = _norm(q)
    return bool(re.search(r"(标准|GB/T|GJB).*(试验过程|试验步骤|章节|第\d+(?:\.\d+)*)", t))


def _looks_enumeration(q: str) -> bool:
    """与 services_v5._looks_like_node_type_enumeration_question 语义对齐的精简版。"""
    t = _norm(q)
    if not t:
        return False
    if re.search(
        r"(?:查询|请问|想了解一下|想了解)?\s*[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:测试|试验)?\s*"
        r"(?:项目|指标|方法|测试方法|试验方法)\s*(?:有哪些|都有什么|包括哪些|是什么)",
        t,
    ):
        return False
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些)\s*(?:测试项目|试验项目|指标|方法|测试方法|试验方法)",
        t,
    ) and not re.search(r"(全部|所有|整个库|整个系统|一共)", t):
        return False
    if re.search(r"(有哪些|都有什么|列出|列举|显示|查看|全部|所有)", t) and re.search(
        r"(设备类型|装备类型|设备类别|环境类型|试验项目|测试项目|指标|测试方法|试验方法|文件)",
        t,
    ):
        return True
    if re.search(r"(设备类型|装备类型|环境类型)\s*(有哪些|是什么|都有什么|包括哪些)", t):
        return True
    return False


def _looks_relation_query(q: str) -> bool:
    """特定对象下的「项目/指标/方法」列表类问题（非全库枚举）。"""
    t = _norm(q)
    if not t:
        return False
    if re.search(
        r"(哪些|哪一些|什么|都有哪些).{0,24}(设备|装备).{0,16}(有|具备|关联).{0,16}项目",
        t,
    ):
        return True
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*的\s*(?:测试|试验)?\s*(?:项目|指标|方法|测试方法|试验方法)\s*(?:有哪些|都有什么|包括哪些|是什么|列出|列举)",
        t,
    ):
        return True
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:有什么|有哪些|包括哪些)\s*(?:测试项目|试验项目|指标|方法|测试方法|试验方法)",
        t,
    ):
        return True
    if re.search(
        r"(?:在|针对)\s*[\u4e00-\u9fffA-Za-z0-9·\-]{2,}\s*(?:上|下|中)?\s*(?:的)?\s*(?:测试项目|试验项目|指标|方法)",
        t,
    ):
        return True
    return False


def _looks_numeric(q: str) -> bool:
    t = _norm(q)
    return bool(re.search(r"(多少|几个|数量|统计|一共|总共有|计数|排名第)", t))


def _looks_explain(q: str) -> bool:
    t = _norm(q)
    return bool(
        re.search(
            r"(为什么|什么原因|原理|机理|区别|差异|影响|优缺点|是否可行|怎么理解|如何解释|意味着什么)",
            t,
        )
    )


def classify_intent(question: str) -> IntentResult:
    """
    快速意图分类。优先级：标准章节 > 枚举 > 关系查询 > 数值 > 解释 > 其他。
    """
    q = _norm(plain_intent_slice(question) or question)
    if not q:
        return IntentResult(Intent.OTHER, "空问题")

    if _looks_standard_chapter(q):
        return IntentResult(Intent.STANDARD_CHAPTER, "命中标准/章节类表述")

    if _looks_enumeration(q):
        return IntentResult(Intent.ENUMERATION, "命中全库/类型枚举问法")

    if _looks_relation_query(q):
        return IntentResult(Intent.RELATION_QUERY, "命中对象下的项目/指标/方法关系问法")

    if _looks_numeric(q):
        return IntentResult(Intent.NUMERIC, "命中数量/统计问法")

    if _looks_explain(q):
        return IntentResult(Intent.EXPLAIN, "命中解释/因果/对比问法")

    return IntentResult(Intent.OTHER, "未命中快速路由规则")


def relation_target_kind(question: str) -> str:
    """
    对 RELATION_QUERY 粗略推断目标侧：project | indicator | method | equipment | unknown
    """
    t = _norm(plain_intent_slice(question) or question)
    # 哪些设备/装备具备某类「…项目」→ 要列的是 Equipment，不是 TestProject 清单
    if re.search(r"(哪些|哪一些|什么|都有哪些).{0,24}(设备|装备)", t) and re.search(
        r"(有|具备|关联).{0,16}项目", t,
    ):
        return "equipment"
    # 「某测试项目/试验项目的方法、具体内容」→ 目标仍是方法（不要因句中含「项目」判成 project）
    if re.search(r"(测试项目|试验项目)", t) and re.search(
        r"(方法|测试方法|试验方法|具体内容|内容是什么|怎么测|步骤|流程)",
        t,
    ):
        return "method"
    if re.search(r"(测试方法|试验方法|方法\s*(有哪些|有|为|包括))", t) or (
        "方法" in t and "项目" not in t and "指标" not in t
    ):
        return "method"
    if "指标" in t:
        if re.search(
            r"(测试项目|试验项目)\s*(和|与|及|、|,|，)?\s*指标|指标\s*(和|与|及|、)?\s*(测试项目|试验项目)",
            t,
        ):
            return "unknown"
        return "indicator"
    if "项目" in t or "试验" in t or "测试" in t:
        return "project"
    return "unknown"
