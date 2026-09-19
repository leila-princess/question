"""ZNWD2 类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union


class IntentJson(TypedDict, total=False):
    """LLM 意图解析输出。"""

    intent_type: str
    start_entities: List[str]
    start_filters: Optional[Dict[str, str]]
    environment_constraint: Optional[Dict[str, str]]
    target_entities: List[str]
    additional_filters: Optional[Dict[str, str]]


class ActionStep(TypedDict, total=False):
    """路径规划动作步骤。"""

    action_name: str
    node_type: str
    filters: Dict[str, str]
    node_set: str
    node_set_a: str
    node_set_b: str
    relation: str
    direction: str
    condition: Dict[str, str]
    attributes: List[str]


class MacroPlan(TypedDict):
    mode: Literal["macro"]
    macro: str
    params: Dict[str, Any]


class ActionsPlan(TypedDict):
    mode: Literal["actions"]
    steps: List[ActionStep]


ExecutionPlan = Union[MacroPlan, ActionsPlan]


@dataclass
class NodeSet:
    """执行引擎内部节点集合。"""

    label: str
    ids: List[int] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.ids and not self.rows


@dataclass
class ExecutionResult:
    """查询执行结果。"""

    rows: List[Dict[str, Any]]
    target_label: str
    plan_mode: str
    macro_name: Optional[str] = None
    error: Optional[str] = None
