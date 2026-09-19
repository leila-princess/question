"""
ZNWD4 分步路由提示词：Step1 判 purpose → Step2 选 route_id + 槽位。
"""

from __future__ import annotations

from typing import List

from ..routing_catalog import list_purpose_options_markdown, routes_prompt_markdown
from ..routing_disambiguation import DISAMBIGUATION_RULES_MARKDOWN
from .graph_schema import graph_schema_ultra_compact_markdown

_STEP1_SAMPLE_UNDER_TYPE = """{
  "step": 1,
  "purpose": "query_equipment_under_type",
  "purpose_zh": "查某装备类型下有哪些具体装备",
  "keywords": ["无人机"],
  "use_history": false
}"""

_STEP1_SAMPLE_TEST_PROJECT = """{
  "step": 1,
  "purpose": "query_equipment_test_projects",
  "purpose_zh": "查某装备有哪些试验项目",
  "keywords": ["无人机"],
  "use_history": false
}"""

_STEP2_SAMPLE_UNDER_TYPE = """{
  "step": 2,
  "route_id": "R-EQUIPMENT-under_type",
  "keywords": ["无人机"],
  "params_draft": {
    "equipment_type_keywords": ["无人机"],
    "focus": "无人机"
  },
  "match_reason": "问类型下有哪些具体装备（不是试验项目）"
}"""

_STEP2_SAMPLE_TEST_PROJECT = """{
  "step": 2,
  "route_id": "R-TEST_PROJECT-by_equipment",
  "confidence": 0.91,
  "route_candidates": [
    {"route_id": "R-TEST_PROJECT-by_equipment", "confidence": 0.91, "reason": "问装备关联的试验项目"},
    {"route_id": "R-INDICATOR-by_equipment", "confidence": 0.12, "reason": "非问指标"}
  ],
  "keywords": ["无人机"],
  "params_draft": {
    "equipment_keywords": ["无人机"],
    "focus": "无人机"
  },
  "match_reason": "问装备关联的试验项目"
}"""


def build_step1_system_prompt() -> str:
    purposes = list_purpose_options_markdown()
    ontology = graph_schema_ultra_compact_markdown()
    return f"""你是试验知识库问答的**路由助手（Step1：判断问题目的）**。

## 任务
1. 理解用户要问什么（purpose），从下列 purpose 枚举中选**最贴切的一项**。
2. 抽取关键词 keywords（实体短词，1～6 字为宜；纯列举/闲聊可留空）。
3. 若含指代、省略、或问「上一个/刚才的问题」→ 结合 Human 中的【对话摘要】【最近对话】理解；必要时调用 znwd4_history_recent_qa。

## 关键区分（易错）
- 「无人机有哪些**具体装备**」→ purpose=**query_equipment_under_type**（类型→具体装备）
- 「无人机有哪些**试验项目**」→ purpose=**query_equipment_test_projects**
- 「地面装备有哪些**二级**装备/类别」→ purpose=**query_level2_equipment_types**

## 闲聊
问候、自我介绍、你能做什么 → purpose=chat，keywords=[]。

## purpose 枚举
{purposes}

{ontology}

{DISAMBIGUATION_RULES_MARKDOWN}

## 输出示例（二选一，按问句语义）
{_STEP1_SAMPLE_UNDER_TYPE}

或

{_STEP1_SAMPLE_TEST_PROJECT}

禁止输出 route_id；Step2 再由程序给出候选业务表。"""


def build_step2_system_prompt(candidate_table: str) -> str:
    return f"""你是试验知识库问答的**路由助手（Step2：选择业务号）**。

## 候选业务（程序已按 Step1 purpose 筛选，只能从中选一条 route_id）
{candidate_table}

## 任务
1. 从表中选**唯一** route_id（写入 route_id 字段）。
2. 填 params_draft 槽位（equipment_type_keywords / equipment_keywords / environment_keywords 等）。
3. 输出 **confidence**（0～1，你对 route_id 选择的置信度）及 **route_candidates**（2～4 条，按 confidence 降序，含 route_id/confidence/reason；即使很确定也列出最可能的 2 条，第二项 confidence 应明显低于第一项）。
4. 当 confidence < 0.82 或前两候选 confidence 差 < 0.06 时，务必让 route_candidates 反映真实歧义（程序可能反问用户确认）。
5. 有实体关键词时**建议**调用 znwd4_probe_keyword 验证（label_hints 用表中「关键词节点」列）。
6. 最后一轮只输出 Step2 JSON。

## 关键区分
- 问「具体装备/设备」→ R-EQUIPMENT-under_type，槽位用 equipment_type_keywords
- 问「试验项目/测试项目」→ R-TEST_PROJECT-by_equipment，槽位用 equipment_keywords

## 输出示例
{_STEP2_SAMPLE_UNDER_TYPE}

或

{_STEP2_SAMPLE_TEST_PROJECT}

不要输出 user_biz/scenario/BIZ_ 代号；只输出 route_id + params_draft。"""


def build_routing_human(
    question: str,
    *,
    session_hint: str = "",
    extra: str = "",
    memory_block: str = "",
) -> str:
    q = (question or "").strip()
    parts: List[str] = []
    if (memory_block or "").strip():
        parts.append(memory_block.strip())
    parts.append(f"【当前问题】\n{q}")
    if session_hint:
        parts.append(session_hint)
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def build_step2_candidate_message(routes: list) -> str:
    table = routes_prompt_markdown(routes)
    return f"【Step2 候选业务表】\n\n{table}\n\n请选择 route_id 并填写槽位。"
