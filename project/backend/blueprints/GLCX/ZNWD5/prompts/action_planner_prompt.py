"""
ZNWD5 ActionPlanner 提示词：本体 + 7 动作 + Zvec Top3 + few-shot。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from blueprints.GLCX.ZNWD4.prompts.graph_schema import graph_schema_compact_markdown

_ACTIONS_MD = """
## 七种动作（action，与 L3 biz_type 一致）

| action | 含义 | 必填字段 |
|--------|------|----------|
| `node_kw` | 按名称/关键词查节点 | purpose_node, focus |
| `node_prop` | 读节点属性（Environment 分类/分级/组合/约束/要求；TestMethod 读 SYNR 正文） | purpose_node, focus |
| `node_prop_rev` | 按属性反查节点（如按 Type 查 EquipmentType） | purpose_node, focus 或 value |
| `enum` | 枚举某类全部节点 | purpose_node |
| `hop_n` | 关系一跳/两跳（forward=已知→目的，reverse=目的←已知） | purpose_node, known_type, focus；第二步可用 use_prev |
| `document` | 标准/章节文档检索（Chroma/Zvec） | query（含章节号/标准号更佳） |

**规则**
- 每步仅一个 action + 一个主 focus；复杂意图拆成 s1,s2,…
- 禁止一步同时 enum + hop；含「以及/并且/还有/分别」先填 sub_questions[]
- hop_n 时 known_type→purpose_node 须在本体关系白名单内
- 装备→试验项目→试验方法：s1 hop_n(TestProject←Equipment)，s2 hop_n(TestMethod←TestProject) + use_prev
- 问环境属性：node_prop(Environment) 或 hop_n 后再 node_prop
- 文档/章节/标准：action=document，query 保留章节号
"""

_FEW_SHOT = """
## Few-shot 示例

### 单跳：装备→试验项目
问：坦克有哪些试验项目
```json
{"question":"坦克有哪些试验项目","sub_questions":[],"steps":[
  {"step_id":"s1","action":"hop_n","purpose_node":"TestProject","known_type":"Equipment","hop_direction":"forward","focus":"坦克","note":"查装备关联项目"}
],"answer_mode":"direct","confidence":"high"}
```

### 拼装：项目+方法
问：汽车2有哪些试验项目和方法
```json
{"question":"汽车2有哪些试验项目和方法","sub_questions":[],"steps":[
  {"step_id":"s1","action":"hop_n","purpose_node":"TestProject","known_type":"Equipment","hop_direction":"forward","focus":"汽车2","note":"查装备关联项目"},
  {"step_id":"s2","action":"hop_n","purpose_node":"TestMethod","known_type":"TestProject","hop_direction":"forward","use_prev":{"step_id":"s1","side":"to","field":"Id"},"note":"用上一步项目查方法"}
],"answer_mode":"assemble","confidence":"high"}
```

### 多问
问：坦克有哪些试验项目，以及高温环境的分级是什么
```json
{"question":"坦克有哪些试验项目，以及高温环境的分级是什么","sub_questions":[
  {"question":"坦克有哪些试验项目","steps":[
    {"step_id":"s1","action":"hop_n","purpose_node":"TestProject","known_type":"Equipment","focus":"坦克"}
  ]},
  {"question":"高温环境的分级是什么","steps":[
    {"step_id":"s1","action":"node_prop","purpose_node":"Environment","focus":"高温","property_fields":["Class","Type"]}
  ]}
],"steps":[],"answer_mode":"synthesize","confidence":"medium"}
```

### 环境属性
问：复杂电磁环境的典型组合和要求是什么
```json
{"question":"复杂电磁环境的典型组合和要求是什么","sub_questions":[],"steps":[
  {"step_id":"s1","action":"node_prop","purpose_node":"Environment","focus":"复杂电磁环境","property_fields":["Combination","Request"]}
],"answer_mode":"direct","confidence":"high"}
```
"""


def build_action_planner_system_prompt() -> str:
    schema = graph_schema_compact_markdown()
    return (
        "你是 ZNWD5 动作规划器。根据用户问句与 Zvec 实体候选，输出**唯一** JSON ActionPlan。\n"
        "不要输出 Playbook/route_id/biz_key；不要写 Cypher/SQL。\n\n"
        f"{schema}\n\n"
        f"{_ACTIONS_MD}\n\n"
        f"{_FEW_SHOT}\n\n"
        "## 输出 JSON Schema\n"
        "```json\n"
        "{\n"
        '  "question": "原文",\n'
        '  "sub_questions": [{"question":"子问","steps":[...]}],\n'
        '  "steps": [{"step_id":"s1","action":"hop_n","purpose_node":"TestProject",'
        '"known_type":"Equipment","focus":"实体名","use_prev":{"step_id":"s1","side":"to","field":"Id"}}],\n'
        '  "answer_mode": "direct|assemble|synthesize",\n'
        '  "confidence": "high|medium|low"\n'
        "}\n"
        "```\n"
        "多步/多问默认 answer_mode=synthesize；单步 enum 可用 direct；项目+方法拼装用 assemble。"
    )


def build_action_planner_human(
    question: str,
    grounding: Dict[str, Any],
) -> str:
    cands = grounding.get("candidates") or []
    lines = ["## 用户问句", question.strip(), "", "## Zvec Top 实体候选"]
    if cands:
        for i, c in enumerate(cands, 1):
            lines.append(
                f"{i}. [{c.get('node_type')}] {c.get('name')} "
                f"(score={c.get('score')}, source={c.get('source')})"
            )
    else:
        lines.append("（无候选，focus 须来自问句原文）")
    lines.append("")
    lines.append("请输出 ActionPlan JSON（仅 JSON，无 markdown 围栏）。")
    return "\n".join(lines)


def build_repair_human(question: str, plan_json: str, errors: List[str]) -> str:
    return (
        f"问句：{question}\n\n"
        f"上次 ActionPlan 校验失败：\n- " + "\n- ".join(errors[:8]) + "\n\n"
        f"上次输出：\n{plan_json[:2000]}\n\n"
        "请仅重输出修正后的 steps/sub_questions JSON（保留 question/answer_mode/confidence）。"
    )


__all__ = [
    "build_action_planner_human",
    "build_action_planner_system_prompt",
    "build_repair_human",
]
