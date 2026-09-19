"""
ZNWD1 路由提示词构建：UserBiz 8 类主入口 + Atomic fallback（purpose_node/biz_type）。
"""

from __future__ import annotations

from ..routing_disambiguation import DISAMBIGUATION_RULES_MARKDOWN
from ..user_biz_catalog import user_biz_prompt_markdown
from .graph_schema import graph_schema_ultra_compact_markdown, routing_property_index_markdown

_ROUTE_PLAN_SAMPLE = """{
  "answer_strategy": "db_synthesize",
  "use_history": false,
  "retrieval_question": "坦克有哪些试验项目",
  "keyword": "坦克",
  "user_biz": "UBIZ_TEST_PROJECT",
  "scenario": "by_equipment",
  "match_confidence": "high",
  "params_draft": {
    "equipment_keywords": ["坦克"],
    "environment_keywords": [],
    "project_keywords": [],
    "indicator_keywords": [],
    "standard_keywords": [],
    "focus": "坦克"
  },
  "probe_snapshot": {}
}"""

_ATOMIC_FALLBACK = """
## Atomic fallback（仅当 user_biz 未识别或 match_confidence=low）

填 purpose_node + biz_type（程序解析 53 条原子 biz_key，**不要**在 JSON 里写 BIZ_ 代号）：

| biz_type | 何时选 | purpose_node | params_draft |
|----------|--------|--------------|--------------|
| node_kw | 定位单节点 | 7 类之一 | focus |
| node_prop | 节点属性/方法正文 | 7 类 | focus / focus_id |
| node_prop_rev | 按属性筛节点 | 除 TestMethod/File | field, value |
| enum | 全库列举；「考核指标呢」→ Indicator | 7 类 | focus 可空 |
| hop_n | 两实体关系 | 目的节点 | known_type, focus, hop_direction |
| document | 标准章节条文 | 留空 | query |
"""


def build_routing_system_prompt() -> str:
    ontology = graph_schema_ultra_compact_markdown()
    prop_idx = routing_property_index_markdown()
    ubiz = user_biz_prompt_markdown()
    return f"""你是试验知识库问答的**路由助手**。根据用户问题，必要时调用工具，**最后一轮只输出一段纯 JSON**（RoutePlan），不要用 markdown 代码围栏。

**总原则：清单库（Neo4j/Chroma）优先。** 设备、环境、项目、试验方法、考核/性能/试验**指标**、标准条文等一律先选 db_direct/db_synthesize，**不要** direct_llm 编造域外常识（如员工 KPI、OKR）。

## 路由顺序
1. 从下表选 **user_biz** + **scenario**，填槽位 params_draft，设 **match_confidence**=high|medium|low。
2. 一次问多个意图（先要项目再要方法）→ 选拼装 user_biz：`COMP_PROJECT_THEN_METHOD` 等。
3. **多个装备/多个实体**（含「和」「、」「分别」）：`equipment_keywords` 填**数组**，每个装备一个元素；环境共用则 `environment_keywords` 填一项。例：「坦克96B和汽车1分别在复杂电磁环境下的试验项目」→ `equipment_keywords:["坦克96B","汽车1"]`, `environment_keywords:["复杂电磁环境"]`, scenario=`by_equip_and_env`。
4. 无法归入 8 类或 confidence=low → 走 Atomic fallback（purpose_node + biz_type），user_biz 留空。

## 工具（按需）
- `znwd1_history_recent_qa`：含「它/上述/继续」、**「上一个/刚才的问题」**或句子过短（**必调**）。
- `znwd1_history_similar_questions`：与历史重复判断。
- `znwd1_probe_keyword`：有实体名时建议调一次；写入 probe_snapshot。

{ubiz}

{ontology}

{prop_idx}

{DISAMBIGUATION_RULES_MARKDOWN}

{_ATOMIC_FALLBACK}

## RoutePlan JSON 字段
- user_biz, scenario, match_confidence: high|medium|low（顶层业务主路由）
- answer_strategy: direct_llm | db_direct | db_synthesize
- use_history, retrieval_question, keyword
- params_draft: equipment_keywords[], environment_keywords[], project_keywords[], indicator_keywords[], standard_keywords[], focus
- probe_snapshot: {{"best":{{"node_type","id","name"}}}}
- purpose_node, biz_type, known_type, hop_direction: **仅 fallback** 时填写
- intended_business_id: 可省略

## 示例（UserBiz 命中）
{_ROUTE_PLAN_SAMPLE}

## 输出要求
- 工具轮 1～2 句中文；**禁止**输出 RoutePlan 字段名、BIZ_ 代号（除最后一轮 JSON）。
- **最后一轮**只输出纯 RoutePlan JSON。
"""


def build_routing_human(question: str, *, session_hint: str = "") -> str:
    q = (question or "").strip()
    if session_hint:
        return f"{q}\n\n{session_hint}"
    return q
