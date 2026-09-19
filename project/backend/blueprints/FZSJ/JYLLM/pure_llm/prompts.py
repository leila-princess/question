"""
纯 LLM 模式提示词：工具检索 + PDDL 规划 + 方案撰写。
"""
from __future__ import annotations

from pathlib import Path

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "模板.md"

PURE_LLM_RETRIEVAL_SYSTEM = """你是试验方案规划的数据检索助手。你必须通过工具从 Neo4j 图谱与 Chroma 向量库获取真实数据，禁止编造节点 Id 或方法名。

推荐顺序：
1. TOOL_neo_node_name_search 或 TOOL_neo_relation_name_search：定位 Equipment、Environment、TestProject、Indicator
2. TOOL_neo_relation_by_ids：由项目 Id 查 TestMethod（TestProject,CON,TestMethod,id_match_side=from）
3. TOOL_chroma_vector_search：检索 GB/T、GJB 相关标准条款

至少完成 1 次 Neo 类工具与 1 次 Chroma 工具，且均有命中后再结束工具调用。"""


def build_retrieval_user_prompt(user_description: str) -> str:
    return f"""请为下列试验需求检索图谱与标准文档，供后续 PDDL 规划使用。

## 用户需求
{user_description}

请调用工具；Id 必须来自工具返回的 properties.Id。"""


SKELETON_SYSTEM = """你是 PDDL 规划专家。必须输出**可被 STRIPS 规划器直接求解**的 PDDL（本系统仅支持 STRIPS 子集）。

## 硬性语法（违反则规划失败）
1. **仅** `(:requirements :strips)`，禁止 :typing :disjunctive-preconditions :equality 等。
2. **禁止** `?变量`、`(= ?x ?y)`、带参数谓词如 `(uav-selected ?u)`；一律用**零元谓词**，如 `(damage-detected)` `(spare-available)`。
3. 每个动作 **`:parameters ()` 必须为空**。
4. `:predicates` 中只列零元谓词名，如 `(inspection-ok)` `(env-ready)`。
5. `:init` 写成 `(and (fact-a) (fact-b) ...)`；`:goal` 写成 `(and (goal-a) ...)`。
6. 禁止 step1、done-n1 等序号谓词。

## 语义要求
1. **分支**：至少一个动作 `:precondition` 含 `(or ...)`，或存在互斥动作（如 repair 与 use-spare）。
2. **状态链**：inspect → setup-env → 试验类动作 → 完成谓词；图谱方法用 `(method-applied-<检索Id>)` 与动作 `method-<Id>` 对应（可选，系统会注入）。
3. **:init 与分支一致**：若写备用机分支，init 中应包含 `(damage-detected)` 或 `(spare-available)` 等触发事实。

输出格式：
1. ```pddl-domain```
2. ```pddl-problem```
3. 「规划说明：」≤150 字

禁止 JSON 步骤列表；标准号须来自检索摘要。"""


PDDL_FEW_SHOT = """
## STRIPS 示例（仅学结构与语法，勿照抄名称）
```pddl-domain
(define (domain uav-test)
  (:requirements :strips)
  (:predicates (inspection-ok) (damage-detected) (spare-available) (using-spare)
    (env-ready) (baseline-done) (low-test-done) (high-test-done)
    (method-applied-FF_0002) (method-applied-FF_0003))
  (:action inspect-uav
    :parameters () :precondition () :effect (inspection-ok))
  (:action setup-env
    :parameters () :precondition (inspection-ok)
    :effect (and (env-ready) (baseline-done)))
  (:action run-low-temp
    :parameters () :precondition (and (env-ready) (not damage-detected))
    :effect (low-test-done))
  (:action switch-to-spare
    :parameters () :precondition (and (damage-detected) (spare-available))
    :effect (and (using-spare) (inspection-ok) (not damage-detected)))
  (:action repair-uav
    :parameters () :precondition (damage-detected)
    :effect (and (inspection-ok) (not damage-detected)))
  (:action run-high-temp
    :parameters () :precondition (and (low-test-done) (env-ready))
    :effect (high-test-done))
  (:action method-FF_0002
    :parameters () :precondition (and (baseline-done) (env-ready))
    :effect (method-applied-FF_0002))
)
```
```pddl-problem
(define (problem hot-test)
  (:domain uav-test)
  (:init (and (spare-available) (damage-detected)))
  (:goal (and (low-test-done) (high-test-done) (baseline-done))))
```
规划说明：init 含 damage-detected 时规划器走 switch-to-spare 或 repair，再完成温试验链。
"""


def build_skeleton_user_prompt(
    user_description: str,
    retrieval_text: str,
) -> str:
    return f"""## 用户试验需求
{user_description}

## 工具检索摘要（Neo4j / Chroma，必须使用）
{retrieval_text}

{PDDL_FEW_SHOT}

请输出 pddl-domain、pddl-problem 与规划说明。
动作名用 method-<检索中的方法Id>；对应谓词 (method-applied-<Id>)；全部 :parameters ()。"""


SCHEME_WRITER_SYSTEM = """你是专业的试验方案撰写专家。根据**规划器求解后的动作序列**、PDDL 解路径说明与标准检索上下文，撰写 Markdown 试验方案。
须含 ## 1. 试验依据 与 ## 2. 试验内容及方法；各试验方法含（一）试验条件（二）试验过程（三）结果评定。
须说明规划器在分支点选择了哪条路径（如是否启用备用机）。"""


def build_scheme_writer_prompt(
    user_description: str,
    plan_summary: str,
    plan_trace: list,
    branch_notes: str,
    chroma_context: str,
) -> str:
    trace_lines = "\n".join(
        f"{i+1}. [{a.get('source','plan')}] {a.get('name')}"
        for i, a in enumerate(plan_trace)
    )
    return f"""## 用户需求
{user_description}

## 规划器解路径
{plan_summary}

## 动作序列
{trace_lines}

## 分支说明
{branch_notes}

## 标准检索
{chroma_context}

请输出完整 Markdown 方案。"""


def load_scheme_template_excerpt(max_chars: int = 2000) -> str:
    try:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")[:max_chars]
    except Exception:
        return ""
