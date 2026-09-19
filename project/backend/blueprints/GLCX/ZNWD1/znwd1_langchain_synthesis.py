"""
ZNWD1 合成：LangChain 流式，<THINK> + <CONTENT>。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .prompts.synthesis import METHOD_CONTENT_SYNTHESIS_APPENDIX, SYNTHESIS_SYSTEM_PROMPT
from .znwd1_thinking_emit import emit_milestone
from .znwd1_tools import resolve_znwd1_tool_call_parser, znwd1_llm_request_timeout

logger = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<\s*THINK\s*>([\s\S]*?)<\s*/\s*THINK\s*>", re.I)
_CONTENT_BLOCK = re.compile(r"<\s*CONTENT\s*>([\s\S]*?)<\s*/\s*CONTENT\s*>", re.I)


def _lc_kwargs(client: Any, model: str, llm_limits: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": 0.3}
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
                kw["max_tokens"] = int(mo)
            except (TypeError, ValueError):
                pass
    tcp = resolve_znwd1_tool_call_parser()
    if tcp:
        kw["extra_body"] = {"tool_call_parser": tcp}
    kw["timeout"] = znwd1_llm_request_timeout()
    kw["max_retries"] = 0
    return kw


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
        labs_n = row.get("n", {}).get("labels") if isinstance(row.get("n"), dict) else []
        labs_m = row.get("m", {}).get("labels") if isinstance(row.get("m"), dict) else []
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
) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
    """yield (event_type, payload) 与 v4 对齐：thinking / answer。"""
    yield emit_milestone("正在根据检索结果组织答复要点…", kind="synthesis")
    evidence = _compress_evidence(neo_rows, chroma_rows)
    sys_prompt = SYNTHESIS_SYSTEM_PROMPT
    if method_content_mode:
        sys_prompt = sys_prompt + "\n" + METHOD_CONTENT_SYNTHESIS_APPENDIX
    user = f"【用户问题】\n{question}\n\n【检索证据】\n{evidence}"
    llm = ChatOpenAI(**_lc_kwargs(client, model, llm_limits))
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
