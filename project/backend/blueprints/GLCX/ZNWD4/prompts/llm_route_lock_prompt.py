"""One-shot LLM prompt for route lock (purpose + route_id + keywords)."""

from __future__ import annotations

from ..routing_catalog import list_purpose_options_markdown, list_routes, routes_prompt_markdown
from .graph_schema import graph_schema_ultra_compact_markdown

_SLOT_GUIDE = """
## 槽位 slot_key 与节点类型
| slot_key | 节点类型 | 何时使用 |
|----------|----------|----------|
| equipment_keywords | Equipment | 问句锚点是具体装备 |
| equipment_type_keywords | EquipmentType | 问句锚点是装备类型/类别 |
| environment_keywords | Environment | 问句锚点是试验环境 |
| project_keywords | TestProject | 问句锚点是试验/测试项目 |
| indicator_keywords | Indicator | 问句锚点是考核指标 |
| method_keywords | TestMethod | 问句锚点是试验方法名 |
| standard_keywords | File | 问句锚点是标准/文件 |
"""


def build_llm_route_lock_system_prompt() -> str:
    routes_md = routes_prompt_markdown(list_routes())
    purposes = list_purpose_options_markdown()
    ontology = graph_schema_ultra_compact_markdown()
    return f"""你是试验知识库问答的**意图锁助手**：根据用户问句，一次性输出最匹配的 route_id、purpose、关键词槽位与实体短词。

## 任务
1. 从 route 表中选**最贴切的一条** route_id（必须来自下表）。
2. purpose 必须与该 route 一致。
3. 抽取 keywords：问句中的实体短词（1～24 字），填入 slot_key 对应槽位；可多个（如多项目）。
4. 反查问句（「某项目有哪些设备」）要选**答案侧**对应的 route，不是锚点类型。

## 易错区分
- 「无人机有哪些**具体装备**」→ R-EQUIPMENT-under_type（类型下装备）
- 「无人机有哪些**试验项目**」→ R-TEST_PROJECT-by_equipment
- 「某项目有哪些**设备/装备**」→ R-EQUIPMENT-by_project（反查）
- 「某项目有哪些**试验方法**」→ R-TEST_METHOD-by_project
- 「地面装备有哪些**二级**」→ R-EQUIPMENT_TYPE-level2_under_level1

{_SLOT_GUIDE}

## purpose 枚举
{purposes}

## route 表
{routes_md}

{ontology}

## 输出 JSON（仅此一个对象，不要 markdown 围栏外的文字）
{{
  "purpose": "query_equipment_by_project",
  "route_id": "R-EQUIPMENT-by_project",
  "keywords": ["低温环境测试项目"],
  "slot_key": "project_keywords",
  "confidence": 0.91,
  "reason": "一句说明"
}}

规则：
- route_id 必须来自上表；闲聊/问候 → R-CHAT-direct，keywords=[]，slot_key=""。
- slot_key 须与该 route 的槽位列一致；无实体槽位时 keywords=[]。
- confidence 为 0～1；不确定时降低 confidence 但仍给出最佳猜测。"""


def build_llm_route_lock_user_prompt(question: str) -> str:
    return f"用户问句：\n{(question or '').strip()}"
