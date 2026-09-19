"""
从 meta_ops 生成注入 LLM 提示词的本体 / 关系 Markdown（单一数据源，不 import ZNWD）。
"""

from __future__ import annotations

from typing import Dict, List

from ..enum_inference import node_type_zh
from ..meta_ops import NODE_ENTITY_FIELDS, NODE_LABELS, RELATION_EXPLANATIONS

# 属性字段中文名（与图库 properties 键名一致）
_FIELD_ZH: Dict[str, str] = {
    "Id": "ID",
    "Name": "名称",
    "Label": "标签",
    "Description": "描述",
    "Type": "分类/类别",
    "Status": "状态",
    "User_id": "上传用户",
    "Create_time": "上传时间",
    "EquipmentTypeId": "设备类型Id",
    "Class": "分级",
    "Combination": "典型组合",
    "Limit": "约束",
    "Request": "要求",
    "EnvironmentId": "环境Id",
    "Equipment_name": "关联设备名",
    "SYNR": "试验内容",
    "SYTJ": "试验条件",
    "SYGC": "试验过程",
    "JGPD": "结果评定",
    "Sy": "术语",
    "Source_name": "来源文件名",
    "Project_name": "关联项目名",
    "Project_rate": "项目关联度",
    "Indicator_name": "关联指标名",
    "Indicator_rate": "指标关联度",
    "Chroma_id": "向量库ID",
    "Chroma_chunks": "向量块数",
    "minIo_id": "文件库id",
    "Image_true": "是否有图片",
    "Image_minIO_id": "图片文件库id",
}

# 节点说明（路由/合成共用；与 executor 语义一致）
_NODE_NOTES: Dict[str, str] = {
    "EquipmentType": "设备类型/二级分类，如「无人机」「传感器」；Type 常为一级分类（如陆地）",
    "Equipment": "具体设备实体；Type 关联 EquipmentType.Id；**考核/性能指标经 CON 直连 Indicator**（勿经 TestProject）",
    "Environment": "试验环境；问分类/分级/组合/约束/要求须读 Type、Class、Combination、Limit、Request，勿与 TestProject 混用",
    "TestProject": "测试/试验项目；Type=test 子级 / category 父级",
    "Indicator": "测试/考核指标；Type 为距离、时间、速度、高度等；可与 Equipment **CON 一步**相连，Equipment_name 可存关联设备名",
    "TestMethod": "测试方法；**SYNR=试验正文（优先）**；无实质 SYNR 时用 SYTJ/SYGC/JGPD；Class 为总则/分则",
    "File": "标准/规范/方案/模板等文件；Chroma_id 关联向量库",
}


def _format_field_list(label: str, *, compact: bool) -> str:
    fields = NODE_ENTITY_FIELDS.get(label) or []
    if compact:
        # 精简：去掉 User_id、Create_time 等维护字段
        skip = frozenset({"User_id", "Create_time", "Project_name", "Project_rate", "Indicator_name", "Indicator_rate"})
        fields = [f for f in fields if f not in skip]
    parts: List[str] = []
    for f in fields:
        zh = _FIELD_ZH.get(f, f)
        if f == "SYNR":
            parts.append(f"**{f}**({zh})")
        elif label == "Environment" and f in ("Type", "Class", "Combination", "Limit", "Request"):
            parts.append(f"**{f}** ({zh})")
        else:
            parts.append(f"{f} ({zh})")
    return "、".join(parts)


def _node_table(*, compact: bool) -> str:
    lines = [
        "| 节点类型 | 属性（图库字段名·中文） | 说明 |",
        "|---------|------------------------|------|",
    ]
    for label in NODE_LABELS:
        zh = node_type_zh(label)
        props = _format_field_list(label, compact=compact)
        note = _NODE_NOTES.get(label, "")
        lines.append(f"| **{label}**（{zh}） | {props} | {note} |")
    return "\n".join(lines)


def _relation_table() -> str:
    lines = [
        "| 起始节点 | 关系 | 目标节点 | 含义 |",
        "|---------|------|---------|------|",
    ]
    for row in RELATION_EXPLANATIONS:
        fr = row.get("from") or ""
        rel = row.get("rel") or ""
        to = row.get("to") or ""
        meaning = row.get("meaning") or ""
        lines.append(f"| `{fr}` | **{rel}** | `{to}` | {meaning} |")
    return "\n".join(lines)


_GRAPH_CONSTRAINTS = """**图谱硬约束（路由 guessed_node_types / 合成表述须遵守）**
- 关系类型仅：**HAS**、**CON**、**BELONGS_TO**；方向必须与上表一致，禁止反向或臆造边。
- 与 **TestMethod** 经 **CON** **一步**相连的只有 **TestProject**、**Indicator**；设备/环境与测试方法**无**直接 CON。
- 问「设备/环境下有哪些方法」= Equipment/Environment → CON → TestProject → CON → TestMethod（两步）。
- 问「某设备类型下有哪些具体设备」= EquipmentType → **HAS** → Equipment（**where_side=from**，过滤 EquipmentType 端）；程序会**扩展同 Type 一级分类**下的全部 EquipmentType 并合并设备（含 EquipmentTypeId 关联）。
- **装备类型三级结构**：一级 = EquipmentType.**Type**（如「地面装备」）；二级 = EquipmentType 节点（**Name**，即装备类型）；三级 = Equipment 节点（具体装备）。问「地面装备下有哪些二级装备类型」→ node_prop_rev(field=**Type**, value=地面装备)，返回二级 EquipmentType；问「某装备属于什么装备类型」→ EquipmentType-[HAS]->Equipment，**where_side=to** 过滤 Equipment，返回 EquipmentType。
- 问「某设备有哪些考核/性能/试验指标」= Equipment → **CON** → Indicator（**一步直连**；指标不是 TestProject 子对象，勿误查项目）。
- **向前反查（where_side=to）**：图边方向不变，过滤条件写在关系**终点**上，RETURN **起点**列表。例：哪些设备具备项目 X → `Equipment-[CON]->TestProject`，where 匹配 TestProject.Name/Id，返回 Equipment。
- **按方法反查设备+项目**：`Equipment-[CON]->TestProject-[CON]->TestMethod`，where 匹配 TestMethod.Name/Source_name（如 GBT38924.4-2020），RETURN 设备与项目组合；**不是** Chroma 文档检索。
- 问方法**正文/内容/步骤** → 读 **TestMethod.SYNR**（优先），其次 SYTJ/SYGC/JGPD；图库无内容才查 Chroma 文档；**Chroma 有命中时必须 LLM 归纳，禁止直出文档片段**。"""


def environment_fields_guide() -> str:
    return """### 试验环境（Environment）— 分类 / 分级 / 组合 / 约束 / 要求

| 用户常问 | 图属性（精确字段名） | 说明 |
|--------|---------------------|------|
| 环境分类、环境类型 | **Type** | 环境分类 |
| 环境分级、级别 | **Class** | 环境分级 |
| 典型组合 | **Combination** | 如「高温+高湿」 |
| 约束、限值 | **Limit** | 约束文本 |
| 要求 | **Request** | 要求文本 |

须从 Environment 节点 properties 读取；**禁止**用 TestProject.Type/Label 充当环境分类。"""


def method_fields_guide() -> str:
    return """### 测试方法（TestMethod）— 正文字段优先级

1. **SYNR**（试验内容/方法正文）：有实质内容时**唯一优先**，连续输出，勿再拆（一）（二）（三）。
2. 无实质 **SYNR** 时依次：**SYTJ**（试验条件）→ **SYGC**（试验过程）→ **JGPD**（结果评定）。
3. **Sy**：术语串，格式「术语1：定义；术语2：定义」。
4. **Source_name**：来源标准/文件名；图库无方法字段时再结合 Chroma 文档片段。"""


def graph_schema_ultra_compact_markdown() -> str:
    """路由静态核：7 节点 + 8 边（无属性长表）。"""
    return """### 图谱（节点与边，名称须英文）
节点: EquipmentType(设备类型) Equipment(设备) Environment(环境) TestProject(项目) Indicator(指标) TestMethod(方法) File(文件)
边(仅8种，方向固定):
- EquipmentType -HAS-> Equipment
- Equipment -CON-> TestProject | Equipment -CON-> Indicator
- Environment -CON-> TestProject
- TestProject -CON-> TestMethod | TestProject -HAS-> TestProject
- Indicator -CON-> TestMethod
- TestMethod -BELONGS_TO-> File
指标只连设备；设备→方法可经项目(EQ→TP→TM)或经指标(EQ→IND→TM)。"""


def routing_property_index_markdown() -> str:
    """路由用：用户说法 → 字段名（非全属性表）。"""
    return """### 路由属性索引（选 node_prop / node_prop_rev 时填 field 或 property_fields）

| 节点 | 用户常问 | field / property_fields |
|------|----------|-------------------------|
| Environment | 环境分类/类型 | Type |
| Environment | 分级、级别 | Class |
| Environment | 典型组合 | Combination |
| Environment | 约束、限值 | Limit |
| Environment | 要求 | Request |
| TestMethod | 方法正文/内容/步骤/怎么做 | SYNR（优先）, SYTJ, SYGC, JGPD |
| TestMethod | 总则/分则 | Class |
| TestMethod | 来源标准名 | Source_name |
| TestProject | 父级/子级项目 | Type |
| TestProject | 关联环境 | EnvironmentId |
| Indicator | 指标类型 | Type |
| Equipment | 关联设备类型 | EquipmentTypeId |
| EquipmentType | 一级分类 | Type |
| File | 标准/规范类型 | Type |

规则：问名称→node_kw(field=Name)；问该节点属性→node_prop；问某属性值有哪些→node_prop_rev(field+value)。"""


def graph_schema_compact_markdown() -> str:
    """路由 Agent：节点 + 关系 + 硬约束（约 v6 159–191 信息量）。"""
    return f"""### Neo4j 图数据库（清单库，只读）

#### 节点类型与属性

{_node_table(compact=True)}

#### 关系（方向必须严格遵循）

{_relation_table()}

{_GRAPH_CONSTRAINTS}

### Chroma 向量库（文档库）
存储标准/规范/方案等文档片段；GB/T、章节条文类问题走文档检索，某 TestMethod 节点正文优先读图库 SYNR。"""


def graph_schema_full_markdown() -> str:
    """合成阶段：完整节点表 + 关系 + 环境/方法字段指南。"""
    return f"""## 可用数据结构

### 1. Neo4j 图数据库（知识图谱）

#### 节点类型与属性

{_node_table(compact=False)}

#### 关系（方向必须严格遵循）

{_relation_table()}

{_GRAPH_CONSTRAINTS}

{environment_fields_guide()}

{method_fields_guide()}

### 2. Chroma 向量数据库（文档知识库）
存储标准、规范、方案、模板等文档的文本片段，支持语义搜索；当图库 TestMethod 的 SYNR/SYTJ/SYGC/JGPD 均为空或不足以作答时，再主要依据 Chroma 片段归纳。"""
