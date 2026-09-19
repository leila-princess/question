"""
JYLLM 多智能体协作方案生成引擎

功能说明:
==========
本系统实现了一个多智能体协作的试验方案生成引擎，采用类似LangGraph的工作流架构。

核心流程:
----------
Phase 1 (Planner): 用户选择试验条件后，Planner智能体根据模板和条件规划方案章节结构
Phase 2 (生成):   用户确认章节大纲后，Writer和Reviewer智能体协作生成各章节内容

多智能体协作机制:
----------------
1. Planner (规划器)
   - 根据用户选择的设备、环境、项目、指标、方法等条件
   - 结合试验方案模板，生成章节大纲
   - 每个章节包含: index(序号), title(标题), writer_prompt(写作要求), reviewer_prompt(评审要求)

2. Writer (写作智能体)
   - 根据章节标题和写作要求，调用LLM生成章节内容
   - 输出格式为JSON，包含:
     * thinking_process: 写作思考过程
     * content: 章节正文内容(Markdown格式)
     * keywords: 检索关键词
     * search_context: 检索返回的参考资料
   - 使用Chroma向量数据库进行混合检索，获取相关参考资料

3. Reviewer (评审智能体)
   - 对Writer生成的内容进行评审
   - 给出评分(score)和评审意见(review)
   - 判断是否通过(is_pass)

4. 写-审循环 (Writer-Reviewer Loop)
   - 如果Reviewer评分低于阈值(CHAPTER_PASS_SCORE=80)，Writer需要重写
   - 最多循环MAX_REVIEW_LOOPS=3次
   - 每次重写时会将Reviewer的评审意见作为参考

流式输出机制:
-------------
- 使用Server-Sent Events (SSE)实现实时流式输出
- emit_event()函数生成符合SSE格式的事件数据
- 前端通过EventSource API接收并实时渲染

事件类型:
- start: 任务开始
- thinking: 思考过程/状态更新
- writer_stream: Writer流式输出内容
- chapter_complete: 章节生成完成
- complete: 全部章节生成完成
- planner_complete: Planner规划完成
"""

import json
import uuid
import re
import logging
import os
import time
from typing import Generator, Dict, Any, List, Optional, TypedDict, Annotated, Callable, Union, Tuple
from pathlib import Path
from functools import reduce
from config import Config
from blueprints.FZSJ.JYLLM.search_tool import get_search_tool
from blueprints.FZSJ.JYLLM.prompts import get_prompt
from blueprints.FZSJ.JYLLM.project_tree_utils import (
    flatten_indicator_headings_for_validation,
    flatten_project_headings_for_validation,
    rebuild_indicator_forest,
    rebuild_project_forest,
)
from services.jyllm_history_manager import get_history_manager
from blueprints.XTGL.MXGL.routes import resolve_llm_model
from utils.llm_context_budget import (
    _text_from_chat_completion,
    _is_transient_llm_error,
    iter_chat_completion_text,
    openai_client_from_mxgl_item,
)

logger = logging.getLogger(__name__)

# ==================== 配置常量 ====================
# 章节通过的最低评分阈值，低于此分数需要重写
CHAPTER_PASS_SCORE = 80
# 最大写-审循环次数，防止无限重写
MAX_REVIEW_LOOPS = 3
# 第2章结构校验失败时，额外触发 Writer 全章重写的次数（与 MAX_REVIEW_LOOPS 独立）
MAX_CH2_STRUCTURE_RETRIES = 1

_CH2_FORBIDDEN_ENV_TITLES = frozenset(
    {"试验环境", "地理环境", "气候环境", "复杂电磁环境", "电磁环境", "振动环境", "温度环境"}
)

# 任务存储: 用于保存各个任务的生成状态
# key: task_id, value: 任务状态字典
_active_tasks: Dict[str, Dict[str, Any]] = {}
# 方案模板文件路径
TEMPLATE_PATH = Path(__file__).parent / "模板.md"

DEFAULT_CHAPTER_TITLES = [
    "1. 试验依据",
    "2. 试验内容及方法",
]

# Planner 须在第二章 writer_prompt 中保留的分段标记（用于有 2.2 时拆成两次写作，保证与规划树一致）
MARK_PLAN_21 = "---PLAN_SEGMENT_2_1---"
MARK_PLAN_22 = "---PLAN_SEGMENT_2_2---"

LOCKED_CH2_INTRO = (
    "## 【系统锁定·标题树】\n"
    "以下为根据用户**已选**设备/环境/项目/指标/方法生成的**固定标题骨架**。\n"
    "- **不得删除、合并、重排或改写**下列 `###/####/#####/######` 标题行中的**编号与条目个数**；名称须与用户数据**一致**。\n"
    "- 方法正文须直接写在对应**父级/子级项目**标题下，**禁止**再增加「试验方法」或方法标准名等独立 `######` 标题行。\n"
    "- 无图库方法时须在项目标题下写出完整（一）（二）（三）**技术正文**（优先加粗行，勿用 `#` 标题），**禁止**把规划说明写入正文。\n"
    "- **LLM 可发挥范围**：仅限各项目标题下的技术叙述、参数、步骤、判据；**不得**新增/替换用户未选的试验项目、环境类别或考核指标标题行。\n\n"
)


def _user_choice_label(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, dict):
        return str(
            item.get("name")
            or item.get("Name")
            or item.get("label")
            or item.get("title")
            or item.get("id")
            or ""
        ).strip() or "未命名"
    return str(item).strip() or "未命名"


def _entity_match_keys(item: Any) -> List[str]:
    if not isinstance(item, dict):
        s = str(item or "").strip()
        return [s] if s else []
    keys: List[str] = []
    for k in ("id", "name", "Name", "label", "title"):
        v = item.get(k)
        if v is not None and str(v).strip():
            keys.append(str(v).strip())
    return keys or ([] if not item else ["未命名"])


def _methods_linked_to_project(methods: Optional[List], project_item: Any) -> List[Dict[str, Any]]:
    pkeys = set(_entity_match_keys(project_item))
    out: List[Dict[str, Any]] = []
    for m in methods or []:
        if not isinstance(m, dict):
            continue
        apps = m.get("applicableProjects") or m.get("applicable_projects") or m.get("projects") or []
        hit = False
        for a in apps:
            ak = _user_choice_label(a) if isinstance(a, dict) else str(a).strip()
            if ak and pkeys and any(ak == pk or pk in ak or ak in pk for pk in pkeys):
                hit = True
                break
        if hit:
            out.append(m)
    return out


def _methods_linked_to_indicator(methods: Optional[List], indicator_item: Any) -> List[Dict[str, Any]]:
    ikeys = set(_entity_match_keys(indicator_item))
    out: List[Dict[str, Any]] = []
    for m in methods or []:
        if not isinstance(m, dict):
            continue
        apps = m.get("applicableIndicators") or m.get("applicable_indicators") or m.get("indicators") or []
        hit = False
        for a in apps:
            ak = _user_choice_label(a) if isinstance(a, dict) else str(a).strip()
            if ak and ikeys and any(ak == ik or ik in ak or ak in ik for ik in ikeys):
                hit = True
                break
        if hit:
            out.append(m)
    return out


def _nonempty_user_list(items: Optional[List]) -> List[Any]:
    if not items or not isinstance(items, list):
        return []
    out: List[Any] = []
    for it in items:
        if isinstance(it, dict):
            if not it:
                continue
            if _user_choice_label(it) != "未命名":
                out.append(it)
            elif any(v not in (None, "", [], {}) for v in it.values()):
                out.append(it)
        elif str(it).strip():
            out.append(it)
    return out


def build_locked_ch2_segments_21_22(
    environments: Optional[List],
    projects: Optional[List],
    indicators: Optional[List],
    methods: Optional[List],
    include_section_2_2: bool,
) -> Tuple[str, str]:
    """
    由用户选择生成 2.1 / 2.2 两段**锁定标题树**（Markdown 行），供插入 writer_prompt 的 PLAN_SEGMENT 间。
    与 skeleton_builder.build_method_tree_and_skeleton 编号一致（多环境、父/子项目与指标）。
    """
    _ = methods
    from blueprints.FZSJ.JYLLM.skeleton_builder import build_locked_ch2_heading_segments

    return build_locked_ch2_heading_segments(
        environments,
        projects,
        indicators if include_section_2_2 else [],
        include_section_2_2,
    )


def merge_chapter2_writer_prompt_with_locked_tree(
    writer_req: str,
    environments: Optional[List],
    projects: Optional[List],
    indicators: Optional[List],
    methods: Optional[List],
    include_section_2_2: bool,
) -> str:
    """将用户锁定标题树插入第2章 writer_prompt（保留 Planner 细化说明）。"""
    s21, s22 = build_locked_ch2_segments_21_22(
        environments, projects, indicators, methods, include_section_2_2
    )
    pre, old_s21, old_s22 = extract_ch2_writer_plan_segments(writer_req or "")

    def _merge_seg(locked: str, old: str, label: str) -> str:
        o = (old or "").strip()
        if not o:
            return locked.strip()
        return f"{locked.strip()}\n\n【Planner·{label}】\n{o}"

    s21_m = _merge_seg(s21, old_s21, "对 2.1 的细化说明（须在不删改锁定标题前提下采纳）")
    if include_section_2_2:
        s22_m = _merge_seg(s22, old_s22, "对 2.2 的细化说明（须在不删改锁定标题前提下采纳）")
    else:
        s22_m = s22
    core = f"{LOCKED_CH2_INTRO}{MARK_PLAN_21}\n{s21_m}\n{MARK_PLAN_22}\n{s22_m}"
    pre_st = (pre or "").strip()
    if MARK_PLAN_21 in (writer_req or ""):
        return f"{pre_st}\n\n{core}".strip() if pre_st else core
    if pre_st:
        return f"{core}\n\n## 【Planner 全文要点】\n{pre_st}".strip()
    return core


def _strip_leading_think_blocks(md: str) -> str:
    if not md or not isinstance(md, str):
        return md or ""
    return re.sub(r"\[\s*THINK\s*\][\s\S]*?\[\s*/\s*THINK\s*\]\s*", "", md, flags=re.IGNORECASE).strip()


# Writer：[THINK] / [CONTENT] 流式与收尾解析（兼容大小写、标签内侧空格）
_RE_W_THINK_OPEN = re.compile(r"\[\s*THINK\s*\]", re.IGNORECASE)
_RE_W_THINK_CLOSE = re.compile(r"\[\s*/\s*THINK\s*\]", re.IGNORECASE)
_RE_W_CONTENT_OPEN = re.compile(r"\[\s*CONTENT\s*\]", re.IGNORECASE)
_RE_W_CONTENT_CLOSE = re.compile(r"\[\s*/\s*CONTENT\s*\]", re.IGNORECASE)


def _strip_planning_leak_phrases(md: str) -> str:
    """去掉锁定树/规划阶段易泄漏进正文的说明性短语。"""
    if not md or not isinstance(md, str):
        return md or ""
    out = md
    out = re.sub(r"[（(]无关联方法[^）\n]*[）)]", "（试验方法）", out)
    out = re.sub(r"无关联方法[—\-][^。\n]*", "", out)
    out = re.sub(r"须依据向量检索与规范补全[（(]一[）)][（(]二[）)][（(]三[）)]", "", out)
    out = re.sub(r"\[\s*THINK\s*\]|\[\s*/\s*THINK\s*\]|\[\s*CONTENT\s*\]|\[\s*/\s*CONTENT\s*\]", "", out, flags=re.IGNORECASE)
    return out


def _validate_ch2_structure(content: str, expected_projects: List[Any]) -> Tuple[bool, List[str]]:
    """
    校验第2章试验项目标题树：#### 环境、##### 父级/孤立项目、###### 子级项目。
    """
    errors: List[str] = []
    text = (content or "").strip()
    if not text:
        errors.append("第2章正文为空")
        return False, errors
    projs_flat = _nonempty_user_list(expected_projects or [])
    if not projs_flat:
        return True, []

    forest = rebuild_project_forest(projs_flat)
    expected = flatten_project_headings_for_validation(forest, "2.1.1")

    pat_proj = re.compile(r"(?m)^(#{5,6})\s+(2\.1\.\d+(?:\.\d+)+)\s+(.+?)\s*$")
    found: List[Tuple[int, str, str]] = []
    for marks, num, title in pat_proj.findall(text):
        lvl = len(marks)
        if lvl in (5, 6):
            found.append((lvl, num.strip(), title.strip()))

    def _title_match(exp: str, act: str) -> bool:
        exp, act = exp.strip(), act.strip()
        if not exp or not act:
            return False
        return exp == act or exp in act or act in exp

    if len(found) != len(expected):
        errors.append(
            f"试验项目标题数量不符：预期 {len(expected)} 个（含父/子级），实际 {len(found)} 个"
        )

    for i in range(min(len(found), len(expected))):
        exp_lvl, exp_name = expected[i]
        act_lvl, _act_no, act_name = found[i]
        if act_lvl != exp_lvl:
            marks = "#" * exp_lvl
            errors.append(
                f"第 {i + 1} 项应为 {marks} 级「{exp_name}」，实际为 {('#' * act_lvl)} 级「{act_name}」"
            )
        elif not _title_match(exp_name, act_name):
            errors.append(f"第 {i + 1} 项标题应为「{exp_name}」，实际「{act_name}」")

    menv = re.search(r"(?m)^####\s+2\.1\.1\s+(.+?)\s*$", text)
    if menv:
        env_t = menv.group(1).strip()
        if env_t in _CH2_FORBIDDEN_ENV_TITLES:
            errors.append(f"#### 2.1.1 使用了泛称「{env_t}」，须改为用户配置的具体环境名称")

    # 禁止独立「试验方法」占位标题行（方法正文应挂在项目标题下）
    for _no, title in [(n, t) for _l, n, t in found]:
        t = title.strip()
        if t in ("试验方法", "方法补全") or t.endswith("试验方法"):
            errors.append(f"禁止出现独立方法标题行：{_no} {t}")
            break

    return len(errors) == 0, errors


def parse_writer_tag_blocks(text: str, allow_unclosed: bool = False) -> Tuple[str, str]:
    """
    从 Writer 全文提取 [THINK] 与 [CONTENT] 块（不含标签）。
    allow_unclosed=True 时：若存在 [CONTENT] 但缺少 [/CONTENT]，则将 [CONTENT] 之后全文视为正文。
    """
    if not (text or "").strip():
        return "", ""
    thinking = ""
    scan_for_content = 0
    mto = _RE_W_THINK_OPEN.search(text)
    if mto:
        rest = text[mto.end() :]
        mtc = _RE_W_THINK_CLOSE.search(rest)
        if mtc:
            thinking = rest[: mtc.start()].strip()
            scan_for_content = mto.end() + mtc.end()
        else:
            scan_for_content = mto.end()
    mco = _RE_W_CONTENT_OPEN.search(text, scan_for_content)
    content = ""
    if mco:
        rest_c = text[mco.end() :]
        mcc = _RE_W_CONTENT_CLOSE.search(rest_c)
        if mcc:
            content = rest_c[: mcc.start()].strip()
        elif allow_unclosed:
            content = rest_c.strip()
    return thinking, content


def _has_selected_indicators(indicators: Optional[List]) -> bool:
    if not indicators:
        return False
    for item in indicators:
        if isinstance(item, dict):
            n = str(item.get("name") or item.get("Name") or item.get("id") or "").strip()
            if n:
                return True
        elif str(item).strip():
            return True
    return False


def _first_environment_label(environments: Optional[List]) -> str:
    """用户在「环境条件」中第一条可展示名称，供 #### 2.1.1 标题使用。"""
    if not environments or not isinstance(environments, list):
        return ""
    for it in environments:
        if isinstance(it, dict):
            n = str(
                it.get("name")
                or it.get("Name")
                or it.get("label")
                or it.get("title")
                or it.get("id")
                or ""
            ).strip()
            if n:
                return n
        elif isinstance(it, str) and it.strip():
            return it.strip()
    return ""


def _method_graph_block_for_writer(methods: Optional[List]) -> str:
    """将所选方法及图库 TestMethod 的 SYNR（优先）与 SYTJ/SYGC/JGPD 提供给 Writer。"""
    try:
        from blueprints.FZSJ.JYLLM.method_synr_markdown import synr_html_is_substantial
    except Exception:

        def synr_html_is_substantial(s: str) -> bool:
            return bool((s or "").strip())

    if not methods or not isinstance(methods, list):
        return (
            "当前未附带方法列表；若写作要求中仍列出具体试验方法，则：有 **SYNR** 时在该方法下写连续正文；"
            "无实质 SYNR 时仍须写完整（一）试验条件、（二）试验过程、（三）结果评定，并结合**向量检索资料**补全，"
            "不得仅留空标题；无图库字段时须在 **[THINK]** 中说明依据，**禁止**在正文中写「图库缺失」类元话语。"
        )
    chunks: List[str] = []
    _synr_cap = 6000
    for item in methods:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("Name") or item.get("id") or "").strip() or "未命名方法"
            synr = ""
            sytj, sygc, jgpd = "", "", ""
            for k, v in item.items():
                lk = str(k).lower()
                if lk in ("synr", "method_content") and v is not None:
                    synr = str(v).strip()
                elif lk == "sytj" and v is not None and str(v).strip():
                    sytj = str(v).strip()
                elif lk == "sygc" and v is not None and str(v).strip():
                    sygc = str(v).strip()
                elif lk == "jgpd" and v is not None and str(v).strip():
                    jgpd = str(v).strip()
        else:
            name = str(item).strip() or "未命名方法"
            synr, sytj, sygc, jgpd = "", "", "", ""

        if synr and synr_html_is_substantial(synr):
            preview = synr if len(synr) <= _synr_cap else (
                synr[:_synr_cap] + "\n\n…（以上为节选；完整 SYNR 以用户消息中该方法字段为准。）"
            )
            chunks.append(
                f"### 方法「{name}」\n"
                "- **SYNR（试验方法内容）**：图库已给出实质正文。\n"
                "- **正文要求**：在对应**项目/指标标题紧下方****只输出**由 SYNR 整理后的**连续试验方法正文**；"
                "**不要**再设「（一）试验条件」「（二）试验过程」「（三）结果评定」子标题，也**不要**再按 SYTJ、SYGC、JGPD 分三段复述；**禁止**增加方法名或「试验方法」独立标题行。\n"
                "- **SYNR 节选（供核对，可含 HTML）**：\n\n"
                f"{preview}\n"
            )
            continue

        _empty = "（图库未返回该字段或为空——须在正文补全可操作内容，并在 [THINK] 中说明来自向量检索或模型补全，**勿**在正文写声明句）"
        chunks.append(
            f"### 方法「{name}」\n"
            f"- **SYTJ（试验条件 → 正文「（一）试验条件」）**：{sytj or _empty}\n"
            f"- **SYGC（试验过程 → 正文「（二）试验过程」）**：{sygc or _empty}\n"
            f"- **JGPD（结果评定 → 正文「（三）结果评定」）**：{jgpd or _empty}\n"
        )
    if not chunks:
        return (
            "方法列表存在但未能解析出方法名称；撰写第2章时须按写作要求：有 SYNR 则只写连续正文，否则按（一）（二）（三）与**向量检索**补全，"
            "不得留空标题；无图库字段时在 **[THINK]** 说明来源，**勿**在正文写「模型补全」类句子。"
        )
    head = (
        "以下为所选试验方法及**知识图谱 TestMethod** 字段。**若某方法已给出实质 SYNR（试验方法内容）**："
        "Writer 在对应**项目/指标标题紧下方**仅依据 SYNR 输出连续方法正文，**禁止**再使用（一）（二）（三）及 SYTJ/SYGC/JGPD 分段结构，**禁止**独立方法名或「试验方法」标题行。"
        "**若 SYNR 无实质内容**：下列 **SYTJ / SYGC / JGPD** 有非空值则须优先采信并分别写入（一）（二）（三）；"
        "若仍缺项，Writer **仍须**写出完整、可操作的（一）（二）（三），并结合**向量检索资料**补全；"
        "**数据来源与缺口说明须写在 [THINK]**，**禁止**在 [CONTENT] 正文中出现「图库缺失」「模型依据检索补全」等元话语。\n\n"
    )
    return head + "\n\n".join(chunks)


def _writer_method_graph_block(chapter_title: str, methods: Optional[List]) -> str:
    t = chapter_title or ""
    if "试验内容及方法" in t:
        return _method_graph_block_for_writer(methods)
    return "（本章为「试验依据」，不涉及试验方法节点 SYNR / SYTJ / SYGC / JGPD。）"


def load_template() -> str:
    """
    加载试验方案模板
    模板定义了方案的标准章节结构，Writer生成内容时需要遵循此格式
    """
    try:
        with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"加载模板失败: {e}")
        return ""


def _resolve_jyllm_client_and_model(
    model: Optional[str], llm_model_key: str
) -> Tuple[Any, str, str]:
    """
    未选 llm_model_key 时与改前一致：仅用 Config.OPENAI_CLIENT + OPENAI_MODEL（.env）。
    仅在用户显式选了「模型管理」项时才用库里的 api_url / model_name。
    """
    client = Config.OPENAI_CLIENT
    model_name = (model or Config.OPENAI_MODEL or "").strip()
    api_url = (getattr(Config, "OPENAI_BASE_URL", None) or "").strip()
    key = (llm_model_key or "").strip()
    if not key:
        return client, model_name, api_url

    try:
        cfg = resolve_llm_model(key, "")
        model_name = (model or cfg.get("model_name") or model_name or "").strip()
        api_url = (cfg.get("api_url") or api_url or "").strip()
        mx = openai_client_from_mxgl_item(cfg)
        if mx:
            client = mx
    except Exception as e:
        logger.warning("[JYLLM] resolve_llm_model(%s) failed, fallback .env: %s", key, e)
    return client, model_name, api_url


def call_llm_stream(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str = None,
    llm_model_key: str = "",
) -> Generator[Union[str, Dict[str, Any]], None, None]:
    """流式调用 LLM。默认走 .env 的 OPENAI_CLIENT；仅在下拉选了模型 key 时用模型管理配置。"""
    client, model_name, api_url = _resolve_jyllm_client_and_model(model, llm_model_key)
    if not client:
        yield "【错误】LLM客户端未初始化（检查 .env 的 OPENAI_BASE_URL / OPENAI_ALLOW_EMPTY_KEY）"
        return
    if not model_name:
        yield "【错误】未配置模型名（OPENAI_MODEL 或模型管理）"
        return

    logger.info(
        "[JYLLM] LLM stream model=%s base_url=%s max_tokens=%s llm_key=%s",
        model_name,
        api_url or "(env client)",
        max_tokens,
        (llm_model_key or "").strip() or "(env)",
    )
    total_chars = 0
    try:
        for piece in iter_chat_completion_text(
            client,
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=int(max_tokens),
        ):
            if piece:
                total_chars += len(str(piece))
                yield str(piece)
        logger.info("[JYLLM] LLM done model=%s chars=%s", model_name, total_chars)
        if total_chars == 0:
            logger.error("[JYLLM] LLM 返回 0 字符 model=%s base_url=%s", model_name, api_url)
    except Exception as e:
        logger.error(
            "LLM流式调用失败: %s (model=%s, base_url=%s)",
            e,
            model_name,
            api_url or "(未配置)",
        )
        hint = ""
        err_s = str(e).lower()
        if "connection" in err_s or "connect" in err_s or "resolve" in err_s:
            hint = (
                f"\n\n请检查：① 本机 LLM 服务是否已启动；② `.env` 或「模型管理」中 api_url 是否为可访问地址"
                f"（当前: {api_url or '未配置'}）；③ 无网环境勿使用公网 dashscope 地址。"
            )
        elif "502" in err_s or "bad gateway" in err_s or "503" in err_s or "504" in err_s:
            hint = (
                f"\n\nLLM 推理服务暂时不可用（502/503/504）。请检查："
                f"① {api_url or '模型服务'} 上 vLLM 是否正常；"
                f"② 运行 JYLLM 的本机能否直连该地址（Windows 系统代理可能导致内网 502）；"
                f"③ GPU/显存是否已满；④ 稍后重试。"
            )
        yield f"\n\n【生成出错】{e}{hint}"


def call_llm_nonstream(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str = None,
    llm_model_key: str = "",
) -> str:
    """仅非流式一次请求（流式无 delta 时的兜底）。"""
    client, model_name, api_url = _resolve_jyllm_client_and_model(model, llm_model_key)
    if not client or not model_name:
        return ""
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=int(max_tokens),
                stream=False,
            )
            text = _text_from_chat_completion(resp)
            logger.info(
                "[JYLLM] LLM nonstream model=%s chars=%s base_url=%s",
                model_name,
                len(text or ""),
                api_url or "(env)",
            )
            return text or ""
        except Exception as e:
            last_err = e
            if attempt < 2 and _is_transient_llm_error(e):
                wait_s = 1.5 * (attempt + 1)
                logger.warning(
                    "[JYLLM] LLM nonstream transient error attempt=%s: %s; retry in %.1fs",
                    attempt + 1,
                    e,
                    wait_s,
                )
                time.sleep(wait_s)
                continue
            logger.error("[JYLLM] LLM nonstream failed: %s", e)
            return f"【生成出错】{e}"
    if last_err:
        return f"【生成出错】{last_err}"
    return ""


def call_llm(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str = None,
    llm_model_key: str = "",
) -> str:
    """非流式调用 LLM（评审等）。"""
    out = "".join(
        x
        for x in call_llm_stream(messages, temperature, max_tokens, model, llm_model_key=llm_model_key)
        if isinstance(x, str)
    )
    if (out or "").strip():
        return out
    return call_llm_nonstream(
        messages, temperature, max_tokens, model, llm_model_key=llm_model_key
    )


def sanitize_scheme_markdown_body(md: str) -> str:
    """
    从方案正文中移除误写入的「图库/模型补全」元话语（应只出现在 [THINK]）。
    并剔除误泄漏进正文的 [THINK]…[/THINK]、[CONTENT]…[/CONTENT] 块及未闭合标签残片。
    """
    if md is None:
        return ""
    if not isinstance(md, str):
        md = str(md)
    out = md
    out = re.sub(r"\[\s*THINK\s*\][\s\S]*?\[\s*/\s*THINK\s*\]", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\[\s*CONTENT\s*\][\s\S]*?\[\s*/\s*CONTENT\s*\]", "", out, flags=re.IGNORECASE)
    out = _strip_planning_leak_phrases(out)
    if not out.strip():
        return ""
    out_lines: List[str] = []
    for line in out.split("\n"):
        s = line.strip()
        if not s:
            out_lines.append(line)
            continue
        if len(s) > 120:
            out_lines.append(line)
            continue
        if "图库缺失" in s and "补全" in s:
            continue
        if "图库未返回" in s and len(s) <= 80:
            continue
        if "模型依据检索" in s and "补全" in s:
            continue
        if "依据检索与方案上下文补全" in s:
            continue
        if re.match(r"^以下(内容|部分)依据检索", s):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


_CH1_FORBIDDEN_H3 = re.compile(
    r"向量|检索资料|结果分析|试验结果|文档概述|数据库检索|语义检索",
    re.IGNORECASE,
)


def enforce_ch1_template_structure(md: str) -> str:
    """
    第1章仅允许 ## 1 / ### 1.1 / ### 1.2（与 模板.md 一致）。
    剔除 LLM 误写的 ### 1.3+、正文内「检索到的参考资料」整节，以及误入的后续章。
    """
    s = (md or "").strip()
    if not s:
        return s

    lines = s.split("\n")
    out: List[str] = []
    skip = False

    for line in lines:
        if re.match(r"^##\s+2\.\s", line):
            break
        if re.match(r"^##\s+(?:[3-9]|\d{2,})\.\s", line):
            break

        if re.match(r"^##\s+", line):
            if re.search(r"检索到的参考资料|向量库", line, re.IGNORECASE):
                skip = True
                continue
            skip = False
            out.append(line)
            continue

        m31 = re.match(r"^###\s+1\.(\d+)\s", line)
        if m31:
            n = int(m31.group(1))
            if n >= 3:
                skip = True
                continue
            skip = False
            out.append(line)
            continue

        if re.match(r"^###\s+", line):
            if _CH1_FORBIDDEN_H3.search(line) and not re.match(
                r"^###\s+1\.[12]\b", line
            ):
                skip = True
                continue
            skip = False
            out.append(line)
            continue

        if skip:
            continue
        out.append(line)

    result = "\n".join(out).strip()
    if not result and s:
        logger.warning(
            "[JYLLM] enforce_ch1_template_structure 裁剪后为空，保留 LLM 原文（请检查是否误写 1.3+ 小节）"
        )
        return s
    return result


def extract_ch2_writer_plan_segments(writer_req: str) -> Tuple[str, str, str]:
    """
    从第2章 writer_prompt 中拆出 Planner 写入的两段规划树。
    返回 (前缀说明, 2.1 段, 2.2 段)。若无 MARK 则 (全文, "", "").
    """
    w = (writer_req or "").strip()
    if MARK_PLAN_21 not in w:
        return w, "", ""
    pre, _, rest = w.partition(MARK_PLAN_21)
    pre = pre.strip()
    if MARK_PLAN_22 not in rest:
        return (pre or w), rest.strip(), ""
    s1, _, s2 = rest.partition(MARK_PLAN_22)
    return (pre or "").strip(), s1.strip(), s2.strip()


def should_two_pass_chapter2(writer_req: str, include_22: bool) -> bool:
    """用户已选考核指标且规划含两段树时，第2章拆成 2.1 与 2.2 两次写作。"""
    if not include_22:
        return False
    _, s1, s2 = extract_ch2_writer_plan_segments(writer_req)
    if len(s1.strip()) < 8:
        return False
    s2t = (s2 or "").strip()
    if not s2t:
        return False
    if "无考核" in s2t[:200] and "不适用" in s2t[:120]:
        return False
    # B 段略短时仍拆段，避免第二段 LLM 从未被调用导致缺 2.2
    if len(s2t) < 6 and "2.2" not in s2t and "边界" not in s2t:
        return False
    return True


def merge_chapter_two_segments(body_21: str, body_22: str) -> str:
    """合并两段 Markdown：去掉后段重复的 ## 2 / ### 2.1 头及泄漏的 [THINK]，直至 ### 2.2。"""
    a = (body_21 or "").rstrip()
    b = _strip_leading_think_blocks(body_22 or "")
    b = re.sub(r"\[\s*THINK\s*\][\s\S]*?\[\s*/\s*THINK\s*\]", "", b, flags=re.IGNORECASE).strip()
    if not b:
        return a
    if not a:
        return b
    lines = b.split("\n")
    idx_22 = next((i for i, ln in enumerate(lines) if re.match(r"^###\s*2\.2\b", ln.strip())), None)
    if idx_22 is not None:
        i = 0
        while i < idx_22:
            s0 = lines[i].strip() if i < len(lines) else ""
            if not s0:
                lines.pop(i)
                idx_22 -= 1
                continue
            if re.match(r"^##\s*2\b", s0) or (
                s0.startswith("##") and "试验内容及方法" in s0 and ("2" in s0[:12] or "二" in s0[:6])
            ):
                lines.pop(i)
                idx_22 -= 1
                continue
            if re.match(r"^###\s*2\.1\b", s0):
                lines.pop(i)
                idx_22 -= 1
                continue
            i += 1
    else:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines:
            s0 = lines[0].strip()
            if re.match(r"^##\s*2\b", s0) or (
                s0.startswith("##") and "试验内容及方法" in s0 and ("2" in s0[:12] or "二" in s0[:6])
            ):
                lines.pop(0)
                continue
            if re.match(r"^###\s*2\.1\b", s0):
                idx2 = next((j for j, ln in enumerate(lines) if re.match(r"^###\s*2\.2\b", ln.strip())), None)
                if idx2 is not None:
                    lines = lines[idx2:]
                else:
                    lines.pop(0)
                continue
            break
    b = "\n".join(lines).strip()
    return f"{a}\n\n{b}".strip()


def flatten_ch2_writer_prompt_for_single_pass(writer_req: str) -> str:
    """去掉分段标记，把两棵树并入写作要求，供单次生成第2章时使用。"""
    pre, s1, s2 = extract_ch2_writer_plan_segments(writer_req)
    if not s1:
        return (writer_req or "").strip()
    tree = "## 规划给出的标题树（正文标题必须与下列编号及用字完全一致）\n" + s1.strip()
    s2t = (s2 or "").strip()
    if s2t and not (s2t.startswith("无考核") and "不适用" in s2t[:120]):
        tree += "\n\n" + s2t
    if pre:
        return f"{pre}\n\n{tree}".strip()
    return tree


def _writer_pass_stream(
    ch_idx: int,
    ch_title: str,
    writer_user_prompt: str,
    llm_key: str,
    *,
    max_tokens: int = 4096,
    search_results: str = "",
) -> Generator[str, None, Dict[str, Any]]:
    """
    执行一次 Writer 流式输出并解析 [THINK]/[CONTENT]。
    流式过程中逐个 yield SSE 事件字符串；结束时 return 解析结果 dict（由调用方用 StopIteration.value 接收）。
    """
    writer_messages = [
        {"role": "system", "content": get_prompt("writer_system")},
        {"role": "user", "content": writer_user_prompt},
    ]
    raw_response = ""
    buffer = ""
    in_think = False
    in_content = False
    TAG_TAIL = 16

    for chunk in call_llm_stream(
        writer_messages, temperature=0.7, max_tokens=max_tokens, llm_model_key=llm_key
    ):
        if isinstance(chunk, dict) and chunk.get("stream_kind") == "context_compress":
            ctxt = str(chunk.get("content") or "")
            if ctxt.strip():
                yield emit_event(
                    "thinking",
                    {"agent": "context_compress", "content": ctxt, "chapter_index": ch_idx},
                )
            continue
        if not isinstance(chunk, str):
            continue
        raw_response += chunk
        buffer += chunk

        while True:
            progressed = False
            if not in_think and not in_content:
                tm = _RE_W_THINK_OPEN.search(buffer)
                cm = _RE_W_CONTENT_OPEN.search(buffer)
                if tm and (not cm or tm.start() < cm.start()):
                    buffer = buffer[tm.end() :]
                    in_think = True
                    progressed = True
                elif cm:
                    buffer = buffer[cm.end() :]
                    in_content = True
                    progressed = True
                if progressed:
                    continue
            if in_think:
                mc = _RE_W_THINK_CLOSE.search(buffer)
                if mc:
                    emit_body = buffer[: mc.start()]
                    buffer = buffer[mc.end() :]
                    if emit_body.strip():
                        yield emit_event(
                            "thinking",
                            {"agent": "writer", "content": emit_body, "chapter_index": ch_idx},
                        )
                    in_think = False
                    continue
                safe = max(0, len(buffer) - TAG_TAIL)
                if safe > 0:
                    to_send = buffer[:safe]
                    buffer = buffer[safe:]
                    if to_send.strip():
                        yield emit_event(
                            "thinking",
                            {"agent": "writer", "content": to_send, "chapter_index": ch_idx},
                        )
                break
            if in_content:
                mc = _RE_W_CONTENT_CLOSE.search(buffer)
                if mc:
                    emit_body = buffer[: mc.start()]
                    buffer = buffer[mc.end() :]
                    if emit_body.strip():
                        yield emit_event(
                            "writer_stream",
                            {
                                "chapter_index": ch_idx,
                                "chapter_title": ch_title,
                                "attempt": 1,
                                "content": emit_body,
                            },
                        )
                    in_content = False
                    continue
                safe = max(0, len(buffer) - TAG_TAIL)
                if safe > 0:
                    to_send = buffer[:safe]
                    buffer = buffer[safe:]
                    if to_send.strip():
                        yield emit_event(
                            "writer_stream",
                            {
                                "chapter_index": ch_idx,
                                "chapter_title": ch_title,
                                "attempt": 1,
                                "content": to_send,
                            },
                        )
                break
            break

        if in_content and buffer.strip():
            yield emit_event(
                "writer_stream",
                {
                    "chapter_index": ch_idx,
                    "chapter_title": ch_title,
                    "attempt": 1,
                    "content": buffer,
                },
            )
            buffer = ""
        elif not in_think and not in_content and buffer.strip():
            yield emit_event(
                "writer_stream",
                {
                    "chapter_index": ch_idx,
                    "chapter_title": ch_title,
                    "attempt": 1,
                    "content": buffer,
                },
            )
            buffer = ""

    tag_think, tag_content = parse_writer_tag_blocks(raw_response, allow_unclosed=True)
    if (tag_content or "").strip():
        thinking = (tag_think or "").strip()
        best_content = tag_content.strip()
        keywords = ch_title
        search_context_data = (
            search_results if search_results and "未检索到" not in search_results else ""
        )
    else:
        writer_json = extract_json_from_text(raw_response)
        if writer_json and isinstance(writer_json, dict):
            thinking = writer_json.get("thinking_process", "")
            keywords = writer_json.get("keywords", "")
            search_context_data = writer_json.get("search_context", "")
            best_content = writer_json.get("content", raw_response)
            if isinstance(best_content, dict):
                best_content = best_content.get("content", "") or ""
            elif isinstance(best_content, str):
                inner = extract_json_from_text(best_content)
                if inner and "content" in inner:
                    best_content = inner["content"]
        else:
            think_match = re.search(
                r"\[\s*THINK\s*\]([\s\S]*?)\[\s*/\s*THINK\s*\]", raw_response, re.DOTALL | re.IGNORECASE
            )
            content_match = re.search(
                r"\[\s*CONTENT\s*\]([\s\S]*?)\[\s*/\s*CONTENT\s*\]",
                raw_response,
                re.DOTALL | re.IGNORECASE,
            )
            thinking = think_match.group(1).strip() if think_match else ""
            best_content = content_match.group(1).strip() if content_match else raw_response
            keywords = ch_title
            search_context_data = (
                search_results if search_results and "未检索到" not in search_results else ""
            )

    if isinstance(best_content, str):
        best_content = sanitize_scheme_markdown_body(best_content)
    else:
        best_content = str(best_content or "")

    return {
        "best_content": best_content,
        "thinking": thinking,
        "keywords": keywords,
        "search_context_data": search_context_data,
    }


def _relay_writer_pass(
    gen: Generator[str, None, Dict[str, Any]],
) -> Generator[str, None, Dict[str, Any]]:
    """将 _writer_pass_stream 产出的 SSE 立即向上转发，结束时把其 return 的 dict 作为本生成器的 return。"""
    while True:
        try:
            yield next(gen)
        except StopIteration as ex:
            ret = ex.value
            return ret if isinstance(ret, dict) else {}


def search_context(query: str) -> str:
    """
    检索相关参考资料
    
    使用Chroma向量数据库进行混合检索:
    - 向量相似度检索: 基于语义相似度
    - 关键词检索: 基于字面匹配
    
    参数:
        query: 检索查询词，通常是章节标题
    返回:
        检索到的参考资料文本，如果无结果返回空字符串
    """
    tool = get_search_tool()
    return tool.search_hybrid(query, n_results=5)


def _slice_first_balanced_json_object(text: str) -> Optional[str]:
    """从 text 截取第一个花括号配对的 JSON 对象（尊重双引号字符串内的括号与转义）。"""
    if not text:
        return None
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    i = start
    while i < len(text):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


def _normalize_json_candidate(s: str) -> str:
    """常见 LLM JSON 瑕疵：尾随逗号、弯引号。"""
    if not s:
        return s
    s = s.strip()
    s = s.replace('\u201c', '"').replace('\u201d', '"').replace('\u2018', "'").replace('\u2019', "'")
    s = re.sub(r',(\s*})', r'\1', s)
    s = re.sub(r',(\s*])', r'\1', s)
    return s


def extract_json_from_text(text: str) -> Optional[Dict]:
    """
    从LLM输出中提取JSON对象
    
    LLM可能返回以下格式:
    1. 纯JSON: {"key": "value"}
    2. JSON代码块: ```json {"key": "value"} ```
    3. JSON嵌入文本: some text {"key": "value"} more text
    
    此函数依次尝试多种解析方式，直到成功或返回None
    """
    if not text or not str(text).strip():
        return None

    candidates: List[str] = []

    def _push(s: Optional[str]):
        if s and s.strip():
            candidates.append(s.strip())

    _push(text)
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        _push(cleaned)

    sliced = _slice_first_balanced_json_object(text)
    if sliced:
        _push(sliced)
        _push(_normalize_json_candidate(sliced))

    for raw in list(candidates):
        _push(_normalize_json_candidate(raw))

    # 去重且保序
    seen = set()
    uniq: List[str] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)

    for cand in uniq:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue

    # 回退：贪婪花括号（易与 Writer 的 [THINK] 内 JSON 示例冲突）；含 CONTENT 标签时不使用
    if not re.search(r"\[\s*CONTENT\s*\]", text or "", re.IGNORECASE):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            for cand in (match.group(), _normalize_json_candidate(match.group())):
                try:
                    return json.loads(cand)
                except (json.JSONDecodeError, TypeError):
                    continue
    return None


def _build_planner_context_bundle(
    equipment: List,
    environments: List,
    environment_config: str,
    projects: List,
    indicators: List,
    methods: List,
    user_case_chapter2: str,
    template: str,
) -> str:
    """合并为单一用户上下文块：避免「名称列表 + 明细 + 模板」重复罗列。"""
    lines: List[str] = [
        "# 本次规划所需的全部用户上下文（仅此一份，已合并明细与模板）\n",
        "以下已包含设备/环境/项目/指标/方法的**完整明细**与试验方案模板；**请勿**再要求用户重复粘贴或摘要同一批数据。\n\n",
    ]
    n_proj = len(_nonempty_user_list(projects))
    n_ind = len(_nonempty_user_list(indicators))
    lines.append("## 用户选择计数（规划树须对齐，不得漏项）\n")
    if n_proj > 0:
        lines.append(
            f"- 试验项目：共 **{n_proj}** 项 — `---PLAN_SEGMENT_2_1---` 中须有 `##### 2.1.1.1` 至 `##### 2.1.1.{n_proj}`，"
            "名称与下列列表一致；**即使某项目无关联方法**也须保留该 `#####` 并在其下增加占位 `###### …` 要求 Writer 补全（一）（二）（三）。\n"
        )
    else:
        lines.append("- 试验项目：当前列表为空，仍须在规划中约定如何据设备/环境补全 2.1 树。\n")
    if n_ind > 0:
        lines.append(
            f"- 考核指标：共 **{n_ind}** 项 — `---PLAN_SEGMENT_2_2---` 中须有 `#### 2.2.1` 至 `#### 2.2.{n_ind}`，"
            f"与下列指标一一对应，**禁止**合并为少于 {n_ind} 个 `####` 条目。\n"
        )
    else:
        lines.append("- 考核指标：**未选择** — 不得出现 ### 2.2 及任何 2.2.x。\n")
    lines.append("\n")

    def _section(title: str, items: Optional[List]) -> None:
        lines.append(f"## {title}\n")
        if not items:
            lines.append("（未选择）\n\n")
            return
        if not isinstance(items, list):
            lines.append(f"{items}\n\n")
            return
        for it in items:
            if isinstance(it, dict):
                slim = {
                    k: v
                    for k, v in it.items()
                    if k not in ("children", "files", "_labels") and v not in (None, "", [], {})
                }
                lines.append(f"- {json.dumps(slim, ensure_ascii=False)}\n")
            else:
                lines.append(f"- {it}\n")
        lines.append("\n")

    _section("设备类型", equipment)
    _section("环境条件", environments)
    ec = (environment_config or "").strip()
    if ec:
        lines.append("## 环境详细设置\n")
        lines.append(ec + "\n\n")
    _section("试验项目", projects)
    _section("考核指标", indicators)
    _section("试验方法", methods)
    lines.append("## 第2章结构约束（须严格遵守）\n")
    lines.append(user_case_chapter2.strip() + "\n\n")
    lines.append("## 试验方案模板（全文仅两章，正文须与之一致）\n")
    lines.append(template or "")
    return "".join(lines)


def _slice_planner_block(text: str, start_marker: str, end_marker: Optional[str]) -> str:
    t = text.replace("\r\n", "\n")
    i = t.find(start_marker)
    if i < 0:
        return ""
    s = i + len(start_marker)
    while s < len(t) and t[s] in " \t":
        s += 1
    if s < len(t) and t[s] == "\n":
        s += 1
    if not end_marker:
        return t[s:].strip()
    j = t.find(end_marker, s)
    if j < 0:
        return t[s:].strip()
    return t[s:j].strip()


def parse_planner_markdown_blocks(text: str) -> List[Dict[str, Any]]:
    """
    解析 Planner 在 [CONTENT] 中输出的 Markdown 分块（非 JSON）。
    优先识别中文标记行；兼容旧版英文标记。
    """
    t = (text or "").strip()
    if not t:
        return []

    marker_sets: List[Tuple[str, str, str, str]] = [
        (
            "### 〖规划·第一章·写作要点〗",
            "### 〖规划·第一章·评审要点〗",
            "### 〖规划·第二章·写作要点〗",
            "### 〖规划·第二章·评审要点〗",
        ),
        ("### PLAN_CH01_W", "### PLAN_CH01_R", "### PLAN_CH02_W", "### PLAN_CH02_R"),
    ]
    for m_w1, m_r1, m_w2, m_r2 in marker_sets:
        w1 = _slice_planner_block(t, m_w1, m_r1)
        r1 = _slice_planner_block(t, m_r1, m_w2)
        w2 = _slice_planner_block(t, m_w2, m_r2)
        r2 = _slice_planner_block(t, m_r2, None)
        if not (w1.strip() or r1.strip() or w2.strip() or r2.strip()):
            continue
        out: List[Dict[str, Any]] = []
        if w1.strip() or r1.strip():
            out.append({"index": 1, "title": "1. 试验依据", "writer_prompt": w1.strip(), "reviewer_prompt": r1.strip()})
        if w2.strip() or r2.strip():
            out.append({"index": 2, "title": "2. 试验内容及方法", "writer_prompt": w2.strip(), "reviewer_prompt": r2.strip()})
        if out:
            return out
    return []


def _minimal_planner_chapters_from_raw(raw: str, has_indicators: bool) -> List[Dict[str, Any]]:
    """解析失败时的回退：保证两章可继续生成。"""
    body = (raw or "").strip()
    ch1: Dict[str, Any] = {
        "index": 1,
        "title": "1. 试验依据",
        "writer_prompt": (
            "按模板撰写 ## 1. 试验依据，须含 ### 1.1 任务来源、### 1.2 依据文件（表格）；"
            "结合用户上下文中的设备/项目及检索结果。"
        ),
        "reviewer_prompt": "核对第1章是否覆盖 1.1、1.2 与模板表格结构。",
    }
    ch2: Dict[str, Any] = {
        "index": 2,
        "title": "2. 试验内容及方法",
        "writer_prompt": (
            (body[:80000] if body else "")
            + "\n\n【系统提示】未识别到标准规划分段标记（须使用中文「〖规划·第×章·…〗」标题行）时，"
            "仍请严格按用户上下文中的「试验方案模板」与第2章结构约束撰写全文。"
        ),
        "reviewer_prompt": "对照模板与用户项目/方法/指标（若有）评审第2章结构与可操作性。",
    }
    return _enforce_planner_two_chapters([ch1, ch2], include_section_2_2=has_indicators)


def _enforce_planner_two_chapters(chapters: List[Dict], include_section_2_2: bool = True) -> List[Dict]:
    """
    大纲只保留两章：1. 试验依据、2. 试验内容及方法（与模板.md 一致）。
    LLM 若仍输出多节，合并除「第1章」外的所有条目为第2章的 writer/reviewer 提示。
    include_section_2_2：用户是否选择了考核指标；为 False 时不应出现 ### 2.2。
    """
    cleaned: List[Dict] = []
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        t = str(ch.get("title") or "").strip()
        if not t:
            continue
        cleaned.append(dict(ch))

    ch2_extra = (
        "与 ### 2.2 边界性能指标（#### 2.2.1… 父级指标 → ##### 子级指标；方法正文挂在指标标题下，无独立方法名标题）。"
        if include_section_2_2
        else "**禁止**出现 ### 2.2 及任何 2.2.x（用户未选考核指标）。"
    )
    _default_ch2 = {
        "index": 2,
        "title": "2. 试验内容及方法",
        "writer_prompt": (
            "请严格按模板撰写 ## 2：含 ### 2.1 试验项目；#### 2.1.1 须为「环境条件」首条**具体环境名**（禁止「试验环境」泛称）；"
            "其下 ##### 2.1.1.q 父级项目、###### 2.1.1.q.r 子级项目（方法正文写在项目标题下，无方法名标题）；多环境时 #### 2.1.2 为第二个环境名（非第二个项目）。"
            + ch2_extra
            + " 各试验方法：若图库给出实质 **SYNR**，正文在该方法下**只写**由 SYNR 整理的连续方法正文；**无实质 SYNR** 时须有（一）（二）（三），**优先**采用图库 **SYTJ / SYGC / JGPD**；若图库无该方法或字段为空，"
            "须结合**向量检索**与用户配置**补全**可操作的（一）（二）（三）；补全与来源说明写在 **[THINK]**，**禁止**在正文写「图库缺失」类元话语。"
            + f"\n\n{MARK_PLAN_21}\n"
            "## 2. 试验内容及方法\n### 2.1 试验项目\n#### 2.1.1 <环境条件列表第一条的具体环境名称>\n##### 2.1.1.1 <第1个父级试验项目全名>\n###### 2.1.1.1.1 <第1个子级试验项目全名（若有）>\n"
            f"{MARK_PLAN_22}\n"
            + (
                "### 2.2 边界性能指标\n#### 2.2.1 <第1个父级考核指标全名>\n##### 2.2.1.1 <第1个子级指标全名（若有）>\n"
                if include_section_2_2
                else "无考核指标，本段不适用。\n"
            )
        ),
        "reviewer_prompt": (
            "核对第2章：#### 2.1.1 是否为**具体环境名**（非「试验环境」泛称）；第二项目是否为 ##### 2.1.1.2；"
            "多环境时 #### 2.1.2 是否为第二环境名。"
            + ("" if include_section_2_2 else " **无考核指标时不得出现 2.2。**")
            + " 有 SYNR 时检查是否误套（一）（二）（三）；无 SYNR 时若图库缺失 SYTJ/SYGC/JGPD，检查正文是否仍完整覆盖（一）（二）（三）；**不得**因正文未写「图库缺失」而扣分，来源说明应在 **[THINK]**；"
            "不应仅因图库为空而判缺项，除非补全内容与标准明显矛盾或仍为空标题。"
        ),
    }

    if not cleaned:
        return []

    def _is_ch1(c: Dict) -> bool:
        t = str(c.get("title") or "").strip()
        return ("试验依据" in t) and (t.startswith("1") or t.startswith("1."))

    idx1 = next((i for i, c in enumerate(cleaned) if _is_ch1(c)), None)
    if idx1 is None:
        idx1 = 0

    first = dict(cleaned[idx1])
    first["index"] = 1
    rest = [c for i, c in enumerate(cleaned) if i != idx1]

    if not rest:
        return [first, dict(_default_ch2)]

    if len(rest) == 1 and len(cleaned) == 2:
        second = dict(rest[0])
        second["index"] = 2
        if not str(second.get("title") or "").strip():
            second["title"] = "2. 试验内容及方法"
        return [first, second]

    head_w = (
        "【硬性结构】正文须以 ## 2. 试验内容及方法 开头。"
        "其下须含 ### 2.1 试验项目。"
        "2.1 下：`#### 2.1.1` 必须为**用户环境条件首条具体环境名**（禁止「试验环境」泛称）；"
        "其下父级项目 ##### 2.1.1.1、2.1.1.2…；子级项目 ###### 2.1.1.1.1…；方法正文写在各项目标题下。"
        "若存在多个环境类别，第二个环境为 `#### 2.1.2 <环境名>`（与模板），**禁止**把第二个项目写成 `#### 2.1.2`。"
    )
    if include_section_2_2:
        head_w += "还须含 ### 2.2 边界性能指标；2.2 下：指标 #### 2.2.1、2.2.2…；方法 ##### 2.2.m.1…。"
    else:
        head_w += "**不得**含 ### 2.2 及一切 2.2.x 编号。"
    head_w += (
        "各试验方法：有实质 **SYNR** 则正文只写连续方法正文；**无实质 SYNR** 时须有（一）（二）（三），**优先**落图库 **SYTJ/SYGC/JGPD**；若图库无该方法或字段为空，"
        "须在 writer_prompt 中明确要求 Writer 结合**向量检索**与用户条件**补全**（一）（二）（三），并在 **[THINK]** 说明来源，**禁止**要求正文写「图库缺失」类句子。\n\n"
        "以下为 Planner 拆分要点，请融入上述骨架：\n\n"
    )
    head_r = (
        "评审时核对：第2章 `#### 2.1.1` 是否为**具体环境名**；第二项目是否为 ##### 2.1.1.2；"
        "多环境时 `#### 2.1.2` 是否为第二环境名。"
        + ("" if include_section_2_2 else " 无指标时不得出现 2.2。")
        + " 若用户已选指标，正文**必须**含 ### 2.2。"
        + " 若用户方法无实质 SYNR 且图库无 SYTJ/SYGC/JGPD，大纲是否仍要求正文补全（一）（二）（三），且 **[THINK]** 是否说明图库/向量检索/补全分工（不要求正文声明）。"
        + "\n\n"
    )
    blocks_w = []
    blocks_r = []
    for j, c in enumerate(rest, start=1):
        tit = str(c.get("title") or "").strip()
        blocks_w.append(f"（段{j} {tit}）\n{str(c.get('writer_prompt') or '').strip()}")
        blocks_r.append(f"（段{j} {tit}）\n{str(c.get('reviewer_prompt') or '').strip()}")
    second = {
        "index": 2,
        "title": "2. 试验内容及方法",
        "writer_prompt": head_w + "\n\n---\n\n".join(blocks_w),
        "reviewer_prompt": head_r + "\n\n---\n\n".join(blocks_r),
    }
    return [first, second]


def emit_event(event_type: str, data: Dict) -> str:
    """
    生成SSE (Server-Sent Events) 格式的事件数据
    
    SSE格式:
    event: <事件类型>
    data: <JSON数据>
    
    前端通过EventSource接收，解析event字段判断事件类型
    """
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ==================== LangGraph 状态类型定义 ====================
# ChapterState 定义了章节生成过程中的状态结构
# 包含: 章节信息、写审状态、评分、当前内容等

class ChapterState(TypedDict):
    chapter_index: int          # 章节序号
    chapter_title: str          # 章节标题
    writer_prompt: str          # Writer写作要求
    reviewer_prompt: str        # Reviewer评审要求
    search_context: str         # 检索到的参考资料
    draft_content: str          # 当前草稿内容
    review_score: int          # 本次评审分数
    review_pass: bool          # 是否通过评审
    review_text: str           # 评审意见
    best_content: str          # 最佳内容(历次最高分)
    best_score: int            # 最佳分数
    review_loop: int           # 已循环次数


class GenerationState(TypedDict):
    task_id: str                # 任务ID
    chapters: List[Dict]        # 章节列表
    current_chapter_idx: int   # 当前章节索引
    chapter_results: Dict      # 各章节结果
    chapter_scores: Dict        # 各章节评分
    full_scheme_parts: List[str]  # 完整方案各部分
    phase: str                  # 当前阶段
    events: List[str]           # 事件列表


def merge_events(existing: List[str], new: List[str]) -> List[str]:
    # 事件合并函数，用于LangGraph状态更新
    return existing + new


# ==================== LangGraph 节点函数 ====================
# 节点函数代表工作流中的各个处理步骤

def create_writer_node(state: ChapterState, task_config: Dict):
    """
    Writer智能体节点 - 生成章节内容
    
    工作流程:
    1. 根据是否首次生成，选择初始写作提示或重写提示
    2. 拼接系统提示和用户提示，调用LLM生成内容
    3. 从LLM输出中解析JSON，提取thinking/content/keywords等字段
    4. 更新状态，返回更新后的state
    """
    ch_title = state["chapter_title"]
    writer_req = state["writer_prompt"]
    search_ctx = state.get("search_context", "")
    review_text = state.get("review_text", "")
    current_content = state.get("draft_content", "")
    loop = state.get("review_loop", 0)

    # 首次生成使用初始提示，重写时使用包含评审意见的提示
    mg_block = _writer_method_graph_block(ch_title, task_config.get("methods"))
    if loop == 0:
        user_prompt = get_prompt(
            "writer_user_initial",
            chapter_title=ch_title,
            writer_prompt=writer_req,
            method_graph_block=mg_block,
            search_context=search_ctx,
            user_comment="",
            current_content="",
        )
    else:
        user_prompt = get_prompt(
            "writer_user_rewrite",
            chapter_title=ch_title,
            writer_prompt=writer_req,
            method_graph_block=mg_block,
            search_context=search_ctx,
            user_comment=review_text,
            current_content=current_content,
        )

    writer_messages = [
        {"role": "system", "content": get_prompt("writer_system")},
        {"role": "user", "content": user_prompt}
    ]

    # 调用LLM生成内容
    mk = (task_config or {}).get("llm_model_key") or ""
    draft_content = ""
    for chunk in call_llm_stream(writer_messages, temperature=0.7, max_tokens=4096, llm_model_key=mk):
        if isinstance(chunk, dict):
            continue
        draft_content += chunk

    tag_t, tag_c = parse_writer_tag_blocks(draft_content, allow_unclosed=True)
    if (tag_c or "").strip():
        thinking = (tag_t or "").strip()
        keywords_raw = ch_title
        search_ctx_raw = (search_ctx or "").strip()
        draft_text = tag_c.strip()
    else:
        writer_json = extract_json_from_text(draft_content)
        if writer_json:
            thinking = writer_json.get("thinking_process", "")
            keywords_raw = writer_json.get("keywords", "")
            search_ctx_raw = writer_json.get("search_context", "")
            draft_text = writer_json.get("content", draft_content)
            if isinstance(draft_text, dict):
                draft_text = draft_text.get("content", json.dumps(draft_text, ensure_ascii=False))
            elif isinstance(draft_text, str):
                inner = extract_json_from_text(draft_text)
                if inner and "content" in inner:
                    draft_text = inner["content"]
        else:
            thinking = ""
            keywords_raw = ""
            search_ctx_raw = ""
            draft_text = draft_content

    if isinstance(draft_text, str):
        draft_text = sanitize_scheme_markdown_body(draft_text)

    # 更新状态
    state["draft_content"] = draft_text
    state["review_loop"] = loop + 1
    state["_thinking"] = thinking
    state["_keywords"] = keywords_raw
    state["_search_context"] = search_ctx_raw
    return state


def create_reviewer_node(state: ChapterState, task_config: Dict) -> ChapterState:
    """
    Reviewer智能体节点 - 评审章节内容
    
    工作流程:
    1. 构建评审提示，包含章节标题、评审要求、当前内容
    2. 调用LLM进行评审，获取评分和评审意见
    3. 解析JSON，提取score、is_pass、review字段
    4. 如果本次评分高于历史最佳，更新best_content
    5. 返回更新后的状态
    """
    ch_title = state["chapter_title"]
    reviewer_req = state["reviewer_prompt"]
    draft_text = state["draft_content"]

    reviewer_messages = [
        {"role": "system", "content": get_prompt("reviewer_system")},
        {"role": "user", "content": get_prompt("reviewer_user",
                                               chapter_title=ch_title,
                                               reviewer_prompt=reviewer_req,
                                               draft_content=draft_text)}
    ]

    # 调用LLM进行评审
    mk = (task_config or {}).get("llm_model_key") or ""
    review_response = call_llm(reviewer_messages, temperature=0.3, max_tokens=2048, llm_model_key=mk)
    review_json = extract_json_from_text(review_response)

    if review_json:
        state["review_score"] = review_json.get("score", 60)
        state["review_pass"] = review_json.get("is_pass", False)
        state["review_text"] = review_json.get("review", "")
    else:
        state["review_score"] = 60
        state["review_pass"] = False
        state["review_text"] = review_response

    # 如果本次评分更高，更新最佳内容
    if state["review_score"] > state.get("best_score", 0):
        state["best_score"] = state["review_score"]
        state["best_content"] = state["draft_content"]

    return state


def should_rewrite(state: ChapterState) -> str:
    """
    条件边函数 - 决定是否需要重写
    
    返回值:
    - "done": 完成，不需要再生成
    - "rewrite": 需要重写
    
    判断逻辑:
    1. 如果Reviewer判定通过(is_pass=True)且评分>=80分，结束
    2. 如果已达最大循环次数(MAX_REVIEW_LOOPS=3)，强制结束
    3. 否则继续重写
    """
    if state["review_pass"] and state["review_score"] >= CHAPTER_PASS_SCORE:
        return "done"
    if state.get("review_loop", 0) >= MAX_REVIEW_LOOPS:
        return "done"
    return "rewrite"


# ==================== LangGraph-like 工作流编排 ====================
# 使用简单的while循环实现类似LangGraph的状态机

def execute_chapter_with_graph(ch_idx: int, ch_title: str, writer_req: str, reviewer_req: str, search_ctx: str):
    """
    执行单个章节的写-审循环
    
    这是一个简化版的LangGraph工作流:
    1. 初始化ChapterState
    2. 进入循环: Writer生成 -> Reviewer评审 -> 判断是否需要重写
    3. 循环结束条件: 通过评审 或 达到最大循环次数
    4. 返回最佳内容和评分
    
    参数:
        ch_idx: 章节序号
        ch_title: 章节标题
        writer_req: Writer写作要求
        reviewer_req: Reviewer评审要求
        search_ctx: 检索到的参考资料
    """
    # 初始化章节状态
    chapter_state: ChapterState = {
        "chapter_index": ch_idx,
        "chapter_title": ch_title,
        "writer_prompt": writer_req,
        "reviewer_prompt": reviewer_req,
        "search_context": search_ctx,
        "draft_content": "",
        "review_score": 0,
        "review_pass": False,
        "review_text": "",
        "best_content": "",
        "best_score": 0,
        "review_loop": 0,
    }

    task_config = {}

    # 写-审循环: 最多MAX_REVIEW_LOOPS次
    while True:
        # Writer生成内容
        chapter_state = create_writer_node(chapter_state, task_config)
        # Reviewer评审内容
        chapter_state = create_reviewer_node(chapter_state, task_config)

        # 判断是否需要继续循环
        action = should_rewrite(chapter_state)
        if action == "done":
            break

    # 返回结果
    return {
        "content": chapter_state["best_content"],
        "score": chapter_state["best_score"],
        "loops": chapter_state["review_loop"],
        "thinking": chapter_state.get("_thinking", ""),
        "keywords": chapter_state.get("_keywords", ""),
        "search_context": chapter_state.get("_search_context", ""),
    }


# ==================== 公开API函数 ====================

def _merge_single_parsed_chapter(single: Dict[str, Any], raw: str) -> List[Dict[str, Any]]:
    """仅解析出一章时，补全另一章占位，便于 _enforce 合并。"""
    title = str(single.get("title") or "")
    idx = int(single.get("index") or 0)
    if "试验依据" in title or idx == 1:
        second: Dict[str, Any] = {
            "index": 2,
            "title": "2. 试验内容及方法",
            "writer_prompt": (raw or "").strip()[:80000],
            "reviewer_prompt": "按模板与用户试验项目/方法/指标（若有）评审第2章。",
        }
        return [single, second]
    first: Dict[str, Any] = {
        "index": 1,
        "title": "1. 试验依据",
        "writer_prompt": "按模板撰写 ## 1. 试验依据：### 1.1 任务来源、### 1.2 依据文件（表格）；结合用户上下文。",
        "reviewer_prompt": "核对第1章与模板及依据表格要求。",
    }
    return [first, single]


def run_planner(task_id: str, equipment: List[str], environments: List[str],
                projects: List[str], indicators: List[str], methods: List[str],
                environment_config: str, template_override: str = "",
                llm_model_key: str = "") -> Generator[str, None, None]:
    """
    Phase 1: Planner规划器
    
    功能:
    根据用户选择的试验条件，结合模板生成方案章节大纲
    
    输入条件:
    - equipment: 设备类型列表
    - environments: 环境条件列表
    - projects: 试验项目列表
    - indicators: 考核指标列表
    - methods: 试验方法列表
    - environment_config: 环境详细配置
    - template_override: 自定义模板(可选)
    
    输出:
    - planner_complete事件: chapters（两章，供 continue 使用）、planner_markdown（[CONTENT] 内原文，供前端展示）
    """
    # 加载模板
    template = template_override or load_template()
    # 发送开始事件
    yield emit_event("start", {"task_id": task_id})

    has_indicators = _has_selected_indicators(indicators)
    _fe = _first_environment_label(environments)
    _env_rule = (
        f"**#### 2.1.1 小节标题**：在规划树与正文中必须使用 **`#### 2.1.1 {_fe}`**（与上文「环境条件」列表**第一条**名称一致），"
        "**禁止**用「试验环境」「地理环境」等泛称。\n"
        "**多环境时**：第二个**环境**为 `#### 2.1.2 <第二个环境全称>`（与模板「环境类别B」一致）；第二个**试验项目**仍为 `##### 2.1.1.2`，**禁止**把项目写成 `#### 2.1.2`。\n"
        if _fe
        else "**#### 2.1.1 小节标题**：必须使用用户在「环境条件」或「环境详细设置」中与所选项目对应的**具体环境名称**（`#### 2.1.1 <具体名称>`），**禁止**单用「试验环境」四字。\n"
        "**多环境时**：第二个环境为 `#### 2.1.2 <环境名>`；第二个项目为 `##### 2.1.1.2`。\n"
    )
    user_case_chapter2 = _env_rule + (
        "**当前用户已选择考核指标**：第2章大纲与写作要点须同时包含完整的 **2.1（### 2.1 → #### 环境 → ##### 父级项目 → ###### 子级项目）** 与 **### 2.2 边界性能指标（#### 父级指标 → ##### 子级指标）** 树；方法正文挂在项目/指标标题下，**禁止**独立方法名标题；**禁止**只规划或只撰写 2.1 而遗漏 2.2。"
        if has_indicators
        else "**当前用户未选择考核指标**：第2章只允许 **### 2.1 试验项目** 及 **#### 2.1.1 <具体环境名>** 与其下 ##### / ###### 编号；**禁止**写入 ### 2.2 及一切 2.2.x；写作与评审要点中须明确约定「无 2.2 节」。"
    )

    context_bundle = _build_planner_context_bundle(
        equipment,
        environments,
        environment_config or "",
        projects,
        indicators,
        methods,
        user_case_chapter2,
        template,
    )

    system_prompt = get_prompt("planner_system")
    user_prompt = get_prompt("planner_user", context_bundle=context_bundle)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # 流式调用LLM生成章节大纲（提高 max_tokens 降低长规划被截断概率）
    planner_content = ""
    buffer = ""
    in_think = False
    in_content = False
    TAG_TAIL = 12

    for chunk in call_llm_stream(messages, temperature=0.3, max_tokens=12288, llm_model_key=llm_model_key or ""):
        if isinstance(chunk, dict) and chunk.get("stream_kind") == "context_compress":
            ctxt = str(chunk.get("content") or "")
            if ctxt:
                yield emit_event("thinking", {
                    "agent": "context_compress",
                    "content": ctxt,
                })
            continue
        if not isinstance(chunk, str):
            continue
        buffer += chunk

        # 处理标签转换，允许在同一次迭代中发生
        while True:
            if not in_think and not in_content:
                if "[THINK]" in buffer:
                    parts = buffer.split("[THINK]", 1)
                    buffer = parts[1] if len(parts) > 1 else ""
                    in_think = True
                    continue
                elif "[CONTENT]" in buffer:
                    parts = buffer.split("[CONTENT]", 1)
                    buffer = parts[1] if len(parts) > 1 else ""
                    in_content = True
                    continue
            elif in_think:
                if "[/THINK]" in buffer:
                    parts = buffer.split("[/THINK]", 1)
                    think_text = parts[0]
                    buffer = parts[1] if len(parts) > 1 else ""
                    if think_text.strip():
                        yield emit_event("thinking", {
                            "agent": "planner",
                            "content": think_text,
                        })
                    in_think = False
                    # 检查是否紧跟 [CONTENT]
                    if "[CONTENT]" in buffer:
                        parts = buffer.split("[CONTENT]", 1)
                        buffer = parts[1] if len(parts) > 1 else ""
                        in_content = True
                else:
                    safe = max(0, len(buffer) - TAG_TAIL)
                    if safe > 0:
                        to_send = buffer[:safe]
                        buffer = buffer[safe:]
                        if to_send.strip():
                            yield emit_event("thinking", {
                                "agent": "planner",
                                "content": to_send,
                            })
            elif in_content:
                if "[/CONTENT]" in buffer:
                    parts = buffer.split("[/CONTENT]", 1)
                    content_text = parts[0]
                    buffer = parts[1] if len(parts) > 1 else ""
                    planner_content += content_text
                    if content_text.strip():
                        yield emit_event("thinking", {
                            "agent": "planner_outline",
                            "content": content_text,
                        })
                    in_content = False
                else:
                    safe = max(0, len(buffer) - TAG_TAIL)
                    if safe > 0:
                        piece = buffer[:safe]
                        planner_content += piece
                        buffer = buffer[safe:]
                        if piece:
                            yield emit_event("thinking", {
                                "agent": "planner_outline",
                                "content": piece,
                            })
            break

    if in_think and buffer.strip():
        yield emit_event("thinking", {
            "agent": "planner",
            "content": buffer,
        })
    if in_content and buffer.strip():
        planner_content += buffer
        yield emit_event("thinking", {
            "agent": "planner_outline",
            "content": buffer,
        })

    # 清理可能残留的标签
    planner_content = planner_content.replace("[CONTENT]", "").replace("[/CONTENT]", "").strip()

    if not planner_content:
        planner_content = buffer.replace("[CONTENT]", "").replace("[/CONTENT]", "").strip()

    logger.info(f"[Planner] Raw output length: {len(planner_content)}, snippet: {planner_content[:200]}...")

    if not planner_content.strip():
        yield emit_event("error", {"message": "Planner 未输出 [CONTENT] 正文，请重试。", "raw_excerpt": ""})
        return

    parsed_md = parse_planner_markdown_blocks(planner_content)
    chapters: List[Dict[str, Any]] = []
    if len(parsed_md) >= 2:
        chapters = _enforce_planner_two_chapters(parsed_md[:2], include_section_2_2=has_indicators)
    elif len(parsed_md) == 1:
        chapters = _enforce_planner_two_chapters(
            _merge_single_parsed_chapter(parsed_md[0], planner_content),
            include_section_2_2=has_indicators,
        )
    else:
        legacy = extract_json_from_text(planner_content)
        if legacy and isinstance(legacy.get("chapters"), list) and legacy["chapters"]:
            chapters = _enforce_planner_two_chapters(
                [c for c in legacy["chapters"] if isinstance(c, dict)],
                include_section_2_2=has_indicators,
            )
        else:
            logger.warning("[Planner] Markdown/JSON 均未解析出章节，使用回退两章骨架。")
            chapters = _minimal_planner_chapters_from_raw(planner_content, has_indicators)

    if len(chapters) < 2:
        chapters = _minimal_planner_chapters_from_raw(planner_content, has_indicators)
    if len(chapters) != 2:
        logger.warning(f"[Planner] chapters count after resolve: {len(chapters)}")
    
    _active_tasks[task_id] = {
        "task_id": task_id,
        "equipment": equipment,
        "environments": environments,
        "projects": projects,
        "indicators": indicators,
        "methods": methods,
        "include_section_2_2": has_indicators,
        "environment_config": environment_config,
        "template": template,
        "chapters": chapters,
        "planner_raw": planner_content,
        "phase": "waiting_confirm",
        "chapter_contents": {},
        "chapter_scores": {},
        "chapter_feedback": {},
    }

    yield emit_event("planner_complete", {
        "task_id": task_id,
        "chapters": chapters,
        "planner_markdown": planner_content,
    })


def continue_generation(task_id: str, chapters: List[Dict],
                       equipment: List[str] = None, environments: List[str] = None,
                       projects: List[str] = None, indicators: List[str] = None,
                       methods: List[str] = None, environment_config: str = "",
                       user_supplement: str = "", llm_model_key: str = "") -> Generator[str, None, None]:
    """
    Phase 2: 用户确认后生成章节内容
    
    工作流程:
    1. 遍历每个章节
    2. 检索相关参考资料
    3. 调用Writer生成内容(流式)
    4. 解析JSON，提取thinking和content
    5. 调用Reviewer评审
    6. 发送chapter_complete事件
    """
    task = _active_tasks.get(task_id)
    _mk = (llm_model_key or "").strip()
    if not task:
        task = {
            "task_id": task_id,
            "equipment": equipment or [],
            "environments": environments or [],
            "projects": projects or [],
            "indicators": indicators or [],
            "methods": methods or [],
            "include_section_2_2": _has_selected_indicators(indicators or []),
            "environment_config": environment_config or "",
            "template": load_template(),
            "chapters": chapters,
            "phase": "generating",
            "chapter_contents": {},
            "chapter_scores": {},
            "chapter_feedback": {},
            "llm_model_key": _mk,
        }
        _active_tasks[task_id] = task
    else:
        if methods is not None:
            task["methods"] = methods
        if indicators is not None:
            task["indicators"] = indicators
            task["include_section_2_2"] = _has_selected_indicators(indicators)
        if projects is not None:
            task["projects"] = projects
        if environments is not None:
            task["environments"] = environments
        if equipment is not None:
            task["equipment"] = equipment
        if _mk:
            task["llm_model_key"] = _mk

    task["chapters"] = chapters
    task["phase"] = "generating"
    task.setdefault("llm_model_key", "")

    if not chapters:
        chapters = [
            {"index": 1, "title": "1. 试验依据"},
            {"index": 2, "title": "2. 试验内容及方法"},
        ]
        task["chapters"] = chapters

    total = len(chapters)
    final_scheme_parts = []
    _task_llm_key = (task.get("llm_model_key") or "").strip()

    for i, chapter in enumerate(chapters):
        ch_idx = chapter.get("index", i + 1)
        ch_title = chapter.get("title") or DEFAULT_CHAPTER_TITLES[i] if i < len(DEFAULT_CHAPTER_TITLES) else f"章节{i+1}"
        writer_req = chapter.get("writer_prompt", "")
        reviewer_req = chapter.get("reviewer_prompt", "")
        include_22 = bool(task.get("include_section_2_2"))
        if ch_idx == 2:
            writer_req = merge_chapter2_writer_prompt_with_locked_tree(
                writer_req,
                task.get("environments"),
                task.get("projects"),
                task.get("indicators"),
                task.get("methods"),
                include_22,
            )

        # 检索参考资料
        # 构建检索关键词：使用章节标题 + 写作要求中的关键术语
        search_keywords = ch_title
        if writer_req:
            req_words = [w for w in re.split(r'[\s,，。、；：]+', writer_req) if len(w) >= 2]
            if req_words:
                search_keywords = f"{ch_title} {' '.join(req_words[:5])}"

        search_results = search_context(search_keywords)
        search_ctx = (
            f"\n\n## 检索到的参考资料（向量库语义检索等）\n{search_results}"
            if search_results and "未检索到" not in search_results
            else ""
        )

        yield emit_event("writer_stream", {
            "chapter_index": ch_idx,
            "chapter_title": ch_title,
            "attempt": 1,
            "content": "",
            "status": "started"
        })

        yield emit_event("thinking", {
            "agent": "planner",
            "content": f"开始生成第 {ch_idx} 章：{ch_title}",
            "chapter_index": ch_idx,
        })

        mg_block = _writer_method_graph_block(ch_title, task.get("methods"))
        _sup = (user_supplement or "").strip()
        sup_block = (
            "\n\n## 用户修订稿（全文 Markdown，供参考；请结合本章任务输出新稿）\n" + _sup[:24000] + "\n"
            if _sup
            else ""
        )

        pre_plan, seg21, seg22 = extract_ch2_writer_plan_segments(writer_req)
        use_two_pass = ch_idx == 2 and should_two_pass_chapter2(writer_req, include_22)

        if use_two_pass:
            yield emit_event(
                "thinking",
                {
                    "agent": "planner",
                    "content": "第2章按规划拆为两段生成：先写 ### 2.1…（至 2.2 前），再续写 ### 2.2…。",
                    "chapter_index": ch_idx,
                },
            )
            part1_inst = get_prompt("writer_instruction_ch2_part1", plan_tree=seg21)
            wr_eff_1 = (f"{pre_plan}\n\n{part1_inst}" if pre_plan else part1_inst).strip()
            writer_user_prompt_1 = (
                get_prompt(
                    "writer_user_initial",
                    chapter_title=ch_title,
                    writer_prompt=wr_eff_1,
                    method_graph_block=mg_block,
                    search_context=search_ctx,
                    user_comment="",
                    current_content="",
                )
                + sup_block
            )
            _r1 = _relay_writer_pass(
                _writer_pass_stream(
                    ch_idx,
                    ch_title,
                    writer_user_prompt_1,
                    _task_llm_key,
                    max_tokens=8192,
                    search_results=search_results,
                )
            )
            while True:
                try:
                    yield next(_r1)
                except StopIteration as _ex1:
                    r1 = _ex1.value if isinstance(_ex1.value, dict) else {}
                    break

            prior_body = r1.get("best_content") or ""
            tail_ex = (prior_body[-7000:] if len(prior_body) > 7000 else prior_body).replace("```", "'''")
            part2_inst = get_prompt(
                "writer_instruction_ch2_part2", plan_tree=seg22, prior_tail=tail_ex
            )
            wr_eff_2 = (f"{pre_plan}\n\n{part2_inst}" if pre_plan else part2_inst).strip()
            writer_user_prompt_2 = (
                get_prompt(
                    "writer_user_initial",
                    chapter_title=ch_title,
                    writer_prompt=wr_eff_2,
                    method_graph_block=mg_block,
                    search_context=search_ctx,
                    user_comment="",
                    current_content="",
                )
                + sup_block
            )
            _r2 = _relay_writer_pass(
                _writer_pass_stream(
                    ch_idx,
                    ch_title,
                    writer_user_prompt_2,
                    _task_llm_key,
                    max_tokens=8192,
                    search_results=search_results,
                )
            )
            while True:
                try:
                    yield next(_r2)
                except StopIteration as _ex2:
                    r2 = _ex2.value if isinstance(_ex2.value, dict) else {}
                    break

            best_content = sanitize_scheme_markdown_body(
                merge_chapter_two_segments(
                    r1.get("best_content") or "", r2.get("best_content") or ""
                )
            )
            thinking = (r1.get("thinking") or "") + "\n\n---（第2章 · 2.2 段）---\n\n" + (r2.get("thinking") or "")
            kw_m = f"{r1.get('keywords') or ''} {r2.get('keywords') or ''}".strip()
            keywords = kw_m or ch_title
            sc1, sc2 = r1.get("search_context_data") or "", r2.get("search_context_data") or ""
            search_context_data = sc2 or sc1 or (
                search_results if search_results and "未检索到" not in search_results else ""
            )
        else:
            writer_req_eff = (
                flatten_ch2_writer_prompt_for_single_pass(writer_req)
                if ch_idx == 2
                else writer_req
            )
            writer_user_prompt = (
                get_prompt(
                    "writer_user_initial",
                    chapter_title=ch_title,
                    writer_prompt=writer_req_eff,
                    method_graph_block=mg_block,
                    search_context=search_ctx,
                    user_comment="",
                    current_content="",
                )
                + sup_block
            )
            mx_tok = 8192 if ch_idx == 2 else 4096
            _rg = _relay_writer_pass(
                _writer_pass_stream(
                    ch_idx,
                    ch_title,
                    writer_user_prompt,
                    _task_llm_key,
                    max_tokens=mx_tok,
                    search_results=search_results,
                )
            )
            while True:
                try:
                    yield next(_rg)
                except StopIteration as _exg:
                    res = _exg.value if isinstance(_exg.value, dict) else {}
                    break
            best_content = res.get("best_content", "")
            thinking = res.get("thinking", "")
            keywords = res.get("keywords") or ch_title
            search_context_data = res.get("search_context_data") or (
                search_results if search_results and "未检索到" not in search_results else ""
            )

        if ch_idx == 1:
            best_content = enforce_ch1_template_structure(
                sanitize_scheme_markdown_body(best_content or "")
            )

        if ch_idx == 2 and bool(task.get("include_section_2_2")):
            _bc = best_content or ""
            if re.search(r"(?m)^###\s*2\.2\b", _bc) is None:
                yield emit_event(
                    "thinking",
                    {
                        "agent": "planner",
                        "content": "检测到正文缺少 ### 2.2，触发补写。",
                        "chapter_index": ch_idx,
                    },
                )
                prex, _, s22x = extract_ch2_writer_plan_segments(writer_req)
                tail_hint = (_bc[-4500:] if len(_bc) > 4500 else _bc).replace("```", "'''")
                plan_22 = (s22x or "").strip()
                if not plan_22 or ("无考核" in plan_22[:120] and "不适用" in plan_22[:120]):
                    plan_22 = (
                        "### 2.2 边界性能指标\n"
                        "#### 2.2.1 （按用户「考核指标」上下文填写第一个父级指标全名）\n"
                        "##### 2.2.1.1 （第一个子级指标全名，若有）\n"
                    )
                rescue_inst = (
                    "## 【系统补写·必选】\n用户已选择考核指标，但当前正文仍缺少 `### 2.2 边界性能指标`。"
                    "本轮**必须**完整输出 ### 2.2 及其下 #### 父级指标 / ##### 子级指标标题，并在各指标标题下写方法正文（**（一）（二）（三）** 或 SYNR 连续正文），**禁止**独立方法名标题，不得留空。\n\n"
                    + get_prompt(
                        "writer_instruction_ch2_part2",
                        plan_tree=plan_22,
                        prior_tail=tail_hint,
                    )
                )
                rescue_wp = (
                    ((prex or "").strip() + "\n\n" + rescue_inst).strip()
                    if (prex or "").strip()
                    else rescue_inst
                )
                rescue_user = (
                    get_prompt(
                        "writer_user_initial",
                        chapter_title=ch_title,
                        writer_prompt=rescue_wp,
                        method_graph_block=mg_block,
                        search_context=search_ctx,
                        user_comment="",
                        current_content="",
                    )
                    + sup_block
                )
                _rr = _relay_writer_pass(
                    _writer_pass_stream(
                        ch_idx,
                        ch_title,
                        rescue_user,
                        _task_llm_key,
                        max_tokens=8192,
                        search_results=search_results,
                    )
                )
                while True:
                    try:
                        yield next(_rr)
                    except StopIteration as _er:
                        rresc = _er.value if isinstance(_er.value, dict) else {}
                        break
                patch = (rresc.get("best_content") or "").strip()
                if patch:
                    best_content = sanitize_scheme_markdown_body(merge_chapter_two_segments(_bc, patch))
                    thinking = (thinking or "") + "\n\n---（补写 2.2）---\n\n" + (rresc.get("thinking") or "")
                    if rresc.get("keywords"):
                        keywords = f"{keywords} {rresc.get('keywords')}".strip()

        ch2_struct_ok = True
        if ch_idx == 2:
            best_content = sanitize_scheme_markdown_body(best_content or "")
            exp_projs = task.get("projects") or []
            ch2_struct_ok, ch2_errs = _validate_ch2_structure(best_content, exp_projs)
            if not ch2_struct_ok and MAX_CH2_STRUCTURE_RETRIES > 0:
                err_lines = "\n".join(f"- {e}" for e in ch2_errs)
                struct_sup = (
                    "## 【结构校验失败】须立即修正（违反则方案无效）\n"
                    "上一轮输出未通过系统对「第2章·2.1.1 试验项目」标题树的自动校验。你必须在本轮 **[CONTENT]** 中输出**整章**修订稿，且满足：\n"
                    f"{err_lines}\n\n"
                    "须逐字使用锁定标题树中的 `#####`/`######` 项目标题行，不得增删改；"
                    "方法正文写在对应项目标题下，禁止新增方法名标题行；"
                    "`#### 2.1.1` 须为用户配置的具体环境名称，不得使用「地理环境」等泛称。\n"
                )
                writer_req_eff = flatten_ch2_writer_prompt_for_single_pass(writer_req)
                rewrite_wp = f"{struct_sup}\n\n{writer_req_eff}".strip()
                rewrite_user = (
                    get_prompt(
                        "writer_user_initial",
                        chapter_title=ch_title,
                        writer_prompt=rewrite_wp,
                        method_graph_block=mg_block,
                        search_context=search_ctx,
                        user_comment="",
                        current_content="",
                    )
                    + sup_block
                )
                yield emit_event(
                    "thinking",
                    {
                        "agent": "planner",
                        "content": "第2章未通过标题树结构校验，触发一次全章重写。",
                        "chapter_index": ch_idx,
                    },
                )
                _rstruct = _relay_writer_pass(
                    _writer_pass_stream(
                        ch_idx,
                        ch_title,
                        rewrite_user,
                        _task_llm_key,
                        max_tokens=8192,
                        search_results=search_results,
                    )
                )
                while True:
                    try:
                        yield next(_rstruct)
                    except StopIteration as _es:
                        rstruct = _es.value if isinstance(_es.value, dict) else {}
                        break
                _new_body = (rstruct.get("best_content") or "").strip()
                if _new_body:
                    best_content = sanitize_scheme_markdown_body(_new_body)
                    thinking = (thinking or "") + "\n\n---（第2章·结构重写）---\n\n" + (rstruct.get("thinking") or "")
                    if rstruct.get("keywords"):
                        keywords = f"{keywords} {rstruct.get('keywords')}".strip()
                    scx = rstruct.get("search_context_data") or ""
                    if scx:
                        search_context_data = scx
                ch2_struct_ok, _ = _validate_ch2_structure(best_content, exp_projs)

        yield emit_event(
            "writer_parsed",
            {
                "chapter_index": ch_idx,
                "chapter_title": ch_title,
                "content": best_content,
                "thinking": thinking,
                "keywords": keywords,
                "search_context": search_context_data,
            },
        )

        reviewer_messages = [
            {"role": "system", "content": get_prompt("reviewer_system")},
            {"role": "user", "content": get_prompt("reviewer_user",
                                                   chapter_title=ch_title,
                                                   reviewer_prompt=reviewer_req,
                                                   draft_content=best_content)}
        ]
        review_response = call_llm(
            reviewer_messages, temperature=0.3, max_tokens=2048, llm_model_key=_task_llm_key
        )
        review_json = extract_json_from_text(review_response)
        best_score = review_json.get("score", 60) if review_json else 60
        if ch_idx == 2 and not ch2_struct_ok:
            best_score = 0

        task["chapter_contents"][ch_idx] = best_content
        task["chapter_scores"][ch_idx] = best_score
        final_scheme_parts.append(best_content)

        yield emit_event("thinking", {
            "agent": "reviewer",
            "content": f"第 {ch_idx} 章评审完成 - 评分: {best_score}"
        })

        yield emit_event("chapter_complete", {
            "chapter_index": ch_idx,
            "chapter_title": ch_title,
            "score": best_score,
            "content": best_content,
            "thinking": thinking,
            "keywords": keywords,
            "search_context": search_context_data,
            "progress": (i + 1) / total * 100
        })

    full_scheme = "\n\n".join(final_scheme_parts)
    task["full_scheme"] = full_scheme
    task["phase"] = "complete"

    yield emit_event("complete", {
        "task_id": task_id,
        "full_scheme": full_scheme,
        "chapters": [{"index": c.get("index", i + 1), "title": c.get("title") or (DEFAULT_CHAPTER_TITLES[i] if i < len(DEFAULT_CHAPTER_TITLES) else f"章节{i + 1}"),
                       "content": task["chapter_contents"].get(c.get("index", i + 1), ""),
                       "score": task["chapter_scores"].get(c.get("index", i + 1), 0)} for i, c in enumerate(chapters)],
    })


def get_task(task_id: str) -> Optional[Dict]:
    return _active_tasks.get(task_id)


def rewrite_chapter_stream(
    task_id: str, chapter_index: int, comment: str, llm_model_key: str = ""
) -> Generator[str, None, None]:
    """重写单个章节"""
    task = _active_tasks.get(task_id)
    if not task:
        yield emit_event("error", {"message": "任务不存在"})
        return
    mk = (llm_model_key or "").strip()
    if mk:
        task["llm_model_key"] = mk
    _rk = (task.get("llm_model_key") or "").strip()

    # 安全查找章节 - 支持index字段或位置索引
    chapter = None
    for i, c in enumerate(task["chapters"]):
        if c.get("index") == chapter_index or i + 1 == chapter_index:
            chapter = c
            break
    
    if not chapter:
        yield emit_event("error", {"message": "章节不存在"})
        return

    current_content = task["chapter_contents"].get(chapter_index, "")

    user_prompt = get_prompt("writer_user_rewrite",
                             chapter_title=chapter.get("title") or chapter.get("chapter_title", ""),
                             writer_prompt=chapter.get("writer_prompt", ""),
                             search_context="",
                             user_comment=comment,
                             current_content=current_content)

    writer_messages = [
        {"role": "system", "content": get_prompt("writer_system")},
        {"role": "user", "content": user_prompt}
    ]

    draft_content = ""
    for chunk in call_llm_stream(writer_messages, temperature=0.7, max_tokens=4096, llm_model_key=_rk):
        if isinstance(chunk, dict):
            continue
        draft_content += chunk

    # 解析 [THINK]/[CONTENT] 标签，提取正文
    thinking_text = ""
    content_text = draft_content

    tag_t, tag_c = parse_writer_tag_blocks(draft_content, allow_unclosed=True)
    if (tag_c or "").strip():
        thinking_text = (tag_t or "").strip()
        content_text = tag_c.strip()
    else:
        think_match = re.search(
            r"\[\s*THINK\s*\]([\s\S]*?)\[\s*/\s*THINK\s*\]", draft_content, re.DOTALL | re.IGNORECASE
        )
        if think_match:
            thinking_text = think_match.group(1).strip()

        content_match = re.search(
            r"\[\s*CONTENT\s*\]([\s\S]*?)\[\s*/\s*CONTENT\s*\]",
            draft_content,
            re.DOTALL | re.IGNORECASE,
        )
        if content_match:
            content_text = content_match.group(1).strip()
        else:
            writer_json = extract_json_from_text(draft_content)
            if writer_json:
                content_text = writer_json.get("content", draft_content)

    if isinstance(content_text, str):
        content_text = sanitize_scheme_markdown_body(content_text)

    task["chapter_contents"][chapter_index] = content_text

    parts = []
    for i, ch in enumerate(task["chapters"]):
        idx = ch.get("index", i + 1)
        parts.append(task["chapter_contents"].get(idx, ""))
    task["full_scheme"] = "\n\n".join(parts)

    yield emit_event("rewrite_complete", {
        "chapter_index": chapter_index,
        "content": content_text,
        "thinking": thinking_text
    })
