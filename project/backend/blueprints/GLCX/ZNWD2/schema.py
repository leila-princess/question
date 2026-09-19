"""ZNWD2 图谱 schema（与 ZNWD1 meta_ops 对齐，独立副本）。"""

from __future__ import annotations

from typing import Dict, List, Tuple

NODE_LABELS: List[str] = [
    "EquipmentType",
    "Equipment",
    "Environment",
    "TestProject",
    "Indicator",
    "TestMethod",
    "File",
]

INTENT_TYPES: List[str] = ["列举", "查关联", "查指标", "查项目", "查方法", "查文件"]

# 7 类节点中文（提示词 / 格式化共用）
NODE_LABEL_ZH: Dict[str, str] = {
    "EquipmentType": "设备类型",
    "Equipment": "设备",
    "Environment": "试验环境",
    "TestProject": "试验项目",
    "Indicator": "考核指标",
    "TestMethod": "试验方法",
    "File": "文件",
}

RELATION_EXPLANATIONS: List[Dict[str, str]] = [
    {"from": "EquipmentType", "rel": "HAS", "to": "Equipment", "meaning": "设备类别包含设备"},
    {"from": "Equipment", "rel": "CON", "to": "TestProject", "meaning": "设备关联测试项目"},
    {"from": "Environment", "rel": "CON", "to": "TestProject", "meaning": "环境关联测试项目"},
    {"from": "Equipment", "rel": "CON", "to": "Indicator", "meaning": "设备关联考核指标（一步直连，勿经 TestProject）"},
    {"from": "TestProject", "rel": "CON", "to": "TestMethod", "meaning": "项目关联测试方法"},
    {"from": "Indicator", "rel": "CON", "to": "TestMethod", "meaning": "指标关联测试方法"},
    {"from": "TestMethod", "rel": "BELONGS_TO", "to": "File", "meaning": "方法关联来源文件"},
    {"from": "TestProject", "rel": "HAS", "to": "TestProject", "meaning": "父项目包含子项目"},
]

RELATION_SCHEMA: Dict[str, List[Tuple[str, str]]] = {
    "CON": [
        ("Equipment", "TestProject"),
        ("Environment", "TestProject"),
        ("Equipment", "Indicator"),
        ("TestProject", "TestMethod"),
        ("Indicator", "TestMethod"),
    ],
    "HAS": [
        ("EquipmentType", "Equipment"),
        ("TestProject", "TestProject"),
    ],
    "BELONGS_TO": [
        ("TestMethod", "File"),
    ],
}

NODE_ENTITY_FIELDS: Dict[str, List[str]] = {
    "EquipmentType": ["Id", "Name", "Label", "Description", "Type", "Status"],
    "Equipment": ["Id", "Name", "Label", "Type", "Description", "Status", "EquipmentTypeId"],
    "Environment": ["Id", "Name", "Label", "Description", "Type", "Class", "Combination", "Limit", "Request", "Status"],
    "TestProject": ["Id", "Name", "Type", "Label", "Description", "Status", "EnvironmentId", "Equipment_name"],
    "Indicator": ["Id", "Name", "Label", "Description", "Type", "Status", "Equipment_name"],
    "TestMethod": ["Id", "Name", "Class", "Label", "Description", "SYNR", "SYTJ", "SYGC", "JGPD", "Source_name", "Status"],
    "File": ["Id", "Type", "Label", "Name", "Chroma_id", "Status"],
}

DEFAULT_RETURN_ATTRIBUTES: Dict[str, List[str]] = {
    "EquipmentType": ["Id", "Name", "Type", "Label"],
    "Equipment": ["Id", "Name", "Type", "Label"],
    "Environment": ["Id", "Name", "Type", "Class", "Label"],
    "TestProject": ["Id", "Name", "Type", "Label", "Equipment_name"],
    "Indicator": ["Id", "Name", "Type", "Label", "Equipment_name"],
    "TestMethod": ["Id", "Name", "Class", "Label", "SYNR", "Source_name"],
    "File": ["Id", "Name", "Type", "Label", "Chroma_id"],
}

DEFAULT_TARGETS_BY_INTENT: Dict[str, List[str]] = {
    "列举": ["EquipmentType"],
    "查关联": ["Equipment"],
    "查指标": ["Indicator"],
    "查项目": ["TestProject"],
    "查方法": ["TestMethod"],
    "查文件": ["File"],
}

DEFAULT_START_BY_INTENT: Dict[str, List[str]] = {
    "列举": ["EquipmentType"],
    "查关联": ["EquipmentType"],
    "查指标": ["Equipment"],
    "查项目": ["Equipment"],
    "查方法": ["Equipment"],
    "查文件": ["TestMethod"],
}
