"""
ZNWD4 合成：LangChain 流式，<THINK> + <CONTENT>。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .prompts.synthesis import (
    ENV_SETTINGS_LIBRARIAN_APPENDIX,
    EVIDENCE_BOUND_APPENDIX,
    LIST_QUERY_SYNTHESIS_APPENDIX,
    METHOD_CONTENT_SYNTHESIS_APPENDIX,
    NO_EVIDENCE_APPENDIX,
    PROJECT_METHOD_GENERATE_APPENDIX,
    SYNTHESIS_SYSTEM_PROMPT,
)
from .executor import _rows_non_empty
from .answer_markdown import html_fragment_to_markdown
from .znwd4_thinking_emit import emit_milestone
from .znwd4_tools import resolve_znwd4_tool_call_parser, znwd4_llm_request_timeout

logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<\s*THINK\s*>([\s\S]*?)<\s*/\s*THINK\s*>", re.I)
_CONTENT_BLOCK = re.compile(r"<\s*CONTENT\s*>([\s\S]*?)<\s*/\s*CONTENT\s*>", re.I)


def _lc_kwargs(
    client: Any,
    model: str,
    llm_limits: Optional[Dict[str, Any]],
    *,
    sys_prompt: str = "",
    user_prompt: str = "",
    temperature: float = 0.3,
) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": temperature}
    if client:
        bu = getattr(client, "base_url", None)
        if bu:
            kw["base_url"] = str(bu).rstrip("/")
        ak = getattr(client, "api_key", None)
        if ak:
            kw["api_key"] = ak
    if llm_limits:
        mo = llm_limits.get("max_output_tokens")
        if mo:
            try:
                cw = int(llm_limits.get("context_window") or 8192)
                inp_est = (len(sys_prompt) + len(user_prompt)) // 3
                headroom = max(256, cw - inp_est - 384)
                kw["max_tokens"] = min(int(mo), headroom, 2048)
            except (TypeError, ValueError):
                pass
    tcp = resolve_znwd4_tool_call_parser()
    if tcp:
        kw["extra_body"] = {"tool_call_parser": tcp}
    kw["timeout"] = znwd4_llm_request_timeout()
    kw["max_retries"] = 0
    base = str(kw.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "")
    if any(x in base for x in ("192.168.", "127.0.0.1", "localhost")):
        try:
            import httpx

            timeout = float(kw["timeout"])
            kw["http_client"] = httpx.Client(trust_env=False, timeout=timeout)
            kw["http_async_client"] = httpx.AsyncClient(trust_env=False, timeout=timeout)
        except Exception:
            pass
    return kw


_ENV_PROP_KEYS = ("Type", "Class", "Combination", "Limit", "Request", "Name", "Description")


def _env_properties_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    for side in ("n", "m"):
        node = row.get(side)
        if not isinstance(node, dict):
            continue
        labs = node.get("labels") if isinstance(node.get("labels"), list) else []
        if "Environment" in labs:
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            return props if props else dict(node)
    node = row.get("n")
    if isinstance(node, dict):
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        return props if props else dict(node)
    return {}


def _compress_env_settings_evidence(
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    *,
    params: Optional[Dict[str, Any]] = None,
    limit: int = 8000,
) -> str:
    """为装备环境设置推荐整理 Environment 全属性 + 装备/项目上下文。"""
    p = params or {}
    equip_kws = p.get("equipment_keywords") if isinstance(p.get("equipment_keywords"), list) else []
    env_kws = p.get("environment_keywords") if isinstance(p.get("environment_keywords"), list) else []
    proj_kws = p.get("project_keywords") if isinstance(p.get("project_keywords"), list) else []
    parts: List[str] = [
        "[问句上下文]",
        f"装备: {', '.join(str(x) for x in equip_kws) or '未指定'}",
        f"试验环境: {', '.join(str(x) for x in env_kws) or '未指定'}",
        f"试验项目: {', '.join(str(x) for x in proj_kws) or '（由图谱关联推断）'}",
        "",
    ]
    seen_env: set = set()
    for row in neo_rows or []:
        if not isinstance(row, dict) or row.get("error"):
            continue
        props = _env_properties_from_row(row)
        name = str(props.get("Name") or props.get("name") or "").strip()
        if not name or name in seen_env:
            if not props:
                labs_n = row.get("n", {}).get("labels") if isinstance(row.get("n"), dict) else []
                labs_m = row.get("m", {}).get("labels") if isinstance(row.get("m"), dict) else []
                if "Equipment" in (labs_n or []) and "TestProject" in (labs_m or []):
                    en = str((row.get("n", {}).get("properties") or {}).get("Name") or "").strip()
                    pn = str((row.get("m", {}).get("properties") or {}).get("Name") or "").strip()
                    if en and pn:
                        parts.append(f"[EquipProject] {en} → {pn}")
            continue
        seen_env.add(name)
        block = [f"[Environment] {name}"]
        for fk in _ENV_PROP_KEYS:
            raw = str(props.get(fk) or "").strip()
            if not raw:
                continue
            md = html_fragment_to_markdown(raw) if "<" in raw else raw
            block.append(f"{fk}={md[:2500]}")
        parts.append("\n".join(block))
    blob = "\n".join(parts)
    if len(blob) < 200:
        blob += "\n" + _compress_evidence(neo_rows, chroma_rows, limit=limit)
    return blob[:limit] if len(blob) > limit else blob


_SLOT_LABELS = {
    "equipment_keywords": "装备",
    "equipment_type_keywords": "装备类型",
    "equipment_type_parent": "一级装备类别",
    "environment_keywords": "试验环境",
    "project_keywords": "试验项目",
    "indicator_keywords": "考核指标",
    "method_keywords": "试验方法",
    "standard_keywords": "标准/文件",
}


def _slot_list_val(val: Any) -> str:
    if isinstance(val, list):
        items = [str(x).strip() for x in val if str(x).strip()]
        return "、".join(items)
    s = str(val or "").strip()
    return s


def _format_slot_hints(params: Optional[Dict[str, Any]]) -> str:
    p = params or {}
    lines: List[str] = []
    for key, label in _SLOT_LABELS.items():
        if key not in p:
            continue
        text = _slot_list_val(p.get(key))
        if text:
            lines.append(f"- {label}：{text}")
    focus = str(p.get("focus") or p.get("focus_id") or "").strip()
    if focus and not any("装备类型" in x for x in lines):
        lines.append(f"- 聚焦实体：{focus}")
    return "\n".join(lines)


def _evidence_has_substance(evidence: str) -> bool:
    ev = (evidence or "").strip()
    if not ev:
        return False
    low = ev.lower()
    if low in ("无", "none", "（无）", "（无检索结果）", "未检索到"):
        return False
    if "error" in low and len(ev) < 48:
        return False
    return True


def _has_retrieval_evidence(
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    evidence: str,
) -> bool:
    if _rows_non_empty(neo_rows, chroma_rows):
        return True
    return _evidence_has_substance(evidence)


def _build_synthesis_system_prompt(
    *,
    has_evidence: bool,
    method_content_mode: bool,
    env_settings_mode: bool,
    project_method_generate_mode: bool,
) -> str:
    sys_prompt = SYNTHESIS_SYSTEM_PROMPT + "\n" + LIST_QUERY_SYNTHESIS_APPENDIX
    if method_content_mode:
        sys_prompt = sys_prompt + "\n" + METHOD_CONTENT_SYNTHESIS_APPENDIX
    if project_method_generate_mode:
        sys_prompt = sys_prompt + "\n" + PROJECT_METHOD_GENERATE_APPENDIX
    if env_settings_mode:
        sys_prompt = sys_prompt + "\n" + ENV_SETTINGS_LIBRARIAN_APPENDIX
    if has_evidence:
        sys_prompt = sys_prompt + "\n" + EVIDENCE_BOUND_APPENDIX
    else:
        sys_prompt = sys_prompt + "\n" + NO_EVIDENCE_APPENDIX
    return sys_prompt


def _build_synthesis_user_prompt(
    question: str,
    evidence: str,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    hints = _format_slot_hints(params)
    parts = [f"【用户问题】\n{(question or '').strip()}"]
    if hints:
        parts.append(f"【已识别槽位】\n{hints}")
    parts.append(f"【检索证据】\n{evidence}")
    return "\n\n".join(parts)


def _node_props(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    node = row.get(side)
    if not isinstance(node, dict):
        return {}
    props = node.get("properties")
    return props if isinstance(props, dict) else {}


def _node_labels(row: Dict[str, Any], side: str) -> List[str]:
    node = row.get(side)
    if not isinstance(node, dict):
        return []
    labs = node.get("labels")
    return labs if isinstance(labs, list) else []


def _compress_evidence(neo_rows: List[Dict[str, Any]], chroma_rows: List[Dict[str, Any]], limit: int = 6000) -> str:
    parts: List[str] = []

    def _node_line(side_key: str, row: Dict[str, Any], label: str) -> str:
        node = row.get(side_key)
        if not isinstance(node, dict):
            return ""
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        name = str(props.get("Name") or props.get("name") or "").strip()
        return f"{label}:{name}" if name else ""

    for row in (neo_rows or [])[:40]:
        if not isinstance(row, dict) or row.get("error"):
            continue
        if row.get("e") or row.get("tp") or row.get("tm"):
            seg = " | ".join(
                x
                for x in (
                    _node_line("e", row, "设备"),
                    _node_line("tp", row, "项目"),
                    _node_line("tm", row, "方法"),
                )
                if x
            )
            if seg:
                parts.append(f"[EquipProjectMethod] {seg}")
            continue
        labs_n = _node_labels(row, "n")
        labs_m = _node_labels(row, "m")
        if "EquipmentType" in labs_n and "Equipment" in labs_m:
            et = _node_props(row, "n")
            eq = _node_props(row, "m")
            type_lv1 = str(et.get("Type") or "").strip()
            type_lv2 = str(et.get("Name") or et.get("Label") or "").strip()
            eq_name = str(eq.get("Name") or eq.get("name") or "").strip()
            parts.append(
                f"[EquipUnderType] 一级类别={type_lv1 or '（未填）'} "
                f"二级类型={type_lv2 or '（未填）'} 具体装备={eq_name or '（未填）'}"
            )
            continue
        if isinstance(labs_n, list) and isinstance(labs_m, list):
            if "Equipment" in labs_n and "TestProject" in labs_m:
                seg = " | ".join(
                    x for x in (_node_line("n", row, "设备"), _node_line("m", row, "项目")) if x
                )
                if seg:
                    parts.append(f"[EquipProject] {seg}")
                continue
            if "TestProject" in labs_n and "TestMethod" in labs_m:
                seg = " | ".join(
                    x for x in (_node_line("n", row, "项目"), _node_line("m", row, "方法")) if x
                )
                if seg:
                    parts.append(f"[ProjectMethod] {seg}")
                continue
        row_has_method = False
        for side in ("n", "m"):
            node = row.get(side)
            if not isinstance(node, dict):
                continue
            labs = node.get("labels") if isinstance(node.get("labels"), list) else []
            if "TestMethod" not in labs:
                continue
            row_has_method = True
            props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            name = str(props.get("Name") or props.get("name") or "").strip()
            synr = str(props.get("SYNR") or props.get("synr") or "")[:2000]
            sytj = str(props.get("SYTJ") or "")[:800]
            sygc = str(props.get("SYGC") or "")[:800]
            jgpd = str(props.get("JGPD") or "")[:800]
            parts.append(f"[TestMethod] {name}\nSYNR={synr}\nSYTJ={sytj}\nSYGC={sygc}\nJGPD={jgpd}")
        if not row_has_method:
            parts.append(str(row)[:400])
    for row in (chroma_rows or [])[:8]:
        if isinstance(row, dict) and not row.get("error"):
            title = row.get("title") or row.get("doc_id")
            prev = str(row.get("content") or "")[:500]
            parts.append(f"[Chroma] {title}: {prev}")
    blob = "\n".join(parts)
    return blob[:limit] if len(blob) > limit else blob


def stream_synthesis(
    client: Any,
    model: str,
    question: str,
    *,
    neo_rows: List[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
    llm_limits: Optional[Dict[str, Any]] = None,
    method_content_mode: bool = False,
    env_settings_mode: bool = False,
    project_method_generate_mode: bool = False,
    synthesis_params: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """yield (event_type, payload) 与 v4 对齐：thinking / answer。"""
    yield emit_milestone("正在根据检索结果组织答复要点…", kind="synthesis")
    if project_method_generate_mode:
        from .project_method_fallback import (
        compress_project_method_generate_context,
        project_names_from_params,
        resolve_project_names,
        )

        evidence = compress_project_method_generate_context(
            neo_rows,
            chroma_rows,
            project_names=resolve_project_names(synthesis_params, question=question),
            question=question,
        )
    elif env_settings_mode:
        evidence = _compress_env_settings_evidence(
            neo_rows, chroma_rows, params=synthesis_params or {}
        )
    else:
        evidence = _compress_evidence(neo_rows, chroma_rows)
    has_evidence = _has_retrieval_evidence(neo_rows, chroma_rows, evidence)
    if not has_evidence:
        evidence = evidence.strip() or "（本轮未检索到可引用的清单或文档内容）"
    sys_prompt = _build_synthesis_system_prompt(
        has_evidence=has_evidence,
        method_content_mode=method_content_mode,
        env_settings_mode=env_settings_mode,
        project_method_generate_mode=project_method_generate_mode,
    )
    user = _build_synthesis_user_prompt(question, evidence, params=synthesis_params)
    llm = ChatOpenAI(
        **_lc_kwargs(
            client,
            model,
            llm_limits,
            sys_prompt=sys_prompt,
            user_prompt=user,
            temperature=0.0 if has_evidence else 0.3,
        )
    )
    buf = ""
    think_emitted = ""
    answer_emitted = ""

    for chunk in llm.stream([SystemMessage(content=sys_prompt), HumanMessage(content=user)]):
        piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content or "")
        if not piece:
            continue
        buf += piece

        m_th = _THINK_BLOCK.search(buf)
        if m_th:
            inner = (m_th.group(1) or "").strip()
            if inner and len(inner) > len(think_emitted):
                delta = inner[len(think_emitted) :]
                think_emitted = inner
                if delta.strip():
                    yield ("thinking", {"content": delta})

        m_co = _CONTENT_BLOCK.search(buf)
        if m_co:
            inner = (m_co.group(1) or "").strip()
            if inner and len(inner) > len(answer_emitted):
                delta = inner[len(answer_emitted) :]
                answer_emitted = inner
                if delta:
                    yield ("answer", {"content": delta})

    if not answer_emitted:
        visible = _CONTENT_BLOCK.sub("", _THINK_BLOCK.sub("", buf)).strip()
        if visible:
            yield ("answer", {"content": visible})
