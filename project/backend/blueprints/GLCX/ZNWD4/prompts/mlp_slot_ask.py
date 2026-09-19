"""Prompts for MLP-fixed route + per-slot LLM ask (LGR-ask / router_mlp_llm_ask)."""

from __future__ import annotations

from ..routing_catalog import RouteEntry

NODE_LABEL_GUIDE = """
## 图谱节点类型（关键词槽位语义）
| node_label | 含义 | 典型 slot_key |
|------------|------|---------------|
| Equipment | 具体装备/设备型号 | equipment_keywords |
| EquipmentType | 装备类型/类别 | equipment_type_keywords |
| Environment | 试验环境 | environment_keywords |
| TestProject | 试验项目 | project_keywords |
| Indicator | 考核指标 | indicator_keywords |
| TestMethod | 试验方法 | method_keywords |
| File | 标准/文件 | standard_keywords |
"""

SLOT_TO_LABEL: dict[str, str] = {
    "equipment_keywords": "Equipment",
    "equipment_type_keywords": "EquipmentType",
    "environment_keywords": "Environment",
    "project_keywords": "TestProject",
    "indicator_keywords": "Indicator",
    "method_keywords": "TestMethod",
    "standard_keywords": "File",
}

SLOT_ASK_ZH: dict[str, str] = {
    "equipment_keywords": "装备/设备型号",
    "equipment_type_keywords": "装备类型/类别",
    "environment_keywords": "试验环境",
    "project_keywords": "试验项目",
    "indicator_keywords": "考核指标",
    "method_keywords": "试验方法",
    "standard_keywords": "标准/文件",
}


def _slot_ask_lines(entry: RouteEntry) -> str:
    lines: list[str] = []
    for slot in entry.required_slots or []:
        if not slot.endswith("_keywords"):
            continue
        label = SLOT_TO_LABEL.get(slot, slot.replace("_keywords", ""))
        zh = SLOT_ASK_ZH.get(slot, label)
        lines.append(
            f"- **{slot}**（{label} / {zh}）：问句中哪一段是「{zh}」？"
            f"不要选任务描述词（如「方法」「指标」「项目」等意图词）。无则 surface_span=\"\"。"
        )
    if not lines:
        return "- （本路由无实体槽位）"
    return "\n".join(lines)


def build_route_slot_ask_prompt(entry: RouteEntry) -> str:
    """MLP 已选定 route；LLM 按槽位反问抽取 surface_span。"""
    title = entry.title or entry.route_id
    purpose = entry.purpose or ""
    asks = _slot_ask_lines(entry)
    return f"""你是试验知识库问答的**槽位反问识别助手**。

## 重要约束
- **检索路径已固定**，禁止选择或修改 route_id。
- 对每个必填槽位，用**反问**方式找出问句中对应的实体片段（surface_span）。
- 不要抽取意图/任务描述词。

## 固定路由（不可更改）
- route_id: `{entry.route_id}`
- 标题: {title}
- purpose: {purpose}

{NODE_LABEL_GUIDE}

## 逐槽反问（必须逐条回答）
{asks}

## 示例
问句：「高温工作实验的方法」
路由固定为查某试验项目的方法 → project_keywords：
→ surface_span="高温工作实验"（不是「方法」）

## 输出 JSON（仅此一个对象）
{{
  "mode": "route_fixed_slot_ask",
  "route_id": "{entry.route_id}",
  "slot_assignments": [
    {{"node_label": "TestProject", "slot_key": "project_keywords", "surface_span": "高温工作实验"}}
  ],
  "focus": "高温工作实验",
  "match_reason": "一句说明"
}}

规则：
- `route_id` 必须与固定值完全一致。
- `slot_assignments` 必须覆盖上述每个必填槽位（无对应片段则 surface_span=""）。
- 禁止输出候选 route 列表或 keywords 数组。"""


__all__ = ["build_route_slot_ask_prompt", "SLOT_TO_LABEL", "SLOT_ASK_ZH", "NODE_LABEL_GUIDE"]
