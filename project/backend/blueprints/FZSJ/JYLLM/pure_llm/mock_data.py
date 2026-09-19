"""Neo4j / Chroma 不可用时的示例数据，保证 Demo 可离线跑通。"""
from __future__ import annotations

from typing import Any, Dict, List


def mock_kg_subgraph() -> Dict[str, Any]:
    return {
        "nodes": [
            {"id": "eqt_uav", "label": "EquipmentType", "name": "多旋翼无人机"},
            {"id": "eq_m350", "label": "Equipment", "name": "Matrice 350 RTK", "type": "多旋翼"},
            {"id": "env_cold", "label": "Environment", "name": "低温环境", "class": "温度与高度"},
            {"id": "proj_start", "label": "TestProject", "name": "低温发动机启动特性试验"},
            {"id": "ind_start", "label": "Indicator", "name": "发动机启动时间"},
            {"id": "tm_prep", "label": "TestMethod", "name": "试验前检查与状态记录"},
            {"id": "tm_cold", "label": "TestMethod", "name": "低温工作试验", "SYTJ": "-30℃", "SYNR": "按 GB/T 38924.2"},
            {"id": "tm_rec", "label": "TestMethod", "name": "恢复至正常大气条件"},
        ],
        "edges": [
            ("eqt_uav", "eq_m350", "HAS"),
            ("eq_m350", "proj_start", "CON"),
            ("env_cold", "proj_start", "CON"),
            ("eq_m350", "ind_start", "CON"),
            ("proj_start", "tm_prep", "CON"),
            ("proj_start", "tm_cold", "CON"),
            ("ind_start", "tm_cold", "CON"),
            ("proj_start", "tm_rec", "CON"),
        ],
    }


def mock_action_candidates() -> List[Dict[str, Any]]:
    return [
        {
            "id": "tm_prep",
            "name": "试验前检查与状态记录",
            "label": "试验前检查",
            "SYTJ": "正常大气条件",
            "SYGC": "外观、结构、功能基线检测",
            "JGPD": "记录初始状态",
            "SYNR": "预处理与试验前检测",
            "source": "mock",
        },
        {
            "id": "tm_install",
            "name": "安装低温辅助装置",
            "label": "安装",
            "SYTJ": "按产品规范",
            "SYGC": "安装保温/加热辅助件",
            "JGPD": "安装牢固",
            "SYNR": "受试设备安装",
            "source": "mock",
        },
        {
            "id": "tm_setbox",
            "name": "设定温箱至试验温度",
            "label": "环境设定",
            "SYTJ": "-30℃",
            "SYGC": "降温速率≤3℃/min",
            "JGPD": "箱温达到设定值",
            "SYNR": "环境参数建立",
            "source": "mock",
        },
        {
            "id": "tm_cold",
            "name": "低温工作试验",
            "label": "低温工作",
            "SYTJ": "-30℃，稳定后保持2h",
            "SYGC": "不工作保持→按规范工作测试",
            "JGPD": "功能性能满足规范",
            "SYNR": "GB/T 38924.2 低温工作",
            "source": "mock",
        },
        {
            "id": "tm_rec",
            "name": "恢复至正常大气条件",
            "label": "恢复",
            "SYTJ": "15～35℃",
            "SYGC": "≤3℃/min 升温恢复",
            "JGPD": "温度稳定",
            "SYNR": "试验后检测",
            "source": "mock",
        },
    ]


def mock_chroma_hits() -> List[Dict[str, Any]]:
    return [
        {
            "id": "mock_gbt38924_2",
            "content": "GB/T 38924.2—2020 低温试验：试验温度由产品规范规定；温度变化率≤3℃/min；稳定后保持时间按工作/贮存区分。",
            "metadata": {"standard": "GB/T 38924.2—2020"},
            "distance": 0.12,
        },
        {
            "id": "mock_gbt38924_1",
            "content": "GB/T 38924.1—2020：温度稳定判据——不工作时功能部件达到试验温度；工作时变化率≤2.0℃/h。",
            "metadata": {"standard": "GB/T 38924.1—2020"},
            "distance": 0.18,
        },
    ]
