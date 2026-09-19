"""
确定性方案内容生成 V2：第 1 章 LLM 流式；第 2 章按方法增量流式填充（校正/生成双模式）；无 Reviewer。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Generator, List, Optional, Set

from blueprints.FZSJ.JYLLM.multi_agent_engine import (
    emit_event,
    call_llm_stream,
    call_llm_nonstream,
    search_context,
    enforce_ch1_template_structure,
)
from blueprints.FZSJ.JYLLM.prompts import get_prompt, prepend_scheme_global_requirements
from blueprints.FZSJ.JYLLM.method_synr_markdown import (
    synr_html_is_substantial,
    apply_method_heading_prefix,
    strip_redundant_method_name_heading,
)
from blueprints.FZSJ.JYLLM.skeleton_builder import METHOD_SENTINEL_PREFIX, METHOD_SENTINEL_SUFFIX, MIN_GRAPH_FIELD_LEN
from blueprints.FZSJ.JYLLM.search_tool import match_reference_files

logger = logging.getLogger(__name__)

_V2_TASKS: Dict[str, Dict[str, Any]] = {}


def store_v2_task(task_id: str, payload: Dict[str, Any]) -> None:
    _V2_TASKS[task_id] = payload


def get_v2_task(task_id: str) -> Optional[Dict[str, Any]]:
    return _V2_TASKS.get(task_id)


def update_v2_task(task_id: str, updates: Dict[str, Any]) -> None:
    if task_id in _V2_TASKS:
        _V2_TASKS[task_id].update(updates)


_TAG_PAT = re.compile(r"\[\s*/?\s*(THINK|CONTENT)\s*\]", re.IGNORECASE)

# 方法正文标准三小节标题（用户要求统一格式）
_STD_SECTION_MARKERS = ("（一）试验条件", "（二）试验过程", "（三）结果评定")
_SECTION_HEADING_PATTERNS = [
    (
        re.compile(
            r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?"
            r"(?:[（(]一[）)]|[\d]+(?:\.[\d]+)*[、.\s]*)?"
            r"试验条件\s*(?:\*\*)?\s*$",
            re.MULTILINE,
        ),
        "**（一）试验条件**",
    ),
    (
        re.compile(
            r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?"
            r"(?:[（(]二[）)]|[\d]+(?:\.[\d]+)*[、.\s]*)?"
            r"试验过程\s*(?:\*\*)?\s*$",
            re.MULTILINE,
        ),
        "**（二）试验过程**",
    ),
    (
        re.compile(
            r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?"
            r"(?:[（(]三[）)]|[\d]+(?:\.[\d]+)*[、.\s]*)?"
            r"(?:结果评定|试验评定)\s*(?:\*\*)?\s*$",
            re.MULTILINE,
        ),
        "**（三）结果评定**",
    ),
]


def _has_standard_three_sections(body: str) -> bool:
    t = body or ""
    return all(m in t for m in _STD_SECTION_MARKERS)


def _enforce_method_three_sections(body: str) -> str:
    """将方法正文小节标题规范为 **（一）试验条件** 等格式。"""
    t = (body or "").strip()
    if not t:
        return t
    for pat, repl in _SECTION_HEADING_PATTERNS:
        t = pat.sub(repl, t)
    return t.strip()


def _min_correction_body_len() -> int:
    return 40


def _skeleton_cumulative_batches(skel: str, batch_lines: int = 4) -> List[str]:
    full = skel or ""
    lines = full.splitlines(keepends=True)
    if not lines:
        return [full]
    out: List[str] = []
    step = max(1, batch_lines)
    end = 0
    while end < len(lines):
        end = min(end + step, len(lines))
        chunk = "".join(lines[:end])
        if not out or chunk != out[-1]:
            out.append(chunk)
    if not out or out[-1] != full:
        out.append(full)
    return out


def _clean_method_body(text: str) -> str:
    t = (text or "").strip()
    t = _TAG_PAT.sub("", t)
    t = re.sub(r"```[a-z]*", "", t, flags=re.IGNORECASE)
    return t.strip()


def _iter_methods_in_order(method_tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def _walk_projects(project_blocks: List[Dict[str, Any]], env_name: str) -> None:
        for pb in project_blocks or []:
            ctx = pb.get("project") or {}
            for m in pb.get("methods") or []:
                mm = dict(m)
                mm["_section"] = "2.1"
                mm["_project_name"] = str(ctx.get("name") or "")
                mm["_indicator_name"] = ""
                mm["_env_name"] = str(m.get("_env_name") or env_name)
                out.append(mm)
            _walk_projects(pb.get("children") or [], env_name)

    def _walk_indicators(blocks: List[Dict[str, Any]]) -> None:
        for block in blocks or []:
            ctx = block.get("indicator") or {}
            for m in block.get("methods") or []:
                mm = dict(m)
                mm["_section"] = "2.2"
                mm["_project_name"] = ""
                mm["_indicator_name"] = str(ctx.get("name") or "")
                mm["_env_name"] = ""
                out.append(mm)
            _walk_indicators(block.get("children") or [])

    for eb in method_tree.get("environment_blocks") or []:
        envn = str((eb.get("environment") or {}).get("name") or "")
        _walk_projects(eb.get("projects") or [], envn)
    _walk_indicators(method_tree.get("indicator_methods") or [])
    return out


def _rule_fill_body(m: Dict[str, Any]) -> str:
    synr = str(m.get("synr") or m.get("SYNR") or "").strip()
    if synr:
        try:
            from blueprints.FZSJ.JYLLM.method_synr_markdown import synr_html_to_markdown

            if synr_html_is_substantial(synr):
                mid = str(m.get("id") or "").strip() or "unknown_method"
                md = synr_html_to_markdown(synr, mid, m.get("imageurls"), m.get("files")).strip()
                if md:
                    return md
        except Exception as e:
            logger.warning("SYNR html convert failed for method %s: %s", m.get("id"), e)

    sytj = (m.get("sytj") or "").strip()
    sygc = (m.get("sygc") or "").strip()
    jgpd = (m.get("jgpd") or "").strip()
    need = m.get("need_llm") or {}
    parts: List[str] = []
    if not need.get("sytj") and sytj:
        parts.append("**（一）试验条件**\n\n" + sytj)
    if not need.get("sygc") and sygc:
        parts.append("**（二）试验过程**\n\n" + sygc)
    if not need.get("jgpd") and jgpd:
        parts.append("**（三）结果评定**\n\n" + jgpd)
    return "\n\n".join(parts).strip()


def _need_llm_any(m: Dict[str, Any]) -> bool:
    need = m.get("need_llm") or {}
    return bool(need.get("sytj") or need.get("sygc") or need.get("jgpd"))


def _method_has_db_data(m: Dict[str, Any]) -> bool:
    synr = str(m.get("synr") or m.get("SYNR") or "").strip()
    if synr and synr_html_is_substantial(synr):
        return True
    return not _need_llm_any(m)


def _resolve_equipment_display(task: Dict[str, Any]) -> str:
    custom = (task.get("device_display_name") or "").strip()
    if custom:
        return custom
    equip_list = task.get("equipment") or []
    return "、".join(
        str(e.get("name") or e.get("id") or "") for e in equip_list if isinstance(e, dict)
    ) or "—"


def _resolve_env_conditions(task: Dict[str, Any]) -> str:
    return (task.get("environment_conditions") or task.get("environment_conditions_text") or "").strip() or "—"


def _selected_sections_set(task: Dict[str, Any], methods: List[Dict[str, Any]]) -> Set[str]:
    raw = task.get("selected_sections")
    if raw is None:
        ids = {"ch1", "ch2"}
        for m in methods:
            mid = str(m.get("id") or "")
            if mid:
                ids.add(mid)
        return ids
    return {str(x) for x in raw}


def _section_option(task: Dict[str, Any], mid: str) -> Dict[str, Any]:
    opts = task.get("section_options") or {}
    return opts.get(mid) or {}


def _resolve_method_mode(m: Dict[str, Any], task: Dict[str, Any], mid: str) -> str:
    """返回 'correct' | 'generate'。"""
    opt = _section_option(task, mid)
    mode = str(opt.get("mode") or "database_correct")
    if mode == "llm_generate":
        return "generate"
    if _method_has_db_data(m):
        return "correct"
    return "generate"


def _reference_context_from_ids(
    ref_ids: List[str],
    query: str,
    fallback_search: bool = True,
) -> str:
    if not ref_ids and not query:
        return "（无）"
    matched = match_reference_files(query[:500], n_results=12) if query else []
    if ref_ids:
        id_set = {str(x) for x in ref_ids}
        picked = [r for r in matched if str(r.get("id")) in id_set]
    else:
        picked = matched[:5]
    if not picked and fallback_search and query:
        sc = search_context(query[:500]) or ""
        if sc and "未检索到" not in sc:
            return sc
        return "（无）"
    lines = []
    for i, r in enumerate(picked, 1):
        nm = r.get("name") or r.get("id")
        snip = (r.get("snippet") or "").strip()
        rel = r.get("relevance")
        lines.append(f"[参考文件{i}] {nm}（相关度 {rel}%）\n{snip}")
    return "\n\n".join(lines) if lines else "（无）"


def _build_method_ctx(m: Dict[str, Any], task: Dict[str, Any], enames: str) -> Dict[str, str]:
    env_nm = str(m.get("_env_name") or (task.get("environment") or {}).get("name") or "")
    return {
        "equipment_names": enames,
        "device_display_name": _resolve_equipment_display(task),
        "environment_conditions": _resolve_env_conditions(task),
        "environment_name": env_nm or "—",
        "project_name": str(m.get("_project_name") or ""),
        "indicator_name": str(m.get("_indicator_name") or ""),
        "parent_section_no": str(m.get("_parent_prefix") or "").strip(),
    }


def _file_row_source(f: dict, method_fallback: str = "") -> str:
    if not isinstance(f, dict):
        return (method_fallback or "").strip()

    def pick(*keys: str) -> str:
        for k in keys:
            v = f.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    src = pick(
        "source_name", "Source_name", "publisher", "Publisher",
        "custodian", "issue_org", "label", "Label",
    )
    if src:
        return src
    t = pick("type", "Type")
    if t:
        return f"文件类型：{t}"
    if method_fallback and method_fallback.strip():
        return method_fallback.strip()
    return "（请根据文件名称与标准号填写发布机关、归口单位或版权方；勿填图数据库、向量库等技术存储名）"


def _collect_files_for_ch1(method_tree: Dict[str, Any]) -> List[Dict[str, str]]:
    seen: set = set()
    rows: List[Dict[str, str]] = []

    def _walk_project_files(project_blocks: List[Dict[str, Any]]) -> None:
        for pb in project_blocks or []:
            for m in pb.get("methods") or []:
                m_src = str(m.get("source_name") or m.get("Source_name") or "").strip()
                for f in m.get("files") or []:
                    if not isinstance(f, dict):
                        continue
                    fid = str(f.get("id") or f.get("name") or "")
                    if fid in seen:
                        continue
                    seen.add(fid)
                    rows.append({"id": fid, "name": str(f.get("name") or fid), "source": _file_row_source(f, m_src)})
            _walk_project_files(pb.get("children") or [])

    def _walk_indicator_files(blocks: List[Dict[str, Any]]) -> None:
        for block in blocks or []:
            for m in block.get("methods") or []:
                m_src = str(m.get("source_name") or m.get("Source_name") or "").strip()
                for f in m.get("files") or []:
                    if not isinstance(f, dict):
                        continue
                    fid = str(f.get("id") or f.get("name") or "")
                    if fid in seen:
                        continue
                    seen.add(fid)
                    rows.append({"id": fid, "name": str(f.get("name") or fid), "source": _file_row_source(f, m_src)})
            _walk_indicator_files(block.get("children") or [])

    for eb in method_tree.get("environment_blocks") or []:
        _walk_project_files(eb.get("projects") or [])
    _walk_indicator_files(method_tree.get("indicator_methods") or [])
    return rows


def _files_list_text(file_rows: List[Dict[str, str]]) -> str:
    if not file_rows:
        return "（无）"
    lines: List[str] = []
    for r in file_rows:
        nm = r.get("name") or r.get("id") or "未命名文件"
        src = (r.get("source") or "").strip()
        if src:
            lines.append(f"- {nm}（表格「来源」列建议：{src}）")
        else:
            lines.append(f"- {nm}")
    return "\n".join(lines)


def _yield_text_chunks(text: str, size: int = 56) -> Generator[str, None, None]:
    t = text or ""
    for i in range(0, len(t), size):
        yield t[i : i + size]


def _llm_method_body_stream(
    m: Dict[str, Any], ctx: Dict[str, str], llm_key: str, ref_ids: Optional[List[str]] = None
) -> Generator[str, None, None]:
    q = " ".join(
        x
        for x in [
            ctx.get("device_display_name"),
            ctx.get("environment_conditions"),
            m.get("name"),
            ctx.get("environment_name"),
            ctx.get("project_name") or ctx.get("indicator_name"),
        ]
        if x
    )
    ref_ids = ref_ids or []
    sc = _reference_context_from_ids(ref_ids, q)

    def blk(label: str, val: str) -> str:
        v = (val or "").strip()
        if len(v) >= MIN_GRAPH_FIELD_LEN:
            return f"**{label}（图库）**：\n{v}\n"
        return f"**{label}（图库）**：\n（无或内容过短，请结合标准与上下文完整撰写）\n"

    project_display = ctx.get("project_name") or ctx.get("indicator_name") or "—"
    user = get_prompt(
        "method_detail_generator",
        device_display_name=ctx.get("device_display_name") or "—",
        equipment_names=ctx.get("equipment_names") or "—",
        environment_conditions=ctx.get("environment_conditions") or "—",
        environment_name=ctx.get("environment_name") or "—",
        project_name=project_display,
        method_name=str(m.get("name") or m.get("id") or "—"),
        parent_section_no=str(ctx.get("parent_section_no") or "—"),
        sytj_existing=blk("试验条件", str(m.get("sytj") or "")),
        sygc_existing=blk("试验过程", str(m.get("sygc") or "")),
        jgpd_existing=blk("结果评定", str(m.get("jgpd") or "")),
        search_context=sc or "（无）",
    )
    user = prepend_scheme_global_requirements(user)
    messages = [
        {"role": "system", "content": "你是试验方案技术编辑。只输出用户要求的三个小节正文。"},
        {"role": "user", "content": user},
    ]
    for piece in call_llm_stream(messages, temperature=0.35, max_tokens=4096, llm_model_key=llm_key):
        if isinstance(piece, dict):
            continue
        if piece:
            yield str(piece)


def _llm_method_correction_stream(
    m: Dict[str, Any],
    draft_body: str,
    ctx: Dict[str, str],
    llm_key: str,
    ref_ids: Optional[List[str]] = None,
) -> Generator[str, None, None]:
    user = get_prompt(
        "method_detail_corrector",
        device_display_name=ctx.get("device_display_name") or "—",
        environment_conditions=ctx.get("environment_conditions") or "—",
        environment_name=ctx.get("environment_name") or "—",
        draft_body=draft_body or "（空）",
    )
    user = prepend_scheme_global_requirements(user)
    messages = [
        {"role": "system", "content": "你是试验方案技术编辑。只输出校正后的三个小节正文，标题必须为（一）（二）（三）格式。"},
        {"role": "user", "content": user},
    ]
    for piece in call_llm_stream(messages, temperature=0.25, max_tokens=4096, llm_model_key=llm_key):
        if isinstance(piece, dict):
            continue
        if piece:
            yield str(piece)


def _llm_method_correction_nonstream(
    m: Dict[str, Any],
    draft_body: str,
    ctx: Dict[str, str],
    llm_key: str,
) -> str:
    user = get_prompt(
        "method_detail_corrector",
        device_display_name=ctx.get("device_display_name") or "—",
        environment_conditions=ctx.get("environment_conditions") or "—",
        environment_name=ctx.get("environment_name") or "—",
        draft_body=draft_body or "（空）",
    )
    user = prepend_scheme_global_requirements(user)
    messages = [
        {"role": "system", "content": "你是试验方案技术编辑。只输出校正后的三个小节正文，标题必须为（一）（二）（三）格式。"},
        {"role": "user", "content": user},
    ]
    return call_llm_nonstream(messages, temperature=0.25, max_tokens=4096, llm_model_key=llm_key) or ""


def _finalize_method_body(body: str, m: Dict[str, Any], parent_prefix: str) -> str:
    body = _enforce_method_three_sections(_clean_method_body(body))
    if not body:
        body = "**（一）试验条件**\n\n（待补充）\n\n**（二）试验过程**\n\n（待补充）\n\n**（三）结果评定**\n\n（待补充）"
    body = strip_redundant_method_name_heading(body, str(m.get("name") or ""))
    # 标准三小节格式下不再套用 parent_prefix 数字编号，避免（一）（二）被改成 2.1.1.1
    if not _has_standard_three_sections(body):
        body = apply_method_heading_prefix(body, parent_prefix)
    return body.strip()


def _ch1_correction_stream(ch1: str, task: Dict[str, Any], llm_key: str) -> Generator[str, None, None]:
    user = get_prompt(
        "ch1_corrector",
        device_display_name=_resolve_equipment_display(task),
        environment_conditions=_resolve_env_conditions(task),
        draft_ch1=ch1,
    )
    user = prepend_scheme_global_requirements(user)
    messages = [
        {"role": "system", "content": get_prompt("deterministic_ch1_stream_system")},
        {"role": "user", "content": user},
    ]
    for piece in call_llm_stream(messages, temperature=0.35, max_tokens=4096, llm_model_key=llm_key):
        if isinstance(piece, dict):
            continue
        if piece:
            yield str(piece)


def _run_db_method_correction(
    m: Dict[str, Any],
    ctx: Dict[str, str],
    llm_key: str,
) -> tuple[str, List[str]]:
    """
    数据库正文经 LLM 校正。返回 (最终正文, 流式片段列表供 SSE 回放)。
    流式失败时非流式重试；仍失败则退回规则规范化草稿。
    """
    draft = _enforce_method_three_sections(_rule_fill_body(m))
    if not draft.strip():
        return "", []

    stream_frags: List[str] = []
    for frag in _llm_method_correction_stream(m, draft, ctx, llm_key):
        stream_frags.append(frag)

    body = _clean_method_body("".join(stream_frags))
    if len(body) < _min_correction_body_len():
        logger.warning("[JYLLM v2] method %s correction stream empty/short, non-stream retry", m.get("id"))
        fallback = _llm_method_correction_nonstream(m, draft, ctx, llm_key)
        body = _clean_method_body(fallback)
        if body and len(body) >= _min_correction_body_len():
            stream_frags = list(_yield_text_chunks(body, 48))

    if len(body) < _min_correction_body_len():
        logger.warning("[JYLLM v2] method %s LLM correction failed, use rule-normalized draft", m.get("id"))
        body = draft
        stream_frags = list(_yield_text_chunks(body, 48))

    return body, stream_frags


def generate_content_stream_v2(task_id: str) -> Generator[str, None, None]:
    task = get_v2_task(task_id)
    if not task:
        yield emit_event("error", {"message": "任务不存在或已过期，请先完成规划流（plan_stream / build_skeleton）"})
        return

    llm_key = (task.get("llm_model_key") or "").strip()
    method_tree = task.get("method_tree") or {}
    skel = task.get("skeleton_markdown") or ""
    equip_list = task.get("equipment") or []
    enames = "、".join(str(e.get("name") or e.get("id") or "") for e in equip_list if isinstance(e, dict)) or "—"
    device_display = _resolve_equipment_display(task)
    env_conditions = _resolve_env_conditions(task)

    methods = _iter_methods_in_order(method_tree)
    selected = _selected_sections_set(task, methods)
    gen_ch1 = "ch1" in selected

    file_rows = _collect_files_for_ch1(method_tree)
    files_list = _files_list_text(file_rows)

    ch1_q_bits: List[str] = [device_display, env_conditions, enames, "试验依据", "依据文件", "任务来源", "GJB", "GB/T"]

    def _append_project_bits(project_blocks: List[Dict[str, Any]]) -> None:
        for pb in project_blocks or []:
            ch1_q_bits.append(str((pb.get("project") or {}).get("name") or ""))
            for m in pb.get("methods") or []:
                ch1_q_bits.append(str(m.get("name") or ""))
            _append_project_bits(pb.get("children") or [])

    def _append_indicator_bits(blocks: List[Dict[str, Any]]) -> None:
        for block in blocks or []:
            ch1_q_bits.append(str((block.get("indicator") or {}).get("name") or ""))
            for m in block.get("methods") or []:
                ch1_q_bits.append(str(m.get("name") or ""))
            _append_indicator_bits(block.get("children") or [])

    for eb in method_tree.get("environment_blocks") or []:
        _append_project_bits(eb.get("projects") or [])
    _append_indicator_bits(method_tree.get("indicator_methods") or [])

    ch1_sc = ""
    try:
        ch1_sc = search_context(" ".join(x for x in ch1_q_bits if x).strip()[:800]) or ""
    except Exception:
        pass
    if ch1_sc and "未检索到" in ch1_sc:
        ch1_sc = ""
    ref_block = (
        f"\n\n## 检索到的参考资料（向量库语义检索等）\n{ch1_sc.strip()}\n"
        if (ch1_sc or "").strip()
        else "（本次未检索到额外段落，可仅依据候选文件与设备上下文撰写。）"
    )

    ch1 = ""
    if gen_ch1:
        yield emit_event("chapter1_start", {"task_id": task_id, "chapter_index": 1})
        yield emit_event("chapter1_context", {"task_id": task_id, "chapter_index": 1, "search_context": ch1_sc or ""})

        ch1_user = prepend_scheme_global_requirements(
            get_prompt(
                "deterministic_ch1_stream_user",
                device_display_name=device_display,
                equipment_names=enames,
                environment_conditions=env_conditions,
                task_source_hint=(task.get("task_source") or "").strip() or "（未填写）",
                files_list=files_list,
                reference_material=ref_block,
            )
        )
        ch1_messages = [
            {"role": "system", "content": get_prompt("deterministic_ch1_stream_system")},
            {"role": "user", "content": ch1_user},
        ]
        yield emit_event("chapter1_llm_start", {"task_id": task_id, "chapter_index": 1, "message": "正在调用 LLM 生成第1章…"})
        ch1_parts: List[str] = []
        for piece in call_llm_stream(ch1_messages, temperature=0.45, max_tokens=4096, llm_model_key=llm_key):
            if isinstance(piece, dict):
                sk = piece.get("stream_kind")
                if sk == "context_compress" and piece.get("content"):
                    yield emit_event("thinking", {"agent": "context_compress", "content": piece.get("content"), "chapter_index": 1})
                continue
            if piece:
                ch1_parts.append(str(piece))
                yield emit_event("chapter1_delta", {"task_id": task_id, "chapter_index": 1, "text": str(piece)})

        if not ch1_parts:
            fallback = call_llm_nonstream(ch1_messages, temperature=0.45, max_tokens=4096, llm_model_key=llm_key)
            if fallback and fallback.strip():
                for frag in _yield_text_chunks(fallback, 96):
                    ch1_parts.append(frag)
                    yield emit_event("chapter1_delta", {"task_id": task_id, "chapter_index": 1, "text": frag})

        raw_ch1 = "".join(ch1_parts)
        ch1 = enforce_ch1_template_structure(_clean_method_body(raw_ch1))
        ch1_fail = (not ch1.strip()) or ("【错误】" in ch1) or ("【生成出错】" in ch1) or ("LLM客户端未初始化" in ch1)
        if ch1_fail:
            yield emit_event("error", {"message": ch1.strip() or "第1章生成失败：LLM 未返回正文。"})
            return

        corr_parts: List[str] = []
        for piece in _ch1_correction_stream(ch1, task, llm_key):
            corr_parts.append(piece)
        if corr_parts:
            ch1 = enforce_ch1_template_structure(_clean_method_body("".join(corr_parts)))

        yield emit_event("chapter1_done", {"task_id": task_id, "chapter_index": 1, "content": ch1})
    else:
        ch1 = "（本段未勾选生成）\n"

    method_ids_list = [str(m.get("id")) for m in methods if m.get("id")]
    if not method_ids_list and "ch2" in selected:
        yield emit_event("error", {"message": "未找到可生成的方法节点。"})
        return

    if "ch2" in selected or any(str(m.get("id")) in selected for m in methods):
        yield emit_event("ch2_outline_start", {"task_id": task_id, "method_ids": method_ids_list})
        for cum in _skeleton_cumulative_batches(skel):
            yield emit_event("ch2_outline_progress", {"task_id": task_id, "markdown_cumulative": cum})
            time.sleep(0.018)
        yield emit_event("skeleton", {"task_id": task_id, "skeleton_markdown": skel, "method_ids": method_ids_list})

    ch2 = skel

    for m in methods:
        mid = str(m.get("id") or "")
        if not mid:
            continue
        if mid not in selected and "ch2" not in selected:
            placeholder = "（本段未勾选生成）"
            sentinel = f"{METHOD_SENTINEL_PREFIX}{mid}{METHOD_SENTINEL_SUFFIX}"
            if sentinel in ch2:
                ch2 = ch2.replace(sentinel, placeholder, 1)
            continue

        yield emit_event("method_start", {"task_id": task_id, "method_id": mid})
        try:
            ctx = _build_method_ctx(m, task, enames)
            parent_prefix = ctx["parent_section_no"]
            opt = _section_option(task, mid)
            ref_ids = opt.get("reference_file_ids") or opt.get("referenceFileIds") or []
            mode = _resolve_method_mode(m, task, mid)
            draft = _rule_fill_body(m)
            has_draft = bool(draft.strip())
            body = ""

            # 数据库有正文时默认走 LLM 校正（仅用户显式选 llm_generate 时才完全重写）
            if has_draft and mode != "llm_generate":
                yield emit_event(
                    "method_correcting",
                    {"task_id": task_id, "method_id": mid, "message": "正在 LLM 校正数据库检索正文…"},
                )
                body, frags = _run_db_method_correction(m, ctx, llm_key)
                for frag in frags:
                    yield emit_event("chapter2_delta", {"task_id": task_id, "method_id": mid, "text": frag})
            else:
                buf: List[str] = []
                for frag in _llm_method_body_stream(m, ctx, llm_key, ref_ids):
                    buf.append(frag)
                    yield emit_event("chapter2_delta", {"task_id": task_id, "method_id": mid, "text": frag})
                body = _clean_method_body("".join(buf))

            body = _finalize_method_body(body, m, parent_prefix)
            sentinel = f"{METHOD_SENTINEL_PREFIX}{mid}{METHOD_SENTINEL_SUFFIX}"
            if sentinel in ch2:
                ch2 = ch2.replace(sentinel, body.strip(), 1)

            yield emit_event("method_content", {"task_id": task_id, "method_id": mid, "content": body, "full_chapter2": ch2})
        except Exception as e:
            logger.exception("method generation failed")
            yield emit_event("method_error", {"task_id": task_id, "method_id": mid, "error": str(e)})

    chapters_out = [
        {"index": 1, "title": "1. 试验依据", "content": ch1.strip(), "score": 100},
        {"index": 2, "title": "2. 试验内容及方法", "content": ch2.strip(), "score": 100},
    ]
    yield emit_event("complete", {"task_id": task_id, "chapters": chapters_out, "engine_version": "deterministic_v2"})


def _compact_base_config_json(cfg: Optional[Dict[str, Any]]) -> str:
    """压缩 base_config，避免区域重写 prompt 超出上下文。"""
    if not cfg:
        return "{}"

    def _pick_names(items: Any, limit: int = 24) -> List[str]:
        if not isinstance(items, list):
            return []
        names: List[str] = []
        for x in items[:limit]:
            if isinstance(x, dict):
                n = x.get("name") or x.get("label") or x.get("title") or x.get("id")
                if n:
                    names.append(str(n)[:100])
            elif x:
                names.append(str(x)[:100])
        return names

    slim = {
        "device_display_name": (cfg.get("device_display_name") or "")[:200],
        "environment_conditions": str(cfg.get("environment_conditions") or "")[:600],
        "equipment": _pick_names(cfg.get("equipment")),
        "projects": _pick_names(cfg.get("projects")),
        "indicators": _pick_names(cfg.get("indicators")),
        "methods": _pick_names(cfg.get("methods")),
    }
    return json.dumps(slim, ensure_ascii=False)


def _truncate_for_prompt(text: str, max_chars: int) -> str:
    t = text or ""
    if len(t) <= max_chars:
        return t
    return t[:max_chars] + f"\n\n…（已截断，原文共 {len(t)} 字）"


def _estimate_tokens(text: str) -> int:
    """中文为主时粗略估算 token。"""
    return max(1, len(text or "") // 2)


def _safe_completion_tokens(messages: List[Dict[str, str]], *, context_window: int = 8192) -> int:
    prompt_text = "".join(str(m.get("content") or "") for m in messages)
    est_input = _estimate_tokens(prompt_text)
    reserve = 256
    cap = context_window - est_input - reserve
    return max(512, min(2048, cap))


def scheme_feedback_rewrite_stream(
    task_id: str,
    full_scheme: str,
    comment: str,
    base_config: Optional[Dict[str, Any]] = None,
    llm_model_key: str = "",
) -> Generator[str, None, None]:
    task = get_v2_task(task_id) or {}
    llm_key = (llm_model_key or task.get("llm_model_key") or "").strip()
    cfg = base_config or {}
    if not cfg and task:
        cfg = {
            "device_display_name": _resolve_equipment_display(task),
            "environment_conditions": _resolve_env_conditions(task),
            "equipment": task.get("equipment"),
            "projects": task.get("projects"),
            "indicators": task.get("indicators"),
            "methods": task.get("methods"),
        }

    user = get_prompt(
        "scheme_feedback_rewrite",
        base_config_json=json.dumps(cfg, ensure_ascii=False, indent=2),
        full_scheme=full_scheme or "（空）",
        user_comment=comment or "（无具体意见，请通读校正）",
    )
    user = prepend_scheme_global_requirements(user)
    messages = [
        {"role": "system", "content": "你是试验方案主编。只输出修改后的完整 Markdown 方案。"},
        {"role": "user", "content": user},
    ]

    yield emit_event("rewrite_start", {"task_id": task_id})
    parts: List[str] = []
    for piece in call_llm_stream(messages, temperature=0.4, max_tokens=8192, llm_model_key=llm_key):
        if isinstance(piece, dict):
            continue
        if piece:
            parts.append(str(piece))
            yield emit_event("rewrite_delta", {"task_id": task_id, "text": str(piece)})

    if not parts:
        fallback = call_llm_nonstream(messages, temperature=0.4, max_tokens=8192, llm_model_key=llm_key)
        if fallback:
            for frag in _yield_text_chunks(fallback, 96):
                parts.append(frag)
                yield emit_event("rewrite_delta", {"task_id": task_id, "text": frag})

    content = _clean_method_body("".join(parts))
    yield emit_event("rewrite_complete", {"task_id": task_id, "content": content, "full_scheme": content})


def region_rewrite_stream(
    task_id: str,
    chapter_index: int,
    region_type: str,
    comment: str,
    chapter_content: str,
    *,
    selected_text: str = "",
    start_offset: Optional[int] = None,
    end_offset: Optional[int] = None,
    heading_id: str = "",
    method_id: str = "",
    chapter_title: str = "",
    heading_text: str = "",
    heading_breadcrumb: str = "",
    full_scheme_excerpt: str = "",
    original_region: str = "",
    base_config: Optional[Dict[str, Any]] = None,
    llm_model_key: str = "",
) -> Generator[str, None, None]:
    from blueprints.FZSJ.JYLLM.region_markdown import extract_region, splice_region, validate_gfm_tables
    from blueprints.FZSJ.JYLLM.region_revision import (
        apply_table_patch_to_region,
        build_scheme_anchor,
        detect_table_edit_intent,
        diff_stats,
        parse_table_patch_json,
        resolve_validation_original,
        validate_region_revision,
    )

    task = get_v2_task(task_id) or {}
    llm_key = (llm_model_key or task.get("llm_model_key") or "").strip()
    cfg = dict(base_config or {})
    if not cfg.get("device_display_name") and task:
        cfg.setdefault("device_display_name", _resolve_equipment_display(task))
    if not cfg.get("environment_conditions") and task:
        cfg.setdefault("environment_conditions", _resolve_env_conditions(task))
    for k in ("equipment", "projects", "indicators", "methods", "planner_prompts"):
        if k not in cfg and task.get(k):
            cfg[k] = task.get(k)

    spec = {
        "region_type": region_type,
        "chapter_index": chapter_index,
        "selected_text": selected_text,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "heading_id": heading_id,
        "method_id": method_id,
        "chapter_title": chapter_title,
        "heading_text": heading_text,
    }

    try:
        slice_ = extract_region(chapter_content or "", spec)
    except ValueError as e:
        yield emit_event("error", {"message": str(e)})
        return

    region_full = slice_.region or ""
    ctx_before = _truncate_for_prompt((slice_.before or "")[-500:], 400)
    ctx_after = _truncate_for_prompt((slice_.after or "")[:500], 400)
    region_cap = 4000 if region_type == "chapter" else 6000
    region_for_prompt = _truncate_for_prompt(region_full, region_cap)
    original = resolve_validation_original(original_region, region_full)
    region_preview = region_full[:800]

    scheme_anchor = build_scheme_anchor(
        device_display_name=str(cfg.get("device_display_name") or ""),
        environment_conditions=str(cfg.get("environment_conditions") or ""),
        chapter_index=chapter_index,
        chapter_title=chapter_title,
        heading_breadcrumb=heading_breadcrumb,
        full_scheme_excerpt=full_scheme_excerpt,
        chapter_content=chapter_content or "",
        heading_text=heading_text,
    )

    table_intent = detect_table_edit_intent(comment, region_full)

    yield emit_event(
        "region_rewrite_start",
        {
            "task_id": task_id,
            "chapter_index": chapter_index,
            "region_type": region_type,
            "region_preview": region_preview,
            "original_region": original,
            "revision_mode": "table_patch" if table_intent.is_table_edit else "llm_revision",
            "meta": slice_.meta,
        },
    )

    new_region = ""
    revision_mode = "llm_revision"
    table_edit_mode = False

    if table_intent.is_table_edit:
        revision_mode = "table_patch"
        target_col = table_intent.target_column or "持续时间"
        user = get_prompt(
            "region_table_patch",
            scheme_anchor=scheme_anchor or "（无）",
            region_content=region_for_prompt or "（空）",
            user_comment=comment,
            target_column=target_col or "（由表头推断）",
        )
        user = prepend_scheme_global_requirements(user)
        messages = [
            {"role": "system", "content": "只输出 JSON，用于表格列补丁。"},
            {"role": "user", "content": user},
        ]
        max_out = min(1024, _safe_completion_tokens(messages))
        raw = call_llm_nonstream(messages, temperature=0.05, max_tokens=max_out, llm_model_key=llm_key) or ""
        patch = parse_table_patch_json(raw)
        if patch:
            patched, ok = apply_table_patch_to_region(region_full, patch)
            if ok:
                new_region = patched
                table_edit_mode = True
                yield emit_event("region_rewrite_delta", {"text": patched, "table_patch": True})

    if not new_region:
        revision_mode = "llm_revision"
        strict_extra = ""
        user = get_prompt(
            "region_revision",
            region_type=region_type,
            scheme_anchor=scheme_anchor or "（无）",
            base_config_json=_compact_base_config_json(cfg),
            context_before=ctx_before or "（无）",
            region_content=region_for_prompt or "（空）",
            context_after=ctx_after or "（无）",
            user_comment=comment or "（请按专业规范润色本区域）",
            strict_extra=strict_extra,
        )
        user = prepend_scheme_global_requirements(user)
        messages = [
            {
                "role": "system",
                "content": "你是试验方案增量修订编辑。只输出修订后的区域 Markdown，不含区域外内容。",
            },
            {"role": "user", "content": user},
        ]
        max_out = _safe_completion_tokens(messages)
        parts: List[str] = []
        try:
            for piece in call_llm_stream(messages, temperature=0.1, max_tokens=max_out, llm_model_key=llm_key):
                if isinstance(piece, dict):
                    continue
                if piece:
                    chunk = str(piece)
                    if "【生成出错】" in chunk or "maximum context length" in chunk.lower():
                        yield emit_event("error", {"message": chunk.strip()})
                        return
                    parts.append(chunk)
                    yield emit_event("region_rewrite_delta", {"text": chunk})
            if not parts:
                fallback = call_llm_nonstream(messages, temperature=0.1, max_tokens=max_out, llm_model_key=llm_key)
                if fallback:
                    if "【生成出错】" in fallback or "maximum context length" in fallback.lower():
                        yield emit_event("error", {"message": fallback.strip()})
                        return
                    for frag in _yield_text_chunks(fallback, 96):
                        parts.append(frag)
                        yield emit_event("region_rewrite_delta", {"text": frag})
        except Exception as e:
            yield emit_event("error", {"message": str(e)})
            return
        new_region = _clean_method_body("".join(parts))

    validation = validate_region_revision(
        original,
        new_region,
        region_type=region_type,
        table_edit_mode=table_edit_mode,
    )
    first_region = new_region
    first_validation = validation

    # 表格程序化 patch 成功则不再走 LLM 重试
    should_retry = (
        not validation.ok
        and not table_edit_mode
        and new_region.strip()
    )

    if should_retry:
        yield emit_event(
            "region_revision_rejected",
            {
                "errors": validation.errors,
                "warnings": validation.warnings,
                "similarity": validation.similarity,
                "retrying": True,
            },
        )
        # strict retry（仅 LLM 修订通道）
        user = get_prompt(
            "region_revision",
            region_type=region_type,
            scheme_anchor=scheme_anchor or "（无）",
            base_config_json=_compact_base_config_json(cfg),
            context_before=ctx_before or "（无）",
            region_content=region_for_prompt or "（空）",
            context_after=ctx_after or "（无）",
            user_comment=comment or "（请按专业规范润色本区域）",
            strict_extra="7. **极严模式**：禁止改动用户未提及的任何句子；禁止新增章节/小节标题；禁止替换设备或标准。",
        )
        user = prepend_scheme_global_requirements(user)
        messages = [
            {
                "role": "system",
                "content": "你是试验方案增量修订编辑。只输出修订后的区域 Markdown，不含区域外内容。",
            },
            {"role": "user", "content": user},
        ]
        max_out = _safe_completion_tokens(messages)
        parts = []
        for piece in call_llm_stream(messages, temperature=0.05, max_tokens=max_out, llm_model_key=llm_key):
            if isinstance(piece, dict):
                continue
            if piece:
                chunk = str(piece)
                if "【生成出错】" in chunk:
                    yield emit_event("error", {"message": chunk.strip()})
                    return
                parts.append(chunk)
                yield emit_event("region_rewrite_delta", {"text": chunk, "retry": True})
        retry_region = _clean_method_body("".join(parts))
        retry_val = validate_region_revision(
            original,
            retry_region,
            region_type=region_type,
            table_edit_mode=False,
        )
        if retry_val.ok and retry_region.strip():
            new_region = retry_region
            validation = retry_val
        elif first_region.strip() and not any("骨架" in e for e in first_validation.errors):
            # 重试更差或为空：保留首次有效结果，交给人审 diff
            new_region = first_region
            from blueprints.FZSJ.JYLLM.region_revision import ValidationResult

            validation = ValidationResult(
                True,
                [],
                first_validation.errors
                + first_validation.warnings
                + ["已保留首次修订结果，请通过 diff 人工确认后应用"],
                first_validation.similarity,
            )
            yield emit_event(
                "region_revision_rejected",
                {
                    "errors": retry_val.errors,
                    "warnings": validation.warnings,
                    "similarity": first_validation.similarity,
                    "retrying": False,
                    "kept_first_pass": True,
                },
            )
        else:
            yield emit_event(
                "region_revision_rejected",
                {
                    "errors": retry_val.errors,
                    "warnings": retry_val.warnings,
                    "similarity": retry_val.similarity,
                    "retrying": False,
                },
            )
            yield emit_event(
                "error",
                {
                    "message": "修订校验未通过：" + "；".join(retry_val.errors),
                    "warnings": retry_val.warnings,
                },
            )
            return

    for w in validate_gfm_tables(new_region):
        logger.warning("region_rewrite GFM table warning task=%s: %s", task_id, w)

    merged = splice_region(slice_.before, new_region, slice_.after)
    stats = diff_stats(original, new_region)
    yield emit_event(
        "region_rewrite_complete",
        {
            "task_id": task_id,
            "chapter_index": chapter_index,
            "new_region": new_region,
            "merged_chapter": merged,
            "original_region": original,
            "revision_mode": revision_mode,
            "validation": {
                "similarity": validation.similarity,
                "warnings": validation.warnings,
            },
            "diff_stats": stats,
            "meta": slice_.meta,
        },
    )
