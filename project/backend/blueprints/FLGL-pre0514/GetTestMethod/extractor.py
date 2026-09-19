import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.config import Config

logger = logging.getLogger(__name__)

from .parser import collect_tables_for_sections, get_sections_by_ids
from .prompts import (
    build_detail_calibration_prompt,
    build_detail_partial_prompt,
    build_detail_prompt,
    build_discovery_prompt,
)


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_json_loads(raw_output: str, default_value: Any) -> Any:
    if not raw_output:
        return default_value

    text = _strip_code_fences(raw_output)

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and start < end:
        candidate = text[start:end + 1]
        candidate = re.sub(r",\s*}", "}", candidate)
        candidate = re.sub(r",\s*]", "]", candidate)
        try:
            return json.loads(candidate)
        except Exception:
            pass

    return default_value


def _deduplicate_str_list(items: List[Any]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
    return result


def _clip_text(text: str, max_chars: int = 12000) -> str:
    text = (text or "").strip()
    return text[:max_chars]


def _get_runtime_client():
    client = getattr(Config, "OPENAI_CLIENT", None)
    model = getattr(Config, "OPENAI_MODEL", None)

    if client is None:
        raise RuntimeError("Config.OPENAI_CLIENT 未初始化，请先完成运行时初始化")
    if not model:
        raise RuntimeError("Config.OPENAI_MODEL 未配置，请检查 backend/config.py")

    return client, model


def _llm_max_completion_tokens(override: Optional[int] = None) -> int:
    if override is not None:
        return max(256, int(override))
    raw = getattr(Config, "LLM_MAX_COMPLETION_TOKENS", None)
    if raw is None:
        return 3072
    return max(256, min(int(raw), 8192))


def _clamp_completion_tokens(prompt: str, requested_max: int) -> int:
    """
    按「上下文总长」压缩 max_tokens，避免 8k 等小上下文模型出现
    input_tokens + max_tokens > context 的 400 错误（服务商计数略不同于字符估算，留余量）。
    """
    try:
        requested_max = max(128, int(requested_max))
    except (TypeError, ValueError):
        requested_max = 256
    raw_ctx = getattr(Config, "LLM_CONTEXT_TOKENS", None)
    try:
        ctx = int(raw_ctx) if raw_ctx is not None else 8192
    except (TypeError, ValueError):
        ctx = 8192
    ctx = max(2048, min(ctx, 200_000))
    safety = 160
    # 中文偏多时 token/字符比偏高，用 //2 并加 slack，偏保守
    input_est = max(512, len(prompt or "") // 2 + 320)
    room = ctx - input_est - safety
    capped = min(requested_max, max(256, room))
    if capped < requested_max:
        logger.warning(
            "[GetTestMethod] max_completion_tokens %s -> %s（上下文上限≈%s，按字符估算输入≈%s tokens）",
            requested_max,
            capped,
            ctx,
            input_est,
        )
    return capped


def _prompt_char_cap_vs_context(config_max: int, reserve_out_tokens: int, *, label: str = "prompt") -> int:
    """
    按上下文与「预留输出 tokens」限制单次 prompt 字符数，与 _clamp_completion_tokens（len//2+320）对齐，
    避免 max_tokens 被压得过低导致 JSON 截断（试验条件/过程/评定为空）。
    """
    try:
        ctx = int(getattr(Config, "LLM_CONTEXT_TOKENS", None) or 8192)
    except (TypeError, ValueError):
        ctx = 8192
    ctx = max(2048, min(ctx, 200_000))
    try:
        rot = int(reserve_out_tokens)
    except (TypeError, ValueError):
        rot = 3072
    want_out = max(512, min(rot, ctx // 2))
    safety_toks = 256
    input_tok_budget = max(512, ctx - want_out - safety_toks)
    slack = 320
    char_cap = max(3500, int(max(256, input_tok_budget - slack) * 2))
    cfg = max(3000, int(config_max))
    out = min(cfg, char_cap)
    if out < cfg:
        logger.info(
            "[GetTestMethod] %s 字符上限按上下文收紧：%s -> %s（context=%s，预留输出≈%s tokens）",
            label,
            cfg,
            out,
            ctx,
            want_out,
        )
    return out


def _detail_prompt_budget_chars(config_max: int) -> int:
    try:
        r = int(getattr(Config, "LLM_MAX_COMPLETION_TOKENS", None) or 3072)
    except (TypeError, ValueError):
        r = 3072
    return _prompt_char_cap_vs_context(config_max, r, label="细粒度单次")


def _discovery_prompt_budget_chars(config_max: int) -> int:
    """发现阶段输出 JSON 通常短于细粒度，预留输出略保守即可多放些目录/预览。"""
    try:
        r = int(getattr(Config, "LLM_MAX_COMPLETION_TOKENS", None) or 3072)
    except (TypeError, ValueError):
        r = 3072
    r = min(r, 2400)
    return _prompt_char_cap_vs_context(config_max, max(768, r), label="发现阶段")


def _call_llm_json(
    prompt: str,
    max_tokens: Optional[int] = None,
    temperature: float = 0.1,
) -> Dict[str, Any]:
    client, model = _get_runtime_client()
    mt = _clamp_completion_tokens(prompt, _llm_max_completion_tokens(max_tokens))

    logger.info("[GetTestMethod][调试] LLM 请求 max_tokens=%d, prompt 长度=%d 字符", mt, len(prompt))

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=mt,
    )
    choice = resp.choices[0] if resp and resp.choices else None
    content = (choice.message.content or "").strip() if choice and choice.message else ""
    fr = getattr(choice, "finish_reason", None) if choice else None
    if fr == "length":
        logger.warning(
            "[GetTestMethod] LLM 输出因长度截断（finish_reason=length），method JSON 可能不完整，请减小单次 prompt 或提高上下文"
        )

    logger.info("[GetTestMethod][调试] LLM 原始输出(前800字): %s", content[:800])

    result = _safe_json_loads(content or "", {})

    # 调试：打印解析后 JSON 中 sytj/sygc/jgpd 的 text 字段
    for key in ("sytj", "sygc", "jgpd"):
        block = result.get(key, {})
        if isinstance(block, dict):
            logger.info("[GetTestMethod][调试] 解析后 %s.text = %s", key, str(block.get("text", ""))[:200])

    return result


def _normalize_terms(terms: Any) -> List[Dict[str, str]]:
    if not isinstance(terms, list):
        return []
    seen = set()
    results = []
    for item in terms:
        if not isinstance(item, dict):
            continue
        term = re.sub(r"^\d+(\.\d+)*\s*", "", str(item.get("term", "")).strip())
        definition = re.sub(r"^\d+(\.\d+)*\s*", "", str(item.get("definition", "")).strip())
        if not term or not definition:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append({"term": term, "definition": definition})
    return results


def _normalize_recall_slot(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    keywords = value.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = []
    reason = str(value.get("reason", "")).strip()
    return {
        "keywords": _deduplicate_str_list(keywords),
        "reason": reason,
    }


def _empty_recall_plan() -> Dict[str, Dict[str, Any]]:
    return {
        "equipment": {"keywords": [], "reason": ""},
        "environment": {"keywords": [], "reason": ""},
        "test_project": {"keywords": [], "reason": ""},
        "indicator": {"keywords": [], "reason": ""},
    }


def _normalize_recall_plan(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        value = {}
    return {
        "equipment": _normalize_recall_slot(value.get("equipment")),
        "environment": _normalize_recall_slot(value.get("environment")),
        "test_project": _normalize_recall_slot(value.get("test_project")),
        "indicator": _normalize_recall_slot(value.get("indicator")),
    }


def _normalize_text_block(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    text = str(value.get("text", "")).strip() or "文档未明确说明"
    tables_html = value.get("tables_html", [])
    formulas_latex = value.get("formulas_latex", [])
    if not isinstance(tables_html, list):
        tables_html = []
    if not isinstance(formulas_latex, list):
        formulas_latex = []
    return {
        "text": text,
        "tables_html": [str(x) for x in tables_html if str(x).strip()],
        "formulas_latex": [str(x) for x in formulas_latex if str(x).strip()],
    }


_SYTJ_TITLE_HINTS = (
    "试验条件", "条件", "环境条件", "设备", "仪器", "安装", "温度", "湿度", "压力", "持续时间", "允差",
)
_SYGC_TITLE_HINTS = (
    "试验过程", "试验程序", "试验步骤", "步骤", "准备", "实施", "中断", "恢复", "流程", "操作",
)
_JGPD_TITLE_HINTS = (
    "结果评定", "结果分析", "判定", "判据", "合格", "不合格", "试后检查", "试验报告", "结果",
)

_SYTJ_TEXT_HINTS = ("条件", "设备", "仪器", "温度", "湿度", "压力", "允许偏差", "持续时间")
_SYGC_TEXT_HINTS = ("步骤", "程序", "过程", "准备", "试验开始", "中断", "恢复", "操作")
_JGPD_TEXT_HINTS = ("结果", "评定", "判定", "判据", "合格", "不合格", "性能", "试后")


def _hint_score(text: str, hints: Tuple[str, ...]) -> int:
    s = str(text or "")
    return sum(1 for h in hints if h in s)


def _classify_section_bucket(section: Dict[str, Any]) -> str:
    title = str(section.get("title", "") or "")
    content = str(section.get("text", "") or "")

    t_sytj = _hint_score(title, _SYTJ_TITLE_HINTS)
    t_sygc = _hint_score(title, _SYGC_TITLE_HINTS)
    t_jgpd = _hint_score(title, _JGPD_TITLE_HINTS)
    if max(t_sytj, t_sygc, t_jgpd) > 0:
        if t_sytj >= t_sygc and t_sytj >= t_jgpd:
            return "sytj"
        if t_sygc >= t_sytj and t_sygc >= t_jgpd:
            return "sygc"
        return "jgpd"

    c_sytj = _hint_score(content, _SYTJ_TEXT_HINTS)
    c_sygc = _hint_score(content, _SYGC_TEXT_HINTS)
    c_jgpd = _hint_score(content, _JGPD_TEXT_HINTS)
    if max(c_sytj, c_sygc, c_jgpd) == 0:
        return "none"
    if c_sytj >= c_sygc and c_sytj >= c_jgpd:
        return "sytj"
    if c_sygc >= c_sytj and c_sygc >= c_jgpd:
        return "sygc"
    return "jgpd"


def _new_text_block() -> Dict[str, Any]:
    return {"text": "", "tables_html": [], "formulas_latex": []}


def _rule_extract_triplet(
    related_sections: List[Dict[str, Any]],
    related_tables: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    out = {
        "sytj": _new_text_block(),
        "sygc": _new_text_block(),
        "jgpd": _new_text_block(),
    }
    section_bucket_map: Dict[str, str] = {}

    for sec in related_sections or []:
        if not isinstance(sec, dict):
            continue
        bucket = _classify_section_bucket(sec)
        sid = str(sec.get("section_id", "") or "")
        if sid:
            section_bucket_map[sid] = bucket
        if bucket == "none":
            continue
        sec_text = str(sec.get("text", "") or "").strip()
        if not sec_text:
            continue
        prev = out[bucket]["text"]
        out[bucket]["text"] = f"{prev}\n\n{sec_text}".strip() if prev else sec_text

    for tb in related_tables or []:
        if not isinstance(tb, dict):
            continue
        sid = str(tb.get("section_id", "") or "")
        html = str(tb.get("html", "") or "").strip()
        if not html:
            continue
        bucket = section_bucket_map.get(sid, "none")
        if bucket in ("sytj", "sygc", "jgpd"):
            out[bucket]["tables_html"].append(html)

    for key in ("sytj", "sygc", "jgpd"):
        block = out[key]
        if not block["text"]:
            block["text"] = "文档未明确说明"
    return out


def _build_rule_based_method_seed(
    candidate: Dict[str, Any],
    document_class: str,
    triplet: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id"),
        "class": document_class or "未判定",
        "section_ids": list(candidate.get("section_ids", []) or []),
        "name": candidate.get("candidate_title", "") or "未命名方法",
        "type": candidate.get("candidate_type_hint", ""),
        "description": candidate.get("summary_hint", "") or "基于规则初步抽取，待 LLM 校准",
        "recall_plan": _empty_recall_plan(),
        "sytj": _normalize_text_block(triplet.get("sytj")),
        "sygc": _normalize_text_block(triplet.get("sygc")),
        "jgpd": _normalize_text_block(triplet.get("jgpd")),
    }


def _normalize_method_candidate(item: Any, fallback_index: int = 1) -> Dict[str, Any]:
    if not isinstance(item, dict):
        item = {}
    candidate_id = str(item.get("candidate_id", "")).strip() or f"M{fallback_index:03d}"
    candidate_title = str(item.get("candidate_title", "")).strip() or f"方法候选{fallback_index}"
    candidate_type_hint = str(item.get("candidate_type_hint", "")).strip()
    summary_hint = str(item.get("summary_hint", "")).strip()
    section_ids = item.get("section_ids", [])
    if not isinstance(section_ids, list):
        section_ids = []
    reason = str(item.get("reason", "")).strip()
    return {
        "candidate_id": candidate_id,
        "candidate_title": candidate_title,
        "candidate_type_hint": candidate_type_hint,
        "summary_hint": summary_hint,
        "section_ids": _deduplicate_str_list(section_ids),
        "reason": reason,
    }


def _normalize_method_detail(item: Any, candidate: Dict[str, Any], document_class: str) -> Dict[str, Any]:
    if not isinstance(item, dict):
        item = {}
    section_ids = item.get("section_ids", [])
    if not isinstance(section_ids, list):
        section_ids = candidate.get("section_ids", []) or []
    return {
        "candidate_id": str(item.get("candidate_id", "")).strip() or candidate["candidate_id"],
        "class": str(item.get("class", "")).strip() or document_class or "未判定",
        "section_ids": _deduplicate_str_list(section_ids) or list(candidate.get("section_ids", [])),
        "name": str(item.get("name", "")).strip() or candidate["candidate_title"],
        "type": str(item.get("type", "")).strip() or candidate.get("candidate_type_hint", ""),
        "description": str(item.get("description", "")).strip() or candidate.get("summary_hint", "") or "文档未明确说明",
        "recall_plan": _normalize_recall_plan(item.get("recall_plan", _empty_recall_plan())),
        "sytj": _normalize_text_block(item.get("sytj")),
        "sygc": _normalize_text_block(item.get("sygc")),
        "jgpd": _normalize_text_block(item.get("jgpd")),
    }


def _prepare_tables_for_prompt(
    tables: List[Dict[str, Any]],
    html_max: int,
    max_count: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in (tables or [])[: max(1, max_count)]:
        if not isinstance(t, dict):
            continue
        row = dict(t)
        h = str(row.get("html", "") or "")
        if len(h) > html_max:
            row["html"] = h[:html_max] + "\n<!-- truncated -->"
        out.append(row)
    return out


def _filter_tables_by_section_ids(tables: List[Dict[str, Any]], section_ids: List[Any]) -> List[Dict[str, Any]]:
    if not section_ids:
        return []
    sid = {str(x) for x in section_ids if x is not None}
    return [t for t in (tables or []) if str(t.get("section_id", "")) in sid]


def _trim_catalogue_for_chars(catalogue: Any, max_json_chars: int) -> Any:
    """章节目录可能极大，按 JSON 字符上限截断列表前缀。"""
    max_json_chars = max(400, int(max_json_chars))
    if catalogue is None:
        return []
    if not isinstance(catalogue, list):
        raw = json.dumps(catalogue, ensure_ascii=False)
        return catalogue if len(raw) <= max_json_chars else []
    if not catalogue:
        return []
    lo, hi = 0, len(catalogue)
    best: List[Any] = []
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = catalogue[:mid]
        if len(json.dumps(cand, ensure_ascii=False)) <= max_json_chars:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best if best else [catalogue[0]]


def _full_detail_prompt_len(
    file_name: str,
    document_class: str,
    candidate: Dict[str, Any],
    secs: List[Dict[str, Any]],
    tabs: List[Dict[str, Any]],
) -> int:
    return len(
        build_detail_prompt(
            file_name=file_name,
            document_class=document_class,
            method_candidate=candidate,
            related_sections=secs,
            related_tables=tabs,
        )
    )


def _pack_detail_chunks_by_prompt_size(
    file_name: str,
    document_class: str,
    candidate: Dict[str, Any],
    related_sections_for_prompt: List[Dict[str, Any]],
    tables_prepared: List[Dict[str, Any]],
    section_cap: int,
    max_prompt_chars: int,
) -> List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """按完整 build_detail_prompt 字符数贪心分包：章节正文 + 表格逐张装入，超限则新起一批（含表格续节）。"""
    section_cap = max(400, int(section_cap))
    base_len = _full_detail_prompt_len(file_name, document_class, candidate, [], [])
    max_prompt_chars = min(22000, int(max_prompt_chars))
    need = base_len + 800
    if max_prompt_chars < need:
        logger.warning(
            "[GetTestMethod] 细粒度 prompt 预算 %s 小于模板基线+余量 %s，将依赖正文截断分包（勿强行抬高上限以免 LLM 输出被截断）",
            max_prompt_chars,
            need,
        )

    def full_len(s: List[Dict[str, Any]], t: List[Dict[str, Any]]) -> int:
        return _full_detail_prompt_len(file_name, document_class, candidate, s, t)

    def shrink_entry_text(entry: Dict[str, Any], s_list: List[Dict[str, Any]], t_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        e = dict(entry)
        txt = str(e.get("text", "") or "")
        while txt and full_len(s_list + [e], t_list) > max_prompt_chars:
            txt = txt[: max(120, int(len(txt) * 0.72))]
            e = {**e, "text": txt or "（正文过长已截断）"}
        return e

    chunks: List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = []
    cur_s: List[Dict[str, Any]] = []
    cur_t: List[Dict[str, Any]] = []

    def flush() -> None:
        if cur_s or cur_t:
            chunks.append((list(cur_s), list(cur_t)))
        cur_s.clear()
        cur_t.clear()

    tables_by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for t in tables_prepared:
        sid = str(t.get("section_id", "") or "")
        tables_by_sid.setdefault(sid, []).append(t)

    for sec in related_sections_for_prompt:
        sid_key = str(sec.get("section_id", "") or "")
        title = str(sec.get("title", "") or "")
        text_clip = _clip_text(str(sec.get("text", "") or ""), max_chars=section_cap)
        entry = {
            "section_id": sec.get("section_id"),
            "title": title,
            "start_page": sec.get("start_page"),
            "end_page": sec.get("end_page"),
            "text": text_clip,
        }
        entry = shrink_entry_text(entry, cur_s, cur_t)
        if full_len(cur_s + [entry], cur_t) > max_prompt_chars:
            flush()
            entry = {
                "section_id": sec.get("section_id"),
                "title": title,
                "start_page": sec.get("start_page"),
                "end_page": sec.get("end_page"),
                "text": text_clip,
            }
            entry = shrink_entry_text(entry, cur_s, cur_t)
        cur_s.append(entry)

        queue = tables_by_sid.get(sid_key, [])
        while queue:
            tb = queue[0]
            cand = dict(tb)
            if full_len(cur_s, cur_t + [cand]) <= max_prompt_chars:
                cur_t.append(queue.pop(0))
                continue
            flush()
            cont = {
                "section_id": sec.get("section_id"),
                "title": f"{title}（表格续）",
                "start_page": sec.get("start_page"),
                "end_page": sec.get("end_page"),
                "text": "（同节正文见上一批；本批为该节表格/内容接续。）",
            }
            cur_s.append(cont)
            shrink_guard = 0
            while full_len(cur_s, cur_t + [cand]) > max_prompt_chars and shrink_guard < 24:
                shrink_guard += 1
                h = str(cand.get("html", "") or "")
                if len(h) > 80:
                    cand = {**cand, "html": h[: max(80, len(h) // 2)] + "\n<!-- truncated -->"}
                else:
                    cand = {**cand, "html": "<!-- table omitted -->"}
                    break
            cur_t.append(queue.pop(0))

    flush()
    if not chunks:
        chunks = [([], [])]
    return chunks


def _skeleton_for_merge(method: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_id": method.get("candidate_id"),
        "name": method.get("name"),
        "type": method.get("type"),
        "class": method.get("class"),
        "description": _clip_text(str(method.get("description", "")), max_chars=1200),
        "section_ids": method.get("section_ids"),
        "recall_plan": method.get("recall_plan"),
    }


def _merge_triplet_patch_into_method(base: Dict[str, Any], patch: Any) -> None:
    if not isinstance(patch, dict):
        return
    for key in ("sytj", "sygc", "jgpd"):
        if key not in patch:
            continue
        pb = _normalize_text_block(patch[key])
        bb = base.setdefault(key, {"text": "", "tables_html": [], "formulas_latex": []})
        pt = str(pb.get("text", "")).strip()
        if pt and pt != "文档未明确说明":
            bt = str(bb.get("text", "")).strip()
            if bt and bt != "文档未明确说明":
                bb["text"] = bt + "\n\n" + pt
            else:
                bb["text"] = pt
        for arr_key in ("tables_html", "formulas_latex"):
            add = pb.get(arr_key) or []
            if not isinstance(add, list):
                continue
            exist = bb.setdefault(arr_key, [])
            if not isinstance(exist, list):
                exist = []
                bb[arr_key] = exist
            seen = {str(x) for x in exist}
            for x in add:
                sx = str(x)
                if sx.strip() and sx not in seen:
                    exist.append(x)
                    seen.add(sx)


def discover_document_structure(parsed_doc: Dict[str, Any], preview_chars: Optional[int] = None) -> Dict[str, Any]:
    max_dp = int(getattr(Config, "DISCOVERY_MAX_PROMPT_CHARS", 11000) or 11000)
    cat_max = int(getattr(Config, "DISCOVERY_CATALOGUE_MAX_CHARS", 5000) or 5000)
    if preview_chars is None:
        preview_chars = int(getattr(Config, "DISCOVERY_PREVIEW_MAX_CHARS", 6000) or 6000)

    file_name = parsed_doc["file_name"]
    empty_discovery = len(
        build_discovery_prompt(file_name=file_name, section_catalogue=[], document_preview="")
    )
    max_dp = max(max_dp, empty_discovery + 400)
    max_dp = _discovery_prompt_budget_chars(max_dp)
    catalogue = _trim_catalogue_for_chars(parsed_doc.get("section_catalogue", []), cat_max)
    preview = _clip_text(parsed_doc.get("full_text", ""), max_chars=max(500, preview_chars))

    guard = 0
    while guard < 80:
        guard += 1
        prompt = build_discovery_prompt(
            file_name=file_name,
            section_catalogue=catalogue,
            document_preview=preview,
        )
        if len(prompt) <= max_dp:
            break
        if len(preview) > 400:
            preview = preview[: int(len(preview) * 0.82)]
            continue
        if isinstance(catalogue, list) and len(catalogue) > 1:
            catalogue = catalogue[: max(1, len(catalogue) * 2 // 3)]
            continue
        preview = preview[: max(200, len(preview) // 2)]
        if len(preview) <= 200:
            break

    prompt = build_discovery_prompt(
        file_name=file_name,
        section_catalogue=catalogue,
        document_preview=preview,
    )
    logger.info(
        "[GetTestMethod] 发现阶段 prompt 约 %d 字符（上限 %d）",
        len(prompt),
        max_dp,
    )
    raw = _call_llm_json(prompt=prompt, temperature=0.1)

    document_class = str(raw.get("document_class", "")).strip() or "未判定"
    document_summary = str(raw.get("document_summary", "")).strip()
    terms = _normalize_terms(raw.get("terms", []))

    method_candidates = raw.get("method_candidates", [])
    if not isinstance(method_candidates, list):
        method_candidates = []

    normalized_candidates = [
        _normalize_method_candidate(item, fallback_index=i + 1)
        for i, item in enumerate(method_candidates)
    ]

    if not normalized_candidates:
        # 兜底：从章节名自动猜测
        fallback_candidates = []
        for sec in parsed_doc.get("sections", []):
            title = str(sec.get("title", "")).strip()
            if any(k in title for k in ("试验", "方法", "程序", "条件", "评定", "判定")):
                fallback_candidates.append({
                    "candidate_id": f"M{len(fallback_candidates)+1:03d}",
                    "candidate_title": title,
                    "candidate_type_hint": "",
                    "summary_hint": f"根据章节标题自动生成的候选：{title}",
                    "section_ids": [sec.get("section_id")],
                    "reason": "大模型未输出候选，按章节标题兜底生成",
                })

        if not fallback_candidates:
            fallback_candidates = [{
                "candidate_id": "M001",
                "candidate_title": parsed_doc["file_name"].rsplit(".", 1)[0],
                "candidate_type_hint": "",
                "summary_hint": "使用全文兜底生成的唯一方法候选",
                "section_ids": [sec["section_id"] for sec in parsed_doc.get("sections", [])[:3]],
                "reason": "大模型未输出候选，使用全文兜底",
            }]
        normalized_candidates = fallback_candidates

    return {
        "document_class": document_class,
        "document_summary": document_summary,
        "terms": terms,
        "method_candidates": normalized_candidates,
    }


def extract_method_details(
    parsed_doc: Dict[str, Any],
    discovery_data: Dict[str, Any],
    max_methods: Optional[int] = None,
) -> List[Dict[str, Any]]:
    methods: List[Dict[str, Any]] = []
    document_class = discovery_data.get("document_class", "未判定")
    candidates = list(discovery_data.get("method_candidates", []) or [])
    if max_methods is not None:
        candidates = candidates[:max_methods]

    section_cap = int(getattr(Config, "DETAIL_SECTION_MAX_CHARS", 2000) or 2000)
    max_prompt_chars = int(getattr(Config, "DETAIL_MAX_PROMPT_CHARS", 10000) or 10000)
    max_prompt_chars = _detail_prompt_budget_chars(max_prompt_chars)
    table_html_max = int(getattr(Config, "DETAIL_TABLE_HTML_MAX_CHARS", 1200) or 1200)
    max_tables = int(getattr(Config, "DETAIL_MAX_TABLES", 40) or 40)

    for candidate in candidates:
        related_sections = get_sections_by_ids(parsed_doc.get("sections", []), candidate.get("section_ids", []))
        if not related_sections:
            related_sections = parsed_doc.get("sections", [])[:3]

        related_sections_for_prompt: List[Dict[str, Any]] = []
        for sec in related_sections:
            related_sections_for_prompt.append({
                "section_id": sec.get("section_id"),
                "title": sec.get("title"),
                "start_page": sec.get("start_page"),
                "end_page": sec.get("end_page"),
                "text": sec.get("text", ""),
            })

        related_tables_raw = collect_tables_for_sections(
            sections=parsed_doc.get("sections", []),
            page_tables=parsed_doc.get("page_table_map", {}),
            section_ids=candidate.get("section_ids", []),
        )
        tables_prepared = _prepare_tables_for_prompt(related_tables_raw, table_html_max, max_tables)

        packed = _pack_detail_chunks_by_prompt_size(
            file_name=parsed_doc["file_name"],
            document_class=document_class,
            candidate=candidate,
            related_sections_for_prompt=related_sections_for_prompt,
            tables_prepared=tables_prepared,
            section_cap=section_cap,
            max_prompt_chars=max_prompt_chars,
        )
        n_chunks = len(packed)
        if n_chunks > 1:
            logger.info(
                "[GetTestMethod] 候选 %s 细粒度抽取按全文 prompt 拆为 %d 次请求（上限约 %d 字符/次）",
                candidate.get("candidate_id"),
                n_chunks,
                max_prompt_chars,
            )

        rule_triplet = _rule_extract_triplet(
            related_sections=related_sections_for_prompt,
            related_tables=tables_prepared,
        )
        method: Optional[Dict[str, Any]] = _build_rule_based_method_seed(
            candidate=candidate,
            document_class=document_class,
            triplet=rule_triplet,
        )
        for idx, (sec_chunk, tab_chunk) in enumerate(packed):
            sec_list = [dict(x) for x in sec_chunk]
            tab_list = [dict(x) for x in tab_chunk]
            if idx == 0:
                prompt = build_detail_calibration_prompt(
                    file_name=parsed_doc["file_name"],
                    document_class=document_class,
                    method_candidate=candidate,
                    rule_based_method=method,
                    related_sections=sec_list,
                    related_tables=tab_list,
                )
                raw = _call_llm_json(prompt=prompt, temperature=0.1)
                llm_method = _normalize_method_detail(raw, candidate=candidate, document_class=document_class)
                # 元信息以 LLM 校准结果为准，正文块优先在规则基底上增量合并，防止“凭空变空”。
                method["class"] = llm_method.get("class", method.get("class"))
                method["section_ids"] = llm_method.get("section_ids", method.get("section_ids", []))
                method["name"] = llm_method.get("name", method.get("name"))
                method["type"] = llm_method.get("type", method.get("type"))
                method["description"] = llm_method.get("description", method.get("description"))
                method["recall_plan"] = llm_method.get("recall_plan", method.get("recall_plan"))
                _merge_triplet_patch_into_method(method, llm_method)
            else:
                assert method is not None
                prompt = build_detail_partial_prompt(
                    skeleton=_skeleton_for_merge(method),
                    related_sections=sec_list,
                    related_tables=tab_list,
                    chunk_index=idx + 1,
                    chunk_total=n_chunks,
                )
                while len(prompt) > max_prompt_chars:
                    shrunk = False
                    for row in reversed(sec_list):
                        tx = str(row.get("text", "") or "")
                        if len(tx) > 100:
                            row["text"] = tx[: int(len(tx) * 0.75)]
                            shrunk = True
                            break
                    if not shrunk:
                        break
                    prompt = build_detail_partial_prompt(
                        skeleton=_skeleton_for_merge(method),
                        related_sections=sec_list,
                        related_tables=tab_list,
                        chunk_index=idx + 1,
                        chunk_total=n_chunks,
                    )
                raw = _call_llm_json(prompt=prompt, temperature=0.1)
                _merge_triplet_patch_into_method(method, raw)

        assert method is not None

        # 调试输出：打印提取后的试验过程、试验条件、结果评定
        for block_name, label in [("sytj", "试验条件"), ("sygc", "试验过程"), ("jgpd", "结果评定")]:
            block = method.get(block_name, {})
            text = block.get("text", "")
            tables_count = len(block.get("tables_html", []))
            formulas_count = len(block.get("formulas_latex", []))
            logger.info(
                "[GetTestMethod][调试] %s - 文本长度: %d 字符, 表格数: %d, 公式数: %d\n文本预览(前300字): %s",
                label,
                len(text),
                tables_count,
                formulas_count,
                text[:300] if text else "(空)",
            )

        for block_name in ("sytj", "sygc", "jgpd"):
            if not method[block_name]["tables_html"] and tables_prepared:
                block_text = method[block_name]["text"]
                if block_text != "文档未明确说明":
                    method[block_name]["tables_html"] = [x["html"] for x in tables_prepared[:3]]

        methods.append(method)

    return methods


def extract_document_methods(
    parsed_doc: Dict[str, Any],
    max_methods: Optional[int] = None,
) -> Dict[str, Any]:
    discovery_data = discover_document_structure(parsed_doc=parsed_doc)
    methods = extract_method_details(
        parsed_doc=parsed_doc,
        discovery_data=discovery_data,
        max_methods=max_methods,
    )

    return {
        "source_file": {
            "file_name": parsed_doc["file_name"],
            "file_path": parsed_doc["file_path"],
            "file_ext": parsed_doc["file_ext"],
            "parse_mode": parsed_doc.get("parse_mode"),
            "ocr_used": bool(parsed_doc.get("ocr_used")),
        },
        "document_meta": {
            "class": discovery_data.get("document_class", "未判定"),
            "summary": discovery_data.get("document_summary", ""),
        },
        "terms": discovery_data.get("terms", []),
        "method_candidates": discovery_data.get("method_candidates", []),
        "methods": methods,
    }
