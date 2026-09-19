"""
LLM 调用 token 预算：近似估算（中文友好）+ 保守余量；超长 user 内容自动分片多轮摘要后再请求。
供 JYLLM、ZNWD、FLGL 等统一调用侧使用。
"""
from __future__ import annotations

import copy
import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx
from openai import OpenAI

from config import Config

logger = logging.getLogger(__name__)

# 与服务商「真实 token」之间的安全余量（近似估本会偏小或偏大）
TOKEN_MARGIN = 384
MIN_COMPLETION_TOKENS = 256
# 偏保守：约每 1.5 字符记 1 token（中文混合）
CHARS_PER_TOKEN_EST = 1.5
# 仅当最长 user 段超过该字符数才做 map-reduce（避免 JYLLM 正常提示词被误压成空）
COMPRESS_MIN_USER_CHARS = int(os.environ.get("LLM_COMPRESS_MIN_USER_CHARS", "8000"))
# 估算 prompt+预留仍超过 context_window 的该比例时才压缩（默认 0.95，避免 8192 窗口下几乎必压）
COMPRESS_PROMPT_RATIO = float(os.environ.get("LLM_COMPRESS_PROMPT_RATIO", "0.95"))


def llm_context_compress_enabled() -> bool:
    """
    默认关闭自动压缩（本地 vLLM 流式摘要常无 delta，会压空 prompt）。
    显式 LLM_ENABLE_CONTEXT_COMPRESS=1 才开启；LLM_SKIP_CONTEXT_COMPRESS=1 强制关闭。
    """
    skip = (os.environ.get("LLM_SKIP_CONTEXT_COMPRESS") or "").strip().lower()
    if skip in ("1", "true", "yes", "on"):
        return False
    enable = (os.environ.get("LLM_ENABLE_CONTEXT_COMPRESS") or "").strip().lower()
    return enable in ("1", "true", "yes", "on")


def completion_reserve_tokens(requested_max_completion: int, model_max_output: Optional[int] = None) -> int:
    """为 completion 预留的 token，勿固定 +2048 以免 8k 窗口下永远触发压缩。"""
    cap_out = int(requested_max_completion or 2048)
    if model_max_output is not None:
        cap_out = min(cap_out, int(model_max_output))
    cap_out = max(MIN_COMPLETION_TOKENS, min(cap_out, 4096))
    return TOKEN_MARGIN + cap_out


def approx_token_count(text: str) -> int:
    if not text:
        return 0
    s = str(text)
    return max(1, int(len(s) / CHARS_PER_TOKEN_EST + 24))


def approx_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 32
    for m in messages:
        role = str(m.get("role") or "")
        c = m.get("content")
        if c is None:
            continue
        body = c if isinstance(c, str) else str(c)
        total += approx_token_count(role) + approx_token_count(body)
    return total


def completion_cap(
    context_window: int,
    prompt_tokens: int,
    requested_max_completion: int,
    model_max_output: Optional[int],
) -> int:
    cw = max(1024, int(context_window or 8192))
    mo_raw = model_max_output if model_max_output is not None else min(4096, cw // 2)
    mo = max(MIN_COMPLETION_TOKENS, min(int(mo_raw), cw - 512))
    room = cw - int(prompt_tokens) - TOKEN_MARGIN
    capped = min(int(requested_max_completion), mo, max(MIN_COMPLETION_TOKENS, room))
    out = max(MIN_COMPLETION_TOKENS, capped)
    if out < requested_max_completion:
        logger.info(
            "[llm_budget] max_completion %s -> %s (cw≈%s, prompt≈%s tok est)",
            requested_max_completion,
            out,
            cw,
            prompt_tokens,
        )
    return out


def probe_llm_connection(cfg: Dict[str, Any], model: str = "") -> Dict[str, Any]:
    """
    探测 LLM 是否可达（用于 JYLLM 离线排障）。
    返回 reachable、api_url、model、error、source 等。
    """
    base = (cfg.get("api_url") or "").strip() or (getattr(Config, "OPENAI_BASE_URL", None) or "").strip()
    model_name = (model or cfg.get("model_name") or getattr(Config, "OPENAI_MODEL", "") or "").strip()
    out: Dict[str, Any] = {
        "reachable": False,
        "api_url": base,
        "model": model_name,
        "source": cfg.get("source") or "unknown",
        "model_key": cfg.get("model_key") or "",
        "error": "",
        "hint": "",
    }
    if not base:
        out["error"] = "未配置 api_url（检查 .env 的 OPENAI_BASE_URL 或「模型管理」中的 LLM）"
        out["hint"] = "JYLLM 优先使用数据库 mxgl_models 里启用的 LLM，不是仅用 .env"
        return out
    client = openai_client_from_mxgl_item(cfg)
    if not client:
        out["error"] = "无法创建 OpenAI 客户端"
        return out
    try:
        if model_name:
            client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
        else:
            client.models.list()
        out["reachable"] = True
        return out
    except Exception as e:
        out["error"] = str(e)
        err = str(e).lower()
        if "connection" in err or "resolve" in err or "connect" in err:
            out["hint"] = (
                f"无法连接 {base}。请确认本机 vLLM/Ollama 已启动；"
                "并在「系统管理-模型管理」把该 LLM 的 api_url 改为 http://127.0.0.1:8000/v1（勿用 dashscope 等公网地址）。"
            )
        return out


def openai_client_from_mxgl_item(cfg: Dict[str, Any]) -> Optional[OpenAI]:
    """按 MXGL 解析出的 api_url/api_key 构建客户端；内网地址禁用系统代理（与 Config.create_openai_client 一致）。"""
    import httpx

    base = (cfg.get("api_url") or "").strip() or (getattr(Config, "OPENAI_BASE_URL", None) or "").strip()
    if not base:
        return None
    key = (cfg.get("api_key") or "").strip()
    if not key:
        eff = getattr(Config, "effective_openai_api_key", None)
        key = (eff() if callable(eff) else "") or (getattr(Config, "OPENAI_API_KEY", None) or "").strip()
    if not key and Config.is_local_openai_base_url(base):
        key = "EMPTY"
    if not key:
        return None
    try:
        trust_env = os.environ.get("OPENAI_HTTP_TRUST_ENV", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if Config.is_local_openai_base_url(base):
            trust_env = False
        timeout_sec = float(os.environ.get("OPENAI_HTTP_TIMEOUT", "180") or "180")
        http_client = httpx.Client(trust_env=trust_env, timeout=timeout_sec)
        kwargs: Dict[str, Any] = {
            "base_url": base.rstrip("/"),
            "api_key": key,
            "http_client": http_client,
            "max_retries": 0,
        }
        if key == "EMPTY":
            kwargs["default_headers"] = {"Authorization": ""}
        return OpenAI(**kwargs)
    except Exception as e:
        logger.warning("[llm_budget] OpenAI client init failed: %s", e)
        return None


def _as_openai_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        out: List[str] = []
        for part in v:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                t = part.get("text") or part.get("content")
                if t:
                    out.append(str(t))
            else:
                t = getattr(part, "text", None) or getattr(part, "content", None)
                if t:
                    out.append(str(t))
        return "".join(out)
    if isinstance(v, dict):
        return str(v.get("text") or v.get("content") or "")
    return str(v)


def _piece_from_stream_chunk(choice0: Any) -> str:
    # 部分 OpenAI 兼容实现（含旧版 vLLM）在 choice 上直接给 text
    direct = _as_openai_text(getattr(choice0, "text", None))
    if direct:
        return direct
    delta = getattr(choice0, "delta", None)
    if delta:
        content_piece = _as_openai_text(getattr(delta, "content", None))
        if not content_piece and isinstance(delta, dict):
            content_piece = _as_openai_text(delta.get("content"))
        if not content_piece:
            content_piece = _as_openai_text(getattr(delta, "reasoning_content", None))
        if not content_piece:
            content_piece = _as_openai_text(getattr(delta, "reasoning", None))
        if not content_piece and isinstance(delta, dict):
            content_piece = _as_openai_text(
                delta.get("reasoning_content") or delta.get("reasoning")
            )
        if content_piece:
            return content_piece
    msg = getattr(choice0, "message", None)
    if msg:
        return _as_openai_text(getattr(msg, "content", None)) or _as_openai_text(
            getattr(msg, "reasoning_content", None)
        )
    return ""


def _text_from_chat_completion(resp: Any) -> str:
    if not getattr(resp, "choices", None):
        return ""
    c0 = resp.choices[0]
    direct = _as_openai_text(getattr(c0, "text", None))
    if direct:
        return direct
    msg = getattr(c0, "message", None)
    if not msg:
        return ""
    return _as_openai_text(getattr(msg, "content", None)) or _as_openai_text(
        getattr(msg, "reasoning_content", None)
    ) or _as_openai_text(getattr(msg, "reasoning", None))


def _is_transient_llm_error(exc: Exception) -> bool:
    err_s = str(exc).lower()
    return any(
        x in err_s
        for x in (
            "502",
            "503",
            "504",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "connection reset",
            "connection aborted",
            "timed out",
            "timeout",
        )
    )


def iter_chat_completion_text(
    client: OpenAI,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> Iterator[str]:
    """
    流式拉取正文；若兼容服务返回 200 但无 delta（常见 vLLM 配置），自动改非流式补全。
    502/503/504 等瞬时错误自动重试最多 3 次。
    """
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            yield from _iter_chat_completion_text_once(
                client,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return
        except Exception as e:
            last_err = e
            if attempt < 2 and _is_transient_llm_error(e):
                wait_s = 1.5 * (attempt + 1)
                logger.warning(
                    "[llm_budget] transient LLM error attempt=%s/%s model=%s: %s; retry in %.1fs",
                    attempt + 1,
                    3,
                    model,
                    e,
                    wait_s,
                )
                time.sleep(wait_s)
                continue
            raise
    if last_err:
        raise last_err


def _iter_chat_completion_text_once(
    client: OpenAI,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float,
    max_tokens: int,
) -> Iterator[str]:
    got = False
    stream_err: Optional[Exception] = None
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            piece = _piece_from_stream_chunk(chunk.choices[0])
            if piece:
                got = True
                yield piece
    except Exception as e:
        stream_err = e
        logger.warning("[llm_budget] stream 请求失败 model=%s: %s，尝试非流式", model, e)
    if got:
        return
    if not stream_err:
        logger.warning(
            "[llm_budget] stream 无正文 delta，改用非流式 model=%s max_tokens=%s",
            model,
            max_tokens,
        )
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    text = _text_from_chat_completion(resp)
    if text:
        yield text
    elif stream_err:
        raise stream_err
    else:
        logger.error(
            "[llm_budget] 非流式也无正文 model=%s choices=%s",
            model,
            len(getattr(resp, "choices", None) or []),
        )


def split_text_into_paragraph_chunks(text: str, max_chars_per_chunk: int) -> List[str]:
    if not text:
        return []
    parts = text.split("\n\n")
    chunks: List[str] = []
    buf = ""
    for p in parts:
        seg = p if not buf else "\n\n" + p
        if len(buf) + len(seg) <= max_chars_per_chunk:
            buf += seg
        else:
            if buf.strip():
                chunks.append(buf)
            buf = p
    if buf.strip():
        chunks.append(buf)
    if not chunks and text.strip():
        return [text[:max_chars_per_chunk]]
    return chunks


def _summarize_one_chunk(
    client: OpenAI,
    model: str,
    chunk: str,
    temperature: float,
    max_out: int,
) -> str:
    msgs = [
        {
            "role": "system",
            "content": "你是技术编辑。将用户给出的片段压缩为结构化要点列表，保留术语、标准号、设备/环境/项目名称，不编造事实。",
        },
        {"role": "user", "content": chunk[:120000]},
    ]
    resp = client.chat.completions.create(
        model=model,
        messages=msgs,
        temperature=temperature,
        max_tokens=max_out,
    )
    return (resp.choices[0].message.content or "").strip()


def _as_text_from_delta(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        out: List[str] = []
        for part in v:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                t = part.get("text") or part.get("content")
                if t:
                    out.append(str(t))
            else:
                t = getattr(part, "text", None) or getattr(part, "content", None)
                if t:
                    out.append(str(t))
        return "".join(out)
    if isinstance(v, dict):
        return str(v.get("text") or v.get("content") or "")
    return str(v)


def _summarize_one_chunk_stream(
    client: OpenAI,
    model: str,
    chunk: str,
    temperature: float,
    max_out: int,
) -> Iterator[str]:
    """流式摘要；无增量时回退非流式，避免「只调一次 LLM 且无正文」。"""
    msgs = [
        {
            "role": "system",
            "content": "你是技术编辑。将用户给出的片段压缩为结构化要点列表，保留术语、标准号、设备/环境/项目名称，不编造事实。",
        },
        {"role": "user", "content": chunk[:120000]},
    ]
    acc = ""
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_out,
            stream=True,
        )
        for part in stream:
            if not getattr(part, "choices", None):
                continue
            choice0 = part.choices[0]
            delta = getattr(choice0, "delta", None)
            if not delta:
                continue
            content_piece = _as_text_from_delta(getattr(delta, "content", None))
            if not content_piece and isinstance(delta, dict):
                content_piece = _as_text_from_delta(delta.get("content"))
            reasoning_piece = ""
            if not content_piece:
                reasoning_piece = _as_text_from_delta(getattr(delta, "reasoning_content", None))
                if not reasoning_piece:
                    reasoning_piece = _as_text_from_delta(getattr(delta, "reasoning", None))
                if not reasoning_piece and isinstance(delta, dict):
                    reasoning_piece = _as_text_from_delta(
                        delta.get("reasoning_content") or delta.get("reasoning")
                    )
            piece = content_piece or reasoning_piece
            if piece:
                acc += piece
                yield piece
    except Exception as e:
        logger.warning("[llm_budget] summarize stream failed, fallback non-stream: %s", e)
    if not acc.strip():
        try:
            fb = _summarize_one_chunk(client, model, chunk, temperature, max_out)
            if fb:
                yield fb
        except Exception as e2:
            logger.warning("[llm_budget] summarize fallback failed: %s", e2)


def iter_compress_text_map_reduce(
    client: OpenAI,
    model: str,
    text: str,
    *,
    context_window: int,
    temperature: float = 0.2,
) -> Iterator[Tuple[str, Any]]:
    """
    分片 map-reduce 压缩，流式输出每个摘要 token。
    Yields:
      ("segment_start", {"index": int, "total": int, "phase": str})  phase 为 map 或 reduce
      ("delta", str)
      ("merged", str)  # 最后一项，完整压缩结果
    """
    if not text or not client:
        yield ("merged", text)
        return
    budget_chars = max(4000, int(context_window * 0.32 * CHARS_PER_TOKEN_EST))
    max_out = min(1024, max(MIN_COMPLETION_TOKENS, context_window // 8))
    chunk_size = max(3500, budget_chars // 2)
    chunks = split_text_into_paragraph_chunks(text, max_chars_per_chunk=chunk_size)
    summaries: List[str] = []
    for i, ch in enumerate(chunks):
        yield ("segment_start", {"index": i + 1, "total": len(chunks), "phase": "map"})
        acc = ""
        try:
            chunk_input = f"[片段 {i + 1}/{len(chunks)}]\n\n" + ch
            for delta in _summarize_one_chunk_stream(client, model, chunk_input, temperature, max_out):
                acc += delta
                yield ("delta", delta)
            summaries.append(acc.strip() or ch[:2500])
        except Exception as e:
            logger.warning("[llm_budget] chunk %s summarize failed: %s", i, e)
            summaries.append(ch[:2500])
    merged = "\n\n---\n\n".join(summaries)
    if approx_token_count(merged) > context_window * 0.42:
        yield ("segment_start", {"index": 1, "total": 1, "phase": "reduce"})
        acc2 = ""
        reduce_user = "以下为多段摘要，请合并压缩为一段连贯要点（保留关键事实）：\n\n" + merged[:80000]
        try:
            for delta in _summarize_one_chunk_stream(client, model, reduce_user, temperature, max_out):
                acc2 += delta
                yield ("delta", delta)
            merged = acc2.strip() or merged[: budget_chars * 2]
        except Exception as e:
            logger.warning("[llm_budget] reduce pass failed: %s", e)
            merged = merged[: budget_chars * 2]
    yield ("merged", merged)


def compress_text_map_reduce(
    client: OpenAI,
    model: str,
    text: str,
    *,
    context_window: int,
    temperature: float = 0.2,
) -> str:
    """分片多轮摘要，将长文本压到可控长度。"""
    out = ""
    for kind, payload in iter_compress_text_map_reduce(
        client, model, text, context_window=context_window, temperature=temperature
    ):
        if kind == "merged":
            out = str(payload)
            break
    return out


def iter_maybe_compress_messages_for_context(
    messages: List[Dict[str, Any]],
    *,
    client: OpenAI,
    model: str,
    context_window: int,
    temperature: float = 0.2,
    reserve_tokens: Optional[int] = None,
    requested_max_completion: int = 2048,
    model_max_output: Optional[int] = None,
) -> Iterator[Tuple[str, Any]]:
    """
    若 prompt 过大则对最长 user 消息做 map-reduce 压缩（最多 3 轮）。
    Yields:
      ("segment_start", dict) / ("delta", str)  — 来自底层压缩流
      ("messages", List[dict]) — 最终可用 messages（每轮压缩结束或无需压缩时）
    """
    msgs: List[Dict[str, Any]] = copy.deepcopy(messages)
    if not llm_context_compress_enabled():
        yield ("messages", msgs)
        return

    reserve = (
        int(reserve_tokens)
        if reserve_tokens is not None
        else completion_reserve_tokens(requested_max_completion, model_max_output)
    )
    cw = max(1024, int(context_window or 8192))
    prompt_budget = int(cw * COMPRESS_PROMPT_RATIO)

    for _round in range(3):
        pt = approx_messages_tokens(msgs)
        if pt + reserve <= prompt_budget:
            yield ("messages", msgs)
            return
        longest_i = -1
        longest_len = 0
        for i, m in enumerate(msgs):
            if str(m.get("role")) != "user":
                continue
            c = m.get("content") or ""
            if not isinstance(c, str):
                c = str(c)
            if len(c) > longest_len:
                longest_len = len(c)
                longest_i = i
        if longest_i < 0 or longest_len < COMPRESS_MIN_USER_CHARS:
            logger.warning(
                "[llm_budget] prompt≈%s tok 仍超预算 cw=%s budget=%s，但 user 段<%s 字符，跳过压缩保留原 prompt",
                pt,
                cw,
                prompt_budget,
                COMPRESS_MIN_USER_CHARS,
            )
            yield ("messages", msgs)
            return
        orig = str(msgs[longest_i].get("content") or "")
        new_text = ""
        for kind, payload in iter_compress_text_map_reduce(
            client, model, orig, context_window=context_window, temperature=temperature
        ):
            if kind in ("segment_start", "delta"):
                yield (kind, payload)
            elif kind == "merged":
                new_text = str(payload)
        cap = int(context_window * CHARS_PER_TOKEN_EST * 0.85)
        merged = (new_text or "").strip()
        if len(merged) < min(400, int(len(orig) * 0.08)):
            logger.warning(
                "[llm_budget] 压缩结果过短(%s chars)，保留原 user 截断 len=%s",
                len(merged),
                min(len(orig), max(4000, cap)),
            )
            merged = orig[: max(4000, cap)]
        msgs[longest_i] = {**msgs[longest_i], "content": merged[: max(4000, cap)]}
        logger.info(
            "[llm_budget] map_reduce round=%s user_msg_idx=%s len %s -> %s",
            _round,
            longest_i,
            longest_len,
            len(merged),
        )
    yield ("messages", msgs)


def maybe_compress_messages_for_context(
    messages: List[Dict[str, Any]],
    *,
    client: OpenAI,
    model: str,
    context_window: int,
    temperature: float = 0.2,
    reserve_tokens: Optional[int] = None,
    requested_max_completion: int = 2048,
    model_max_output: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """若估算 prompt 仍过大，对最长的 user 消息做 map-reduce 压缩（最多 3 轮）。"""
    for kind, payload in iter_maybe_compress_messages_for_context(
        messages,
        client=client,
        model=model,
        context_window=context_window,
        temperature=temperature,
        reserve_tokens=reserve_tokens,
        requested_max_completion=requested_max_completion,
        model_max_output=model_max_output,
    ):
        if kind == "messages":
            return payload  # type: ignore[return-value]
    return copy.deepcopy(messages)


def drain_messages_after_context_compress(
    messages: List[Dict[str, Any]],
    *,
    client: OpenAI,
    model: str,
    context_window: int,
    temperature: float = 0.2,
    requested_max_completion: int = 2048,
    model_max_output: Optional[int] = None,
    on_compress_event: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    消费压缩迭代器并返回最终 messages；保证一定落到主 LLM 调用。
    on_compress_event(kind, payload) 可选，用于转发 segment_start/delta。
    """
    final: Optional[List[Dict[str, Any]]] = None
    for kind, payload in iter_maybe_compress_messages_for_context(
        messages,
        client=client,
        model=model,
        context_window=context_window,
        temperature=temperature,
        requested_max_completion=requested_max_completion,
        model_max_output=model_max_output,
    ):
        if kind == "messages":
            final = payload  # type: ignore[assignment]
        elif on_compress_event is not None:
            on_compress_event(kind, payload)
    return final if final is not None else copy.deepcopy(messages)
