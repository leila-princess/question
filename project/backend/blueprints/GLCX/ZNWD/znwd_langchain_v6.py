"""
ZNWD V6：LangChain ChatOpenAI + 工具调用（工具定义在 znwd_v6_tools）。
流程由 prompts_v6.V6_AGENT_SYSTEM_PROMPT 中的 RAG 硬约束与精简图谱说明约束；本模块负责多轮 tool 循环与最终流式生成。

与 LangChain 1.x / langchain-core 1.x 对齐：`AIMessage`/`AIMessageChunk` 的 `content`
可能为 str 或 OpenAI 风格块列表，统一经 `_lc_text_content` 再参与拼接与 SSE。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from .memory_store import get_memory_store
from .prompts_v6 import V6_AGENT_SYSTEM_PROMPT, V6_USER_TOOL_APPENDIX
from .znwd_v6_executor import ZNWD_CHROMA_UI_MAX, chroma_rows_nonempty
from .source_table import build_znwd_table_keyword
from .znwd_preflight import preflight_enabled, run_preflight_or_agent
from .znwd_v6_tools import (
    build_znwd_v6_openai_tool_definitions,
    build_znwd_v6_tool_dispatch,
    canonical_langchain_tool_name,
    resolve_znwd_v6_tool_call_parser,
)

logger = logging.getLogger(__name__)

_SOURCE_PICK_START = "<<<SOURCE_PICK>>>"
_SOURCE_PICK_END = "<<<END_SOURCE_PICK>>>"

# 工具轮 LLM 流式 chunk 可能把 `<THINK>` 等拆成多段；对整段做清洗前须保留尾部避免半标签误发。
_TOOL_STREAM_TAG_HOLD = 36

_THINK_BLOCK = re.compile(r"<\s*THINK\s*>([\s\S]*?)<\s*/\s*THINK\s*>", re.I)
_THINKING_BLOCK_LEGACY = re.compile(r"<\s*thinking\s*>([\s\S]*?)<\s*/\s*thinking\s*>", re.I)
_CONTENT_BLOCK = re.compile(r"<\s*CONTENT\s*>([\s\S]*?)<\s*/\s*CONTENT\s*>", re.I)
_CONTENT_BLOCK_LOWER = re.compile(r"<\s*content\s*>([\s\S]*?)<\s*/\s*content\s*>", re.I)


def _znwd_strip_fenced_code_blocks(t: str) -> str:
    """去掉 ```…``` 代码围栏（防止工具/协议 JSON 泄漏到正文或思考区）。"""
    if not t:
        return t
    return re.sub(r"```[\w]*\s*[\s\S]*?```", "", t, flags=re.I)


def _znwd_strip_tool_like_json_paragraphs(t: str) -> str:
    """去掉独立成段的类检索规划 JSON（与前端 znwdStripToolJsonParagraphs 对齐）。"""
    if not t:
        return t
    parts = re.split(r"\n{2,}", t)
    kept: List[str] = []
    for p in parts:
        s = p.strip()
        if not s.startswith("{"):
            kept.append(p)
            continue
        try:
            o = json.loads(s)
            if isinstance(o, dict) and (
                isinstance(o.get("steps"), list)
                or isinstance(o.get("step_id"), str)
                or isinstance(o.get("op_code"), str)
            ):
                continue
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        kept.append(p)
    return "\n\n".join(kept).strip()


def _znwd_strip_malformed_protocol_fragments(t: str) -> str:
    """去掉模型输出损坏的协议拼接（如 </THINK<CONTENT、残缺 SOURCE_PICK）。"""
    if not t:
        return t
    s = t
    s = re.sub(r"</\s*THINK\s*<\s*CONTENT", "", s, flags=re.I)
    s = re.sub(r"</\s*thinking\s*<\s*content", "", s, flags=re.I)
    s = re.sub(r"</\s*CONTENT\s*E_PICK\s*>>", "", s, flags=re.I)
    s = re.sub(r"<\s*CONTENT\s*E_PICK\s*>>", "", s, flags=re.I)
    s = re.sub(r"<\s*content\s*e_pick\s*>>", "", s, flags=re.I)
    s = re.sub(r"(?:SOURCE_)?PICK\s*>>\s*", "", s, flags=re.I)
    s = re.sub(r"<<<\s*END_SOURCE_PICK\s*>>>\s*", "", s, flags=re.I)
    return s


def _znwd_remove_angle_protocol_tokens(t: str) -> str:
    """仅删除完整闭合的 THINK/CONTENT 尖括号标签（禁止无 `>` 的宽松匹配，避免误删中文）。"""
    if not t:
        return t
    s = t
    s = re.sub(r"<\s*/?\s*(?:THINK|thinking|CONTENT|content)\s*>", "", s, flags=re.I)
    return s


def _hold_back_incomplete_tag_suffix(t: str) -> Tuple[str, str]:
    """流式清洗前：未闭合的 `<THINK` 等尾部保留，避免误伤紧跟的中文（如「用户问」）。"""
    if not t:
        return "", ""
    upper = t.upper()
    for marker in ("<THINK", "<THINKING", "<CONTENT"):
        i = upper.rfind(marker)
        if i < 0:
            continue
        tail = t[i:]
        if ">" not in tail:
            return t[:i], tail
    return t, ""


def _znwd_sse_thinking_payload(text: str) -> str:
    """下发 `event: thinking` 前：只保留可读推理句，去掉标签与正文块、SOURCE_PICK。"""
    if not text:
        return ""
    s = text
    s = re.sub(r"\[\s*THINK\s*\][\s\S]*?\[\s*/\s*THINK\s*\]", "", s, flags=re.I)
    s = re.sub(r"\[\s*CONTENT\s*\][\s\S]*?\[\s*/\s*CONTENT\s*\]", "", s, flags=re.I)
    s = _CONTENT_BLOCK.sub("", s)
    s = _CONTENT_BLOCK_LOWER.sub("", s)
    s = _THINK_BLOCK.sub(lambda m: ((m.group(1) or "").strip() + "\n"), s)
    s = _THINKING_BLOCK_LEGACY.sub(lambda m: ((m.group(1) or "").strip() + "\n"), s)
    s = re.sub(
        r"<<<\s*SOURCE_PICK\s*>>>[\s\S]*?<<<\s*END_SOURCE_PICK\s*>>>",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"<<<\s*SOURCE_PICK\s*>>>[\s\S]*", "", s, flags=re.I)
    s = re.sub(
        r"\{\s*\"doc_id\"\s*:\s*\"[^\"]*\"[\s\S]*?\"reason\"\s*:\s*\"[^\"]*\"\s*\}\s*",
        "",
        s,
    )
    s = _znwd_strip_malformed_protocol_fragments(s)
    s = _znwd_remove_angle_protocol_tokens(s)
    s = _znwd_strip_fenced_code_blocks(s)
    s = _znwd_strip_tool_like_json_paragraphs(s)
    from .znwd_thinking_narrative import humanize_agent_thinking_text, strip_thinking_result_noise

    s = strip_thinking_result_noise(humanize_agent_thinking_text(s))
    return s.strip()


def _lc_text_content(content: Any) -> str:
    """将 LangChain 消息 / 流式块中的 content 规范为纯文本（兼容 str 与块列表）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                typ = str(block.get("type") or "")
                if typ == "text":
                    parts.append(str(block.get("text") or ""))
                elif typ in ("reasoning", "thinking"):
                    parts.append(str(block.get("text") or block.get("summary") or ""))
                continue
            typ = getattr(block, "type", None)
            if typ == "text":
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(content)


def _last_user_question(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "").strip()
    return ""


def _znwd_sanitize_visible_answer(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = _THINK_BLOCK.sub("", t)
    t = _THINKING_BLOCK_LEGACY.sub("", t)
    t = re.sub(r"\[\s*THINK\s*\][\s\S]*?\[\s*/\s*THINK\s*\]", "", t, flags=re.I)
    t = re.sub(r"</?\s*translation\s*>", "", t, flags=re.I)
    t = re.sub(r"<\s*content\s*>", "", t, flags=re.I)
    t = re.sub(r"<\s*/\s*content\s*>", "", t, flags=re.I)
    t = re.sub(r"<\s*CONTENT\s*>", "", t, flags=re.I)
    t = re.sub(r"<\s*/\s*CONTENT\s*>", "", t, flags=re.I)
    t = re.sub(
        r"<<<\s*SOURCE_PICK\s*>>>[\s\S]*?<<<\s*END_SOURCE_PICK\s*>>>",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"<<<\s*SOURCE_PICK\s*>>>[\s\S]*\Z", "", t, flags=re.I)
    t = re.sub(
        r"\{\s*\"doc_id\"\s*:\s*\"[^\"]*\"\s*,\s*\"title\"\s*:\s*\"[^\"]*\"\s*,\s*\"minio_path\"\s*:\s*\"[^\"]*\""
        r"[\s\S]*?\"reason\"\s*:\s*\"[^\"]*\"\s*\}\s*",
        "",
        t,
    )
    t = _znwd_strip_malformed_protocol_fragments(t)
    t = _znwd_remove_angle_protocol_tokens(t)
    t = _znwd_strip_fenced_code_blocks(t)
    t = _znwd_strip_tool_like_json_paragraphs(t)
    t = _truncate_open_think_tail(t)
    return t.strip()


def _strip_complete_think_blocks(buf: str) -> str:
    s = buf
    while True:
        m = _THINK_BLOCK.search(s)
        if not m:
            break
        s = s[: m.start()] + s[m.end() :]
    while True:
        m = _THINKING_BLOCK_LEGACY.search(s)
        if not m:
            break
        s = s[: m.start()] + s[m.end() :]
    return s


def _truncate_open_think_tail(buf: str) -> str:
    for open_re, close_re in (
        (re.compile(r"<\s*THINK\s*>", re.I), re.compile(r"<\s*/\s*THINK\s*>", re.I)),
        (re.compile(r"<\s*thinking\s*>", re.I), re.compile(r"<\s*/\s*thinking\s*>", re.I)),
    ):
        mo = open_re.search(buf)
        if not mo:
            continue
        tail = buf[mo.end() :]
        if close_re.search(tail):
            return buf
        return buf[: mo.start()]
    return buf


def _visible_for_answer_stream(buf: str) -> str:
    s = _strip_complete_think_blocks(buf)
    s = _truncate_open_think_tail(s)
    return _znwd_sanitize_visible_answer(s)


def _pop_first_think_block(buf: str) -> Tuple[str, Optional[str]]:
    """若存在完整 THINK（或旧 thinking）块，返回 (去掉该块后的文本, inner)；否则 (buf, None)。"""
    m = _THINK_BLOCK.search(buf)
    if m:
        inner = (m.group(1) or "").strip()
        rest = buf[: m.start()] + buf[m.end() :]
        return rest, inner or None
    m2 = _THINKING_BLOCK_LEGACY.search(buf)
    if m2:
        inner = (m2.group(1) or "").strip()
        rest = buf[: m2.start()] + buf[m2.end() :]
        return rest, inner or None
    return buf, None


def _extract_think_inner_for_done(raw: str) -> str:
    m = _THINK_BLOCK.search(raw)
    if m:
        return (m.group(1) or "").strip()
    m2 = _THINKING_BLOCK_LEGACY.search(raw)
    if m2:
        return (m2.group(1) or "").strip()
    return ""


def _strip_and_parse_source_pick(text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    if not text:
        return text, None
    s = text
    start = s.find(_SOURCE_PICK_START)
    if start < 0:
        return s, None
    end = s.find(_SOURCE_PICK_END, start)
    if end < 0:
        return s[:start].strip(), None
    blob = s[start + len(_SOURCE_PICK_START) : end].strip()
    try:
        data = json.loads(blob)
    except Exception:
        merged = (s[:start].strip() + s[end + len(_SOURCE_PICK_END) :].strip()).strip()
        return merged, None
    pick = data if isinstance(data, dict) else None
    merged = (s[:start].strip() + s[end + len(_SOURCE_PICK_END) :].strip()).strip()
    return merged, pick


def _extract_minio_path_from_metadata(md: Dict[str, Any]) -> str:
    if not isinstance(md, dict):
        return ""
    for k in ("source_file", "minio_path", "minIo_id", "minio_id", "original_file", "object_path"):
        v = str(md.get(k) or "").strip()
        if v and v.lower() not in ("none", "null"):
            return v
    return ""


def _infer_file_format_from_minio_path(path: str) -> str:
    p = (path or "").lower().split("?", 1)[0]
    if p.endswith(".pdf"):
        return "pdf"
    if p.endswith(".docx"):
        return "docx"
    if p.endswith(".doc"):
        return "doc"
    return "other"


def _merge_primary_source_with_chroma(
    pick: Optional[Dict[str, Any]],
    chroma_rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not pick:
        return None
    doc_id = str(pick.get("doc_id") or "").strip()
    title = str(pick.get("title") or "").strip()
    reason = str(pick.get("reason") or "").strip()
    for c in chroma_rows or []:
        if not isinstance(c, dict) or c.get("error"):
            continue
        md = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
        path = str(c.get("minio_path") or "").strip() or _extract_minio_path_from_metadata(md)
        fmt = str(c.get("file_format") or "").strip() or _infer_file_format_from_minio_path(path)
        if doc_id and str(c.get("doc_id") or "").strip() == doc_id:
            return {
                "doc_id": doc_id,
                "title": str(c.get("title") or "") or title,
                "minio_path": path,
                "file_format": fmt,
                "reason": reason or "向量片段同 doc_id",
            }
        if title and str(c.get("title") or "").strip() == title:
            return {
                "doc_id": str(c.get("doc_id") or ""),
                "title": title,
                "minio_path": path,
                "file_format": fmt,
                "reason": reason or "向量片段同 title",
            }
    return {
        "doc_id": doc_id,
        "title": title,
        "minio_path": str(pick.get("minio_path") or ""),
        "file_format": str(pick.get("file_format") or "other"),
        "reason": reason,
    }


def _finalize_memory(
    user_id: Optional[str],
    session_id: Optional[str],
    question: str,
    final_answer: str,
    client: Any,
    model: str,
) -> None:
    if not user_id or not session_id:
        return
    text = (final_answer or "").strip()
    if not text:
        return
    store = get_memory_store()
    if not store:
        return
    try:
        store.add_message(user_id, session_id, "user", question)
        store.add_message(user_id, session_id, "assistant", text)
        store.maybe_compress_async(user_id, session_id, client, model)
        logger.info("[V6][LC][memory] 已写入 Redis session=%s", session_id[:16])
    except Exception as e:
        logger.warning("[V6][LC][memory] 失败: %s", e)


def _openai_messages_to_lc(messages: List[Dict[str, Any]]) -> List[Any]:
    out: List[Any] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        c = str(m.get("content") or "")
        if role == "system":
            out.append(SystemMessage(content=c))
        elif role == "user":
            out.append(HumanMessage(content=c))
        elif role == "assistant":
            out.append(AIMessage(content=c))
    return out


def _lc_kwargs_from_openai_client(
    client: Any,
    model: str,
    llm_limits: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    kw: Dict[str, Any] = {"model": model, "temperature": 0.3}
    if client:
        bu = getattr(client, "base_url", None)
        if bu is not None:
            kw["base_url"] = str(bu).rstrip("/")
        ak = getattr(client, "api_key", None)
        if ak:
            kw["api_key"] = ak
    if llm_limits:
        mo = llm_limits.get("max_output_tokens")
        if mo:
            try:
                mo_i = int(mo)
                cw = int(llm_limits.get("context_window") or 0)
                est = llm_limits.get("estimated_input_tokens")
                if cw and est is not None:
                    try:
                        est_i = int(est)
                        room = cw - est_i - 128
                        mo_i = min(mo_i, max(256, room))
                    except (TypeError, ValueError):
                        pass
                kw["max_tokens"] = mo_i
            except (TypeError, ValueError):
                pass
    tcp = resolve_znwd_v6_tool_call_parser()
    if tcp:
        kw["extra_body"] = {"tool_call_parser": tcp}
        logger.info("[V6][LC] extra_body.tool_call_parser=%s", tcp)
    else:
        logger.info("[V6][LC] 未附带 extra_body.tool_call_parser（ZNWD_V6_TOOL_CALL_PARSER=none）")
    return kw


def _normalize_tool_call_args(tc: Any) -> Dict[str, Any]:
    if isinstance(tc, dict):
        raw = tc.get("args")
        if raw is None and "arguments" in tc:
            raw = tc.get("arguments")
        tid = tc.get("id") or ""
        name = tc.get("name") or ""
    else:
        raw = getattr(tc, "args", None)
        if raw is None:
            raw = getattr(tc, "arguments", {})
        tid = getattr(tc, "id", "") or ""
        name = getattr(tc, "name", "") or ""
    if isinstance(raw, str):
        try:
            return {"id": tid, "name": name, "args": json.loads(raw) if raw.strip() else {}}
        except json.JSONDecodeError:
            return {"id": tid, "name": name, "args": {}}
    return {"id": tid, "name": name, "args": dict(raw) if isinstance(raw, dict) else {}}


# 🔧 修改点：工具回写 LLM 的上下文压缩（禁止整段 JSON 进入 ToolMessage）
_CHROMA_COMPRESS_LEN = 300


def _rough_token_estimate(text: str) -> int:
    return max(0, len(text or "") // 4)


def _rough_tokens_for_message(msg: Any) -> int:
    if isinstance(msg, SystemMessage):
        c = msg.content
        t = c if isinstance(c, str) else str(c)
        return max(1, _rough_token_estimate(t))
    if isinstance(msg, HumanMessage):
        c = msg.content
        t = c if isinstance(c, str) else _lc_text_content(c)
        return max(1, _rough_token_estimate(t))
    if isinstance(msg, AIMessage):
        return max(1, _rough_token_estimate(_lc_text_content(msg.content)))
    if isinstance(msg, ToolMessage):
        return max(1, _rough_token_estimate(str(msg.content or "")))
    return max(1, _rough_token_estimate(str(msg)))


def _total_rough_tokens(msgs: List[Any]) -> int:
    return sum(_rough_tokens_for_message(m) for m in msgs)


def _compress_node_name_for_edge(node: Any) -> str:
    if not isinstance(node, dict):
        return str(node)[:120]
    if node.get("error"):
        return "?"
    if "labels" in node:
        labs = node.get("labels") or []
        lab0 = str(labs[0]) if labs else "Node"
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        nm = str((props or {}).get("Name") or (props or {}).get("Label") or (props or {}).get("Id") or "").strip()
        return f"{lab0}:{nm}"[:200] if nm else lab0[:80]
    return str(node)[:120]


def _compress_rel_type(rel: Any) -> str:
    if isinstance(rel, dict) and rel.get("type"):
        return str(rel.get("type"))[:80]
    if isinstance(rel, dict):
        return str(rel.get("name") or rel.get("relationship_type") or "?")[:80]
    return "?"


def _compress_tool_result(result: dict) -> str:
    """将工具 dict 压成短纯文本：Neo 为「起点->关系->终点」行列表；Chroma 为 title + content 前 300 字。禁止整段 JSON。"""
    if not isinstance(result, dict):
        return str(result)[:500]
    lines: List[str] = []
    err = result.get("error")
    neo = list(result.get("neo4jResults") or [])
    chroma = list(result.get("chromaResults") or [])
    if err and not neo and not chroma:
        return f"错误: {err}"
    if neo:
        lines.append("[Neo4j]（起点名称->关系类型->终点名称）")
        for i, row in enumerate(neo):
            if not isinstance(row, dict):
                lines.append(f"  {i}: {str(row)[:120]}")
                continue
            if row.get("error"):
                lines.append(f"  {i}: error={row.get('error')}")
                continue
            edge = f"{_compress_node_name_for_edge(row.get('n'))}->{_compress_rel_type(row.get('r'))}->{_compress_node_name_for_edge(row.get('m'))}"
            lines.append(f"  {i}: {edge}")
    if chroma:
        lines.append(f"[Chroma]（每条 title + content 前{_CHROMA_COMPRESS_LEN}字）")
        for j, c in enumerate(chroma):
            if not isinstance(c, dict):
                lines.append(f"  {j}: {str(c)[:120]}")
                continue
            if c.get("error"):
                lines.append(f"  {j}: error={c.get('error')}")
                continue
            tit = str(c.get("title") or "")[:200]
            body = str(c.get("content") or "")
            snip = body[:_CHROMA_COMPRESS_LEN].replace("\n", " ")
            lines.append(f"  {j}: title={tit!r} | content={snip!r}")
    ag = result.get("answer_guidance")
    if isinstance(ag, str) and ag.strip():
        lines.append(f"[提示] {ag.strip()[:400]}")
    if err and (neo or chroma):
        lines.append(f"[注意] error={err}")
    if not lines:
        lines.append("(无 neo/chroma 摘要)")
    return "\n".join(lines)


def _pop_oldest_ai_tool_block_from(msgs: List[Any], start: int = 0) -> bool:
    """自 start 起找到首条带 tool_calls 的 AIMessage，连同其后连续 ToolMessage 一并删除。"""
    for i in range(start, len(msgs)):
        m = msgs[i]
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            j = i + 1
            while j < len(msgs) and isinstance(msgs[j], ToolMessage):
                j += 1
            del msgs[i:j]
            return True
    return False


def _trim_context_for_llm(messages: list, max_tokens: int = 11000) -> list:
    """保留 SystemMessage + 自最后一条 HumanMessage 起的后缀；超预算时优先整轮删除旧的 AIMessage+ToolMessage 块。"""
    if not messages:
        return []
    systems = [m for m in messages if isinstance(m, SystemMessage)]
    rest = [m for m in messages if not isinstance(m, SystemMessage)]
    last_h = -1
    for i, m in enumerate(rest):
        if isinstance(m, HumanMessage):
            last_h = i
    trimmed = list(rest[last_h:] if last_h >= 0 else rest)
    while _total_rough_tokens(systems) + _total_rough_tokens(trimmed) > max_tokens:
        # 🔧 修改点：不删 trimmed[0]（Human），从 1 起删旧工具轮
        if len(trimmed) > 1 and _pop_oldest_ai_tool_block_from(trimmed, 1):
            continue
        if len(trimmed) > 1:
            del trimmed[1]
            continue
        break
    return systems + trimmed


def _v6_agent_stream(
    client,
    model,
    messages,
    driver,
    collection,
    llm_limits: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_interest: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, dict], None, None]:
    """LangChain 工具多轮 + 最终流式合成（原 V6 主流程）。"""
    if not client:
        yield ("error", {"message": "LLM 客户端未初始化"})
        return

    work_messages: List[Dict[str, Any]] = [dict(m) if isinstance(m, dict) else m for m in messages]
    question = _last_user_question(work_messages)

    system_content = V6_AGENT_SYSTEM_PROMPT

    agent_messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
    last_user_idx: Optional[int] = None
    for m in work_messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "system":
            continue
        agent_messages.append(dict(m))
        if m.get("role") == "user":
            last_user_idx = len(agent_messages) - 1
    if last_user_idx is not None:
        agent_messages[last_user_idx] = dict(agent_messages[last_user_idx])
        um = agent_messages[last_user_idx]
        base = um.get("content")
        um["content"] = f"{base if isinstance(base, str) else (base or '')}{V6_USER_TOOL_APPENDIX}"

    table_kw = build_znwd_table_keyword(question)
    yield ("search_keywords", {"ssyqTableKeyword": table_kw})

    lc_base = ChatOpenAI(**_lc_kwargs_from_openai_client(client, model, llm_limits))
    tool_defs = build_znwd_v6_openai_tool_definitions()
    dispatch = build_znwd_v6_tool_dispatch(
        driver, collection, user_interest, user_id, session_id
    )
    llm_tools = lc_base.bind_tools(tool_defs)

    lc_msgs: List[Any] = _openai_messages_to_lc(agent_messages)
    last_neo: List[Any] = []
    last_chroma: List[Any] = []

    yield ("thinking_start", {})
    # 🔧 修改点：降低工具轮上限，避免上下文与调用发散
    max_tool_rounds = 3
    for tr in range(max_tool_rounds):
        # 🔧 修改点：每次调用模型前裁剪 lc_msgs，丢弃旧工具轮、控制估算 token
        lc_msgs = _trim_context_for_llm(lc_msgs, 11000)
        # 工具轮若用 invoke，会整轮阻塞到模型结束才 yield，前端表现为长时间无增量。
        # 使用 stream 并在每个文本 chunk 上立刻下发 thinking，与最终答复阶段的流式体验一致。
        ai: AIMessage
        tool_think_raw = ""
        tool_think_san_emitted = ""
        try:
            gathered: Any = None
            for chunk in llm_tools.stream(lc_msgs):
                if gathered is None:
                    gathered = chunk
                else:
                    gathered = gathered + chunk
                piece = _lc_text_content(getattr(chunk, "content", None))
                if piece:
                    tool_think_raw += piece
                    if len(tool_think_raw) > _TOOL_STREAM_TAG_HOLD:
                        pref = tool_think_raw[: len(tool_think_raw) - _TOOL_STREAM_TAG_HOLD]
                        safe, _held = _hold_back_incomplete_tag_suffix(pref)
                        san = _znwd_sse_thinking_payload(safe)
                        if len(san) > len(tool_think_san_emitted):
                            yield ("thinking", {"content": san[len(tool_think_san_emitted) :]})
                            tool_think_san_emitted = san
            if tool_think_raw.strip():
                san_all = _znwd_sse_thinking_payload(tool_think_raw)
                if len(san_all) > len(tool_think_san_emitted):
                    yield ("thinking", {"content": san_all[len(tool_think_san_emitted) :]})
                    tool_think_san_emitted = san_all
            if gathered is None:
                logger.warning("[V6][LC] tool round stream empty round=%s", tr)
                break
            ai = gathered  # type: ignore[assignment]
        except Exception as e:
            logger.warning("[V6][LC] tool round stream failed, invoke fallback round=%s err=%s", tr, e)
            ai = llm_tools.invoke(lc_msgs)
            fb_pre = _lc_text_content(getattr(ai, "content", None)).strip()
            if fb_pre:
                rest_fb, tin_fb = _pop_first_think_block(fb_pre)
                if tin_fb:
                    yield ("thinking", {"content": _znwd_sse_thinking_payload(tin_fb + "\n")})
                else:
                    yield ("thinking", {"content": _znwd_sse_thinking_payload(fb_pre + "\n\n")})
        lc_msgs.append(ai)
        inv = getattr(ai, "invalid_tool_calls", None) or []
        if inv:
            try:
                sample = json.dumps(inv[:3], ensure_ascii=False, default=str)[:1500]
            except TypeError:
                sample = str(inv)[:500]
            logger.warning(
                "[V6][LC] invalid_tool_calls round=%s count=%s sample=%s",
                tr,
                len(inv),
                sample,
            )
        pre = _lc_text_content(getattr(ai, "content", None)).strip()
        rest_pre = pre
        if pre:
            rest_pre, _tin_pre = _pop_first_think_block(pre)
        tcs = list(ai.tool_calls or [])
        if not tcs:
            if pre:
                raw = pre
                ans_visible = _znwd_sanitize_visible_answer(rest_pre)
                if ans_visible.strip():
                    yield ("answer", {"content": ans_visible})
                thinking_text = _extract_think_inner_for_done(raw)
                thinking_text = _znwd_sse_thinking_payload(thinking_text) if thinking_text else ""
                merged_tail = raw.strip()
                clean_answer, raw_pick = _strip_and_parse_source_pick(merged_tail)
                clean_answer = _znwd_sanitize_visible_answer(clean_answer or "")
                chroma_nonempty = chroma_rows_nonempty(last_chroma)
                primary = (
                    _merge_primary_source_with_chroma(raw_pick, (last_chroma or [])[:ZNWD_CHROMA_UI_MAX])
                    if chroma_nonempty
                    else None
                )
                if not clean_answer.strip():
                    clean_answer = _znwd_sanitize_visible_answer(raw.strip())
                _finalize_memory(user_id, session_id, question, clean_answer, client, model)
                yield (
                    "done",
                    {
                        "fullAnswer": clean_answer,
                        "fullThinking": thinking_text,
                        "primaryChromaSource": primary,
                        "ssyqTableKeyword": table_kw,
                    },
                )
                return
            break
        for tc in tcs:
            norm = _normalize_tool_call_args(tc)
            name = canonical_langchain_tool_name(str(norm.get("name") or ""))
            args = norm.get("args") if isinstance(norm.get("args"), dict) else {}
            tid = str(norm.get("id") or f"call_{tr}")
            logger.info("[V6][LC] tool_call name=%s args=%s", name, json.dumps(args, ensure_ascii=False)[:2000])
            fn = dispatch.get(name)
            result: Dict[str, Any]
            if not fn:
                result = {"error": f"unknown_tool:{name}"}
            else:
                result = fn(args)
            if isinstance(result, dict):
                neo_add = list(result.get("neo4jResults") or [])
                chr_add = list(result.get("chromaResults") or [])
                if neo_add:
                    last_neo = list(last_neo or []) + neo_add
                if chr_add:
                    last_chroma = list(last_chroma or []) + chr_add
                if neo_add or chr_add:
                    chroma_disp = (last_chroma or [])[:ZNWD_CHROMA_UI_MAX]
                    yield ("source_data", {"neo4jResults": last_neo, "chromaResults": chroma_disp})
                    refined_kw = build_znwd_table_keyword(question, last_neo)
                    if refined_kw:
                        table_kw = refined_kw
                        yield ("search_keywords", {"ssyqTableKeyword": table_kw})
            yield ("tool_call", {"name": name, "args": args})
            yield ("tool_result", {"name": name, "result": result})
            # 🔧 修改点：ToolMessage 仅写入压缩摘要，禁止 json.dumps 全量图谱/向量正文
            tool_body = _compress_tool_result(result) if isinstance(result, dict) else str(result)[:2000]
            lc_msgs.append(
                ToolMessage(
                    content=tool_body,
                    tool_call_id=tid,
                    name=name or None,
                )
            )
    else:
        logger.warning("[V6][LC] 工具轮次达到上限 %s", max_tool_rounds)

    lc_msgs.append(
        HumanMessage(
            content=(
                "请根据工具返回判断是否足以回答用户。"
                "然后只输出 **一对** `<THINK>`…`</THINK>` 与 **一对** `<CONTENT>`…`</CONTENT>`："
                "`<THINK>` 仅 1～2 句回答思路，禁止复述工具结果、命中条数或未命中原因；"
                "先流式写完思考再闭合；正文内禁止再出现 `<THINK>`/`<CONTENT>` 标签、禁止裸 `SOURCE_PICK` 或整段 JSON。"
                "禁止 `</translation>`、`<thinking>` 等其它标签。"
                "最后在 **唯一的** `</CONTENT>` 之后附上要求的 <<<SOURCE_PICK>>>…<<<END_SOURCE_PICK>>> 块（仅此一处）。"
            )
        )
    )

    final_parts: List[str] = []
    stream_buf = ""
    think_sent_from_final_stream = False
    last_visible_len = 0
    for chunk in lc_base.stream(lc_msgs):
        piece = _lc_text_content(getattr(chunk, "content", None))
        if not piece:
            continue
        final_parts.append(piece)
        stream_buf += piece
        if not think_sent_from_final_stream and (
            re.search(r"<\s*/\s*THINK\s*>", stream_buf, re.I)
            or re.search(r"<\s*/\s*thinking\s*>", stream_buf, re.I)
        ):
            stream_buf, tin = _pop_first_think_block(stream_buf)
            if tin:
                yield ("thinking", {"content": _znwd_sse_thinking_payload(tin + "\n")})
            think_sent_from_final_stream = True
            last_visible_len = len(_visible_for_answer_stream(stream_buf))
        vis = _visible_for_answer_stream(stream_buf)
        if len(vis) > last_visible_len:
            delta = vis[last_visible_len:]
            yield ("answer", {"content": _znwd_sanitize_visible_answer(delta)})
            last_visible_len = len(vis)

    if stream_buf and not think_sent_from_final_stream:
        stream_buf, tin = _pop_first_think_block(stream_buf)
        if tin:
            yield ("thinking", {"content": _znwd_sse_thinking_payload(tin + "\n")})
        vis = _visible_for_answer_stream(stream_buf)
        if len(vis) > last_visible_len:
            delta = vis[last_visible_len:]
            yield ("answer", {"content": _znwd_sanitize_visible_answer(delta)})
            last_visible_len = len(vis)

    raw = "".join(final_parts)
    thinking_text = _extract_think_inner_for_done(raw)
    thinking_text = _znwd_sse_thinking_payload(thinking_text) if thinking_text else ""
    merged_tail = raw.strip()
    clean_answer, raw_pick = _strip_and_parse_source_pick(merged_tail)
    clean_answer = _znwd_sanitize_visible_answer(clean_answer or "")
    chroma_nonempty = chroma_rows_nonempty(last_chroma)
    primary = (
        _merge_primary_source_with_chroma(raw_pick, (last_chroma or [])[:ZNWD_CHROMA_UI_MAX])
        if chroma_nonempty
        else None
    )
    if not clean_answer.strip():
        clean_answer = _znwd_sanitize_visible_answer(raw.strip())
    table_kw = build_znwd_table_keyword(question, last_neo) or table_kw
    if table_kw:
        yield ("search_keywords", {"ssyqTableKeyword": table_kw})
    _finalize_memory(user_id, session_id, question, clean_answer, client, model)
    yield (
        "done",
        {
            "fullAnswer": clean_answer,
            "fullThinking": thinking_text,
            "primaryChromaSource": primary,
            "ssyqTableKeyword": table_kw,
        },
    )


def llm_stream_single_v6(
    client,
    model,
    messages,
    driver,
    collection,
    llm_limits: Optional[Dict[str, Any]] = None,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    user_interest: Optional[Dict[str, Any]] = None,
) -> Generator[Tuple[str, dict], None, None]:
    """V6 入口：可选 Preflight（V4 plan + V5 快速直出），否则 LangChain Agent。"""
    if not client:
        yield ("error", {"message": "LLM 客户端未初始化"})
        return

    def _agent() -> Generator[Tuple[str, dict], None, None]:
        yield from _v6_agent_stream(
            client,
            model,
            messages,
            driver,
            collection,
            llm_limits,
            user_id=user_id,
            session_id=session_id,
            user_interest=user_interest,
        )

    if preflight_enabled():
        yield from run_preflight_or_agent(
            client,
            model,
            messages,
            driver,
            collection,
            llm_limits,
            user_id=user_id,
            session_id=session_id,
            user_interest=user_interest,
            agent_stream=_agent,
        )
    else:
        logger.info("[V6] ZNWD_V6_PREFLIGHT=0，直连 Agent")
        yield from _agent()
