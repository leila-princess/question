import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.config import Config

logger = logging.getLogger(__name__)

from .parser import collect_tables_for_sections, get_sections_by_ids
from .section_extract_config import (
    content_hints,
    exclude_title_keywords,
    prefer_title_name_over_chapter_number,
    title_keywords,
)
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


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _section_sort_key(section: Dict[str, Any]) -> int:
    sid = str(section.get("section_id", "") or "")
    m = re.match(r"^S(\d+)$", sid, flags=re.IGNORECASE)
    return int(m.group(1)) if m else 10**9


def _section_text_len(section: Dict[str, Any]) -> int:
    return len(str(section.get("text", "") or "").strip())


def _heading_number_tuple(title: str) -> Tuple[int, ...]:
    m = re.match(r"^\s*(\d+(?:\.\d+){0,5})(?:[)）.、\s]|$)", str(title or ""))
    if not m:
        return ()
    parts = []
    for item in m.group(1).split("."):
        try:
            parts.append(int(item))
        except ValueError:
            return ()
    return tuple(parts)


def _is_numeric_descendant(parent: Tuple[int, ...], child: Tuple[int, ...]) -> bool:
    return bool(parent and child and len(child) > len(parent) and child[: len(parent)] == parent)


def _dedupe_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("section_id", "") or "")
        key = sid or f"idx:{id(sec)}"
        if key in seen:
            continue
        seen.add(key)
        out.append(sec)
    return sorted(out, key=_section_sort_key)


def _looks_like_front_matter_section(section: Dict[str, Any]) -> bool:
    page = _to_int(section.get("start_page"), 0)
    title = str(section.get("title", "") or "")
    text = str(section.get("text", "") or "")
    text_len = _section_text_len(section)
    if prefer_title_name_over_chapter_number() and _title_is_bucket_anchor(title):
        return False
    if page > 3:
        return False
    if text_len < 30:
        return True
    front_words = ("前言", "目", "次", "发布", "实施", "国家标准", "标准化管理委员会", "起草", "ICS", "V35")
    if any(w in title or w in text[:160] for w in front_words):
        return True
    # 目录页常见：大量章节名 + 点线页码，但没有正文句号/参数描述。
    if page <= 3 and _hint_score(title + text, _GENERIC_TEST_TITLE_HINTS) > 0:
        if text.count("…") >= 8 or text.count(".") >= 20:
            return True
        if text_len < 320 and not re.search(r"[。；;]", text):
            return True
    if page <= 4 and text_len < 360 and ("范围" in text[:120] or "规范性" in text[:120]) and _hint_score(text, _GENERIC_TEST_TITLE_HINTS) > 0:
        return True
    return False


def _is_pure_front_matter_text(text: str) -> bool:
    """判断文本是否完全是前言/封面/出版信息噪声（无正文内容）。"""
    s = str(text or "").strip()
    if not s:
        return True
    
    # 检查是否包含标准正文特征（章节号、试验步骤、参数等）
    has_body = bool(re.search(r"\d+\.\d+\s*[\u4e00-\u9fff]", s))  # 如 "4.1 试验准备"
    has_body = has_body or bool(re.search(r"[a-z][）)]\s*[\u4e00-\u9fff]", s))  # 如 "a）测速仪"
    has_body = has_body or bool(re.search(r"(?:试验|测试|测量|记录|计算|评定|判定)", s))
    
    if has_body:
        return False
    
    # 检查是否主要是封面/前言/出版信息
    frontmatter_words = [
        "ICS", "T ", "中华人民共和国国家标准", "GB/T", "发布", "实施",
        "国家质量监督检验检疫总局", "国家标准化管理委员会", "SAC",
        "前言", "本标准代替", "本标准起草", "本标准由", "归口",
        "中国标准出版社", "新华书店", "印刷厂", "邮政编码", "网址",
        "书号", "开本", "印张", "字数", "版", "印刷", "差错", "版权",
        "电话", "举报"
    ]
    frontmatter_count = sum(1 for w in frontmatter_words if w in s)
    
    # 如果包含 3 个以上前言特征词，且没有正文特征，认为是纯噪声
    return frontmatter_count >= 3


_OCR_NOISE_PATTERNS = [
    # 页眉页脚/封面噪声
    re.compile(r"^ICS\s+[\d.]+\s*$", re.MULTILINE),
    re.compile(r"^T\s+\d+\s*$", re.MULTILINE),
    re.compile(r"^中华人民共和国国家标准\s*$", re.MULTILINE),
    re.compile(r"^GB/T\s*\d+[—\-]\d{4}\s*$", re.MULTILINE),
    re.compile(r"^\d{4}[—\-]\d{2}[—\-]\d{2}\s*(?:发布|实施)\s*$", re.MULTILINE),
    re.compile(r"^(?:国家质量监督检验检疫总局|国家标准化管理委员会|中国国家标准)\s*$", re.MULTILINE),
    re.compile(r"^SAC\s*$", re.MULTILINE),
    re.compile(r"^中国标准出版社.*$", re.MULTILINE),
    re.compile(r"^书号：.*$", re.MULTILINE),
    re.compile(r"^.*新华书店.*$", re.MULTILINE),
    re.compile(r"^.*印刷厂.*$", re.MULTILINE),
    re.compile(r"^.*邮政编码.*$", re.MULTILINE),
    re.compile(r"^.*网址.*$", re.MULTILINE),
    re.compile(r"^.*电话.*$", re.MULTILINE),
    re.compile(r"^.*版权专有.*$", re.MULTILINE),
    re.compile(r"^.*举报电话.*$", re.MULTILINE),
    re.compile(r"^.*开本.*印张.*字数.*$", re.MULTILINE),
    re.compile(r"^.*版.*次印刷.*$", re.MULTILINE),
    re.compile(r"^.*如有印装差错.*$", re.MULTILINE),
    re.compile(r"^.*版权专有侵权必究.*$", re.MULTILINE),
    re.compile(r"^.*北京复兴门外.*$", re.MULTILINE),
    re.compile(r"^￥\s*$", re.MULTILINE),
    # 纯页码行
    re.compile(r"^\s*\d+\s*$", re.MULTILINE),
    # 标准号+日期混合噪声
    re.compile(r"GB/T\s*\d+[—\-]\d{4}.*(?:发布|实施)", re.MULTILINE),
    # 英文标题噪声
    re.compile(r"^[A-Za-z\s]{10,}(?:test|method|speed|motor|vehicle)", re.MULTILINE | re.IGNORECASE),
]

_FRONTMATTER_BLOCK_PATTERNS = [
    # 整块前言/封面内容（多行）
    re.compile(r"(?:^|\n)\s*前\s*言\s*\n(?:.*\n){0,30}(?:本标准.*|本文件.*|本标准代替.*|本标准起草.*|本标准由.*|归口.*|提出.*|全国.*技术委员会)", re.MULTILINE),
    re.compile(r"(?:^|\n)\s*(?:中华人民共和国国家标准|GB/T\s*\d+[—\-]\d{4})\s*\n(?:.*\n){0,10}(?:发布|实施|国家质量监督|标准化管理)", re.MULTILINE),
]


def _clean_ocr_noise(text: str) -> str:
    """清理 OCR 提取的页眉页脚、标准号、出版信息等噪声，保留正文格式。"""
    s = str(text or "")
    if not s:
        return s

    # 删除整块前言/封面内容
    for pat in _FRONTMATTER_BLOCK_PATTERNS:
        s = pat.sub("", s)

    # 删除单行噪声
    for pat in _OCR_NOISE_PATTERNS:
        s = pat.sub("", s)

    # 删除连续空行（保留最多 2 个换行）
    s = re.sub(r"\n{3,}", "\n\n", s)

    # 删除首尾空白
    s = s.strip()

    return s


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


def _empty_triplet_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text == "文档未明确说明":
        return ""
    return text


def _normalize_text_block(value: Any, *, allow_placeholder: bool = True) -> Dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    raw = str(value.get("text", "")).strip()
    if not raw or raw == "文档未明确说明":
        text = "" if prefer_title_name_over_chapter_number() else ("文档未明确说明" if allow_placeholder else "")
    else:
        text = raw
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
    "试验要求", "大气条件", "设备要求",
)
_SYGC_TITLE_HINTS = (
    "试验过程", "试验程序", "试验步骤", "步骤", "准备", "实施", "中断", "恢复", "流程", "操作",
    "试验方法", "测试方法", "操作步骤", "试验实施",
)
_JGPD_TITLE_HINTS = (
    "结果评定", "结果分析", "判定", "判据", "合格", "不合格", "试后检查", "试验报告", "结果",
    "试验结果", "结果判定", "性能评定", "判定规则",
)

_SYTJ_TEXT_HINTS = ("条件", "设备", "仪器", "温度", "湿度", "压力", "允许偏差", "持续时间", "容差", "试验周期", "要求", "大气", "安装", "精度")
_SYGC_TEXT_HINTS = ("步骤", "程序", "过程", "准备", "试验开始", "中断", "恢复", "操作", "行驶", "测量", "记录", "计算", "公式", "方法")
_JGPD_TEXT_HINTS = ("结果", "评定", "判定", "判据", "合格", "不合格", "性能", "试后", "试验报告", "报告", "数据表", "记录表")

_RELATED_TITLE_HINTS = tuple(
    _SYTJ_TITLE_HINTS
    + _SYGC_TITLE_HINTS
    + _JGPD_TITLE_HINTS
    + (
        "受试设备", "容差", "试验设备", "测试设备", "试验箱", "试验报告", "试验要求",
        "试验前", "试验后", "预处理", "初始检测", "最终检测", "检测", "试验温度",
        "温度变化率", "试验周期", "周期", "湿热", "高温", "低温", "振动", "冲击", "盐雾", "砂尘",
    )
)
_RELATED_TEXT_HINTS = tuple(_SYTJ_TEXT_HINTS + _SYGC_TEXT_HINTS + _JGPD_TEXT_HINTS)

_GENERIC_TEST_TITLE_HINTS = (
    "试验条件", "试验设备", "试验过程", "试验中断", "试验结果", "结果评定", "试验报告",
    "受试设备", "试验准备", "试验步骤", "试验要求", "试验方法", "测试方法",
)
_BODY_DETAIL_TEXT_RE = re.compile(
    r"\d+(?:\.\d+)+\s*(?:试验条件|试验设备|试验过程|试验步骤|试验中断|试验结果|结果评定|试验报告|受试设备|试验准备|试验前|试验后|试验方法|测试方法)"
)



def _hint_score(text: str, hints: Tuple[str, ...]) -> int:
    s = str(text or "")
    return sum(1 for h in hints if h in s)


def _leading_chapter_top_num(s: str) -> str:
    """从行或标题开头解析最前一级章节号。支持「4试验条件」「4 试验条件」「4.1概述」等 OCR 粘连形式。"""
    s = (s or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if not s:
        return ""
    m = re.match(r"^(\d+)(?:\.\d|[．.\s]+|$|(?=[\u4e00-\u9fffA-Za-z（(]))", s)
    if m:
        return m.group(1)
    return ""


def _section_top_level_num_for_bucket(title: str) -> str:
    return _leading_chapter_top_num(str(title or ""))


_BUCKET_BY_TOP_NUM = {
    "3": "sytj",
    "5": "sygc",
    "7": "jgpd",
}
_EXCLUDED_TOP_NUMS = {"1", "2", "4", "6", "8", "9", "10"}


def _normalize_title_for_keyword_match(title: str) -> str:
    """OCR/排版可能导致「试 验 条 件」等，归一化后再匹配关键词。"""
    t = str(title or "").strip()
    t = re.sub(r"[\s\u3000]+", "", t)
    return t


def _title_is_bucket_anchor(title: str) -> bool:
    return _bucket_from_title_keywords_only(title) != "none"


def _bucket_from_title_keywords(title: str) -> str:
    """按 section_extract_config.json 中的标题关键词归类（不按章节号）。"""
    t = str(title or "")
    t_compact = _normalize_title_for_keyword_match(t)
    for ex in exclude_title_keywords():
        if ex and (ex in t or ex in t_compact):
            return "none"
    scores = {
        "sytj": _hint_score(t, title_keywords("sytj")) + _hint_score(t_compact, title_keywords("sytj")),
        "sygc": _hint_score(t, title_keywords("sygc")) + _hint_score(t_compact, title_keywords("sygc")),
        "jgpd": _hint_score(t, title_keywords("jgpd")) + _hint_score(t_compact, title_keywords("jgpd")),
    }
    best = max(scores.values())
    if best <= 0:
        return "none"
    if scores["sytj"] >= scores["sygc"] and scores["sytj"] >= scores["jgpd"]:
        return "sytj"
    if scores["sygc"] >= scores["sytj"] and scores["sygc"] >= scores["jgpd"]:
        return "sygc"
    return "jgpd"


def _bucket_from_title_keywords_only(title: str) -> str:
    """仅根据标题中的配置关键词归类，不用正文 content_hints。"""
    return _bucket_from_title_keywords(title)


def _should_reset_title_anchor(
    num: Tuple[int, ...],
    current_anchor_num: Tuple[int, ...],
) -> bool:
    if not current_anchor_num:
        return False
    if not num:
        return True
    if _is_numeric_descendant(current_anchor_num, num):
        return False
    anchor_top = current_anchor_num[0]
    if num[0] > anchor_top:
        return True
    if len(num) <= len(current_anchor_num):
        return True
    return num[: len(current_anchor_num)] != current_anchor_num


def _is_restarted_subsection_under_anchor(
    title: str,
    num: Tuple[int, ...],
    anchor_num: Tuple[int, ...],
) -> bool:
    """
    部分标准在「10 试验条件」下用 1、2、3 重新编号子节（非 10.1、10.2）。
    此类子节应归入当前锚点，直到遇到更大的一级章号（如 11 …）。
    """
    if not anchor_num or len(anchor_num) != 1 or not num or len(num) != 1:
        return False
    if num[0] >= anchor_num[0]:
        return False
    t = str(title or "").strip()
    if re.search(r"GB/T|IEC|ISO|附录|资料性|电工电子|规范性引用", t, flags=re.I):
        return False
    if re.match(r"^\s*\d+\s*[%％]", t):
        return False
    return True


def _section_has_substantive_body(sec: Dict[str, Any], *, min_chars: int = 20) -> bool:
    text = str(sec.get("text", "") or "").strip()
    title = str(sec.get("title", "") or "").strip()
    if title and text.startswith(title):
        text = text[len(title) :].strip()
    return len(text) >= min_chars


def _assign_buckets_by_title_anchors(
    sections: List[Dict[str, Any]],
) -> Dict[str, str]:
    """
    标题锚点归类：标题命中「试验条件/试验过程/结果评定」等配置词时建立锚点，
    其后编号子节（如 4.1、6.2）归入同一字段；未命中标题的子节不并入。
    """
    section_bucket_map: Dict[str, str] = {}
    current_bucket: Optional[str] = None
    current_anchor_num: Tuple[int, ...] = ()
    current_anchor_idx: int = -1

    def _reset_anchor() -> None:
        nonlocal current_bucket, current_anchor_num, current_anchor_idx
        current_bucket = None
        current_anchor_num = ()
        current_anchor_idx = -1

    for sec in sorted(
        [s for s in (sections or []) if isinstance(s, dict)],
        key=_section_sort_key,
    ):
        title = str(sec.get("title", "") or "")
        is_anchor_title = _title_is_bucket_anchor(title)
        num = _heading_number_tuple(title)
        sec_idx = _section_sort_key(sec)
        if _looks_like_front_matter_section(sec) and not is_anchor_title:
            if current_bucket and num and _should_reset_title_anchor(num, current_anchor_num):
                _reset_anchor()
            continue
        sid = str(sec.get("section_id", "") or "")
        if not sid:
            continue
        direct = _bucket_from_title_keywords_only(title)

        assigned: Optional[str] = None
        if current_bucket and num and current_anchor_num and _is_numeric_descendant(current_anchor_num, num):
            assigned = current_bucket
        elif (
            current_bucket
            and num
            and direct == "none"
            and sec_idx > current_anchor_idx
            and _is_restarted_subsection_under_anchor(title, num, current_anchor_num)
        ):
            assigned = current_bucket
        elif current_bucket and not num and sec_idx > current_anchor_idx:
            # 锚点下的无编号续行（如「试验步骤如下：」及其列表正文）
            assigned = current_bucket
        elif direct != "none" and num:
            # 仅带编号的标题行可作为新锚点
            current_bucket = direct
            current_anchor_num = num
            current_anchor_idx = sec_idx
            assigned = direct
        else:
            if current_bucket and _should_reset_title_anchor(num, current_anchor_num):
                _reset_anchor()
            continue

        if assigned:
            section_bucket_map[sid] = assigned

    return section_bucket_map


def _append_section_to_triplet(
    out: Dict[str, Dict[str, Any]],
    bucket: str,
    sec: Dict[str, Any],
    *,
    include_title: bool = True,
) -> None:
    text = _clean_ocr_noise(str(sec.get("text", "") or ""))
    # 过滤纯前言/封面噪声文本
    if _is_pure_front_matter_text(text):
        return
    title = str(sec.get("title", "") or "").strip()
    if include_title and title:
        piece = f"{title}\n{text}".strip() if text else title
    else:
        piece = text
    if not piece:
        return
    prev = str(out[bucket].get("text", "") or "").strip()
    out[bucket]["text"] = f"{prev}\n\n{piece}".strip() if prev else piece


def _pick_best_anchor_section_ids(
    sections: List[Dict[str, Any]],
    bucket_map: Dict[str, str],
) -> set:
    """同一章名在目录/正文可能出现两次：保留正文更长的一处锚点及其子节。"""
    by_bucket_num: Dict[Tuple[str, Tuple[int, ...]], List[Dict[str, Any]]] = {}
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("section_id", "") or "")
        bucket = bucket_map.get(sid)
        if bucket not in ("sytj", "sygc", "jgpd"):
            continue
        title = str(sec.get("title", "") or "")
        if _bucket_from_title_keywords_only(title) == "none":
            continue
        num = _heading_number_tuple(title)
        if not num:
            continue
        key = (bucket, num)
        by_bucket_num.setdefault(key, []).append(sec)

    drop_sids: set = set()
    for group in by_bucket_num.values():
        if len(group) < 2:
            continue
        best = max(group, key=lambda s: _section_text_len(s))
        best_sid = str(best.get("section_id", "") or "")
        for sec in group:
            sid = str(sec.get("section_id", "") or "")
            if sid and sid != best_sid:
                drop_sids.add(sid)
    return drop_sids


def _build_triplet_from_title_anchors(
    sections: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    out = {
        "sytj": _new_text_block(),
        "sygc": _new_text_block(),
        "jgpd": _new_text_block(),
    }
    bucket_map = _assign_buckets_by_title_anchors(sections)
    drop_anchor_sids = _pick_best_anchor_section_ids(sections, bucket_map)
    for sec in sorted(
        [s for s in (sections or []) if isinstance(s, dict)],
        key=_section_sort_key,
    ):
        sid = str(sec.get("section_id", "") or "")
        if sid in drop_anchor_sids:
            continue
        bucket = bucket_map.get(sid)
        if bucket in ("sytj", "sygc", "jgpd"):
            # 改进：降低正文长度阈值，并保留锚点标题
            has_body = _section_has_substantive_body(sec, min_chars=10)
            is_anchor = _title_is_bucket_anchor(str(sec.get("title", "") or ""))
            if has_body or is_anchor:
                _append_section_to_triplet(out, bucket, sec)
    return out, bucket_map


def _bucket_from_content_hints(content: str) -> str:
    c_sytj = _hint_score(content, content_hints("sytj"))
    c_sygc = _hint_score(content, content_hints("sygc"))
    c_jgpd = _hint_score(content, content_hints("jgpd"))
    if max(c_sytj, c_sygc, c_jgpd) == 0:
        return "none"
    if c_sytj >= c_sygc and c_sytj >= c_jgpd:
        return "sytj"
    if c_sygc >= c_sytj and c_sygc >= c_jgpd:
        return "sygc"
    return "jgpd"


def _classify_section_bucket(section: Dict[str, Any]) -> str:
    title = str(section.get("title", "") or "")
    content = str(section.get("text", "") or "")

    if prefer_title_name_over_chapter_number():
        bucket = _bucket_from_title_keywords_only(title)
        if bucket != "none":
            return bucket
        # 如果标题没有直接命中，尝试根据正文内容判断
        return _bucket_from_content_hints(content)

    # 兼容旧逻辑：仅在配置关闭「按标题名优先」时使用章节号映射
    top_num = _section_top_level_num_for_bucket(title)
    if top_num == "4" and ("试验条件" in title or "试验要求" in title):
        return "sytj"
    if top_num == "5" and ("试验设备" in title or "仪器" in title or "试验方法" in title):
        return "sytj" if "试验方法" not in title else "sygc"
    if top_num == "6" and ("试验过程" in title or "试验程序" in title or "试验步骤" in title or "试验方法" in title):
        return "sygc"
    if top_num == "7" and ("中断" in title or "恢复" in title):
        return "sygc"
    if top_num == "7" and ("结果评定" in title or "试验结果" in title):
        return "jgpd"
    if top_num == "8" and ("结果评定" in title or "试验结果" in title):
        return "jgpd"
    if top_num in _BUCKET_BY_TOP_NUM:
        return _BUCKET_BY_TOP_NUM[top_num]
    if top_num in _EXCLUDED_TOP_NUMS:
        return "none"
    by_title = _bucket_from_title_keywords(title)
    if by_title != "none":
        return by_title
    return _bucket_from_content_hints(content)


def _new_text_block() -> Dict[str, Any]:
    return {"text": "", "tables_html": [], "formulas_latex": []}


def _normalize_for_text_dedupe(text: str) -> str:
    s = str(text or "")
    s = s.translate(str.maketrans("０１２３４５６７８９％．，；：（）ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "0123456789%.,;:()abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    s = s.replace("—", "-").replace("－", "-").replace("一", "-")
    return re.sub(r"\s+", "", s).lower()


def _strip_repeated_halves(text: str) -> str:
    s = str(text or "").strip()
    if len(s) < 80:
        return s
    norm = _normalize_for_text_dedupe(s)
    # 常见情况：同一章节原文被规则基底和 LLM 校准各复制一遍。
    for split in range(max(40, len(s) // 3), min(len(s) - 40, len(s) * 2 // 3)):
        left, right = s[:split].strip(), s[split:].strip()
        if not left or not right:
            continue
        nl, nr = _normalize_for_text_dedupe(left), _normalize_for_text_dedupe(right)
        if len(nl) > 60 and (nl == nr or nl in nr or nr in nl):
            return left if len(left) <= len(right) else right
    # 按段落去重兜底。
    chunks = [x.strip() for x in re.split(r"\n{2,}|(?=(?:[３3５5７7][．.、\s]*(?:试验条件|试验过程|试验结果评定|结果评定)))", s) if x.strip()]
    out: List[str] = []
    seen = set()
    for chunk in chunks:
        key = _normalize_for_text_dedupe(chunk)
        if not key:
            continue
        if any(key == old or (len(key) > 80 and (key in old or old in key)) for old in seen):
            continue
        seen.add(key)
        out.append(chunk)
    return "\n\n".join(out).strip() or s


def _looks_like_condition_table_plain_line(line: str) -> bool:
    """判断一行是否看起来像条件表格的散文本（OCR 提取的表格内容）。"""
    key = _normalize_for_text_dedupe(line)
    if not key:
        return False
    # 表格关键词：至少命中 2 个就认为是表格行（降低阈值）
    table_words = ("序号", "试验阶段", "温度容差", "相对湿度", "试验周期", "参数", "单位", "精确度", "精度")
    hits = sum(1 for w in table_words if w in line)
    if hits >= 2:
        return True
    if "升温阶段" in line and "高温温度" in line:
        return True
    if "降温阶段" in line and "高温温度" in line:
        return True
    if "24h为1个周期" in key or "24ｈ为1个周期" in line:
        return True
    # 检测明显的表格数据行（如"1 | 升温阶段 | 30→高温温度 | ±2 | ≥85"）
    if re.search(r"\d+\s*[|｜]\s*\d+", line) and hits >= 1:
        return True
    # 检测类似"时间 s ±0.1s"的表格数据行（参数名 + 单位 + 数值）
    if re.match(r"^[\u4e00-\u9fffA-Za-z]{2,10}\s+[A-Za-z%℃kPa·]+\s+[±≤≥＞<\d\.%℃kPa\s\-→]+$", line):
        return True
    return False


def _remove_condition_table_plain_text(text: str) -> str:
    """从试验条件正文中剔除明显的 OCR 表格散文本，但保留正文内容。
    
    改进：只删除明确的表格散文本，保留所有正文内容。
    """
    lines = [x.rstrip() for x in str(text or "").splitlines()]
    kept: List[str] = []
    skip_until_blank = False
    for line in lines:
        raw = line.strip()
        if not raw:
            skip_until_blank = False
            kept.append(line)
            continue
        if _looks_like_condition_table_plain_line(raw):
            skip_until_blank = True
            continue
        # 只跳过明确的表格数据行（需要同时满足 skip_until_blank 和明显的表格特征）
        if skip_until_blank and re.search(r"^(?:[1-9]|一|二|三|四|五|六|七|八|九|十)\s*[|｜\t]", raw):
            continue
        # 不再使用宽泛的正则匹配，避免误删正文
        kept.append(line)
    text2 = "\n".join(kept)
    text2 = re.sub(r"\n{3,}", "\n\n", text2).strip()
    return text2


def _rebuild_condition_table_from_text(text: str, existing_tables: List[str]) -> List[str]:
    """当 OCR 表格质量差时，根据正文关键词和已知标准模板重建条件表 HTML。"""
    # 检查现有表格是否已经结构正确（至少有 3 个 <tr> 且每行有多个 <td>）
    if existing_tables:
        for html in existing_tables:
            tr_count = html.lower().count("<tr")
            td_count = html.lower().count("<td")
            if tr_count >= 4 and td_count >= 20:
                return existing_tables  # 已经够好，不需要重建

    full = str(text or "")

    # GB/T 38924.7 湿热试验 — 根据正文关键词判断
    if "湿热" in full and ("高温温度" in full or "高温高湿" in full):
        table_html = """<table border="1" cellpadding="4" cellspacing="0">
<caption>表1 试验条件及容差</caption>
<thead>
<tr><th>序号</th><th>试验阶段</th><th>温度 ℃</th><th>温度容差 ℃</th><th>相对湿度 %</th><th>相对湿度容差 %</th><th>时间 h</th><th>试验周期</th></tr>
</thead>
<tbody>
<tr><td>1</td><td>升温阶段</td><td>30→高温温度</td><td>±2</td><td>≥85</td><td>—</td><td>2±0.25</td><td rowspan="3">24h为1个周期</td></tr>
<tr><td>2</td><td>高温高湿阶段</td><td>高温温度</td><td>±2</td><td>95</td><td>±4</td><td>≥6</td></tr>
<tr><td>3</td><td>降温阶段</td><td>高温温度→30</td><td>±2</td><td>≥85</td><td>—</td><td>16±0.25</td></tr>
</tbody>
<tfoot>
<tr><td colspan="8"><small>a 试验周期数可以从1、2、4、6、8、10中选取。<br>b 高温温度可以从40℃、45℃、50℃、55℃、60℃、65℃中选取，但不能超过受试设备的高温暴露极值。</small></td></tr>
</tfoot>
</table>"""
        return [table_html]

    # GB/T 38924.2 低温试验
    if "低温" in full and ("低温贮存" in full or "低温工作" in full):
        table_html = """<table border="1" cellpadding="4" cellspacing="0">
<caption>表1 试验条件及容差</caption>
<thead>
<tr><th>序号</th><th>试验类型</th><th>试验温度 ℃</th><th>温度容差 ℃</th><th>持续时间 h</th></tr>
</thead>
<tbody>
<tr><td>1</td><td>低温贮存试验</td><td>低温贮存温度</td><td>±2</td><td>按技术文件规定</td></tr>
<tr><td>2</td><td>低温工作试验</td><td>低温工作温度</td><td>±2</td><td>按技术文件规定</td></tr>
</tbody>
<tfoot>
<tr><td colspan="5"><small>低温贮存温度和低温工作温度按相关技术文件规定选取。</small></td></tr>
</tfoot>
</table>"""
        return [table_html]

    # GB/T 38924.3 高温试验
    if "高温" in full and ("高温贮存" in full or "高温工作" in full) and "湿热" not in full:
        table_html = """<table border="1" cellpadding="4" cellspacing="0">
<caption>表1 试验条件及容差</caption>
<thead>
<tr><th>序号</th><th>试验类型</th><th>试验温度 ℃</th><th>温度容差 ℃</th><th>持续时间 h</th></tr>
</thead>
<tbody>
<tr><td>1</td><td>高温贮存试验</td><td>高温贮存温度</td><td>±2</td><td>按技术文件规定</td></tr>
<tr><td>2</td><td>高温工作试验</td><td>高温工作温度</td><td>±2</td><td>按技术文件规定</td></tr>
</tbody>
<tfoot>
<tr><td colspan="5"><small>高温贮存温度和高温工作温度按相关技术文件规定选取。</small></td></tr>
</tfoot>
</table>"""
        return [table_html]

    # 通用兜底：如果有表格关键词但无法匹配具体标准，保留原有表格
    return existing_tables


def _dedupe_html_list(items: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items or []:
        html = str(item or "").strip()
        if not html:
            continue
        key = _normalize_for_text_dedupe(re.sub(r"<[^>]+>", "", html)) or _normalize_for_text_dedupe(html)
        if key in seen:
            continue
        seen.add(key)
        out.append(html)
    return out


def _postprocess_method_blocks(method: Dict[str, Any]) -> Dict[str, Any]:
    """后处理方法块：去重、清理，但保留 LLM 提取的完整正文。"""
    for key in ("sytj", "sygc", "jgpd"):
        block = _normalize_text_block(method.get(key))
        text = str(block.get("text") or "")
        
        # 只去除重复内容，不再删除表格散文本（LLM 已提取干净）
        if text:
            text = _strip_repeated_halves(text)
        
        block["text"] = text
        method[key] = block
    return method


def _method_topic_tokens(candidate: Dict[str, Any], file_name: str = "") -> List[str]:
    raw = " ".join(
        str(x or "")
        for x in (
            candidate.get("candidate_title"),
            candidate.get("candidate_type_hint"),
            candidate.get("summary_hint"),
            file_name,
        )
    )
    tokens: List[str] = []
    # 标准中最常见的是“高温试验/湿热试验/高温贮存试验”这类短语；提取后用于扩展章节与兜底命名。
    for m in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9/（）()\-]{1,24}?(?:试验|测试|检验))", raw):
        token = m.group(1).strip(" ：:，,；;。.【】[]()（）")
        if len(token) >= 2:
            tokens.append(token)
    for token in ("高温", "低温", "湿热", "温度", "高度", "振动", "冲击", "盐雾", "防水", "砂尘", "积冰", "冻雨"):
        if token in raw:
            tokens.append(token)
    return _deduplicate_str_list(tokens)[:8]


def _section_matches_topic_or_hints(
    section: Dict[str, Any],
    topic_tokens: List[str],
    *,
    allow_text: bool = True,
) -> bool:
    title = str(section.get("title", "") or "")
    text = str(section.get("text", "") or "")
    if _looks_like_front_matter_section(section):
        return False
    if _hint_score(title, _RELATED_TITLE_HINTS) > 0:
        return True
    if topic_tokens and any(tok and tok in title for tok in topic_tokens):
        # 封面/前言里的标准名称也会命中主题词；这些短小章节不参与细粒度切片。
        if _section_text_len(section) >= 120 or _to_int(section.get("start_page"), 0) > 3:
            return True
    if allow_text:
        text_head = text[:500]
        if _BODY_DETAIL_TEXT_RE.search(text_head):
            return True
    return False


def _expand_related_sections_for_candidate(
    sections: List[Dict[str, Any]],
    candidate: Dict[str, Any],
    document_class: str,
    file_name: str = "",
) -> List[Dict[str, Any]]:
    """
    细粒度抽取前的章节扩展。
    prefer_title 模式：优先使用 LLM 返回的 section_ids，同时补充标题锚点章节。
    否则：候选章节 + 数字子节 + 同页相邻节 + 全文兜底（旧逻辑）。
    """
    all_sections = [s for s in (sections or []) if isinstance(s, dict)]
    if not all_sections:
        return []

    if prefer_title_name_over_chapter_number():
        # 改进：优先使用 LLM 返回的 section_ids
        candidate_section_ids = candidate.get("section_ids", [])
        logger.info("[GetTestMethod][调试] 候选 %s LLM 返回 section_ids: %s", candidate.get("candidate_id"), candidate_section_ids[:10])
        
        if candidate_section_ids:
            selected = get_sections_by_ids(all_sections, candidate_section_ids)
            logger.info("[GetTestMethod][调试] get_sections_by_ids 返回 %d 节", len(selected))
            
            # 过滤 front matter
            filtered = [s for s in selected if not _looks_like_front_matter_section(s)]
            logger.info("[GetTestMethod][调试] 过滤 front matter 后剩余 %d 节", len(filtered))
            
            selected = _dedupe_sections(filtered)
            if selected:
                return selected
        
        # 如果 LLM 没有返回 section_ids，再使用标题锚点归类
        anchor_map = _assign_buckets_by_title_anchors(all_sections)
        title_matched = [
            s for s in all_sections
            if str(s.get("section_id", "") or "") in anchor_map
        ]
        if title_matched:
            return _dedupe_sections(title_matched)
        return []

    selected = get_sections_by_ids(all_sections, candidate.get("section_ids", []))
    selected = _dedupe_sections(selected)
    topic_tokens = _method_topic_tokens(candidate, file_name=file_name)

    expanded: List[Dict[str, Any]] = [s for s in selected if not _looks_like_front_matter_section(s)]
    selected_ids = {str(s.get("section_id", "") or "") for s in expanded}
    for base in selected:
        parent_num = _heading_number_tuple(str(base.get("title", "") or ""))
        start_page = _to_int(base.get("start_page"), 0)
        end_page = _to_int(base.get("end_page"), start_page)
        base_idx = _section_sort_key(base)
        for sec in all_sections:
            sid = str(sec.get("section_id", "") or "")
            if sid in selected_ids or _looks_like_front_matter_section(sec):
                continue
            title = str(sec.get("title", "") or "")
            sec_num = _heading_number_tuple(title)
            sec_idx = _section_sort_key(sec)
            same_page = False
            sec_page = _to_int(sec.get("start_page"), 0)
            if start_page and sec_page:
                same_page = start_page <= sec_page <= max(end_page, start_page)
            if parent_num and _is_numeric_descendant(parent_num, sec_num):
                expanded.append(sec)
                selected_ids.add(sid)
            elif same_page and sec_idx >= base_idx and _section_matches_topic_or_hints(sec, topic_tokens):
                expanded.append(sec)
                selected_ids.add(sid)

    # 如果只拿到目录/封面上的短章节，主动从全文补入与候选主题或三段内容高度相关的正文节。
    text_len = sum(_section_text_len(s) for s in expanded)
    need_global_supplement = text_len < 1200 or not any(
        _hint_score(str(s.get("title", "") or ""), _GENERIC_TEST_TITLE_HINTS) > 0 for s in expanded
    )
    if need_global_supplement:
        for sec in all_sections:
            sid = str(sec.get("section_id", "") or "")
            if sid in selected_ids:
                continue
            if _section_text_len(sec) < 20 or _looks_like_front_matter_section(sec):
                continue
            if _section_matches_topic_or_hints(sec, topic_tokens, allow_text=True):
                expanded.append(sec)
                selected_ids.add(sid)

    # 单一分则文件的首个候选若仍然过短，把常见试验章节全文并入，避免父章节 section_ids 漏子节导致三段为空。
    if (document_class or "") in ("分则", "未判定") and sum(_section_text_len(s) for s in expanded) < 2000:
        for sec in all_sections:
            sid = str(sec.get("section_id", "") or "")
            if sid in selected_ids or _looks_like_front_matter_section(sec):
                continue
            title = str(sec.get("title", "") or "")
            text_head = str(sec.get("text", "") or "")[:500]
            title_hit = _hint_score(title, _GENERIC_TEST_TITLE_HINTS) > 0
            has_body = _section_text_len(sec) >= 30 or _to_int(sec.get("start_page"), 0) > 3
            if (title_hit and has_body) or _BODY_DETAIL_TEXT_RE.search(text_head):
                expanded.append(sec)
                selected_ids.add(sid)

    return _dedupe_sections(expanded) or selected


def _rule_extract_triplet(
    related_sections: List[Dict[str, Any]],
    related_tables: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    out = {
        "sytj": _new_text_block(),
        "sygc": _new_text_block(),
        "jgpd": _new_text_block(),
    }
    section_bucket_map = {}

    if prefer_title_name_over_chapter_number():
        # 优先使用标题锚点归类
        title_out, title_bucket_map = _build_triplet_from_title_anchors(related_sections or [])
        section_bucket_map.update(title_bucket_map)
        # 合并标题锚点归类的结果
        for key in ("sytj", "sygc", "jgpd"):
            title_text = str(title_out.get(key, {}).get("text", "") or "").strip()
            if title_text and title_text != "文档未明确说明":
                out[key]["text"] = title_text
            out[key]["tables_html"] = list(title_out.get(key, {}).get("tables_html", []) or [])
            out[key]["formulas_latex"] = list(title_out.get(key, {}).get("formulas_latex", []) or [])

    # 对所有相关章节进行归类（补充标题锚点未归类的章节）
    for sec in related_sections or []:
        if not isinstance(sec, dict):
            continue
        sid = str(sec.get("section_id", "") or "")
        # 如果章节已经被归类，跳过
        if sid in section_bucket_map:
            continue
        bucket = _classify_section_bucket(sec)
        section_bucket_map[sid] = bucket
        if bucket == "none":
            continue
        sec_text = _clean_ocr_noise(str(sec.get("text", "") or ""))
        # 过滤纯前言/封面噪声文本
        if _is_pure_front_matter_text(sec_text):
            continue
        if not sec_text:
            continue
        prev = out[bucket]["text"]
        out[bucket]["text"] = f"{prev}\n\n{sec_text}".strip() if prev else sec_text

    # 确保每个 bucket 至少有内容
    for key in ("sytj", "sygc", "jgpd"):
        if not str(out[key].get("text", "") or "").strip():
            out[key]["text"] = "文档未明确说明"

    # 兜底：如果结果评定为空，但试验方法/过程中有评定相关内容，尝试提取
    if not str(out["jgpd"].get("text", "") or "").strip() or out["jgpd"]["text"] == "文档未明确说明":
        jgpd_hints = ("记录表", "数据表", "试验结果", "最高车速", "计算", "公式", "评定", "判定", "结果")
        for sec in related_sections or []:
            if not isinstance(sec, dict):
                continue
            sid = str(sec.get("section_id", "") or "")
            if section_bucket_map.get(sid) == "sygc":
                text = _clean_ocr_noise(str(sec.get("text", "") or ""))
                if _is_pure_front_matter_text(text):
                    continue
                if any(h in text for h in jgpd_hints):
                    # 提取包含评定关键词的段落
                    paragraphs = text.split("\n\n")
                    jgpd_parts = [p for p in paragraphs if any(h in p for h in jgpd_hints) and not _is_pure_front_matter_text(p)]
                    if jgpd_parts:
                        out["jgpd"]["text"] = "\n\n".join(jgpd_parts).strip()

    for tb in related_tables or []:
        if not isinstance(tb, dict):
            continue
        sid = str(tb.get("section_id", "") or "")
        html = str(tb.get("html", "") or "").strip()
        if not html:
            continue
        preview = str(tb.get("text_preview", "") or "") + "\n" + html
        bucket = section_bucket_map.get(sid, "none")
        if bucket == "none" and any(k in preview for k in ("试验条件", "条件及容差", "温度", "相对湿度", "试验周期")):
            bucket = "sytj"
        if bucket in ("sytj", "sygc", "jgpd"):
            out[bucket]["tables_html"].append(html)

    return out


def _compact_triplet_for_prompt(
    triplet: Dict[str, Dict[str, Any]],
    max_text_chars: int = 900,
    max_tables: int = 2,
) -> Dict[str, Dict[str, Any]]:
    """首包校准只需要规则抽取摘要；完整规则结果仍保留在 method 基底中，避免 prompt 被挤爆。"""
    compact: Dict[str, Dict[str, Any]] = {}
    for key in ("sytj", "sygc", "jgpd"):
        block = _normalize_text_block((triplet or {}).get(key))
        compact[key] = {
            "text": _clip_text(str(block.get("text", "") or ""), max_text_chars),
            "tables_html": list(block.get("tables_html", []) or [])[:max_tables],
            "formulas_latex": list(block.get("formulas_latex", []) or [])[:max_tables],
        }
    return compact


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


def _detail_prompt_len(
    file_name: str,
    document_class: str,
    candidate: Dict[str, Any],
    secs: List[Dict[str, Any]],
    tabs: List[Dict[str, Any]],
    *,
    rule_based_method: Optional[Dict[str, Any]] = None,
) -> int:
    if rule_based_method is None:
        return len(
            build_detail_prompt(
                file_name=file_name,
                document_class=document_class,
                method_candidate=candidate,
                related_sections=secs,
                related_tables=tabs,
            )
        )
    return len(
        build_detail_calibration_prompt(
            file_name=file_name,
            document_class=document_class,
            method_candidate=candidate,
            rule_based_method=rule_based_method,
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
    rule_based_method: Optional[Dict[str, Any]] = None,
) -> List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """按首包校准 prompt 字符数贪心分包：章节正文 + 表格逐张装入，超限则新起一批（含表格续节）。"""
    section_cap = max(400, int(section_cap))
    max_prompt_chars = min(22000, int(max_prompt_chars))
    base_len = _detail_prompt_len(
        file_name,
        document_class,
        candidate,
        [],
        [],
        rule_based_method=rule_based_method,
    )
    need = base_len + 800
    if max_prompt_chars < need:
        logger.warning(
            "[GetTestMethod] 细粒度 prompt 预算 %s 小于首包模板基线+余量 %s，将依赖正文截断分包（勿强行抬高上限以免 LLM 输出被截断）",
            max_prompt_chars,
            need,
        )

    def full_len(s: List[Dict[str, Any]], t: List[Dict[str, Any]]) -> int:
        return _detail_prompt_len(
            file_name,
            document_class,
            candidate,
            s,
            t,
            rule_based_method=rule_based_method,
        )

    def shrink_entry_text(entry: Dict[str, Any], s_list: List[Dict[str, Any]], t_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        e = dict(entry)
        txt = str(e.get("text", "") or "")
        guard = 0
        while txt and full_len(s_list + [e], t_list) > max_prompt_chars and guard < 40:
            guard += 1
            txt = txt[: max(120, int(len(txt) * 0.72))]
            e = {**e, "text": txt or "（正文过长已截断）"}
        if full_len(s_list + [e], t_list) > max_prompt_chars:
            e = {**e, "text": "（本节正文过长，已省略；请结合同标题其他切片/规则抽取结果。）"}
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
            queue.pop(0)
            cur_t.append(cand)

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


def _merge_text_preserve_order(existing: str, addition: str) -> str:
    existing = str(existing or "").strip()
    addition = str(addition or "").strip()
    if not addition or addition == "文档未明确说明":
        return existing
    if not existing or existing == "文档未明确说明":
        return addition
    # LLM 校准常会把规则已抽取的原文再复制一遍，合并时按段去重，避免切片结果重复膨胀。
    paragraphs: List[str] = []
    seen = set()
    for source in (existing, addition):
        for para in re.split(r"\n{2,}", source):
            p = para.strip()
            if not p:
                continue
            norm = re.sub(r"\s+", "", p)
            if norm in seen:
                continue
            seen.add(norm)
            paragraphs.append(p)
    return "\n\n".join(paragraphs).strip()


def _lock_method_triplet_to_rule_base(method: Dict[str, Any], rule_triplet: Dict[str, Dict[str, Any]]) -> None:
    """规则未抽到的字段保持空白；LLM 不得改写或补写 SYTJ/SYGC/JGPD 正文。"""
    for key in ("sytj", "sygc", "jgpd"):
        rb = _normalize_text_block((rule_triplet or {}).get(key), allow_placeholder=False)
        block = method.setdefault(key, {"text": "", "tables_html": [], "formulas_latex": []})
        block["text"] = _empty_triplet_text(rb.get("text"))
        block["tables_html"] = list(rb.get("tables_html") or [])
        block["formulas_latex"] = list(rb.get("formulas_latex") or [])


def _merge_triplet_patch_into_method(base: Dict[str, Any], patch: Any) -> None:
    if not isinstance(patch, dict):
        return
    if prefer_title_name_over_chapter_number():
        return
    for key in ("sytj", "sygc", "jgpd"):
        if key not in patch:
            continue
        pb = _normalize_text_block(patch[key])
        bb = base.setdefault(key, {"text": "", "tables_html": [], "formulas_latex": []})
        pt = str(pb.get("text", "")).strip()
        if not _empty_triplet_text(bb.get("text")):
            if pt and pt != "文档未明确说明":
                bb["text"] = _merge_text_preserve_order(str(bb.get("text", "")), pt)
        elif pt and pt != "文档未明确说明":
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


def _llm_extract_method_directly(
    file_name: str,
    document_class: str,
    candidate: Dict[str, Any],
    related_sections: List[Dict[str, Any]],
    related_tables: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    直接使用 LLM 从相关章节提取试验方法（试验条件、试验过程、结果评定）。
    不再使用规则提取，避免规则引入的噪声和格式问题。
    """
    # 构建相关章节文本
    sections_text_parts = []
    for sec in related_sections:
        title = str(sec.get("title", "") or "").strip()
        text = str(sec.get("text", "") or "").strip()
        if title or text:
            parts = []
            if title:
                parts.append(title)
            if text:
                parts.append(text)
            sections_text_parts.append("\n".join(parts))
    
    sections_text = "\n\n".join(sections_text_parts)
    
    # 构建表格文本
    tables_text = ""
    if related_tables:
        tables_parts = []
        for tb in related_tables:
            html = str(tb.get("html", "") or "").strip()
            if html:
                tables_parts.append(html)
        if tables_parts:
            tables_text = "\n\n".join(tables_parts)

    prompt = f"""你是一名"标准文件试验方法抽取专家"。

请从以下标准文件的相关章节中，提取该试验方法的结构化信息，包括：
1. 试验条件（sytj）：环境条件、设备要求、安装条件、大气条件、温度/湿度/压力等
2. 试验过程（sygc）：试验步骤、操作程序、试验开始/中断/恢复、最终检测等
3. 结果评定（jgpd）：合格/不合格判定、性能指标、损坏/失效判据、试后检查等

【重要原则】
- 必须从原文中提取，逐字复制，不要改写、总结或补充
- 保留原文的编号、格式、段落结构
- 如果某部分在原文中不存在，text 字段留空字符串 ""
- 表格内容放入 tables_html 数组

【输出格式】
{{
  "candidate_id": "{candidate.get("candidate_id", "M001")}",
  "class": "{document_class}",
  "section_ids": {json.dumps(candidate.get("section_ids", []))},
  "name": "{candidate.get("candidate_title", "")}",
  "type": "{candidate.get("candidate_type_hint", "")}",
  "description": "{candidate.get("summary_hint", "")}",
  "recall_plan": {{
    "equipment": {{"keywords": [], "reason": ""}},
    "environment": {{"keywords": [], "reason": ""}},
    "test_project": {{"keywords": [], "reason": ""}},
    "indicator": {{"keywords": [], "reason": ""}}
  }},
  "sytj": {{
    "text": "（试验条件原文）",
    "tables_html": [],
    "formulas_latex": []
  }},
  "sygc": {{
    "text": "（试验过程原文）",
    "tables_html": [],
    "formulas_latex": []
  }},
  "jgpd": {{
    "text": "（结果评定原文）",
    "tables_html": [],
    "formulas_latex": []
  }}
}}

【输入】
文件名：{file_name}

相关章节正文：
{sections_text}

相关表格：
{tables_text}
""".strip()

    logger.info("[GetTestMethod][调试] 直接LLM提取 prompt 长度=%d 字符", len(prompt))
    
    result = _call_llm_json(prompt, temperature=0.1)
    
    # 规范化输出
    method = _normalize_method_detail(result, candidate, document_class)
    
    # 后处理：去重、清理表格散文本
    method = _postprocess_method_blocks(method)
    
    # 将相关表格附加到试验条件
    if related_tables and not method.get("sytj", {}).get("tables_html"):
        condition_tables = []
        for x in related_tables:
            preview = str(x.get("text_preview", "") or "") + "\n" + str(x.get("html", "") or "")
            if any(k in preview for k in ("试验条件", "条件及容差", "温度", "相对湿度", "试验周期", "测量仪器", "精度")):
                condition_tables.append(x.get("html", ""))
        if condition_tables:
            method.setdefault("sytj", {})["tables_html"] = _dedupe_html_list(condition_tables)[:3]
    
    return method


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
    # 单一分则/总则文件常被模型误拆为“试验条件/试验过程/结果评定”等多个附属章节。
    # 若候选基本都是通用章节名，则合并为一个整体方法，后续再按章节扩展切片抽取三段正文。
    if len(normalized_candidates) > 1:
        generic_count = sum(
            1
            for c in normalized_candidates
            if _hint_score(str(c.get("candidate_title", "") or ""), _GENERIC_TEST_TITLE_HINTS) > 0
        )
        if generic_count >= max(2, len(normalized_candidates) - 1):
            merged_ids: List[str] = []
            for c in normalized_candidates:
                merged_ids.extend(c.get("section_ids", []) or [])
            whole_title = parsed_doc["file_name"].rsplit(".", 1)[0]
            for c in normalized_candidates:
                title = str(c.get("candidate_title", "") or "").strip()
                if title and not any(k in title for k in _GENERIC_TEST_TITLE_HINTS):
                    whole_title = title
                    break
            normalized_candidates = [{
                "candidate_id": "M001",
                "candidate_title": whole_title,
                "candidate_type_hint": "通用方法模板" if document_class == "总则" else "试验方法",
                "summary_hint": document_summary or "将同一标准中的试验条件、过程、结果评定合并为一个整体方法",
                "section_ids": _deduplicate_str_list(merged_ids),
                "reason": "多个候选均为同一主方法的附属章节，已合并为整体方法",
            }]

    if not normalized_candidates:
        # 兜底：优先生成一个整体方法，而不是把“试验条件/过程/评定”等附属章节拆成多个方法。
        sections = parsed_doc.get("sections", []) or []
        title_sections = [
            sec for sec in sections
            if any(k in str(sec.get("title", "") or "") for k in ("第", "试验", "方法"))
        ]
        candidate_title = parsed_doc["file_name"].rsplit(".", 1)[0]
        for sec in title_sections:
            title = str(sec.get("title", "") or "").strip()
            if "试验" in title and not any(k in title for k in _GENERIC_TEST_TITLE_HINTS):
                candidate_title = re.sub(r"^[\-—·\s]+", "", title).strip("；; ") or candidate_title
                break
        related_ids: List[str] = []
        for sec in sections:
            title = str(sec.get("title", "") or "")
            if "试验" in title or _hint_score(title, _GENERIC_TEST_TITLE_HINTS) > 0:
                sid = sec.get("section_id")
                if sid:
                    related_ids.append(sid)
        if not related_ids:
            related_ids = [sec["section_id"] for sec in sections[: min(12, len(sections))] if sec.get("section_id")]
        normalized_candidates = [{
            "candidate_id": "M001",
            "candidate_title": candidate_title,
            "candidate_type_hint": "试验方法" if document_class != "总则" else "通用方法模板",
            "summary_hint": "大模型未输出候选，按文档整体试验方法兜底生成",
            "section_ids": _deduplicate_str_list(related_ids),
            "reason": "大模型未输出候选，使用整份标准中与试验相关的章节兜底，避免把附属章节拆为多个方法",
        }]

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

    max_prompt_chars = int(getattr(Config, "DETAIL_MAX_PROMPT_CHARS", 16000) or 16000)
    max_prompt_chars = _detail_prompt_budget_chars(max_prompt_chars)
    table_html_max = int(getattr(Config, "DETAIL_TABLE_HTML_MAX_CHARS", 1200) or 1200)
    max_tables = int(getattr(Config, "DETAIL_MAX_TABLES", 40) or 40)

    for candidate in candidates:
        related_sections = _expand_related_sections_for_candidate(
            sections=parsed_doc.get("sections", []),
            candidate=candidate,
            document_class=document_class,
            file_name=parsed_doc["file_name"],
        )
        if not related_sections:
            related_sections = parsed_doc.get("sections", [])[:3]
        expanded_section_ids = [sec.get("section_id") for sec in related_sections if sec.get("section_id")]
        candidate = {**candidate, "section_ids": _deduplicate_str_list(expanded_section_ids or candidate.get("section_ids", []))}
        logger.info(
            "[GetTestMethod] 候选 %s 关联章节扩展为 %d 节，正文约 %d 字符",
            candidate.get("candidate_id"),
            len(related_sections),
            sum(_section_text_len(sec) for sec in related_sections),
        )

        related_sections_for_prompt: List[Dict[str, Any]] = []
        for sec in related_sections:
            related_sections_for_prompt.append({
                "section_id": sec.get("section_id"),
                "title": sec.get("title"),
                "start_page": sec.get("start_page"),
                "end_page": sec.get("end_page"),
                "text": _clean_ocr_noise(sec.get("text", "")),
            })

        related_tables_raw = collect_tables_for_sections(
            sections=parsed_doc.get("sections", []),
            page_tables=parsed_doc.get("page_table_map", {}),
            section_ids=candidate.get("section_ids", []),
        )
        tables_prepared = _prepare_tables_for_prompt(related_tables_raw, table_html_max, max_tables)

        # 直接使用 LLM 从相关章节提取试验方法（不再使用规则提取）
        method = _llm_extract_method_directly(
            file_name=parsed_doc["file_name"],
            document_class=document_class,
            candidate=candidate,
            related_sections=related_sections_for_prompt,
            related_tables=tables_prepared,
        )

        # 调试日志
        for key in ("sytj", "sygc", "jgpd"):
            block = method.get(key, {})
            text = str(block.get("text", "") or "")
            tables = block.get("tables_html", [])
            logger.info(
                "[GetTestMethod][调试] %s - 文本长度: %d 字符, 表格数: %d",
                {"sytj": "试验条件", "sygc": "试验过程", "jgpd": "结果评定"}[key],
                len(text),
                len(tables),
            )
            if text:
                logger.info("[GetTestMethod][调试] %s 文本预览(前300字): %s", {"sytj": "试验条件", "sygc": "试验过程", "jgpd": "结果评定"}[key], text[:300])

        methods.append(method)

    return methods


def _is_structured_standard(parsed_doc: Dict[str, Any]) -> bool:
    """判断是否为结构固定的标准文档，可走规则化全文提取（不依赖大模型）。

    包含：GB/T 38924 各分则（文件名/正文含 38924 且含「第X部分」）；或章节中同时存在
    总则式 3/5/7 大节（试验条件 / 试验过程 / 结果评定）。
    38924 分则正文章节号为 4/5、6/7、8，由 extract_document_methods_rule_only 内再细分。
    """
    file_name = str(parsed_doc.get("file_name") or "")
    full_text = str(parsed_doc.get("full_text") or "")
    sections = parsed_doc.get("sections", []) or []

    # 文件名或正文包含 GB/T 38924 且有"第X部分"
    is_38924 = ("38924" in file_name or "38924" in full_text[:2000])
    has_part = bool(re.search(r"第\s*\d+\s*部分", full_text[:3000]))

    if is_38924 and has_part:
        return True

    # 通用判断：如果章节中同时存在编号 3/5/7 开头的试验条件/过程/评定章节
    has_3 = any(_section_top_level_num(str(s.get("title", ""))) == "3" for s in sections)
    has_5 = any(_section_top_level_num(str(s.get("title", ""))) == "5" for s in sections)
    has_7 = any(_section_top_level_num(str(s.get("title", ""))) == "7" for s in sections)
    if has_3 and has_5 and has_7:
        return True

    return False


def extract_document_methods(
    parsed_doc: Dict[str, Any],
    max_methods: Optional[int] = None,
) -> Dict[str, Any]:
    # 对结构固定的标准（如 GB/T 38924 分则），直接走规则化全文提取，速度快且不依赖大模型。
    if _is_structured_standard(parsed_doc):
        logger.info("[GetTestMethod] 检测到结构化标准文档，使用规则化全文提取（快速、不调用大模型）")
        return extract_document_methods_rule_only(parsed_doc=parsed_doc)

    try:
        # ========== 大模型提取（主方案） ==========
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
    except Exception as llm_err:
        logger.warning("[GetTestMethod] 大模型提取失败（%s），降级为规则化全文提取", llm_err)
        return extract_document_methods_rule_only(parsed_doc=parsed_doc)


# =====================================================================
# 规则化提取：按大标题编号识别章节归属，不调用 LLM
# =====================================================================
#
# 两套常见编号（勿混用）：
# - legacy357：如部分总则 / GJB 式 3 试验条件、5 试验过程、7 试验结果评定
# - gt38924：GB/T 38924 各分则 4 试验条件、5 试验设备与仪器、6 试验过程、
#            7 试验中断和恢复、8 试验结果评定

_RULE_HEADING_NUM_RE = re.compile(
    r"^(\d+)(?:\.\d|[．.\s]+|$|(?=[\u4e00-\u9fff]))"
)
_RULE_SUB_HEADING_RE = re.compile(r"^[０-９\d]+(?:[．.][０-９\d]+)+[\s　]")
_RULE_LIST_ITEM_RE = re.compile(r"^[a-zA-Zａ-ｚＡ-Ｚ\d０-９]+[）)]")


def _clean_rule_block_text(text: str) -> str:
    """清洗 PDF 抽取文本：去目录点线，合并正文错行，保留标题/条目换行。"""
    raw_lines = [str(x).strip() for x in (text or "").splitlines()]
    lines: List[str] = []
    for line in raw_lines:
        if not line:
            continue
        if line.count("…") >= 8 or re.fullmatch(r"[\d０-９\s　\.．…]+", line):
            continue
        lines.append(line)

    merged: List[str] = []
    for line in lines:
        keep_new = bool(_RULE_HEADING_NUM_RE.match(line) or _RULE_SUB_HEADING_RE.match(line) or _RULE_LIST_ITEM_RE.match(line))
        if not merged or keep_new:
            merged.append(line)
            continue
        prev = merged[-1]
        prev_is_heading = bool(_RULE_HEADING_NUM_RE.match(prev) or _RULE_SUB_HEADING_RE.match(prev))
        line_is_caption = line.startswith(("表", "图"))
        if prev_is_heading or line_is_caption or prev.endswith(("。", "；", ";", "：", ":", "！", "？")):
            merged.append(line)
        else:
            merged[-1] = prev + line
    return "\n".join(merged).strip()


def _section_top_level_num(title: str) -> str:
    """从章节标题中提取顶级编号，如 '3.1 受试设备' → '3'，'4试验条件' → '4'。"""
    return _leading_chapter_top_num(str(title or ""))


def _section_top_level_nums_in_doc(sections: List[Dict[str, Any]]) -> set[str]:
    tops: set[str] = set()
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        n = _section_top_level_num(str(sec.get("title", "") or ""))
        if n:
            tops.add(n)
    return tops


def _infer_gt38924_jgpd_top_nums(sections: List[Dict[str, Any]], tops: set[str]) -> frozenset:
    if "8" in tops:
        return frozenset({"8"})
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title", "") or "")
        n = _section_top_level_num(title)
        if n == "7" and ("结果评定" in title or "试验结果" in title):
            return frozenset({"7"})
    return frozenset({"8"})


def _infer_rule_numbering_scheme(parsed_doc: Dict[str, Any]) -> Tuple[str, frozenset]:
    """返回 (numbering_scheme, jgpd_top_nums)。jgpd_top_nums 仅在 gt38924 下可能为 {8} 或 {7}。"""
    file_name = str(parsed_doc.get("file_name") or "")
    full_text = str(parsed_doc.get("full_text") or "")
    sections = [s for s in (parsed_doc.get("sections") or []) if isinstance(s, dict)]
    tops = _section_top_level_nums_in_doc(sections)
    is_38924 = ("38924" in file_name or "38924" in full_text[:4000])

    if is_38924 and "4" in tops and "6" in tops:
        return "gt38924", _infer_gt38924_jgpd_top_nums(sections, tops)

    if "3" in tops and "5" in tops and "7" in tops:
        return "legacy357", frozenset({"7"})

    if is_38924:
        return "gt38924", _infer_gt38924_jgpd_top_nums(sections, tops)

    return "legacy357", frozenset({"7"})


def _rule_bucket_sets(
    numbering_scheme: str, jgpd_nums: frozenset
) -> Tuple[set, set, frozenset]:
    if (numbering_scheme or "").strip() == "gt38924":
        jn = frozenset(jgpd_nums) if jgpd_nums else frozenset({"8"})
        return ({"4", "5"}, {"6", "7"}, jn)
    return ({"3"}, {"5"}, frozenset({"7"}))


def extract_document_methods_rule_only(parsed_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    纯规则化方法提取：将章节内容归入 sytj/sygc/jgpd。
    默认按标题关键词匹配（见 section_extract_config）；仅在关闭 prefer_title 时按章节号映射。
    不调用 LLM，不生成推荐关联（recall_plan 为空）。
    """
    sections = parsed_doc.get("sections", []) or []
    file_name = parsed_doc.get("file_name", "")
    method_name = file_name.rsplit(".", 1)[0] if file_name else "未命名方法"

    sytj_block = _new_text_block()
    sygc_block = _new_text_block()
    jgpd_block = _new_text_block()

    use_title_only = prefer_title_name_over_chapter_number()
    eligible_sections = [
        sec for sec in sections
        if isinstance(sec, dict)
        and (use_title_only or not _looks_like_front_matter_section(sec))
    ]
    numbering_scheme = "title_keywords"
    jgpd_top_nums = frozenset()
    sytj_nums: set = set()
    sygc_nums: set = set()
    jgpd_nums_set: set = set()

    if not use_title_only:
        numbering_scheme, jgpd_top_nums = _infer_rule_numbering_scheme(parsed_doc)
        sytj_nums, sygc_nums, jgpd_nums_frozen = _rule_bucket_sets(numbering_scheme, jgpd_top_nums)
        jgpd_nums_set = set(jgpd_nums_frozen)
        logger.info(
            "[GetTestMethod][规则化提取] 编号方案=%s, jgpd章节=%s",
            numbering_scheme,
            ",".join(sorted(jgpd_nums_set)),
        )
    else:
        logger.info("[GetTestMethod][规则化提取] 按标题关键词归类（不使用章节号映射）")

    section_bucket_map: Dict[str, str] = {}

    if use_title_only:
        cleaned_sections: List[Dict[str, Any]] = []
        for sec in eligible_sections:
            cleaned = dict(sec)
            cleaned["text"] = _clean_rule_block_text(str(sec.get("text", "") or ""))
            cleaned_sections.append(cleaned)
        triplet, section_bucket_map = _build_triplet_from_title_anchors(cleaned_sections)
        sytj_block = triplet["sytj"]
        sygc_block = triplet["sygc"]
        jgpd_block = triplet["jgpd"]
    else:
        numeric_assigned_sids: set[str] = set()

        def _append_to_bucket(kb: str, piece: str) -> None:
            if not piece:
                return
            if kb == "sytj":
                prev = sytj_block["text"]
                sytj_block["text"] = f"{prev}\n\n{piece}".strip() if prev else piece
            elif kb == "sygc":
                prev = sygc_block["text"]
                sygc_block["text"] = f"{prev}\n\n{piece}".strip() if prev else piece
            elif kb == "jgpd":
                prev = jgpd_block["text"]
                jgpd_block["text"] = f"{prev}\n\n{piece}".strip() if prev else piece

        for sec in eligible_sections:
            title = str(sec.get("title", "") or "")
            top_num = _section_top_level_num(title)
            text = _clean_rule_block_text(str(sec.get("text", "") or ""))

            if top_num in sytj_nums:
                if text:
                    _append_to_bucket("sytj", text)
                    sid = str(sec.get("section_id", "") or "")
                    if sid:
                        numeric_assigned_sids.add(sid)
            elif top_num in sygc_nums:
                if text:
                    _append_to_bucket("sygc", text)
                    sid = str(sec.get("section_id", "") or "")
                    if sid:
                        numeric_assigned_sids.add(sid)
            elif top_num in jgpd_nums_set:
                if text:
                    _append_to_bucket("jgpd", text)
                    sid = str(sec.get("section_id", "") or "")
                    if sid:
                        numeric_assigned_sids.add(sid)

        for sec in eligible_sections:
            sid = str(sec.get("section_id", "") or "")
            if sid and sid in numeric_assigned_sids:
                continue
            kb = _classify_section_bucket(sec)
            if kb == "none":
                continue
            text = _clean_rule_block_text(str(sec.get("text", "") or ""))
            if not text:
                continue
            title = str(sec.get("title", "") or "").strip()
            piece = f"{title}\n{text}" if title else text
            _append_to_bucket(kb, piece)

        for sec in eligible_sections:
            title = str(sec.get("title", "") or "")
            top_num = _section_top_level_num(title)
            sid = str(sec.get("section_id", "") or "")
            if top_num in sytj_nums:
                section_bucket_map[sid] = "sytj"
            elif top_num in sygc_nums:
                section_bucket_map[sid] = "sygc"
            elif top_num in jgpd_nums_set:
                section_bucket_map[sid] = "jgpd"

        for sec in eligible_sections:
            sid = str(sec.get("section_id", "") or "")
            if not sid or sid in section_bucket_map:
                continue
            kb = _classify_section_bucket(sec)
            if kb != "none":
                section_bucket_map[sid] = kb

    # 表格归入对应字段
    page_tables = parsed_doc.get("page_table_map", {})

    all_tables = collect_tables_for_sections(
        sections=eligible_sections,
        page_tables=page_tables,
        section_ids=[sec.get("section_id") for sec in eligible_sections if sec.get("section_id")],
    )
    for tb in all_tables or []:
        if not isinstance(tb, dict):
            continue
        sid = str(tb.get("section_id", "") or "")
        html = str(tb.get("html", "") or "").strip()
        if not html:
            continue
        preview = str(tb.get("text_preview", "") or "") + "\n" + html
        bucket = section_bucket_map.get(sid)
        if not bucket and any(k in preview for k in ("试验条件", "条件及容差", "温度", "相对湿度", "试验周期")):
            bucket = "sytj"
        if bucket == "sytj":
            sytj_block["tables_html"].append(html)
        elif bucket == "sygc":
            sygc_block["tables_html"].append(html)
        elif bucket == "jgpd":
            jgpd_block["tables_html"].append(html)

    if not use_title_only:
        for block in (sytj_block, sygc_block, jgpd_block):
            if not str(block.get("text", "") or "").strip():
                block["text"] = "文档未明确说明"

    method = _postprocess_method_blocks({
        "candidate_id": "M001",
        "class": "分则",
        "section_ids": [sec.get("section_id") for sec in eligible_sections if sec.get("section_id")],
        "name": method_name,
        "type": "试验方法",
        "description": f"从 {file_name} 按章节规则提取的试验方法",
        "keywords": [],
        "recall_plan": {"environment": {"keywords": []}, "test_project": {"keywords": []}, "indicator": {"keywords": []}},
        "sytj": sytj_block,
        "sygc": sygc_block,
        "jgpd": jgpd_block,
        "_rule_numbering_scheme": numbering_scheme,
        "_rule_jgpd_top_nums": tuple(sorted(jgpd_nums_set)),
    })


    logger.info(
        "[GetTestMethod][规则化提取] 方法: %s, sytj=%d字符, sygc=%d字符, jgpd=%d字符",
        method_name,
        len(sytj_block["text"]),
        len(sygc_block["text"]),
        len(jgpd_block["text"]),
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
            "class": "分则",
            "summary": f"规则化提取，来源文件: {file_name}",
        },
        "terms": [],
        "method_candidates": [{
            "candidate_id": "M001",
            "candidate_title": method_name,
            "candidate_type_hint": "试验方法",
            "summary_hint": "规则化提取（按大标题编号归类）",
            "section_ids": method["section_ids"],
            "reason": "规则化提取，未使用大模型",
        }],
        "methods": [method],
    }
