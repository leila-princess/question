"""
ZNWD V4 智能问答 — 整体流程说明
================================

本文件为稳定运行的 V4 实现（现网/联调已验证）。入口路由：`POST /api/znwd/chat/stream_v4`
（`__init__.py::chat_stream_v4`）。V5/V6 可通过 `pipelineVersion` 切换；V5 在能力缺口时会
调用本文件的 `collect_v4_retrieval_results` / `llm_stream_single` 回退。

V6（默认 pipeline）在 LangChain Agent 之前经 `znwd_preflight.py` 做前置判别：
复用本文件的 `_quick_plan`（strategy/answerMode）、`_enrich_retrieval_question_with_focus`、
`collect_v4_retrieval_results` 与 V5 快速路径（`znwd_fast_route` + `znwd_v6_executor`）。
可直出则不经 Agent；否则进入 `znwd_langchain_v6._v6_agent_stream`。

重要约定：
1) V4 逻辑保持可回退、可对照，不做破坏性替换。
2) 程序化元操作编排见 `services_v5.py`；V6 见 `services_v6.py`。
3) 默认运行模式为 **plan**（两阶段：先规划检索、再整合回答），非 OpenAI Tools 多轮 Agent。


一、请求入口（蓝图层）
--------------------
1. 解析 `question` / `sessionId` / `userId` / `llmModelKey`。
2. 从 Redis（`memory_store`）加载历史消息与摘要，组装 `messages`：
   `[摘要 system?] + 历史轮次 + 当前 user`。
3. 估算上下文 Token；超限则 SSE 返回 `token_limit`，不再调 LLM。
4. 按 `pipelineVersion`（默认 v6）选择 `llm_stream_single` / v5 / v6；
   显式走 V4 时调用本模块 `llm_stream_single`。
5. 将生成器事件映射为 SSE（`thinking` / `answer` / `step` / `done` 等），
   结束后写回 Redis，必要时触发 MySQL 持久化。


二、V4 总入口：`llm_stream_single`
---------------------------------
环境变量 `ZNWD_TOOL_CALL_MODE`（默认 `plan`）决定分支：

| 模式   | 值              | 主流程函数              | 说明 |
|--------|-----------------|-------------------------|------|
| plan   | plan / prefetch | `_stream_plan_then_answer` | 服务端执行检索，适配 vLLM 等无稳定 tools 的部署 |
| agent  | agent           | `_stream_single_llm`（tools 开启） | 模型通过 `query_neo4j` / `search_knowledge_base` 多轮工具调用 |

两种模式最终均由 `_stream_single_llm` 做流式输出解析，并 `yield ("done", {fullAnswer, fullThinking})`。


三、plan 模式主流程（`_stream_plan_then_answer`）
----------------------------------------------
```
用户 messages
    │
    ▼
① 问题上下文化
    _last_user_question → _enrich_retrieval_question_with_focus
    （短追问拼接上一轮；省略主语时补【承前主体】；自洽列举问句不拼接）
    │
    ▼
② 检索方案（意图判别 + 规划）
    规则初案 _quick_plan(retrieval_question)
    → LLM 非流式调用（RETRIEVAL_PLAN_PROMPT，输出 [RETRIEVAL_PLAN]{JSON}[/RETRIEVAL_PLAN]）
    → parse_retrieval_plan → normalize_plan_from_llm
    → _finalize_retrieval_plan（按问句强制 neo4j.pattern、answerMode）
    → yield search_keywords（ssyqTableKeyword，供前端 SSYQ 表格检索）
    │
    ├─ strategy == "none"（闲聊等）─────────────────────────────┐
    │                                                              ▼
    │                                    DIRECT_ANSWER_PROMPT + _stream_single_llm → 结束
    │
    ▼ 需要检索
③ 并行数据源检索
    Neo4j：query_neo4j(pattern, keywords, limit)
      - 结构化模式：equipment_con_testproject / environment_con_testproject /
        equipment_con_indicator / equipment_via_project_testmethod / equipment_bundle
      - 失败则降级 keyword_fuzzy（全属性 CONTAINS）
    Chroma：仅当方案启用且 user_question_allows_chroma（法规/标准/方案等文档意图正则命中）
    → yield source_data、neo4j_search / chroma_search 步骤事件
    │
    ▼
④ 回答路径分支
    A) database_direct：枚举测试项目 + 结构化 Neo 全命中
       → _yield_database_direct_answer_stream（服务端拼表，不经 LLM 整合）
    B) Neo/Chroma 均无命中
       → DIRECT_ANSWER_PROMPT + _stream_single_llm（声明未命中，模型直接答）
    C) 有命中（常规）
       → _build_evidence_user_content（方案 + Neo/Chroma JSON，受 ZNWD_EVIDENCE_JSON_MAX 截断）
       → ANSWER_SYNTHESIS_PROMPT + _stream_single_llm（流式整合回答）
```


四、检索方案 JSON 字段（规划阶段产出）
------------------------------------
- `intent` / `strategy`：`direct` | `fuzzy` | `none`
- `answerMode`：`database_direct`（清单直出）| `llm_synthesize`（模型整合）
- `neo4j`：`use`, `query`（短关键词）, `limit`, `pattern`, `rationale`
- `chroma`：`use`, `query`, `rationale`
- `ssyqTableKeyword`：与 Neo 检索词对齐，供实施要求（SSYQ）表格搜索


五、流式输出（`_stream_single_llm`）
----------------------------------
1. 调用 OpenAI 兼容 API（`stream=True`）；超长上下文时 `maybe_compress_messages_for_context`。
2. 累积 delta，解析两种标签格式：
   - 方括号：`[THINK]…[/THINK]`、`[CONTENT]…[/CONTENT]`
   - XML：`<thinking>…</thinking>`、`<content>…</content>`
3. 映射 SSE 事件：`thinking_start` / `thinking` / `answer`；
   抑制误写入 content 的工具 JSON（`_znwd_text_looks_like_tool_steps_json`）。
4. agent 模式：解析 `tool_calls` → 执行 TOOL_DISPATCH → 将 tool 结果追加 messages → 递归 `_stream_single_llm`。
5. `_safe_tool_arguments_for_api`：修复模型输出多个顶层步骤对象未包在 `steps` 数组的问题。


六、对外复用 API
---------------
- `collect_v4_retrieval_results`：只跑「规划 + Neo + Chroma」，不生成回答（供 V5 补检索）。
- `llm_stream_single`：完整 V4 对话流（plan 或 agent）。
- `query_neo4j` / `search_knowledge_base`：图谱与向量库底层检索。


七、V6 整合全流程（Preflight + Agent，默认 pipelineVersion=v6）
----------------------------------------------------------
```
HTTP stream_v4 → Redis 记忆 → pipelineVersion=v6 → llm_stream_single_v6
    │
    ▼ [ZNWD_V6_PREFLIGHT=1]
znwd_preflight.run_preflight_or_agent
    │
    ├─ build_retrieval_question（V5 多轮 + 摘要）
    ├─ _quick_plan → strategy / answerMode（本文件，无 LLM）
    │
    ├─ strategy=none → DIRECT_ANSWER（单次 LLM）→ done
    │
    ├─ classify_intent + try_build_fast_path_program（znwd_fast_route）
    │     → execute_meta_steps（znwd_v6_executor）
    │     ├─ database_direct 且命中充足 → format_fast_direct_body → done（零 Agent）
    │     ├─ 无命中 → collect_v4_retrieval_results（可选）→ V4 项目清单直出 或 DIRECT_ANSWER
    │     └─ Chroma 命中 / 需合成 → handoff Agent
    │
    └─ 未命中快速路径 / 需推理 → handoff
          ▼
    _v6_agent_stream（LangChain bind_tools，最多 3 轮 + 最终流式合成）→ done
```

八、相关文件
-----------
- `prompts_v4.py`：RETRIEVAL_PLAN_PROMPT、ANSWER_SYNTHESIS_PROMPT、DIRECT_ANSWER_PROMPT、图谱 schema
- `znwd_preflight.py`：V6 前置判别与快速直出
- `znwd_fast_route.py`：快速路径共享 API（V5/V6）
- `znwd_langchain_v6.py` / `znwd_v6_executor.py` / `znwd_v6_tools.py`：V6 Agent
- `__init__.py`：路由、SSE 封装、记忆读写
- `memory_store.py` / `models.py`：Redis 短期记忆、MySQL 历史
- `说明.md`：模块级架构与 API 列表

环境变量（常用）：
- `ZNWD_TOOL_CALL_MODE`：plan（默认）| agent（仅 V4 显式调用时）
- `ZNWD_V6_PREFLIGHT`：1（默认）| 0 — 关闭则 V6 直连 Agent
- `ZNWD_V6_PREFLIGHT_V4_SUPPLEMENT`：1（默认）— 快速路径无命中时 V4 补检索
- `ZNWD_EVIDENCE_JSON_MAX`：整合阶段证据 JSON 最大字符（默认 12000）
- `ZNWD_AGENT_TOOL_CHOICE`：agent 模式下 tool_choice（默认 required）
"""

import copy
import json
import logging
import os
import re
from typing import Any, Dict, List, Generator, Tuple, Optional

from utils.llm_context_budget import (
    approx_messages_tokens,
    completion_cap,
    maybe_compress_messages_for_context,
)

from .prompts_v4 import ANSWER_SYNTHESIS_PROMPT, RETRIEVAL_PLAN_PROMPT, DIRECT_ANSWER_PROMPT

logger = logging.getLogger(__name__)


def _looks_like_znwd_meta_step_dict(d: Any) -> bool:
    """模型常把多步写成 `{...}, {...}` 而无 `steps` 数组；用于识别顶层步骤对象。"""
    if not isinstance(d, dict):
        return False
    if not str(d.get("op_code") or "").strip():
        return False
    return isinstance(d.get("params"), dict)


def _safe_tool_arguments_for_api(raw: Optional[str]) -> Tuple[Dict[str, Any], str]:
    """
    解析模型流式拼出的 function.arguments 字符串，返回 (dict, 可放入下一轮请求的合法 JSON 字符串)。
    - 若根对象已是 `{"steps":[...]}`，行为与原先一致；其后若仍有 tail 则告警并丢弃 tail。
    - 若根对象为单个步骤 `{ "op_code", "params", ... }` 且后面紧跟 `, {` 另一对象，则把多个顶层对象合并为 `{"steps":[...]}`（修复「只 decode 到第一步、steps 缺失」导致检索未执行）。
    """
    s = (raw or "").strip()
    if not s:
        return {}, "{}"
    try:
        first, end = json.JSONDecoder().raw_decode(s)
        if not isinstance(first, dict):
            logger.warning("[Tool] arguments JSON root is not an object, coerced to {}")
            return {}, "{}"
        collected: List[Dict[str, Any]] = [first]
        rest = s[end:].strip()
        while rest.startswith(","):
            chunk = rest[1:].strip()
            if not chunk:
                break
            try:
                nxt, nend = json.JSONDecoder().raw_decode(chunk)
            except json.JSONDecodeError:
                logger.warning(
                    "[Tool] arguments trailing chunk JSON decode failed tail_prefix=%r",
                    chunk[:240],
                )
                break
            if not isinstance(nxt, dict):
                break
            collected.append(nxt)
            rest = chunk[nend:].strip()

        # 已是规范包裹：{"steps": [...], ...}
        if len(collected) == 1 and isinstance(collected[0].get("steps"), list):
            out = collected[0]
            if rest:
                logger.warning(
                    "[Tool] arguments had trailing data after first object (tail=%r), using first JSON object only",
                    rest[:200],
                )
            return out, json.dumps(out, ensure_ascii=False)

        # 多个或未包裹的「顶层步骤」对象 → 合并为 steps
        if collected and all(_looks_like_znwd_meta_step_dict(x) for x in collected):
            out = {"steps": collected}
            if rest:
                logger.warning(
                    "[Tool] arguments merged %d top-level step dict(s) into steps; unused_tail=%r",
                    len(collected),
                    rest[:200],
                )
            elif len(collected) > 1:
                logger.info(
                    "[Tool] arguments merged %d comma-separated top-level step dict(s) into steps",
                    len(collected),
                )
            return out, json.dumps(out, ensure_ascii=False)

        out = collected[0]
        if rest:
            logger.warning(
                "[Tool] arguments had trailing data after pos %s (tail=%r), using first JSON object only",
                end,
                rest[:160],
            )
        return out, json.dumps(out, ensure_ascii=False)
    except json.JSONDecodeError as e:
        logger.warning("[Tool] arguments JSON decode failed: %s | raw_prefix=%r", e, s[:240])
        return {}, "{}"


# plan：LLM 先输出检索方案 → 服务端按需查 Neo4j / Chroma → 再流式整合回答（无 tools API，适配 vLLM）
# agent：由模型发起工具调用；首轮 tool_choice 默认 required
_TOOL_MODE_RAW = os.environ.get("ZNWD_TOOL_CALL_MODE", "plan").strip().lower()
TOOL_CALL_MODE = "plan" if _TOOL_MODE_RAW in ("plan", "prefetch") else _TOOL_MODE_RAW
AGENT_TOOL_CHOICE = os.environ.get("ZNWD_AGENT_TOOL_CHOICE", "required").strip().lower()

# 整合阶段写入 LLM user 消息里的 Neo/Chroma JSON 各自最大字符数（过小会截断检索结果，模型只能「总结一部分」）
def _evidence_json_slice_limit() -> int:
    try:
        return max(2000, min(int(os.environ.get("ZNWD_EVIDENCE_JSON_MAX", "12000")), 200000))
    except ValueError:
        return 12000


def _v4_summarize_neo_hits(nodes: List[Dict], max_lines: int = 50) -> str:
    lines: List[str] = []
    for i, n in enumerate(nodes or []):
        if i >= max_lines:
            lines.append(f"  ... 共 {len(nodes)} 条，此处仅列前 {max_lines} 条")
            break
        if not isinstance(n, dict):
            lines.append(f"  [{i}] <非字典>")
            continue
        if "error" in n:
            lines.append(f"  [{i}] error={n.get('error')}")
            continue
        rk = n.get("rowKind")
        if rk == "EquipmentToTestProject":
            ep = (n.get("equipment") or {}).get("properties") or {}
            pp = (n.get("testProject") or {}).get("properties") or {}
            lines.append(
                f"  [{i}] Equipment→TestProject  设备={ep.get('Name')!r}  项目={pp.get('Name')!r}"
            )
            continue
        if rk == "EnvironmentToTestProject":
            ep = (n.get("environment") or {}).get("properties") or {}
            pp = (n.get("testProject") or {}).get("properties") or {}
            lines.append(
                f"  [{i}] Environment→TestProject  环境={ep.get('Name')!r}  项目={pp.get('Name')!r}"
            )
            continue
        if rk == "EquipmentToIndicator":
            ep = (n.get("equipment") or {}).get("properties") or {}
            ip = (n.get("indicator") or {}).get("properties") or {}
            lines.append(
                f"  [{i}] Equipment→Indicator  设备={ep.get('Name')!r}  指标={ip.get('Name')!r}"
            )
            continue
        if rk == "EquipmentProjectTestMethod":
            ep = (n.get("equipment") or {}).get("properties") or {}
            pp = (n.get("testProject") or {}).get("properties") or {}
            mp = (n.get("testMethod") or {}).get("properties") or {}
            lines.append(
                f"  [{i}] Equipment→Project→Method  设备={ep.get('Name')!r}  项目={pp.get('Name')!r}  方法={mp.get('Name')!r}"
            )
            continue
        labels = n.get("labels") or []
        if isinstance(labels, str):
            labels = [labels]
        lab = labels[0] if labels else "?"
        p = n.get("properties") or {}
        name = str(p.get("Name") or p.get("name") or p.get("Label") or p.get("title") or "")[:80]
        nid = str(p.get("Id") or p.get("id") or "")[:40]
        lines.append(f"  [{i}] {lab}  Name={name!r}  Id={nid!r}")
    return "\n".join(lines) if lines else "  (无)"


def _v4_summarize_chroma_chunks(chunks: List[Dict], max_lines: int = 12) -> str:
    lines: List[str] = []
    for i, c in enumerate(chunks or []):
        if i >= max_lines:
            lines.append(f"  ... 共 {len(chunks)} 条片段，此处仅列前 {max_lines} 条")
            break
        if not isinstance(c, dict):
            lines.append(f"  [{i}] <非字典>")
            continue
        title = str(c.get("title") or "")[:60]
        did = str(c.get("doc_id") or "")[:40]
        content = str(c.get("content") or "")
        lines.append(
            f"  [{i}] doc_id={did!r} title={title!r} content_len={len(content)}"
        )
    return "\n".join(lines) if lines else "  (无)"


def _v4_log_retrieval_for_debug(
    *,
    question: str,
    retrieval_question: str,
    plan_norm: Dict[str, Any],
    neo_query_used: str,
    neo_executed: bool,
    chroma_executed: bool,
    neo_res: List[Dict],
    chroma_res: List[Dict],
    evidence_text: str,
) -> None:
    lim = _evidence_json_slice_limit()
    neo_full = json.dumps(neo_res, ensure_ascii=False, default=str)
    ch_full = json.dumps(chroma_res or [], ensure_ascii=False, default=str)
    neo_snip = neo_full[:lim]
    ch_snip = ch_full[:lim]
    logger.info("[V4][DEBUG] ========== 问答检索调试（本轮）==========")
    logger.info("[V4][DEBUG] 用户问题: %s", question[:500])
    logger.info("[V4][DEBUG] 上下文化问题: %s", retrieval_question[:500])
    logger.info("[V4][DEBUG] ssyqTableKeyword=%r neo_query_used=%r", plan_norm.get("ssyqTableKeyword"), neo_query_used)
    logger.info("[V4][DEBUG] neo_executed=%s chroma_executed=%s", neo_executed, chroma_executed)
    logger.info("[V4][DEBUG] Neo4j 条数=%d JSON全长=%d 送入LLM截取=%d (ZNWD_EVIDENCE_JSON_MAX=%d)", len(neo_res), len(neo_full), len(neo_snip), lim)
    if len(neo_full) > len(neo_snip):
        logger.warning("[V4][DEBUG] Neo4j 证据 JSON 被截断，约丢失 %d 字符", len(neo_full) - len(neo_snip))
    logger.info("[V4][DEBUG] Chroma 条数=%d JSON全长=%d 送入LLM截取=%d", len(chroma_res or []), len(ch_full), len(ch_snip))
    if len(ch_full) > len(ch_snip):
        logger.warning("[V4][DEBUG] Chroma 证据 JSON 被截断，约丢失 %d 字符", len(ch_full) - len(ch_snip))
    logger.info("[V4][DEBUG] ---- Neo4j 命中摘要（名称/类型）----\n%s", _v4_summarize_neo_hits(neo_res))
    logger.info("[V4][DEBUG] ---- Chroma 命中摘要 ----\n%s", _v4_summarize_chroma_chunks(chroma_res or []))
    logger.info("[V4][DEBUG] 整合阶段 evidence（user 单条）长度=%d 字符", len(evidence_text))
    tail = 800
    if len(evidence_text) > tail * 2:
        logger.info("[V4][DEBUG] ---- 证据正文头 %d 字 ----\n%s", tail, evidence_text[:tail])
        logger.info("[V4][DEBUG] ---- 证据正文尾 %d 字 ----\n%s", tail, evidence_text[-tail:])
    else:
        logger.info("[V4][DEBUG] ---- 证据正文（全量）----\n%s", evidence_text)
    logger.info("[V4][DEBUG] ========== 检索调试结束 ==========")


# 用户问题需匹配以下意图才允许执行 Chroma（与「法规、指导文件、标准、方案、大纲、报告」等文档诉求一致）
CHROMA_DOC_INTENT_RE = re.compile(
    r"法规|条例|法律|规章|指导文件|指导性文件|标准|规范|方案|大纲|报告|模板"
    r"|GB\s*/\s*T|GB/T|GJB|国军标|技术文件|条文|条款"
)

# 结构化 Neo 检索模式（由问句解析，覆盖 LLM 易误判的 pattern）
NEO_STRUCTURED_PATTERNS = frozenset({
    "equipment_con_testproject",
    "environment_con_testproject",
    "equipment_con_indicator",
    "equipment_via_project_testmethod",
    "testproject_con_testmethod",
    "equipment_bundle",
})

_METHOD_FOCUS_RE = re.compile(
    r"(测试方法|试验方法|检测方法|(?:什么|哪些|哪几种|有几).{0,6}方法|方法.{0,6}(有哪些|是什么|哪几种|包括|列出)|采用.{0,8}方法|依据.{0,8}方法|用到.{0,6}方法)",
)
_INDICATOR_FOCUS_RE = re.compile(r"(指标|性能指标|技术参数|考核指标)")


def question_asks_test_project_enumeration(text: str) -> bool:
    """仅当用户明确在问「测试/试验项目」枚举时返回 True（避免「有哪些测试」误伤）。"""
    t = text or ""
    if _METHOD_FOCUS_RE.search(t) and not re.search(r"(测试项目|试验项目)", t):
        return False
    if re.search(r"(测试项目|试验项目)", t):
        return True
    if re.search(r"(测哪些项目|项目有哪些|有哪些项目)", t):
        return True
    if re.search(r"哪些测试\s*(项目|试验)", t):
        return True
    if re.search(r"关联.{0,10}项目", t):
        return True
    return False


def question_asks_indicator_focus(text: str) -> bool:
    t = text or ""
    if not _INDICATOR_FOCUS_RE.search(t):
        return False
    return True


def question_asks_method_focus(text: str) -> bool:
    return bool(_METHOD_FOCUS_RE.search(text or ""))


def _looks_like_testproject_method_by_name(rq: str) -> bool:
    """问法为「某测试/试验项目名 + 有什么方法」，主体是 TestProject 而非 Equipment。"""
    t = (rq or "").strip()
    if not t or not question_asks_method_focus(t):
        return False
    if question_asks_test_project_enumeration(t):
        return False
    if re.search(r"(哪些|哪一些|什么|都有哪些).{0,20}(设备|装备)", t):
        return False
    from .znwd_business_templates import _extract_test_project_keyword_for_method_question

    kw = _extract_test_project_keyword_for_method_question(t, t)
    if len(kw) >= 2:
        return True
    if re.search(r"[\u4e00-\u9fff]{2,30}(?:测试|试验)", t) and re.search(
        r"(方法|试验方法|测试方法)", t
    ):
        return True
    return False


def resolve_neo_pattern_from_question(rq: str) -> str:
    """按图模型与问句意图选择 Neo 检索模式（优先于 LLM 的 pattern 字段）。"""
    t = (rq or "").strip()
    if not t:
        return "keyword_fuzzy"
    has_enum = question_asks_test_project_enumeration(t)
    has_ind = question_asks_indicator_focus(t)
    has_meth = question_asks_method_focus(t)
    mentions_proj = "项目" in t
    mentions_ind = bool(_INDICATOR_FOCUS_RE.search(t))
    both_pi = mentions_proj and mentions_ind

    if has_meth and not has_enum and _looks_like_testproject_method_by_name(t):
        return "testproject_con_testmethod"
    if has_meth and not has_enum:
        return "equipment_via_project_testmethod"
    if both_pi and (has_enum or has_ind):
        return "equipment_bundle"
    if has_ind and not has_enum:
        return "equipment_con_indicator"
    if has_enum:
        if "环境" in t and "设备" not in t:
            return "environment_con_testproject"
        return "equipment_con_testproject"
    return "keyword_fuzzy"


def _serialize_neo_graph_node(node) -> Dict[str, Any]:
    if not node or not hasattr(node, "items"):
        return {}
    return {
        "labels": list(node.labels),
        "properties": {k: v for k, v in node.items() if v is not None},
        "__elementId": getattr(node, "element_id", None),
    }


def _query_neo4j_equipment_con_test_projects(driver, keywords: List[str], limit: int) -> List[Dict]:
    kws = [k.strip().lower() for k in keywords if k and str(k).strip()]
    if not kws:
        return []
    limit = max(1, min(int(limit), 120))
    cypher = """
    MATCH (e:Equipment)-[r:CON]->(p:TestProject)
    WHERE ANY(sub IN $kws WHERE toLower(e.Name) CONTAINS sub)
    RETURN e AS equipment, p AS testProject, type(r) AS rel_type, properties(r) AS rel_props
    LIMIT $limit
    """
    rows: List[Dict] = []
    try:
        with driver.session() as session:
            result = session.run(cypher, kws=kws, limit=limit)
            for record in result:
                e = record.get("equipment")
                p = record.get("testProject")
                if not e or not p:
                    continue
                rows.append({
                    "rowKind": "EquipmentToTestProject",
                    "equipment": _serialize_neo_graph_node(e),
                    "testProject": _serialize_neo_graph_node(p),
                    "relationship": {
                        "type": record.get("rel_type") or "CON",
                        "properties": record.get("rel_props") or {},
                    },
                })
        logger.info("[Neo4j Structured] Equipment-CON->TestProject rows=%s kws=%s", len(rows), kws)
    except Exception as ex:
        logger.error("[Neo4j Structured Equipment] %s", ex)
        return [{"error": f"equipment project query failed: {ex}"}]
    return rows


def _query_neo4j_environment_con_test_projects(driver, keywords: List[str], limit: int) -> List[Dict]:
    kws = [k.strip().lower() for k in keywords if k and str(k).strip()]
    if not kws:
        return []
    limit = max(1, min(int(limit), 120))
    cypher = """
    MATCH (n:Environment)-[r:CON]->(p:TestProject)
    WHERE ANY(sub IN $kws WHERE toLower(n.Name) CONTAINS sub)
    RETURN n AS environment, p AS testProject, type(r) AS rel_type, properties(r) AS rel_props
    LIMIT $limit
    """
    rows: List[Dict] = []
    try:
        with driver.session() as session:
            result = session.run(cypher, kws=kws, limit=limit)
            for record in result:
                env = record.get("environment")
                p = record.get("testProject")
                if not env or not p:
                    continue
                rows.append({
                    "rowKind": "EnvironmentToTestProject",
                    "environment": _serialize_neo_graph_node(env),
                    "testProject": _serialize_neo_graph_node(p),
                    "relationship": {
                        "type": record.get("rel_type") or "CON",
                        "properties": record.get("rel_props") or {},
                    },
                })
        logger.info("[Neo4j Structured] Environment-CON->TestProject rows=%s kws=%s", len(rows), kws)
    except Exception as ex:
        logger.error("[Neo4j Structured Environment] %s", ex)
        return [{"error": f"environment project query failed: {ex}"}]
    return rows


def _query_neo4j_equipment_con_indicators(driver, keywords: List[str], limit: int) -> List[Dict]:
    kws = [k.strip().lower() for k in keywords if k and str(k).strip()]
    if not kws:
        return []
    limit = max(1, min(int(limit), 120))
    cypher = """
    MATCH (e:Equipment)-[r:CON]->(i:Indicator)
    WHERE ANY(sub IN $kws WHERE toLower(e.Name) CONTAINS sub)
    RETURN e AS equipment, i AS indicator, type(r) AS rel_type, properties(r) AS rel_props
    LIMIT $limit
    """
    rows: List[Dict] = []
    try:
        with driver.session() as session:
            result = session.run(cypher, kws=kws, limit=limit)
            for record in result:
                e = record.get("equipment")
                ind = record.get("indicator")
                if not e or not ind:
                    continue
                rows.append({
                    "rowKind": "EquipmentToIndicator",
                    "equipment": _serialize_neo_graph_node(e),
                    "indicator": _serialize_neo_graph_node(ind),
                    "relationship": {
                        "type": record.get("rel_type") or "CON",
                        "properties": record.get("rel_props") or {},
                    },
                })
        logger.info("[Neo4j Structured] Equipment-CON->Indicator rows=%s kws=%s", len(rows), kws)
    except Exception as ex:
        logger.error("[Neo4j Structured Equipment->Indicator] %s", ex)
        return [{"error": f"equipment indicator query failed: {ex}"}]
    return rows


def _query_neo4j_testproject_con_testmethods(driver, keywords: List[str], limit: int) -> List[Dict]:
    """TestProject.Name 子串匹配 → 关联 TestMethod（与 JQCX /methods 同语义）。"""
    from .znwd_business_templates import expand_testproject_method_keywords

    raw_kws = [k.strip() for k in keywords if k and str(k).strip()]
    if not raw_kws:
        return []
    or_kws: List[str] = []
    must_all: List[str] = []
    for rk in raw_kws[:4]:
        ok, ma = expand_testproject_method_keywords(rk)
        for x in ok:
            xl = x.strip().lower()
            if xl and xl not in or_kws:
                or_kws.append(xl)
        for x in ma:
            xl = x.strip().lower()
            if xl and xl not in must_all:
                must_all.append(xl)
    if not or_kws:
        return []
    limit = max(1, min(int(limit), 120))
    try:
        from utils.neo4j_flgl_status import flgl_status_cypher_param

        stp = flgl_status_cypher_param()
    except Exception:
        stp = {}
    cypher = """
    MATCH (p:TestProject)-[r:CON]->(m:TestMethod)
    WHERE (
      ANY(sub IN $kws WHERE toLower(coalesce(p.Name,'')) CONTAINS sub)
      OR (
        size($must_all) > 0
        AND ALL(tok IN $must_all WHERE toLower(coalesce(p.Name,'')) CONTAINS tok)
      )
    )
      AND (p.Status IS NULL OR p.Status IN $flgl_enabled_status)
      AND (m.Status IS NULL OR m.Status IN $flgl_enabled_status)
    RETURN p AS testProject, m AS testMethod, type(r) AS rel_type
    LIMIT $limit
    """
    rows: List[Dict] = []
    try:
        with driver.session() as session:
            result = session.run(
                cypher, kws=or_kws, must_all=must_all, limit=limit, **stp
            )
            for record in result:
                p = record.get("testProject")
                m = record.get("testMethod")
                if not p or not m:
                    continue
                rows.append({
                    "rowKind": "TestProjectToTestMethod",
                    "testProject": _serialize_neo_graph_node(p),
                    "testMethod": _serialize_neo_graph_node(m),
                    "n": _serialize_neo_graph_node(p),
                    "m": _serialize_neo_graph_node(m),
                })
        logger.info(
            "[Neo4j Structured] TestProject-Method rows=%s kws=%s cypher=\n%s",
            len(rows),
            {"or": or_kws, "must_all": must_all},
            cypher.strip(),
        )
    except Exception as ex:
        logger.error("[Neo4j Structured TestProject->Method] %s", ex)
        return [{"error": f"testproject method query failed: {ex}"}]
    return rows


def _query_neo4j_equipment_project_testmethods(driver, keywords: List[str], limit: int) -> List[Dict]:
    kws = [k.strip().lower() for k in keywords if k and str(k).strip()]
    if not kws:
        return []
    limit = max(1, min(int(limit), 120))
    cypher = """
    MATCH (e:Equipment)-[:CON]->(p:TestProject)-[:CON]->(m:TestMethod)
    WHERE ANY(sub IN $kws WHERE toLower(e.Name) CONTAINS sub)
    RETURN e AS equipment, p AS testProject, m AS testMethod
    LIMIT $limit
    """
    rows: List[Dict] = []
    try:
        with driver.session() as session:
            result = session.run(cypher, kws=kws, limit=limit)
            for record in result:
                e = record.get("equipment")
                p = record.get("testProject")
                m = record.get("testMethod")
                if not e or not p or not m:
                    continue
                rows.append({
                    "rowKind": "EquipmentProjectTestMethod",
                    "equipment": _serialize_neo_graph_node(e),
                    "testProject": _serialize_neo_graph_node(p),
                    "testMethod": _serialize_neo_graph_node(m),
                })
        logger.info("[Neo4j Structured] Equipment-Project-Method rows=%s kws=%s", len(rows), kws)
    except Exception as ex:
        logger.error("[Neo4j Structured Equipment->Project->Method] %s", ex)
        return [{"error": f"equipment method query failed: {ex}"}]
    return rows


def _query_neo4j_equipment_bundle(driver, keywords: List[str], limit: int) -> List[Dict]:
    half = max(10, min(limit // 2, 80))
    pr = _query_neo4j_equipment_con_test_projects(driver, keywords, half)
    ir = _query_neo4j_equipment_con_indicators(driver, keywords, max(10, min(limit - half, 80)))
    if pr and isinstance(pr[0], dict) and pr[0].get("error"):
        pr = []
    if ir and isinstance(ir[0], dict) and ir[0].get("error"):
        ir = []
    return pr + ir


def _chroma_nonempty(chunks: Optional[List[Dict]]) -> bool:
    for x in chunks or []:
        if not isinstance(x, dict) or x.get("error"):
            continue
        if str(x.get("content") or "").strip():
            return True
    return False


def _neo_rows_all_structured_projects(neo_res: List[Dict]) -> bool:
    if not neo_res:
        return False
    ok_kinds = ("EquipmentToTestProject", "EnvironmentToTestProject")
    for x in neo_res:
        if not isinstance(x, dict) or x.get("error"):
            return False
        if x.get("rowKind") not in ok_kinds:
            return False
    return True


def _neo_rows_has_structured_results(neo_res: List[Dict]) -> bool:
    """检查是否有任何结构化结果（项目、指标、方法等）。"""
    if not neo_res:
        return False
    ok_kinds = (
        "EquipmentToTestProject",
        "EnvironmentToTestProject",
        "EquipmentToIndicator",
        "EquipmentViaProjectTestMethod",
        "TestProjectToTestMethod",
    )
    for x in neo_res:
        if not isinstance(x, dict) or x.get("error"):
            continue
        if x.get("rowKind") in ok_kinds:
            return True
    return False


def _format_database_direct_answer(neo_res: List[Dict], question: str) -> str:
    """将结构化图查询结果排版为中文清单（不经 LLM）。"""
    lines: List[str] = []
    q = (question or "").strip()
    if q:
        lines.append(f"针对问题「{q}」，根据图数据库中与设备/环境关联的记录，整理如下：\n")

    eq_rows = [r for r in neo_res if isinstance(r, dict) and r.get("rowKind") == "EquipmentToTestProject"]
    env_rows = [r for r in neo_res if isinstance(r, dict) and r.get("rowKind") == "EnvironmentToTestProject"]
    eq_ind_rows = [r for r in neo_res if isinstance(r, dict) and r.get("rowKind") == "EquipmentToIndicator"]

    if eq_rows:
        from collections import defaultdict
        by_eq: Dict[str, List[str]] = defaultdict(list)
        for r in eq_rows:
            ep = (r.get("equipment") or {}).get("properties") or {}
            pp = (r.get("testProject") or {}).get("properties") or {}
            en = str(ep.get("Name") or ep.get("Label") or "（未命名设备）").strip()
            pn = str(pp.get("Name") or pp.get("Label") or "（未命名项目）").strip()
            if pn not in by_eq[en]:
                by_eq[en].append(pn)
        lines.append("**测试项目：**")
        for ename, projects in sorted(by_eq.items(), key=lambda x: x[0]):
            lines.append(f"- 设备：{ename}")
            for pj in projects:
                lines.append(f"  - {pj}")
            lines.append("")

    if env_rows:
        from collections import defaultdict
        by_env: Dict[str, List[str]] = defaultdict(list)
        for r in env_rows:
            ep = (r.get("environment") or {}).get("properties") or {}
            pp = (r.get("testProject") or {}).get("properties") or {}
            en = str(ep.get("Name") or ep.get("Label") or "（未命名环境）").strip()
            pn = str(pp.get("Name") or pp.get("Label") or "（未命名项目）").strip()
            if pn not in by_env[en]:
                by_env[en].append(pn)
        lines.append("**测试项目（按环境）：**")
        for ename, projects in sorted(by_env.items(), key=lambda x: x[0]):
            lines.append(f"- 环境：{ename}")
            for pj in projects:
                lines.append(f"  - {pj}")
            lines.append("")

    if eq_ind_rows:
        from collections import defaultdict
        by_eq_ind: Dict[str, List[str]] = defaultdict(list)
        for r in eq_ind_rows:
            ep = (r.get("equipment") or {}).get("properties") or {}
            ip = (r.get("indicator") or {}).get("properties") or {}
            en = str(ep.get("Name") or ep.get("Label") or "（未命名设备）").strip()
            ind_name = str(ip.get("Name") or ip.get("Label") or "（未命名指标）").strip()
            if ind_name not in by_eq_ind[en]:
                by_eq_ind[en].append(ind_name)
        lines.append("**考核指标：**")
        for ename, indicators in sorted(by_eq_ind.items(), key=lambda x: x[0]):
            lines.append(f"- 设备：{ename}")
            for ind in indicators:
                lines.append(f"  - {ind}")
            lines.append("")

    if not eq_rows and not env_rows and not eq_ind_rows:
        return "图数据库已命中记录，但暂无法按设备/环境—测试项目/指标关系自动排版，请改用智能整合回答。"

    return "\n".join(lines).strip()


def _should_database_direct_answer(
    plan_norm: Dict[str, Any],
    neo_res: List[Dict],
    chroma_res: List[Dict],
    retrieval_question: str,
) -> bool:
    if not neo_res or any(isinstance(x, dict) and x.get("error") for x in neo_res):
        return False
    if _chroma_nonempty(chroma_res):
        return False
    # 检查是否有任何结构化结果（不再要求纯项目类型）
    if not _neo_rows_has_structured_results(neo_res):
        return False
    if str(plan_norm.get("answerMode") or "").strip().lower() == "llm_synthesize":
        return False
    # 接受项目枚举或混合查询（项目+指标）
    return bool(
        question_asks_test_project_enumeration(retrieval_question)
        or "指标" in (retrieval_question or "")
    )


def _yield_database_direct_answer_stream(question: str, neo_res: List[Dict]) -> Generator[Tuple[str, dict], None, None]:
    yield ("thinking_start", {})
    yield ("thinking", {"content": "已按图数据库结构（设备/环境—CON—测试项目）查询；以下为由服务端直接整理的关系清单，未经过大模型二次归纳。\n"})
    body = _format_database_direct_answer(neo_res, question)
    step = 480
    for i in range(0, len(body), step):
        yield ("answer", {"content": body[i : i + step]})


def _finalize_retrieval_plan(plan_norm: Dict[str, Any], retrieval_question: str) -> None:
    """补全 neo4j.pattern、answerMode：pattern 以问句解析为准；直出仅用于明确枚举测试项目。"""
    neo = plan_norm.setdefault("neo4j", {})
    ch = plan_norm.setdefault("chroma", {})
    rq = (retrieval_question or "").strip()

    neo["pattern"] = resolve_neo_pattern_from_question(rq)
    if neo["pattern"] == "testproject_con_testmethod":
        from .znwd_business_templates import _extract_test_project_keyword_for_method_question

        pk = _extract_test_project_keyword_for_method_question(rq, rq)
        if pk:
            neo["query"] = pk

    try:
        lim = int(neo.get("limit") or 20)
    except (TypeError, ValueError):
        lim = 20
    pat = str(neo.get("pattern") or "keyword_fuzzy")
    neo["limit"] = max(15, min(lim, 120)) if pat in NEO_STRUCTURED_PATTERNS else max(10, min(lim, 50))

    if ch.get("use"):
        plan_norm["answerMode"] = "llm_synthesize"
    elif str(plan_norm.get("answerMode") or "").strip().lower() == "llm_synthesize":
        plan_norm["answerMode"] = "llm_synthesize"
    elif question_asks_test_project_enumeration(rq):
        plan_norm["answerMode"] = "database_direct"
    else:
        plan_norm["answerMode"] = "llm_synthesize"


def _extract_ssyq_keyword_from_question(text: str) -> str:
    """
    从用户问句中抽取用于 SSYQ /search 与图谱模糊检索的核心实体词（1～8 个汉字为主），
    例如「无人机有哪些测试项目」→「无人机」。避免整句检索导致无关设备（如坦克）被命中。
    会先去掉「对于/关于」等前缀，避免「对于命中率有什么方法」→「对于命中率」这类错误。
    """
    cut = (text or "").strip()
    if not cut:
        return ""
    cut = re.sub(r"^[请问一下：:，,。.「」【】]+", "", cut)
    cut = re.sub(
        r"^(对于|关于|针对|对|就|在|请|麻烦|帮我|想|想要|需要|想了解一下|想咨询)+",
        "",
        cut,
    ).strip()
    cut = re.sub(r"^(?:查询|请问|想了解一下|想了解|想知道)\s*", "", cut).strip()
    for sep in (
        "有哪些", "有什么", "哪些", "哪一些", "哪类", "请列举", "列举一下", "列举",
        "的测试项目", "的试验项目", "测试项目", "试验项目", "的项目", "的指标", "的测试", "的试验",
        "的方法", "的标准", "是什么", "怎样", "如何", "吗", "呢", "？", "?",
    ):
        if sep in cut:
            cut = cut.split(sep)[0].strip()
    cut = re.sub(r"^[请问一下：:，,。.「」【】]+", "", cut)
    cut = re.sub(
        r"^(对于|关于|针对|对|就|在|请|麻烦|帮我|想|想要|需要)+",
        "",
        cut,
    ).strip()
    cut = re.sub(r"(查询|了解|知道|告诉|说说|介绍一下|帮我|麻烦)$", "", cut).strip()
    m = re.match(r"^([\u4e00-\u9fff]{1,10})", cut)
    if m:
        return m.group(1).strip()
    return cut[:16].strip()


def query_neo4j(
    driver,
    query: str,
    limit: int = 20,
    *,
    contextual_question: str = "",
    neo_pattern: str = "",
) -> List[Dict]:
    if not driver:
        return [{"error": "Neo4j driver not initialized"}]
    query = (query or "").strip()
    if not query:
        return []
    try:
        lim_req = int(limit)
    except (TypeError, ValueError):
        lim_req = 20
    lim_req = max(1, min(lim_req, 120))
    try:
        pat = (neo_pattern or "").strip().lower()
        allowed = NEO_STRUCTURED_PATTERNS | frozenset({"keyword_fuzzy"})
        if pat not in allowed:
            pat = ""
        if not pat and contextual_question:
            pat = resolve_neo_pattern_from_question(contextual_question.strip())
        elif not pat:
            pat = "keyword_fuzzy"

        logger.info(
            "[Neo4j Query] pattern=%s query=%r limit=%s ctx=%r",
            pat,
            query,
            lim_req,
            (contextual_question or "")[:120],
        )
        keywords = [kw.strip() for kw in re.split(r"[,\s]+", query) if kw.strip()]
        keywords = keywords[:4]
        if not keywords:
            return []

        if pat == "equipment_con_testproject":
            structured = _query_neo4j_equipment_con_test_projects(driver, keywords, lim_req)
            if structured and not (len(structured) == 1 and isinstance(structured[0], dict) and structured[0].get("error")):
                return structured
        elif pat == "environment_con_testproject":
            structured = _query_neo4j_environment_con_test_projects(driver, keywords, lim_req)
            if structured and not (len(structured) == 1 and isinstance(structured[0], dict) and structured[0].get("error")):
                return structured
        elif pat == "equipment_con_indicator":
            structured = _query_neo4j_equipment_con_indicators(driver, keywords, lim_req)
            if structured and not (len(structured) == 1 and isinstance(structured[0], dict) and structured[0].get("error")):
                return structured
        elif pat == "testproject_con_testmethod":
            structured = _query_neo4j_testproject_con_testmethods(driver, keywords, lim_req)
            if structured and not (len(structured) == 1 and isinstance(structured[0], dict) and structured[0].get("error")):
                return structured
        elif pat == "equipment_via_project_testmethod":
            structured = _query_neo4j_equipment_project_testmethods(driver, keywords, lim_req)
            if structured and not (len(structured) == 1 and isinstance(structured[0], dict) and structured[0].get("error")):
                return structured
        elif pat == "equipment_bundle":
            structured = _query_neo4j_equipment_bundle(driver, keywords, lim_req)
            if structured and not (len(structured) == 1 and isinstance(structured[0], dict) and structured[0].get("error")):
                return structured

        fuzzy_cap = max(1, min(lim_req, 50))
        intent_label_map = {
            "设备类型": "EquipmentType",
            "设备": "Equipment",
            "环境": "Environment",
            "项目": "TestProject",
            "指标": "Indicator",
            "方法": "TestMethod",
            "测试方法": "TestMethod",
            "文件": "File",
            "标准": "File",
        }

        for zh, label in intent_label_map.items():
            if zh in query:
                cypher = f"""
                MATCH (n:{label})
                RETURN DISTINCT n
                LIMIT $limit
                """
                with driver.session() as session:
                    result = session.run(cypher, limit=fuzzy_cap)
                    nodes = []
                    for record in result:
                        for key in record.keys():
                            value = record[key]
                            if value and hasattr(value, 'items'):
                                nodes.append({
                                    'labels': list(value.labels),
                                    'properties': {
                                        **{k: v for k, v in value.items() if v is not None},
                                        '__elementId': getattr(value, 'element_id', None)
                                    }
                                })
                if nodes:
                    logger.info(f"[Neo4j Intent Query] label={label}, nodes={len(nodes)}")
                    return nodes

        where_parts = []
        params: Dict[str, str | int] = {"limit": fuzzy_cap}
        for idx, kw in enumerate(keywords):
            key = f"kw{idx}"
            params[key] = kw.lower()
            where_parts.append(f"ANY(k IN keys(n) WHERE toLower(toString(n[k])) CONTAINS ${key})")
        where_clause = " OR ".join(where_parts)
        cypher = f"""
        MATCH (n)
        WHERE {where_clause}
        RETURN DISTINCT n
        LIMIT $limit
        """

        with driver.session() as session:
            result = session.run(cypher, **params)
            nodes = []
            for record in result:
                for key in record.keys():
                    value = record[key]
                    if value and hasattr(value, 'items'):
                        nodes.append({
                            'labels': list(value.labels),
                            'properties': {
                                **{k: v for k, v in value.items() if v is not None},
                                '__elementId': getattr(value, 'element_id', None)
                            }
                        })
            logger.info(f"[Neo4j Result] {len(nodes)} nodes returned (fuzzy)")
            return nodes
    except Exception as e:
        logger.error(f"[Neo4j Error] {str(e)}")
        return [{"error": f"Neo4j fuzzy query failed: {str(e)}"}]


def search_knowledge_base(collection, query: str, top_k: int = 3) -> List[Dict]:
    if not collection:
        return [{"error": "Chroma collection not initialized"}]
    try:
        logger.info(f"[Chroma Query] Searching for: {query}")
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        ef = ONNXMiniLM_L6_V2()
        query_embedding = ef([query])[0]
        results = collection.query(query_embeddings=[query_embedding], n_results=top_k)
        chunks = []
        if results and results.get('documents') and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                metadata = results['metadatas'][0][i] if results.get('metadatas') and results['metadatas'][0] else {}
                chunks.append({
                    'doc_id': metadata.get('doc_id', ''),
                    'title': metadata.get('title', ''),
                    'type': metadata.get('type', ''),
                    'source_file': metadata.get('source_file', ''),
                    'content': doc[:500]
                })
        logger.info(f"[Chroma Result] {len(chunks)} chunks returned")
        return chunks
    except Exception as e:
        logger.error(f"[Chroma Error] {str(e)}")
        return [{"error": f"Knowledge base search failed: {str(e)}"}]


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "query_neo4j",
            "description": "Fuzzy search nodes in graph database with 2-5 keywords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "2-5 keywords for fuzzy matching, e.g. '无人机 高温 环境'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max returned nodes, default 20"
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the document knowledge base using semantic similarity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "2-5 keywords extracted from the user question for semantic search."
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    }
]


TOOL_DISPATCH = {
    "query_neo4j": lambda args, driver, collection: query_neo4j(driver, args.get("query", "") or args.get("cypher", ""), args.get("limit", 20)),
    "search_knowledge_base": lambda args, driver, collection: search_knowledge_base(collection, args.get("query", "")),
}


def user_question_allows_chroma(text: str) -> bool:
    return bool(CHROMA_DOC_INTENT_RE.search(text or ""))

def _quick_plan(question: str) -> Dict[str, Any]:
    q = (question or "").strip()
    q_lower = q.lower()
    if not q:
        return {
            "intent": "数据库无法命中",
            "strategy": "none",
            "answerMode": "llm_synthesize",
            "neo4j": {"use": False, "query": "", "limit": 20, "pattern": "keyword_fuzzy", "rationale": "空问题不检索"},
            "chroma": {"use": False, "query": "", "rationale": "空问题不检索"},
            "ssyqTableKeyword": "",
        }
    casual = ("你好", "hello", "hi", "你是谁", "天气", "笑话")
    if any(k in q_lower for k in casual):
        return {
            "intent": "数据库无法命中",
            "strategy": "none",
            "answerMode": "llm_synthesize",
            "neo4j": {"use": False, "query": "", "limit": 20, "pattern": "keyword_fuzzy", "rationale": "闲聊问题，直接回答"},
            "chroma": {"use": False, "query": "", "rationale": "闲聊问题，直接回答"},
            "ssyqTableKeyword": "",
        }
    direct_keywords = ("设备", "环境", "项目", "指标", "方法", "测试", "标准", "文件", "清单")
    is_direct = any(k in q for k in direct_keywords)
    chroma_use = user_question_allows_chroma(q)
    kw = _extract_ssyq_keyword_from_question(q) or (q[:24] if q else "")
    neo_q = kw or q[:80]
    list_tp = question_asks_test_project_enumeration(q)
    neo_pat = resolve_neo_pattern_from_question(q)
    neo_lim = 40 if neo_pat in NEO_STRUCTURED_PATTERNS else 20
    ans_mode = "llm_synthesize" if chroma_use else ("database_direct" if list_tp else "llm_synthesize")
    return {
        "intent": "数据库可答" if is_direct else "数据库可能可答",
        "strategy": "direct" if is_direct else "fuzzy",
        "answerMode": ans_mode,
        "neo4j": {
            "use": True,
            "query": neo_q,
            "limit": neo_lim,
            "pattern": neo_pat,
            "rationale": "规则判别：按问句选择 Equipment→项目/指标/方法 等结构化 Cypher 或关键词模糊",
        },
        "chroma": {
            "use": chroma_use,
            "query": (kw[:200] if chroma_use and kw else (q[:500] if chroma_use else "")),
            "rationale": "规则判别，按文档意图决定是否查文件",
        },
        "ssyqTableKeyword": kw or neo_q,
    }


def _extract_entity_from_user_turn(t: str) -> str:
    """从单条用户话术中抽「设备/对象」短词（不含问法尾巴）。"""
    t = (t or "").strip()
    if not t:
        return ""
    m = re.match(r"^[\s]*([\u4e00-\u9fff]{2,10})呢[？?！!。.\s]*$", t)
    if m and m.group(1) not in ("什么", "哪些", "这个", "那个", "同样", "一样"):
        return m.group(1).strip()
    m2 = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,32})\s*[,，]?\s*(?:有什么|有哪些|包括哪些|想问|请问).{0,12}"
        r"(?:测试项目|试验项目|项目|指标|方法|标准|环境)",
        t,
    )
    if m2 and m2.group(1) not in ("什么", "哪些", "如何", "怎样"):
        return m2.group(1).strip()
    # 增加对"关联度"问题的实体提取
    m3 = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9·\-]{2,20}).*关联度",
        t,
    )
    if m3 and m3.group(1) not in ("什么", "哪些", "如何", "怎样"):
        return m3.group(1).strip()
    return ""


def _infer_focus_entity_from_messages(messages: List[Dict], current_q: str) -> str:
    """
    从最近多轮 user（及必要时 assistant）推断当前省略的主语，例如先问坦克再问「有什么指标」→ 坦克。
    优先取时间上更近的用户句中的实体。
    """
    cq = (current_q or "").strip()
    user_texts: List[str] = []
    for m in reversed(messages[:-1]):
        if m.get("role") == "user":
            t = str(m.get("content") or "").strip()
            if t:
                user_texts.append(t)
            if len(user_texts) >= 10:
                break
    for t in user_texts:
        ent = _extract_entity_from_user_turn(t)
        if ent:
            return ent
    for t in user_texts:
        k = _extract_ssyq_keyword_from_question(t)
        if k and len(k) >= 2 and k not in ("什么", "哪些", "如何", "怎样", "请问"):
            return k[:40].strip()
    # 增加：从最近 assistant 回答中提取提到的实体名称（如"高温工作性能测试"）
    tail = 3500
    for m in reversed(messages[:-1]):
        if m.get("role") != "assistant":
            continue
        ac = str(m.get("content") or "")
        if not ac.strip():
            continue
        chunk = ac[-tail:]
        # 匹配"测试项目：XXX"或"高温工作性能测试"等
        mm = re.search(
            r"(?:测试项目|试验项目|项目)[：:]\s*([^\n]{2,30})",
            chunk,
        )
        if mm:
            return mm.group(1).strip().strip("」』\"'")
        mm2 = re.search(r"\*\*设备[：:]\s*([^*\n]{2,18})", chunk)
        if mm2:
            return re.sub(r"\s+", "", mm2.group(1).strip())[:18]
        break
    return ""


def _followup_needs_focus_anchor(current_q: str) -> bool:
    """当前句未写明对象、但明显在承前问指标/方法/项目等。"""
    cq = (current_q or "").strip()
    if not cq or len(cq) > 36:
        return False
    if re.search(r"(?:无人机|坦克|雷达|通信|设备|装备|机型|环境)[\u4e00-\u9fff]{0,4}", cq):
        return False
    # 增加对"关联度"的识别
    if "关联度" in cq:
        return True
    return bool(
        re.search(
            r"(?:^|[，,。.\s])(?:那|那么|还有|继续|再)?\s*"
            r"(?:有什么|有哪些|还有哪些|还有什么|列出|查询|告诉我)?\s*"
            r"(?:指标|方法|测试项目|试验项目|项目|标准|环境|关联度)",
            cq,
        )
    )


def _self_contained_subject_listing_question(q: str) -> bool:
    """
    「XX有哪些项目/指标…」「XX的方法是什么…」等已在句内写明主体与问法，
    不应再与上一轮用户句做「补充问题」拼接。
    与 _extract_entity_from_user_turn 中「主体+列举问法」一致，避免 8 字整句被误判为短追问。
    """
    t = (q or "").strip()
    if not t:
        return False
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,20}\s*[,，]?\s*"
        r"(?:有什么|有哪些|包括哪些|想问|请问).{0,10}"
        r"(?:测试项目|试验项目|项目|指标|方法|标准|环境)",
        t,
    ):
        return True
    # 已定名对象下问「方法/步骤/流程/具体内容」——句内主体完整，禁止与上一轮拼接后丢「方法」等限定
    if re.search(
        r"[\u4e00-\u9fffA-Za-z0-9·\-、（）]{2,40}"
        r"的\s*(?:测试方法|试验方法|方法|步骤|流程|具体内容)\s*"
        r"(?:有哪些|是什么|哪几种|包括哪些|包括|怎么|如何|怎样)",
        t,
    ):
        return True
    return False


def _contextualize_question(messages: List[Dict], current_q: str) -> str:
    q = (current_q or "").strip()
    if not q:
        return q
    if _self_contained_subject_listing_question(q):
        return q
    short_follow = ("呢", "还有", "那", "它", "这个", "那个")
    if len(q) > 8 and not any(k in q for k in short_follow):
        return q
    prev_user = ""
    for m in reversed(messages[:-1]):
        if m.get("role") == "user":
            prev_user = str(m.get("content") or "").strip()
            if prev_user:
                break
    if not prev_user:
        return q
    return f"{prev_user}；补充问题：{q}"


def _enrich_retrieval_question_with_focus(messages: List[Dict], current_q: str) -> str:
    """
    在链式上文（_contextualize_question）基础上，为多轮省略主语补上「承前主体」，供意图/Neo 关键词使用。
    """
    base = _contextualize_question(messages, current_q)
    cq = (current_q or "").strip()
    focus = _infer_focus_entity_from_messages(messages, cq)
    if not focus or not _followup_needs_focus_anchor(cq):
        return base
    if focus in base:
        return base
    return f"【承前主体】{focus}。{base}"


def parse_retrieval_plan(assistant_text: str) -> Optional[Dict[str, Any]]:
    if not assistant_text:
        return None
    start = assistant_text.find("[RETRIEVAL_PLAN]")
    end = assistant_text.find("[/RETRIEVAL_PLAN]")
    if start == -1 or end == -1 or end <= start:
        return None
    inner = assistant_text[start + len("[RETRIEVAL_PLAN]"): end].strip()
    try:
        data = json.loads(inner)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        logger.warning("[V4] RETRIEVAL_PLAN JSON 解析失败，使用回退策略")
        return None


def normalize_plan_from_llm(raw: Optional[Dict[str, Any]], context_question: str) -> Dict[str, Any]:
    """context_question 应为承前补全后的检索用语（与本轮 raw 用户句可不同）。"""
    q = (context_question or "").strip()
    fallback_chroma = user_question_allows_chroma(q)
    _fb_kw = _extract_ssyq_keyword_from_question(q) or (q[:24] if q else "测试")
    _list_tp = question_asks_test_project_enumeration(q)
    _neo_pat = resolve_neo_pattern_from_question(q)
    default: Dict[str, Any] = {
        "intent": "数据库可能可答",
        "strategy": "fuzzy",
        "answerMode": "llm_synthesize" if fallback_chroma else ("database_direct" if _list_tp else "llm_synthesize"),
        "neo4j": {
            "use": True,
            "query": _fb_kw,
            "limit": 40 if _neo_pat in NEO_STRUCTURED_PATTERNS else 20,
            "pattern": _neo_pat,
            "rationale": "方案解析失败，按短关键词与问法推断图谱检索模式",
        },
        "chroma": {
            "use": fallback_chroma,
            "query": (_fb_kw[:200] if fallback_chroma else ""),
            "rationale": "方案解析失败，按关键词规则决定是否查文档库",
        },
        "ssyqTableKeyword": _fb_kw,
    }
    if not raw:
        return default

    intent = str(raw.get("intent") or "").strip() or default["intent"]
    strategy = str(raw.get("strategy") or "").strip().lower()
    if strategy not in ("direct", "fuzzy", "none"):
        strategy = default["strategy"]

    neo = raw.get("neo4j") if isinstance(raw.get("neo4j"), dict) else {}
    ch = raw.get("chroma") if isinstance(raw.get("chroma"), dict) else {}

    neo_use = neo.get("use", True)
    if not isinstance(neo_use, bool):
        neo_use = True
    neo_query = str(neo.get("query") or "").strip() or _fb_kw
    neo_lim = neo.get("limit", 20)
    try:
        neo_lim = int(neo_lim)
    except (TypeError, ValueError):
        neo_lim = 20
    neo_lim = max(10, min(neo_lim, 120))

    neo_pat = str(neo.get("pattern") or "").strip().lower()
    _allowed_pat = NEO_STRUCTURED_PATTERNS | frozenset({"keyword_fuzzy"})
    if neo_pat not in _allowed_pat:
        neo_pat = ""

    ans_mode = str(raw.get("answerMode") or "").strip().lower()
    if ans_mode not in ("database_direct", "llm_synthesize"):
        ans_mode = ""

    ch_use = ch.get("use", False)
    if not isinstance(ch_use, bool):
        ch_use = bool(ch_use)
    ch_query = str(ch.get("query") or "").strip()
    if ch_use and not ch_query:
        ch_query = q[:500]

    ssyq_kw = str(raw.get("ssyqTableKeyword") or raw.get("tableSearchKeyword") or "").strip()
    if not ssyq_kw:
        ssyq_kw = _extract_ssyq_keyword_from_question(q) or (neo_query.split()[0] if neo_query else "")
    ssyq_kw = ssyq_kw[:40].strip()
    # 图谱检索与 SSYQ 表格统一用短关键词，避免整句误命中
    if ssyq_kw:
        neo_query = ssyq_kw

    if not neo_pat:
        neo_pat = resolve_neo_pattern_from_question(q)

    if not ans_mode:
        ans_mode = "llm_synthesize" if ch_use else (
            "database_direct"
            if neo_pat in ("equipment_con_testproject", "environment_con_testproject")
            and question_asks_test_project_enumeration(q)
            else "llm_synthesize"
        )

    return {
        "intent": intent[:80],
        "strategy": strategy,
        "answerMode": ans_mode,
        "neo4j": {
            "use": neo_use,
            "query": neo_query[:500],
            "limit": neo_lim,
            "pattern": neo_pat,
            "rationale": str(neo.get("rationale") or "")[:400],
        },
        "chroma": {
            "use": ch_use,
            "query": ch_query[:500],
            "rationale": str(ch.get("rationale") or "")[:400],
        },
        "ssyqTableKeyword": ssyq_kw or neo_query[:40],
    }


def _last_user_question(messages: List[Dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def _complete_retrieval_plan(client, model: str, phase1_messages: List[Dict]) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=phase1_messages,
        temperature=0.2,
        max_tokens=384,
        stream=False,
    )
    msg = resp.choices[0].message
    return (msg.content or "").strip()


def _build_evidence_user_content(
    question: str,
    plan_norm: Dict[str, Any],
    neo_res: List[Dict],
    chroma_res: List[Dict],
    neo_executed: bool,
    chroma_executed: bool,
    chroma_note: str,
) -> str:
    _lim = _evidence_json_slice_limit()
    neo_part = json.dumps(neo_res, ensure_ascii=False, default=str)[:_lim]
    ch_part = (
        json.dumps(chroma_res, ensure_ascii=False, default=str)[:_lim]
        if chroma_res
        else "[]"
    )
    return (
        f"【用户问题】\n{question}\n\n"
        f"【检索方案】\n{json.dumps(plan_norm, ensure_ascii=False)}\n\n"
        f"【图数据库检索】\n是否执行：{neo_executed}\n{neo_part}\n\n"
        f"【文档库检索】\n{chroma_note}\n是否执行：{chroma_executed}\n{ch_part}\n"
    )


def collect_v4_retrieval_results(
    client,
    model: str,
    messages: List[Dict],
    driver,
    collection,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    仅执行 V4 的「规划 + Neo4j + Chroma」，不生成回答。
    供 V5 在程序化检索无命中时按 V5→V4→LLM 顺序进行二次检索。
    """
    _ = llm_limits
    base_messages = [dict(m) for m in messages]
    question = _last_user_question(base_messages)
    retrieval_question = _enrich_retrieval_question_with_focus(base_messages, question)
    compact_msgs: List[Dict] = []
    for m in base_messages[-4:]:
        c = str(m.get("content") or "")[:800]
        if m.get("role") == "user" and (str(m.get("content") or "").strip() == question.strip()):
            c = retrieval_question[:800]
        compact_msgs.append({"role": m.get("role"), "content": c})
    phase1_messages = [{"role": "system", "content": RETRIEVAL_PLAN_PROMPT}] + compact_msgs

    plan_norm = _quick_plan(retrieval_question)
    if client:
        try:
            raw_plan = _complete_retrieval_plan(client, model, phase1_messages)
            parsed_plan = parse_retrieval_plan(raw_plan)
            if parsed_plan:
                plan_norm = normalize_plan_from_llm(parsed_plan, retrieval_question)
        except Exception as e:
            logger.warning("[V4] LLM 检索方案失败（collect_v4_retrieval_results），沿用规则方案: %s", e)
    if not str(plan_norm.get("ssyqTableKeyword") or "").strip():
        _parts = str(plan_norm.get("neo4j", {}).get("query") or "").strip().split()
        plan_norm["ssyqTableKeyword"] = (
            _extract_ssyq_keyword_from_question(retrieval_question)
            or (_parts[0][:30] if _parts else "")
        )
    _finalize_retrieval_plan(plan_norm, retrieval_question)

    if plan_norm.get("strategy") == "none":
        return {
            "neo_res": [],
            "chroma_res": [],
            "plan_norm": plan_norm,
            "question": question,
            "retrieval_question": retrieval_question,
            "compact_msgs": compact_msgs,
            "ssyq_kw": str(plan_norm.get("ssyqTableKeyword") or "").strip(),
        }

    chroma_wanted = plan_norm["chroma"]["use"]
    chroma_allowed = user_question_allows_chroma(question)
    chroma_executed = bool(chroma_wanted and chroma_allowed)

    neo_executed = bool(plan_norm["neo4j"]["use"])
    neo_query_used = str(plan_norm["neo4j"].get("query") or "")
    neo_pat_used = str(plan_norm["neo4j"].get("pattern") or "keyword_fuzzy")
    neo_res: List[Dict] = []
    if neo_executed:
        neo_res = query_neo4j(
            driver,
            neo_query_used,
            plan_norm["neo4j"]["limit"],
            contextual_question=retrieval_question,
            neo_pattern=neo_pat_used,
        )
        if (not neo_res or all(isinstance(x, dict) and "error" in x for x in neo_res if isinstance(x, dict))) and question:
            neo_query_used = retrieval_question[:500]
            neo_res = query_neo4j(
                driver,
                neo_query_used,
                plan_norm["neo4j"]["limit"],
                contextual_question=retrieval_question,
                neo_pattern="keyword_fuzzy",
            )
            neo_pat_used = "keyword_fuzzy"

    if chroma_executed:
        chroma_res = search_knowledge_base(collection, plan_norm["chroma"]["query"], ZNWD_CHROMA_UI_MAX)
    else:
        chroma_res = []

    return {
        "neo_res": neo_res,
        "chroma_res": chroma_res,
        "plan_norm": plan_norm,
        "question": question,
        "retrieval_question": retrieval_question,
        "compact_msgs": compact_msgs,
        "ssyq_kw": str(plan_norm.get("ssyqTableKeyword") or "").strip(),
        "neo_query_used": neo_query_used,
        "neo_pat_used": neo_pat_used,
    }


def _stream_plan_then_answer(
    client,
    model: str,
    messages: List[Dict],
    driver,
    collection,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, dict], None, None]:
    base_messages = [dict(m) for m in messages]
    question = _last_user_question(base_messages)
    retrieval_question = _enrich_retrieval_question_with_focus(base_messages, question)
    logger.info("[V4] retrieval_question=%r", retrieval_question[:400])
    # 兼容本地中小模型：规划阶段仅喂最近几轮；最后一条 user 用承前补全后的语义，便于意图与检索词一致
    compact_msgs: List[Dict] = []
    for m in base_messages[-4:]:
        c = str(m.get("content") or "")[:800]
        if m.get("role") == "user" and (str(m.get("content") or "").strip() == question.strip()):
            c = retrieval_question[:800]
        compact_msgs.append({"role": m.get("role"), "content": c})
    phase1_messages = [{"role": "system", "content": RETRIEVAL_PLAN_PROMPT}] + compact_msgs

    # 不在检索阶段发 thinking_start，由 LLM 输出 [THINK] 时再发，便于前端仅在「三点加载」处覆盖展示 step
    yield ("step", {"type": "intent_judge", "status": "loading", "message": "正在进行意图判别..."})
    plan_norm = _quick_plan(retrieval_question)
    if client:
        try:
            raw_plan = _complete_retrieval_plan(client, model, phase1_messages)
            parsed_plan = parse_retrieval_plan(raw_plan)
            if parsed_plan:
                plan_norm = normalize_plan_from_llm(parsed_plan, retrieval_question)
        except Exception as e:
            logger.warning("[V4] LLM 检索方案失败，沿用规则方案: %s", e)
    if not str(plan_norm.get("ssyqTableKeyword") or "").strip():
        _parts = str(plan_norm.get("neo4j", {}).get("query") or "").strip().split()
        plan_norm["ssyqTableKeyword"] = (
            _extract_ssyq_keyword_from_question(retrieval_question)
            or (_parts[0][:30] if _parts else "")
        )
    _finalize_retrieval_plan(plan_norm, retrieval_question)
    _kw_pub = str(plan_norm.get("ssyqTableKeyword") or "").strip()
    yield ("search_keywords", {"ssyqTableKeyword": _kw_pub})
    logger.info(
        "[V4] 检索方案: %s | ssyqTableKeyword=%r",
        json.dumps({k: v for k, v in plan_norm.items() if k != "chroma"}, ensure_ascii=False),
        _kw_pub,
    )

    intent_text = "正在判断问题是否需要检索。"
    if plan_norm.get("strategy") == "none":
        intent_text = "判断为无需检索，直接由LLM回答。"
    elif plan_norm["neo4j"].get("use") or plan_norm["chroma"].get("use"):
        intent_text = "判断为需要先检索清单文件/文件，再生成回答。"
    yield ("step", {
        "type": "intent_judge",
        "status": "done",
        "message": intent_text,
    })

    if plan_norm.get("strategy") == "none":
        yield ("source_data", {"neo4jResults": [], "chromaResults": []})
        yield ("step", {
            "type": "retrieve",
            "status": "done",
            "message": "判定为无需数据库检索，直接由 LLM 回答",
        })
        yield ("step", {"type": "synthesis", "status": "loading", "message": "正在生成直接回答"})
        direct_messages = (
            [{"role": "system", "content": DIRECT_ANSWER_PROMPT}]
            + compact_msgs[:-1]
            + [{"role": "user", "content": question}]
        )
        for event in _stream_single_llm(
            client,
            model,
            direct_messages,
            driver,
            collection,
            tools_disabled=True,
            llm_limits=llm_limits,
        ):
            yield event
        yield ("step", {"type": "synthesis", "status": "done", "message": "直接回答完成"})
        return

    chroma_wanted = plan_norm["chroma"]["use"]
    chroma_allowed = user_question_allows_chroma(question)
    chroma_executed = bool(chroma_wanted and chroma_allowed)
    if chroma_wanted and not chroma_allowed:
        chroma_note = (
            "检索方案要求查询文档库，但用户问题未体现法规/标准/方案/报告等文档类意图（服务端规则），已跳过向量检索。"
        )
    elif not chroma_wanted:
        chroma_note = "检索方案未启用文档库（Chroma）。"
    else:
        chroma_note = "已按方案执行文档库向量检索。"

    neo_executed = bool(plan_norm["neo4j"]["use"])
    neo_query_used = plan_norm["neo4j"]["query"]
    neo_pat_used = str(plan_norm["neo4j"].get("pattern") or "keyword_fuzzy")
    if neo_executed:
        neo_res = query_neo4j(
            driver,
            neo_query_used,
            plan_norm["neo4j"]["limit"],
            contextual_question=retrieval_question,
            neo_pattern=neo_pat_used,
        )
        # 结构化检索无命中时，降级为全库关键词模糊
        if (not neo_res or all(isinstance(x, dict) and "error" in x for x in neo_res if isinstance(x, dict))) and question:
            neo_query_used = retrieval_question[:500]
            neo_res = query_neo4j(
                driver,
                neo_query_used,
                plan_norm["neo4j"]["limit"],
                contextual_question=retrieval_question,
                neo_pattern="keyword_fuzzy",
            )
            neo_pat_used = "keyword_fuzzy"
    else:
        neo_res = []
        logger.info("[V4] 检索方案跳过 Neo4j")

    if chroma_executed:
        chroma_res = search_knowledge_base(collection, plan_norm["chroma"]["query"], ZNWD_CHROMA_UI_MAX)
    else:
        chroma_res = []

    yield ("source_data", {"neo4jResults": neo_res, "chromaResults": chroma_res})

    if chroma_wanted and not chroma_allowed:
        yield ("step", {
            "type": "retrieve",
            "status": "done",
            "message": "该问题不需要文件检索，仅执行清单文件检索。",
        })

    if neo_executed:
        yield ("step", {
            "type": "neo4j_search",
            "status": "loading",
            "message": f"正在查询清单文件，模式 {neo_pat_used}，关键词：{neo_query_used}",
        })
        yield ("step", {
            "type": "neo4j_search",
            "status": "done",
            "message": f"清单文件检索完成，命中 {len(neo_res)} 条",
            "hits": len(neo_res),
            "results": neo_res,
        })
    else:
        yield ("step", {
            "type": "neo4j_search",
            "status": "done",
            "message": "未启用清单文件检索。",
            "hits": 0,
            "results": [],
        })

    if chroma_executed:
        yield ("step", {
            "type": "chroma_search",
            "status": "loading",
            "message": f"正在查询文件，检索语句：{plan_norm['chroma']['query'] or retrieval_question[:80]}",
        })
        yield ("step", {
            "type": "chroma_search",
            "status": "done",
            "message": f"文件检索完成，命中 {len(chroma_res)} 条",
            "chunks": len(chroma_res),
            "matchedChunks": chroma_res,
        })
    else:
        yield ("step", {
            "type": "chroma_search",
            "status": "done",
            "message": "未启用文件检索。",
            "chunks": 0,
            "matchedChunks": [],
            "sources": [],
        })

    if _should_database_direct_answer(plan_norm, neo_res, chroma_res, retrieval_question):
        yield ("step", {
            "type": "synthesis",
            "status": "loading",
            "message": "正在按图数据库命中结果直接生成回答（跳过大模型整合）",
        })
        for ev in _yield_database_direct_answer_stream(question, neo_res):
            yield ev
        yield ("step", {"type": "synthesis", "status": "done", "message": "数据库直出完成"})
        return

    if not neo_res and not chroma_res:
        yield ("step", {
            "type": "retrieve",
            "status": "done",
            "message": "数据库未命中，转为 LLM 直接回答",
        })
        yield ("step", {"type": "synthesis", "status": "loading", "message": "正在生成直接回答"})
        direct_messages = (
            [{"role": "system", "content": DIRECT_ANSWER_PROMPT}]
            + compact_msgs[:-1]
            + [{"role": "user", "content": question}]
        )
        for event in _stream_single_llm(
            client,
            model,
            direct_messages,
            driver,
            collection,
            tools_disabled=True,
            llm_limits=llm_limits,
        ):
            yield event
        yield ("step", {"type": "synthesis", "status": "done", "message": "直接回答完成"})
        return

    yield ("step", {"type": "synthesis", "status": "loading", "message": "正在整合检索结果并回答"})
    evidence = _build_evidence_user_content(
        question, plan_norm, neo_res, chroma_res, neo_executed, chroma_executed, chroma_note
    )
    _v4_log_retrieval_for_debug(
        question=question,
        retrieval_question=retrieval_question,
        plan_norm=plan_norm,
        neo_query_used=neo_query_used,
        neo_executed=neo_executed,
        chroma_executed=chroma_executed,
        neo_res=neo_res,
        chroma_res=chroma_res,
        evidence_text=evidence,
    )
    phase2_messages = (
        [{"role": "system", "content": ANSWER_SYNTHESIS_PROMPT}]
        + compact_msgs[:-1]
        + [{"role": "user", "content": evidence}]
    )

    for event in _stream_single_llm(
        client,
        model,
        phase2_messages,
        driver,
        collection,
        tools_disabled=True,
        llm_limits=llm_limits,
    ):
        yield event
    yield ("step", {"type": "synthesis", "status": "done", "message": "整合回答完成"})


def estimate_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
        return len(enc.encode(text))
    except Exception:
        return len(text) // 2


def estimate_context_tokens(messages: List[Dict]) -> int:
    total = 0
    for msg in messages:
        total += estimate_tokens(json.dumps(msg, ensure_ascii=False))
    return total + 50


TAG_TAIL = 48

# 流式解析：方括号标签 + XML 标签（<thinking>/<content>）
ZNWD_STREAM_BUFFER_MIN_CHUNKS = 10
ZNWD_STREAM_BUFFER_MAX_HOLD = 24000
ZNWD_STREAM_SSE_SLICE = 256
ZNWD_CHROMA_UI_MAX = 3

_RE_THINK_OPEN = re.compile(r"\[\s*THINK\s*\]", re.I)
_RE_THINK_CLOSE = re.compile(r"\[\s*/\s*THINK\s*\]", re.I)
_RE_CONTENT_OPEN = re.compile(r"\[\s*CONTENT\s*\]", re.I)
_RE_CONTENT_CLOSE = re.compile(r"\[\s*/\s*CONTENT\s*\]", re.I)
_RE_XML_THINK_OPEN = re.compile(r"<\s*thinking\s*>", re.I)
_RE_XML_THINK_CLOSE = re.compile(r"<\s*/\s*thinking\s*>", re.I)
_RE_XML_CONTENT_OPEN = re.compile(r"<\s*content\s*>", re.I)
_RE_XML_CONTENT_CLOSE = re.compile(r"<\s*/\s*content\s*>", re.I)


def _znwd_text_looks_like_tool_steps_json(s: str) -> bool:
    """模型在 tool_calls 前常把 steps JSON 打进 content；此类文本不应作为 SSE 的 answer 下发。"""
    t = (s or "").strip()
    if len(t) < 24 or not t.startswith("{"):
        return False
    if re.search(r'"steps"\s*:\s*\[\s*\{', t):
        return True
    if re.search(r'"step_id"\s*:', t) and re.search(r'"op_code"\s*:', t):
        return True
    return False


def _znwd_strip_stream_leading_blank_lines(s: str) -> str:
    i = 0
    while i < len(s) and s[i] in "\r\n":
        i += 1
    return s[i:]


def _znwd_buffer_has_tag_signal(buf: str) -> bool:
    head = buf[: min(len(buf), 8000)]
    for rx in (
        _RE_XML_THINK_OPEN,
        _RE_XML_CONTENT_OPEN,
        _RE_THINK_OPEN,
        _RE_CONTENT_OPEN,
    ):
        if rx.search(head):
            return True
    return False


def _znwd_next_tag_start(buf: str) -> int:
    positions: List[int] = []
    for rx in (_RE_XML_THINK_OPEN, _RE_THINK_OPEN, _RE_XML_CONTENT_OPEN, _RE_CONTENT_OPEN):
        m = rx.search(buf)
        if m:
            positions.append(m.start())
    return min(positions) if positions else -1


def _znwd_try_extract_thinking_block(buf: str) -> Optional[Tuple[str, str]]:
    s = _znwd_strip_stream_leading_blank_lines(buf)
    offset = len(buf) - len(s)
    for open_rx, close_rx in (
        (_RE_XML_THINK_OPEN, _RE_XML_THINK_CLOSE),
        (_RE_THINK_OPEN, _RE_THINK_CLOSE),
    ):
        mo = open_rx.match(s)
        if not mo:
            continue
        mc = close_rx.search(s, mo.end())
        if not mc:
            return None
        inner = s[mo.end() : mc.start()]
        rest = buf[offset + mc.end() :]
        return inner, rest
    return None


def _znwd_try_extract_content_block(buf: str) -> Optional[Tuple[str, str]]:
    s = _znwd_strip_stream_leading_blank_lines(buf)
    offset = len(buf) - len(s)
    for open_rx, close_rx in (
        (_RE_XML_CONTENT_OPEN, _RE_XML_CONTENT_CLOSE),
        (_RE_CONTENT_OPEN, _RE_CONTENT_CLOSE),
    ):
        mo = open_rx.match(s)
        if not mo:
            continue
        mc = close_rx.search(s, mo.end())
        if not mc:
            return None
        inner = s[mo.end() : mc.start()]
        tail = buf[offset + mc.end() :]
        return inner, tail
    return None


def _znwd_chunk_sse_text(text: str) -> List[str]:
    if not text:
        return []
    return [text[i : i + ZNWD_STREAM_SSE_SLICE] for i in range(0, len(text), ZNWD_STREAM_SSE_SLICE)]


def _znwd_buffer_drain_tagged(buffer: str, thinking_started: bool) -> Tuple[List[Tuple[str, Any]], str, bool, bool]:
    """从左到右解析完整标签块；返回 (events, new_buf, thinking_started, content_closed)。"""
    events: List[Tuple[str, Any]] = []
    buf = buffer
    content_closed = False
    while buf:
        buf = _znwd_strip_stream_leading_blank_lines(buf)
        if not buf:
            break
        nt = _znwd_next_tag_start(buf)
        if nt > 0:
            pre = buf[:nt]
            buf = buf[nt:]
            if _znwd_text_looks_like_tool_steps_json(pre):
                logger.info(
                    "[ZNWD][stream] 抑制发往 SSE 的工具步 JSON 前缀 len=%d head=%r",
                    len(pre),
                    pre[:1200],
                )
                continue
            for piece in _znwd_chunk_sse_text(pre):
                if piece:
                    events.append(("answer", piece))
            continue
        if nt < 0:
            break
        th = _znwd_try_extract_thinking_block(buf)
        if th:
            inner, rest = th
            if not thinking_started:
                events.append(("thinking_start", None))
                thinking_started = True
            for piece in _znwd_chunk_sse_text(inner):
                if piece:
                    events.append(("thinking", piece))
            buf = rest
            continue
        ct = _znwd_try_extract_content_block(buf)
        if ct:
            inner, tail = ct
            for piece in _znwd_chunk_sse_text(inner):
                if piece:
                    events.append(("answer", piece))
            events.append(("pick_tail", tail))
            content_closed = True
            buf = ""
            break
        break
    return events, buf, thinking_started, content_closed


def _znwd_plain_flush(buf: str) -> Tuple[str, str]:
    safe = max(0, len(buf) - TAG_TAIL)
    return buf[:safe], buf[safe:]


def _stream_single_llm(
    client,
    model,
    messages,
    driver,
    collection,
    *,
    tools_disabled: bool = False,
    llm_limits: Optional[Dict[str, Any]] = None,
    tool_definitions: Optional[List[Dict[str, Any]]] = None,
    tool_dispatch: Optional[Dict[str, Any]] = None,
    tool_choice: Optional[str] = None,
) -> Generator[Tuple[str, dict], None, str]:
    raw_response = ""
    buffer = ""
    think_accumulated = ""
    content_accumulated = ""
    content_closed = False
    pick_tail = ""
    chunk_count = 0
    stream_mode: Optional[str] = None
    thinking_started = False

    msgs_work: List[Dict[str, Any]] = copy.deepcopy(messages) if llm_limits else list(messages)

    try:
        kwargs: Dict = dict(model=model, messages=msgs_work, temperature=0.3, stream=True)
        if llm_limits and client:
            cw = int(llm_limits.get("context_window") or 8192)
            mo = int(llm_limits.get("max_output_tokens") or 4096)
            try:
                if approx_messages_tokens(msgs_work) + 400 > cw * 0.88:
                    msgs_work = maybe_compress_messages_for_context(
                        msgs_work,
                        client=client,
                        model=str(model),
                        context_window=cw,
                        temperature=0.25,
                    )
            except Exception as ex:
                logger.warning("[V4] evidence compress skipped: %s", ex)
            pt = approx_messages_tokens(msgs_work)
            kwargs["messages"] = msgs_work
            kwargs["max_tokens"] = completion_cap(cw, pt, mo, mo)
        has_tool_roles = any(m.get("role") == "tool" for m in msgs_work)
        last_is_tool = bool(msgs_work and msgs_work[-1].get("role") == "tool")
        defs = tool_definitions if tool_definitions is not None else TOOL_DEFINITIONS
        enable_tool_round = not tools_disabled and (not has_tool_roles or last_is_tool)
        if enable_tool_round:
            kwargs["tools"] = defs
            tc_default = AGENT_TOOL_CHOICE if AGENT_TOOL_CHOICE in ("auto", "required", "none") else "required"
            choice = tool_choice if tool_choice is not None else tc_default
            if choice not in ("auto", "required", "none"):
                choice = "auto"
            kwargs["tool_choice"] = choice
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.error(f"[V4] LLM stream creation failed: {e}")
        yield ("error", {"message": f"LLM调用失败: {str(e)}"})
        return ""

    tool_calls_found = False
    tool_calls_data = []

    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        if delta.tool_calls:
            tool_calls_found = True
            for tc in delta.tool_calls:
                idx = tc.index
                while len(tool_calls_data) <= idx:
                    tool_calls_data.append({"id": "", "name": "", "arguments": ""})
                if tc.id:
                    tool_calls_data[idx]["id"] = tc.id
                if tc.function and tc.function.name:
                    tool_calls_data[idx]["name"] = tc.function.name
                if tc.function and tc.function.arguments is not None:
                    tool_calls_data[idx]["arguments"] += tc.function.arguments

        if delta.content:
            raw_response += delta.content
            if content_closed:
                pick_tail += delta.content
                continue

            buffer += delta.content
            chunk_count += 1

            if tool_calls_found:
                continue

            ready = chunk_count >= ZNWD_STREAM_BUFFER_MIN_CHUNKS or len(buffer) >= ZNWD_STREAM_BUFFER_MAX_HOLD
            if not ready:
                continue

            if stream_mode is None:
                stream_mode = "tagged" if _znwd_buffer_has_tag_signal(buffer) else "plain"

            if stream_mode == "tagged":
                while True:
                    evs, buffer, thinking_started, cd = _znwd_buffer_drain_tagged(buffer, thinking_started)
                    for tup in evs:
                        kind = tup[0]
                        if kind == "thinking_start":
                            yield ("thinking_start", {})
                        elif kind == "thinking":
                            piece = tup[1]
                            think_accumulated += piece
                            yield ("thinking", {"content": piece})
                        elif kind == "answer":
                            piece = tup[1]
                            content_accumulated += piece
                            yield ("answer", {"content": piece})
                        elif kind == "pick_tail":
                            pick_tail += tup[1]
                            content_closed = True
                    if cd:
                        break
                    if not evs:
                        break
            else:
                piece, buffer = _znwd_plain_flush(buffer)
                if piece:
                    if _znwd_text_looks_like_tool_steps_json(piece):
                        logger.info(
                            "[ZNWD][stream] 抑制 plain 流中的工具状 JSON len=%d head=%r",
                            len(piece),
                            piece[:1200],
                        )
                        continue
                    content_accumulated += piece
                    yield ("answer", {"content": piece})

    if not content_closed and buffer.strip() and not tool_calls_found:
        if stream_mode is None:
            stream_mode = "tagged" if _znwd_buffer_has_tag_signal(buffer) else "plain"
        if stream_mode == "tagged":
            while True:
                evs, buffer, thinking_started, cd = _znwd_buffer_drain_tagged(buffer, thinking_started)
                for tup in evs:
                    kind = tup[0]
                    if kind == "thinking_start":
                        yield ("thinking_start", {})
                    elif kind == "thinking":
                        piece = tup[1]
                        think_accumulated += piece
                        yield ("thinking", {"content": piece})
                    elif kind == "answer":
                        piece = tup[1]
                        content_accumulated += piece
                        yield ("answer", {"content": piece})
                    elif kind == "pick_tail":
                        pick_tail += tup[1]
                        content_closed = True
                if cd:
                    break
                if not evs:
                    break
        if not content_closed and buffer.strip():
            if _znwd_text_looks_like_tool_steps_json(buffer):
                logger.info(
                    "[ZNWD][stream] 抑制收尾 buffer 中的工具状 JSON len=%d head=%r",
                    len(buffer),
                    buffer[:1200],
                )
            else:
                content_accumulated += buffer
                yield ("answer", {"content": buffer})
            buffer = ""

    if content_closed and pick_tail.strip():
        yield ("internal_source_pick", {"raw": pick_tail.strip()})

    if tool_calls_found:
        if buffer.strip() and not content_closed:
            if stream_mode is None:
                stream_mode = "tagged" if _znwd_buffer_has_tag_signal(buffer) else "plain"
            if stream_mode == "tagged":
                while True:
                    evs, buffer, thinking_started, cd = _znwd_buffer_drain_tagged(buffer, thinking_started)
                    for tup in evs:
                        kind = tup[0]
                        if kind == "thinking_start":
                            yield ("thinking_start", {})
                        elif kind == "thinking":
                            piece = tup[1]
                            think_accumulated += piece
                            yield ("thinking", {"content": piece})
                        elif kind == "answer":
                            piece = tup[1]
                            content_accumulated += piece
                            yield ("answer", {"content": piece})
                        elif kind == "pick_tail":
                            pick_tail += tup[1]
                            content_closed = True
                    if cd:
                        break
                    if not evs:
                        break
            if buffer.strip() and not content_closed:
                if _znwd_text_looks_like_tool_steps_json(buffer):
                    logger.info(
                        "[ZNWD][stream] 抑制 tool_calls 前 buffer 中的工具状 JSON len=%d head=%r",
                        len(buffer),
                        buffer[:1200],
                    )
                elif stream_mode == "plain":
                    content_accumulated += buffer
                    yield ("answer", {"content": buffer})
                else:
                    think_accumulated += buffer
                    yield ("thinking", {"content": buffer})
                buffer = ""

        disp = tool_dispatch if tool_dispatch is not None else TOOL_DISPATCH
        assistant_tool_calls: List[Dict[str, Any]] = []
        tool_blocks: List[Dict[str, Any]] = []
        seen_ids: set = set()
        for tc in tool_calls_data:
            if not tc.get("name"):
                continue
            tool_name = tc["name"]
            raw_id = (tc.get("id") or "").strip() or f"znwd_tool_{len(assistant_tool_calls)}"
            tool_id = raw_id
            if tool_id in seen_ids:
                tool_id = f"{raw_id}_{len(assistant_tool_calls)}"
            seen_ids.add(tool_id)
            args, args_str_for_api = _safe_tool_arguments_for_api(tc.get("arguments"))
            logger.info(
                "[ZNWD][Tool] call name=%s id=%s args=%s",
                tool_name,
                tool_id,
                json.dumps(args, ensure_ascii=False)[:4000],
            )
            if tool_name in ("znwd_execute_meta_steps", "znwd_meta_ops_retrieve") and isinstance(args.get("steps"), list):
                steps_preview = args.get("steps") or []
                logger.info(
                    "[ZNWD][Tool] %s steps_count=%d op_chain=%s",
                    tool_name,
                    len(steps_preview),
                    " -> ".join(
                        str((isinstance(x, dict) and x.get("op_code")) or "")
                        for x in steps_preview[:16]
                    ),
                )
            yield ("tool_call", {"name": tool_name, "args": args})
            if tool_name in disp:
                result = disp[tool_name](args, driver, collection)
            else:
                result = {"error": f"Unknown tool: {tool_name}"}
            logger.info(
                "[ZNWD][Tool] result name=%s bytes=%s preview=%s",
                tool_name,
                len(json.dumps(result, ensure_ascii=False, default=str)),
                json.dumps(result, ensure_ascii=False, default=str)[:500],
            )
            yield ("tool_result", {"name": tool_name, "result": result})
            assistant_tool_calls.append(
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": args_str_for_api},
                }
            )
            tool_blocks.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
        if not assistant_tool_calls:
            return content_accumulated or raw_response
        next_messages = msgs_work + [
            {"role": "assistant", "content": "", "tool_calls": assistant_tool_calls},
            *tool_blocks,
        ]
        for event in _stream_single_llm(
            client,
            model,
            next_messages,
            driver,
            collection,
            tools_disabled=tools_disabled,
            llm_limits=llm_limits,
            tool_definitions=tool_definitions,
            tool_dispatch=tool_dispatch,
            tool_choice=tool_choice,
        ):
            yield event
        return content_accumulated or raw_response

    return content_accumulated or raw_response


def llm_stream_single(
    client,
    model,
    messages,
    driver,
    collection,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, dict], None, None]:
    if not client:
        yield ("error", {"message": "LLM 客户端未初始化"})
        return

    try:
        final_answer = ""
        full_thinking = ""
        if TOOL_CALL_MODE == "agent":
            _uq = _last_user_question(messages)
            yield ("search_keywords", {"ssyqTableKeyword": _extract_ssyq_keyword_from_question(_uq)})
            for event_type, data in _stream_single_llm(
                client,
                model,
                messages,
                driver,
                collection,
                tools_disabled=False,
                llm_limits=llm_limits,
            ):
                yield (event_type, data)
                if event_type == "answer":
                    final_answer += data.get("content", "")
                elif event_type == "thinking":
                    full_thinking += data.get("content", "")
        else:
            for event_type, data in _stream_plan_then_answer(
                client, model, messages, driver, collection, llm_limits=llm_limits
            ):
                yield (event_type, data)
                if event_type == "answer":
                    final_answer += data.get("content", "")
                elif event_type == "thinking":
                    full_thinking += data.get("content", "")
        yield ("done", {"fullAnswer": final_answer, "fullThinking": full_thinking})
    except Exception as e:
        logger.error(f"[V4] LLM 调用失败: {e}", exc_info=True)
        yield ("error", {"message": f"生成出错：{str(e)}"})
