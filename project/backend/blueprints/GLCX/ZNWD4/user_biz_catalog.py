"""
ZNWD4 用户层顶层业务（8 类）+ 场景 → 执行层 business_id 与默认参数形态。
Playbook 展开见 playbook_resolver.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .business_catalog import (
    BIZ_DOCUMENT,
    BIZ_ENUM_NODES,
    BIZ_METHOD_CONTENT,
    BIZ_NODE_KW,
    BIZ_NODE_PROP,
    BIZ_NODE_PROP_REV,
    BIZ_PROJECT_TO_METHOD,
    BIZ_REL_ONE_HOP,
    BIZ_REL_REVERSE_ONE_HOP,
    BIZ_REL_TWO_HOP,
    BIZ_STANDARD_CONDITION,
)

# --- 8 类顶层业务 ID ---
UBIZ_TEST_PROJECT = "UBIZ_TEST_PROJECT"
UBIZ_INDICATOR = "UBIZ_INDICATOR"
UBIZ_TEST_METHOD = "UBIZ_TEST_METHOD"
UBIZ_EQUIPMENT = "UBIZ_EQUIPMENT"
UBIZ_EQUIPMENT_TYPE = "UBIZ_EQUIPMENT_TYPE"
UBIZ_ENVIRONMENT = "UBIZ_ENVIRONMENT"
UBIZ_STANDARD = "UBIZ_STANDARD"
UBIZ_KNOWLEDGE = "UBIZ_KNOWLEDGE"

# --- 拼装 Playbook ---
COMP_PROJECT_THEN_METHOD = "COMP_PROJECT_THEN_METHOD"
COMP_PROJECT_ENV_METHOD = "COMP_PROJECT_ENV_METHOD"
COMP_INDICATOR_THEN_METHOD = "COMP_INDICATOR_THEN_METHOD"
COMP_EQUIP_PROJECT_ENV_SETTINGS = "COMP_EQUIP_PROJECT_ENV_SETTINGS"
COMP_INTENT_PLAN = "COMP_INTENT_PLAN"

USER_BIZ_IDS = (
    UBIZ_TEST_PROJECT,
    UBIZ_INDICATOR,
    UBIZ_TEST_METHOD,
    UBIZ_EQUIPMENT,
    UBIZ_EQUIPMENT_TYPE,
    UBIZ_ENVIRONMENT,
    UBIZ_STANDARD,
    UBIZ_KNOWLEDGE,
    COMP_PROJECT_THEN_METHOD,
    COMP_PROJECT_ENV_METHOD,
    COMP_INDICATOR_THEN_METHOD,
    COMP_EQUIP_PROJECT_ENV_SETTINGS,
    COMP_INTENT_PLAN,
)

USER_BIZ_TITLES: Dict[str, str] = {
    UBIZ_TEST_PROJECT: "试验项目问答",
    UBIZ_INDICATOR: "考核指标问答",
    UBIZ_TEST_METHOD: "试验方法问答",
    UBIZ_EQUIPMENT: "装备问答",
    UBIZ_EQUIPMENT_TYPE: "装备类别问答",
    UBIZ_ENVIRONMENT: "环境类别问答",
    UBIZ_STANDARD: "标准问答",
    UBIZ_KNOWLEDGE: "其他试验知识问答",
    COMP_PROJECT_THEN_METHOD: "试验项目+方法（拼装）",
    COMP_PROJECT_ENV_METHOD: "装备环境+项目+方法（拼装）",
    COMP_INDICATOR_THEN_METHOD: "考核指标+方法（拼装）",
    COMP_EQUIP_PROJECT_ENV_SETTINGS: "装备项目环境设置（推理）",
    COMP_INTENT_PLAN: "多意图检索",
}


@dataclass
class PlaybookStepSpec:
    business_id: str
    param_builder: str  # 见 playbook_resolver._PARAM_BUILDERS
    step_id: str = "s1"


@dataclass
class ScenarioSpec:
    user_biz: str
    scenario: str
    title: str
    steps: List[PlaybookStepSpec]
    answer_strategy: str = "db_direct"
    required_slots: List[str] = field(default_factory=list)


# (user_biz, scenario) -> ScenarioSpec
SCENARIO_REGISTRY: Dict[Tuple[str, str], ScenarioSpec] = {}

# composite_id -> ScenarioSpec（user_biz 字段存 composite id）
COMPOSITE_REGISTRY: Dict[str, ScenarioSpec] = {}
# (composite_id, scenario) -> ScenarioSpec
COMPOSITE_SCENARIO_REGISTRY: Dict[Tuple[str, str], ScenarioSpec] = {}


def _reg(spec: ScenarioSpec) -> None:
    SCENARIO_REGISTRY[(spec.user_biz, spec.scenario)] = spec


def _reg_comp(spec: ScenarioSpec) -> None:
    COMPOSITE_REGISTRY[spec.user_biz] = spec
    COMPOSITE_SCENARIO_REGISTRY[(spec.user_biz, spec.scenario)] = spec


def _reg_comp_scenario(spec: ScenarioSpec) -> None:
    COMPOSITE_SCENARIO_REGISTRY[(spec.user_biz, spec.scenario)] = spec


# 1 试验项目
_reg(
    ScenarioSpec(
        UBIZ_TEST_PROJECT,
        "by_equipment",
        "某装备/类装备有哪些试验项目",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_tp_fwd")],
        required_slots=["equipment_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_PROJECT,
        "by_environment",
        "某环境有哪些试验项目",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_env_tp_fwd")],
        required_slots=["environment_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_PROJECT,
        "by_method",
        "某试验方法涉及哪些试验项目",
        [PlaybookStepSpec(BIZ_REL_REVERSE_ONE_HOP, "rel_tp_tm_rev")],
        required_slots=["method_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_PROJECT,
        "by_equip_and_env",
        "某装备且某环境下的试验项目",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_tp_with_env_filter")],
        answer_strategy="db_direct",
        required_slots=["equipment_keywords", "environment_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_PROJECT,
        "enum",
        "列举全部试验项目",
        [PlaybookStepSpec(BIZ_ENUM_NODES, "enum_testproject")],
    )
)

# 2 考核指标
_reg(
    ScenarioSpec(
        UBIZ_INDICATOR,
        "by_equipment",
        "某装备要考核哪些指标",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_ind_fwd")],
        required_slots=["equipment_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_INDICATOR,
        "by_equip_type",
        "某类装备要考核哪些指标",
        [PlaybookStepSpec(BIZ_REL_TWO_HOP, "rel_et_eq_ind_hop2")],
        required_slots=["equipment_type_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_INDICATOR,
        "list_all",
        "有哪些考核指标",
        [PlaybookStepSpec(BIZ_ENUM_NODES, "enum_indicator")],
    )
)

# 3 试验方法
_reg(
    ScenarioSpec(
        UBIZ_TEST_METHOD,
        "by_project",
        "某试验项目有哪些方法",
        [PlaybookStepSpec(BIZ_PROJECT_TO_METHOD, "project_to_method")],
        required_slots=["project_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_METHOD,
        "by_indicator",
        "某指标关联的方法",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_ind_tm_fwd")],
        required_slots=["indicator_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_METHOD,
        "by_equipment_via_project",
        "某设备经项目对应的方法",
        [PlaybookStepSpec(BIZ_REL_TWO_HOP, "rel_eq_tm_via_tp")],
        required_slots=["equipment_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_METHOD,
        "by_equipment_via_indicator",
        "某设备经指标对应的方法",
        [PlaybookStepSpec(BIZ_REL_TWO_HOP, "rel_eq_tm_via_ind")],
        required_slots=["equipment_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_TEST_METHOD,
        "content",
        "方法正文/步骤/内容",
        [PlaybookStepSpec(BIZ_METHOD_CONTENT, "method_content")],
        answer_strategy="db_direct",
    )
)

# 4 装备
_reg(
    ScenarioSpec(
        UBIZ_EQUIPMENT,
        "list",
        "列举全部具体装备",
        [PlaybookStepSpec(BIZ_ENUM_NODES, "enum_equipment")],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_EQUIPMENT,
        "under_type",
        "某装备类型下有哪些具体装备",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_et_eq_has_fwd")],
        required_slots=["equipment_type_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_EQUIPMENT,
        "by_project",
        "某项目涉及哪些装备",
        [PlaybookStepSpec(BIZ_REL_REVERSE_ONE_HOP, "rel_eq_tp_rev")],
        required_slots=["project_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_EQUIPMENT,
        "by_indicator",
        "某指标涉及哪些装备",
        [PlaybookStepSpec(BIZ_REL_REVERSE_ONE_HOP, "rel_eq_ind_rev")],
        required_slots=["indicator_keywords"],
    )
)

# 5 装备类别
_reg(
    ScenarioSpec(
        UBIZ_EQUIPMENT_TYPE,
        "level1_list",
        "一级装备类别有哪些",
        [PlaybookStepSpec(BIZ_ENUM_NODES, "enum_equipment_type")],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_EQUIPMENT_TYPE,
        "level2_under_level1",
        "某一级类别下二级类别",
        [PlaybookStepSpec(BIZ_NODE_PROP_REV, "et_type_children")],
        required_slots=["equipment_type_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_EQUIPMENT_TYPE,
        "belongs_to",
        "某装备属于哪类装备类型",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_et_eq_has_rev")],
        required_slots=["equipment_keywords"],
    )
)

# 6 环境
_reg(
    ScenarioSpec(
        UBIZ_ENVIRONMENT,
        "list",
        "有哪些试验环境",
        [PlaybookStepSpec(BIZ_ENUM_NODES, "enum_environment")],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_ENVIRONMENT,
        "properties",
        "环境分类/分级/组合/要求/约束",
        [PlaybookStepSpec(BIZ_NODE_PROP, "env_properties")],
        answer_strategy="db_direct",
        required_slots=["environment_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_ENVIRONMENT,
        "for_project",
        "某项目在什么环境/条件下试验",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_env_tp_rev")],
        required_slots=["project_keywords"],
    )
)

# 7 标准
_reg(
    ScenarioSpec(
        UBIZ_STANDARD,
        "for_projects",
        "某试验项目相关标准文件",
        [PlaybookStepSpec(BIZ_REL_TWO_HOP, "rel_tp_tm_file_chain")],
        required_slots=["project_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_STANDARD,
        "methods_in_standard",
        "某标准包含哪些试验方法",
        [PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_tm_file_rev")],
        required_slots=["standard_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_STANDARD,
        "chapter_content",
        "标准章节条文",
        [PlaybookStepSpec(BIZ_DOCUMENT, "document_query")],
        answer_strategy="db_direct_doc",
        required_slots=["standard_keywords"],
    )
)
_reg(
    ScenarioSpec(
        UBIZ_STANDARD,
        "condition_param",
        "标准试验条件/参数（温度、湿度、贮存时长等）",
        [PlaybookStepSpec(BIZ_STANDARD_CONDITION, "standard_condition")],
        answer_strategy="db_synthesize",
        required_slots=["standard_keywords"],
    )
)

# 8 其他知识
_reg(
    ScenarioSpec(
        UBIZ_KNOWLEDGE,
        "keyword_doc",
        "关键词检索标准/知识文档",
        [PlaybookStepSpec(BIZ_DOCUMENT, "document_query")],
        answer_strategy="db_direct_doc",
    )
)

# --- 拼装 Playbook（多步，resolver 内展开为带 ref 的 steps）---
_reg_comp(
    ScenarioSpec(
        COMP_PROJECT_THEN_METHOD,
        "default",
        "先查试验项目再查对应方法",
        [
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_tp_fwd", "s1"),
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_tp_tm_from_s1", "s2"),
        ],
        answer_strategy="db_direct",
    )
)
_reg_comp(
    ScenarioSpec(
        COMP_PROJECT_ENV_METHOD,
        "default",
        "装备+环境下项目及方法",
        [
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_tp_fwd", "s1"),
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_tp_tm_from_s1", "s2"),
        ],
        answer_strategy="db_direct",
        required_slots=["equipment_keywords", "environment_keywords"],
    )
)
_reg_comp(
    ScenarioSpec(
        COMP_INDICATOR_THEN_METHOD,
        "default",
        "先查考核指标再查对应方法",
        [
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_ind_fwd", "s1"),
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_ind_tm_from_s1", "s2"),
        ],
        answer_strategy="db_direct",
        required_slots=["equipment_keywords"],
    )
)
_reg_comp(
    ScenarioSpec(
        COMP_EQUIP_PROJECT_ENV_SETTINGS,
        "default",
        "某装备做某项目时的环境属性设置（分类/分级/组合/约束/要求）",
        [
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_tp_fwd", "s1"),
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_tp_env_from_s1", "s2"),
            PlaybookStepSpec(BIZ_NODE_PROP, "env_prop_from_s2", "s3"),
        ],
        answer_strategy="db_synthesize",
        required_slots=["equipment_keywords"],
    )
)
_reg_comp_scenario(
    ScenarioSpec(
        COMP_EQUIP_PROJECT_ENV_SETTINGS,
        "by_environment",
        "某装备在某环境下的推荐环境设置（分类/分级/组合/约束/要求）",
        [
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_tp_with_env_filter", "s1"),
            PlaybookStepSpec(BIZ_NODE_PROP, "env_properties", "s2"),
        ],
        answer_strategy="db_synthesize",
        required_slots=["equipment_keywords", "environment_keywords"],
    )
)
_reg_comp_scenario(
    ScenarioSpec(
        COMP_EQUIP_PROJECT_ENV_SETTINGS,
        "by_project",
        "某装备做某项目时的环境属性设置（分类/分级/组合/约束/要求）",
        [
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_eq_tp_fwd", "s1"),
            PlaybookStepSpec(BIZ_REL_ONE_HOP, "rel_tp_env_from_s1", "s2"),
            PlaybookStepSpec(BIZ_NODE_PROP, "env_prop_from_s2", "s3"),
        ],
        answer_strategy="db_synthesize",
        required_slots=["equipment_keywords", "project_keywords"],
    )
)
_reg_comp(
    ScenarioSpec(
        COMP_INTENT_PLAN,
        "default",
        "多意图并列检索（意图步展开）",
        [],
        answer_strategy="db_direct",
        required_slots=["equipment_keywords"],
    )
)


def get_scenario_spec(user_biz: str, scenario: str) -> Optional[ScenarioSpec]:
    ub = (user_biz or "").strip()
    sc = (scenario or "default").strip() or "default"
    comp_key = (ub, sc)
    if comp_key in COMPOSITE_SCENARIO_REGISTRY:
        return COMPOSITE_SCENARIO_REGISTRY[comp_key]
    if ub in COMPOSITE_REGISTRY:
        return COMPOSITE_REGISTRY.get(ub)
    return SCENARIO_REGISTRY.get((ub, sc))


def user_biz_prompt_markdown() -> str:
    """路由 LLM 静态核：8 类顶层业务 + 场景表。"""
    lines = [
        "## 顶层业务 user_biz（先选大类，再选 scenario，填槽位）",
        "",
        "槽位（params_draft）：",
        "- equipment_keywords[] 装备/具体装备名",
        "- equipment_type_keywords[] 装备类型/类别",
        "- environment_keywords[] 试验环境",
        "- project_keywords[] 试验项目",
        "- indicator_keywords[] 考核指标",
        "- standard_keywords[] 标准号/文件名",
        "- focus 主关键词（可与上列合并）",
        "",
        "| user_biz | 中文 | scenario | 说明 |",
        "|----------|------|----------|------|",
    ]
    seen_ub: set = set()
    for (ub, sc), spec in sorted(SCENARIO_REGISTRY.items()):
        title = USER_BIZ_TITLES.get(ub, ub)
        if ub not in seen_ub:
            seen_ub.add(ub)
        lines.append(f"| {ub} | {title} | {sc} | {spec.title} |")
    lines.append("")
    lines.append("### 拼装（多步一次答复）")
    lines.append("| user_biz | 说明 |")
    lines.append("|----------|------|")
    for cid, spec in COMPOSITE_REGISTRY.items():
        lines.append(f"| {cid} | {spec.title} |")
    lines.append("")
    lines.append(
        "未命中或 match_confidence=low 时：填 purpose_node + biz_type（fallback），或 composite 留空。"
    )
    return "\n".join(lines)
