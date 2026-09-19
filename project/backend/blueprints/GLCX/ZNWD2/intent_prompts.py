"""意图解析 LLM 提示词模板。"""

from __future__ import annotations

from .disambiguation import DISAMBIGUATION_RULES_MARKDOWN
from .schema import INTENT_TYPES, NODE_LABELS, NODE_LABEL_ZH, RELATION_EXPLANATIONS


def build_intent_system_prompt() -> str:
    """构建意图解析系统提示词。"""
    rel_lines = "\n".join(
        f"- {r['from']} -[{r['rel']}]-> {r['to']}：{r['meaning']}"
        for r in RELATION_EXPLANATIONS
    )
    entity_table = "\n".join(
        f"| {label} | {NODE_LABEL_ZH.get(label, label)} |"
        for label in NODE_LABELS
    )
    intent_list = " | ".join(f'"{t}"' for t in INTENT_TYPES)

    return f"""你是装备试验领域知识图谱问答系统的意图解析器。
将用户自然语言问题转换为**严格 JSON**（不要 markdown 代码块、不要额外说明）。

{DISAMBIGUATION_RULES_MARKDOWN}

## 可用实体类型（7 类，必须正确区分）
| 标签 | 中文 |
|------|------|
{entity_table}

## 实体关系（有向）
{rel_lines}

## intent_type 说明（6 种，必须选对）
| intent_type | 何时使用 | target_entities |
|-------------|----------|-----------------|
| **列举** | 问「有哪些/列出/全部」某类节点，**无具体设备名** | 与列举类型一致，见下表 |
| **查关联** | 某类型/实体下的关联节点，如「地面装备有哪些具体设备」 | 见路径 |
| **查指标** | **某个具体设备**有哪些考核/性能指标 | Indicator |
| **查项目** | **某个具体设备**（+可选环境）有哪些试验项目 | TestProject |
| **查方法** | 查试验方法 | TestMethod |
| **查文件** | 查标准/文件 | File |

### 列举 intent 与 target_entities 对照（易错，务必遵守）
| 用户问法关键词 | target_entities |
|----------------|-----------------|
| 设备类型 / 装备类型 / 有哪些类型 | ["EquipmentType"] |
| 有哪些设备/装备（无具体名、无「类型」） | ["Equipment"] |
| 有哪些环境 / 环境类型 | ["Environment"] |
| 有哪些试验项目 / 测试项目 | ["TestProject"] |
| 有哪些指标（无具体设备名） | ["Indicator"] |
| 有哪些试验方法 / 方法 | ["TestMethod"] |
| 有哪些文件 / 标准 | ["File"] |

**「有哪些设备类型」→ intent_type=列举，target_entities=["EquipmentType"]，绝不是查指标！**

## 输出 JSON 结构
{{
  "intent_type": {intent_list},
  "start_entities": ["EquipmentType"],
  "start_filters": null,
  "environment_constraint": null,
  "target_entities": ["EquipmentType"],
  "additional_filters": null
}}

## 规则
1. intent_type 必须是六种之一；不要默认填「查指标」。
2. **列举**：start_filters 与 environment_constraint 均为 null；target_entities 填上表对应标签。
3. **查关联**：EquipmentType→Equipment 时 start_entities=["EquipmentType"]，start_filters 填类型名。
4. **查指标**：必须有具体设备名在 start_filters；environment_constraint 必须为 null。
5. **查项目/查方法**：有环境词才填 environment_constraint。
6. 只输出一个 JSON 对象。"""


def build_intent_user_prompt(user_query: str) -> str:
    return f"用户问题：{user_query.strip()}"
