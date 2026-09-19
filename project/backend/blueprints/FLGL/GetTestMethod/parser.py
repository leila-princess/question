import os
import re
import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from docx import Document
except ImportError:
    Document = None


PAGE_MARKER_RE = re.compile(r"^\s*=+\s*Page\s+(\d+)\s*=+\s*$", re.IGNORECASE)
DECIMAL_HEADING_RE = re.compile(
    r"^\s*(\d+(?:\.\d+){0,5})(?:[)\.、\s]+|(?=[\u4e00-\u9fff]))(.+?)\s*$"
)
CHAPTER_HEADING_RE = re.compile(r"^\s*第[一二三四五六七八九十百零0-9]+[章节部分篇]\s*(.+?)?\s*$")
APPENDIX_HEADING_RE = re.compile(r"^\s*附录\s*[A-Za-z0-9一二三四五六七八九十]?\s*(.*)$")
COMMON_HEADING_PREFIXES = (
    "范围", "规范性引用文件", "引证文件", "引用文件", "术语和定义", "术语", "定义", "缩略语",
    "总则", "通用要求", "目的和应用", "目的", "应用", "限制", "裁裁指南", "剪裁指南",
    "试验条件", "试验程序", "试验方法", "试验步骤", "试验过程", "试验要求", "信息要求",
    "试验中断", "中断", "恢复", "结果评定", "结果分析", "判定规则", "检验规则",
    "高温", "低温", "湿热", "振动", "冲击", "跌落", "贮存", "工作",
)
DEFAULT_SECTION_TITLE = "全文"

WATERMARK_HINTS = (
    "weisstech", "gjb标准", "中国可靠性网", "kekaoxing", "http://", "https://",
    "confidential", "internal use", "draft", "样张", "机密", "内部资料",
)
STANDARD_SIGNAL_WORDS = (
    "范围", "术语", "定义", "目的", "应用", "限制", "试验", "方法", "过程",
    "结果", "分析", "信息要求", "试验要求", "确定试验条件", "工作考虑", "表",
)


def _normalize_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").replace("\r", "")).strip()


def _strip_table_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


_GB_KNOWN_SECTION_TITLES = (
    "试验设备和仪器",
    "试验中断和恢复",
    "试验结果评定",
    "试验报告",
    "低温贮存试验",
    "低温工作试验",
    "低温贮存试验结果",
    "低温工作试验结果",
    "欠试验中断",
    "过试验中断",
    "试验前准备",
    "试验前检测",
    "试验后检测",
    "试验持续时间",
    "温度变化率",
    "试验条件",
    "试验过程",
    "术语和定义",
    "规范性引用文件",
    "试验准备",
    "试验步骤",
    "试验温度",
    "受试设备",
    "概述",
    "预处理",
    "恢复",
    "范围",
)


def split_merged_decimal_heading_line(line: str) -> Tuple[str, Optional[str]]:
    """
    MinerU Markdown 常把「## 5 标题正文…」合成一行。
    拆成 (标题行, 正文行)；无法拆分则正文为 None。
    """
    raw = (line or "").rstrip()
    s = raw.strip()
    if not s:
        return raw, None

    md_prefix = ""
    body = s
    if s.startswith("#"):
        hm = re.match(r"^(#+\s*)(.+)$", s)
        if hm:
            md_prefix = hm.group(1)
            body = hm.group(2).strip()

    m = DECIMAL_HEADING_RE.match(body)
    if not m:
        return raw, None

    sec_no = m.group(1)
    rest = (m.group(2) or "").strip()
    if not rest or len(body) <= 80:
        return raw, None

    for title in sorted(_GB_KNOWN_SECTION_TITLES, key=len, reverse=True):
        if rest.startswith(title) and len(rest) > len(title):
            tail = rest[len(title):].lstrip()
            if tail:
                heading = f"{md_prefix}{sec_no} {title}".strip()
                return heading, tail

    m2 = re.match(
        r"^([\u4e00-\u9fffA-Za-z（）()·]{2,16})"
        r"(用于|除|按|应|宜|若|当|对|在|各|凡|同\s*\d|试验开始|本部分|下列|下列文件|下列术语)",
        rest,
    )
    if m2:
        title = m2.group(1)
        tail = rest[len(title):].lstrip()
        if tail:
            heading = f"{md_prefix}{sec_no} {title}".strip()
            return heading, tail
    return raw, None


def preprocess_merged_section_lines(text: str) -> str:
    out: List[str] = []
    for line in (text or "").splitlines():
        head, tail = split_merged_decimal_heading_line(line)
        out.append(head)
        if tail:
            out.append(tail)
    return "\n".join(out)


def _rebuild_full_text_from_pages(pages: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for page in pages:
        text = (page.get("text") or "").strip()
        if text:
            parts.append(f"===== Page {page.get('page_number')} =====\n{text}")
    return "\n\n".join(parts).strip()


def _normalize_line_for_compare(line: str) -> str:
    s = _normalize_spaces(line).lower()
    s = re.sub(r"\s+", "", s)
    return s


def _is_probable_noise_line(line: str) -> bool:
    raw = _normalize_spaces(line)
    if not raw:
        return False
    lowered = raw.lower()
    if any(h in lowered for h in WATERMARK_HINTS):
        return True
    # 单独页码、罗马数字页码、过短的英文水印
    if re.fullmatch(r"[ivxlcdmIVXLCDM0-9]+", raw):
        return True
    if len(raw) <= 20 and re.fullmatch(r"[A-Za-z0-9_\-./: ]+", raw):
        return True
    return False


def _collect_repeated_noise_lines(pages: List[Dict[str, Any]], min_page_ratio: float = 0.6) -> set:
    total_pages = max(len(pages), 1)
    threshold = max(2, int(total_pages * min_page_ratio + 0.999))
    counter: Counter = Counter()
    original_map: Dict[str, str] = {}

    for page in pages:
        seen_once = set()
        for line in (page.get("text") or "").splitlines():
            key = _normalize_line_for_compare(line)
            if not key or key in seen_once:
                continue
            seen_once.add(key)
            counter[key] += 1
            original_map.setdefault(key, line)

    repeated = set()
    for key, cnt in counter.items():
        line = original_map.get(key, "")
        if cnt >= threshold and _is_probable_noise_line(line):
            repeated.add(key)
    return repeated


def _clean_pages_noise(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not pages:
        return []

    repeated_noise = _collect_repeated_noise_lines(pages)
    cleaned_pages: List[Dict[str, Any]] = []
    global_seen = set()

    for page in pages:
        kept_lines: List[str] = []
        page_no = int(page.get("page_number", 0) or 0)
        for line in (page.get("text") or "").splitlines():
            key = _normalize_line_for_compare(line)
            if not key:
                continue
            if key in repeated_noise:
                continue
            raw = _normalize_spaces(line)
            # 目录/目次页常见噪声：大量点线、孤立页码、短标题页码组合。
            if page_no <= 3 and (raw.count("…") >= 5 or re.fullmatch(r"[\.．·…\s\d]+", raw)):
                continue
            # 跨页 OCR 重复片段去重，避免同一页整页 OCR 与区域 OCR 叠加造成方法内容膨胀。
            if len(key) >= 12 and key in global_seen:
                continue
            if len(key) >= 12:
                global_seen.add(key)
            kept_lines.append(line)

        cleaned_pages.append({
            **page,
            "text": "\n".join(kept_lines).strip(),
        })

    return cleaned_pages


def _pdf_text_quality(data: Dict[str, Any]) -> Dict[str, Any]:
    pages = list(data.get("pages", []) or [])
    full_text = (data.get("full_text") or "").strip()
    nonempty_pages = [p for p in pages if (p.get("text") or "").strip()]
    page_texts = [_normalize_spaces(p.get("text") or "") for p in nonempty_pages]
    unique_ratio = len(set(page_texts)) / max(len(page_texts), 1)

    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", full_text))
    replacement_like = full_text.count("�") + len(re.findall(r"[\u00c0-\u024f\ue000-\uf8ff]", full_text))
    keyword_hits = sum(1 for kw in STANDARD_SIGNAL_WORDS if kw in full_text)

    meaningful_pages = 0
    noise_pages = 0
    for t in page_texts:
        zh_count = len(re.findall(r"[\u4e00-\u9fff]", t))
        if zh_count >= 20 or any(kw in t for kw in STANDARD_SIGNAL_WORDS):
            meaningful_pages += 1
        if _is_probable_noise_line(t) or all(_is_probable_noise_line(x) for x in t.splitlines() if x.strip()):
            noise_pages += 1

    return {
        "page_count": len(pages),
        "nonempty_page_count": len(nonempty_pages),
        "full_text_len": len(full_text),
        "chinese_chars": chinese_chars,
        "garbled_chars": replacement_like,
        "keyword_hits": keyword_hits,
        "meaningful_pages": meaningful_pages,
        "unique_ratio": unique_ratio,
        "noise_pages": noise_pages,
    }


def _should_force_ocr(fitz_data: Dict[str, Any]) -> bool:
    q = _pdf_text_quality(fitz_data)
    full_text = (fitz_data.get("full_text") or "")

    try:
        from blueprints.ZSGL.WJGL1.text_normalize import has_cid_garbled, count_symbol_font_headings
        if has_cid_garbled(full_text[:4000]):
            return True
        if count_symbol_font_headings(full_text) >= 3:
            return True
    except Exception:
        pass

    if os.environ.get("FLGL_FORCE_OCR", "").strip() in ("1", "true", "yes"):
        return True

    if q["nonempty_page_count"] == 0:
        return True
    if q["full_text_len"] < 800:
        return True
    if q.get("garbled_chars", 0) > max(80, q["chinese_chars"] * 2) and q["keyword_hits"] == 0:
        return True
    if q["chinese_chars"] < 120:
        return True
    if q["meaningful_pages"] < 2:
        return True
    if q["unique_ratio"] < 0.5:
        return True
    if q["noise_pages"] >= max(3, q["page_count"] // 2) and q["keyword_hits"] == 0:
        return True
    return False


def _is_heading_candidate(line: str) -> bool:
    s = _normalize_spaces(line)
    if not s:
        return False
    if len(s) > 80:
        return False
    m = DECIMAL_HEADING_RE.match(s)
    if m:
        title = (m.group(2) or "").strip()
        if not title:
            return False
        if _contains_chinese(title):
            return True
        if any(title.startswith(prefix) for prefix in COMMON_HEADING_PREFIXES):
            return True
        if re.match(r"^[A-Za-z犃-犲]类", title):
            return True
        return False
    if CHAPTER_HEADING_RE.match(s):
        return True
    if APPENDIX_HEADING_RE.match(s):
        return True
    if any(s.startswith(prefix) for prefix in COMMON_HEADING_PREFIXES):
        return True
    if re.match(r"^[（(]?[0-9一二三四五六七八九十]+[)）][、\s]?", s):
        return True
    return False


def _heading_level(line: str) -> int:
    s = _normalize_spaces(line)
    m = DECIMAL_HEADING_RE.match(s)
    if m:
        return len(m.group(1).split("."))
    if CHAPTER_HEADING_RE.match(s):
        return 1
    if APPENDIX_HEADING_RE.match(s):
        return 1
    return 2


def _read_txt(file_path: str) -> Dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    return {
        "mode": "txt",
        "full_text": text.strip(),
        "pages": [{"page_number": 1, "text": text.strip(), "tables_html": [], "figures": []}],
        "all_tables": [],
        "ocr_used": False,
    }


def _read_docx(file_path: str) -> Dict[str, Any]:
    """Word 原生解析（段落/表格/图片/标题 HTML），不经过 MinerU。"""
    from blueprints.ZSGL.WJGL1.word_native_parse import parse_word_document
    return parse_word_document(file_path)


def _read_word_native(file_path: str, ocr_output_dir: Optional[str] = None) -> Dict[str, Any]:
    """.docx / .doc 统一走原生 Word 解析。"""
    from blueprints.ZSGL.WJGL1.word_native_parse import parse_word_document
    return parse_word_document(file_path, ocr_output_dir=ocr_output_dir)


def _read_pdf_with_fitz(file_path: str) -> Dict[str, Any]:
    if fitz is None:
        raise RuntimeError("缺少 PyMuPDF，请先安装：pip install pymupdf")

    doc = fitz.open(file_path)
    pages: List[Dict[str, Any]] = []
    all_text_parts: List[str] = []
    for i in range(len(doc)):
        page = doc[i]
        text = (page.get_text("text") or "").strip()
        pages.append({
            "page_number": i + 1,
            "text": text,
            "tables_html": [],
            "figures": [],
        })
        if text:
            all_text_parts.append(f"===== Page {i + 1} =====\n{text}")
    doc.close()

    return {
        "mode": "pdf_fitz",
        "full_text": "\n\n".join(all_text_parts).strip(),
        "pages": pages,
        "all_tables": [],
        "ocr_used": False,
    }


def _tables_from_worker_page_tables(raw_tables: Any) -> List[str]:
    """backend/ocr 子进程页内 tables 可能为 {html, page, bbox} 或 HTML 字符串。"""
    out: List[str] = []
    for t in raw_tables or []:
        if isinstance(t, dict):
            html = str(t.get("html") or t.get("res", {}).get("html") or "").strip()
            if html:
                out.append(html)
        elif isinstance(t, str) and t.strip():
            out.append(t.strip())
    return out


def _subprocess_ocr_data_to_pages_and_tables(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """将 backend.ocr.run_ocr_pdf 返回的 data 转为与 pdf_ocr_extractor 一致的页结构与 all_tables。"""
    pages_out: List[Dict[str, Any]] = []
    all_tables: List[Dict[str, Any]] = []
    for wp in data.get("pages") or []:
        if not isinstance(wp, dict):
            continue
        pno = int(wp.get("page_num") or wp.get("page_number") or 0)
        text = (wp.get("text") or "").strip()
        tables_html = _tables_from_worker_page_tables(wp.get("tables"))
        for h in tables_html:
            all_tables.append({"page": pno, "html": h})
        pages_out.append({
            "page_number": pno,
            "text": text,
            "tables_html": tables_html,
            "figures": list(wp.get("figures") or []),
        })
    return pages_out, all_tables


def _write_ocr_cache_like_pdf_extractor(
    output_dir: str,
    pdf_path: str,
    result: Dict[str, Any],
) -> None:
    """写入 extracted_text.txt / ocr_result.json，与 PDFOCRExtractor 缓存格式一致，便于下次命中缓存。"""
    from datetime import datetime

    base = _ocr_stem_for_pdf(pdf_path)
    out_dir = os.path.join(output_dir, f"ocr_{base}")
    os.makedirs(out_dir, exist_ok=True)
    pages = result.get("pages") or []
    full_text = (result.get("full_text") or "").strip()
    txt_path = os.path.join(out_dir, "extracted_text.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(full_text)
    cache_pages = []
    for p in pages:
        cache_pages.append({
            "page_number": p.get("page_number"),
            "text": (p.get("text") or "").strip(),
            "tables": list(p.get("tables_html") or []),
            "figures": list(p.get("figures") or []),
        })
    payload = {
        "pdf_name": result.get("pdf_name") or os.path.basename(pdf_path),
        "total_pages": int(result.get("total_pages") or len(cache_pages)),
        "pages": cache_pages,
        "full_text": full_text,
        "all_tables": list(result.get("all_tables") or []),
        "all_figures": list(result.get("all_figures") or []),
        "metadata": {
            "pdf_name": result.get("pdf_name"),
            "processed_at": datetime.now().isoformat(),
            "ocr_engine": "PaddleOCR(subprocess)",
            "dpi": 300,
            "table_count": len(result.get("all_tables") or []),
            "figure_count": len(result.get("all_figures") or []),
            "text_length": len(full_text),
        },
    }
    json_path = os.path.join(out_dir, "ocr_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _normalize_all_tables_from_pages(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in pages or []:
        pno = int(p.get("page_number") or 0)
        for h in p.get("tables_html") or []:
            hs = str(h or "").strip()
            if hs:
                out.append({"page": pno, "html": hs})
    return out


def _read_pdf_with_subprocess_backend_ocr(file_path: str, output_dir: str) -> Optional[Dict[str, Any]]:
    """
    优先使用 backend/ocr（独立 paddle 子进程），避免与主进程 torch/numpy 冲突。
    若未安装、paddle_env 不可用或调用失败则返回 None。
    """
    if os.environ.get("FLGL_METHOD_OCR_SUBPROCESS", "1").strip().lower() in ("0", "false", "no"):
        return None
    try:
        from backend.ocr import run_ocr_pdf
    except ImportError:
        try:
            from ocr import run_ocr_pdf
        except ImportError:
            logger.warning("backend.ocr 不可用，跳过子进程 OCR")
            return None

    try:
        timeout_s = int(os.environ.get("FLGL_OCR_SUBPROCESS_TIMEOUT", "1200"))
    except ValueError:
        timeout_s = 1200
    try:
        ocr_start = int(os.environ.get("FLGL_OCR_START_PAGE", "3"))
    except ValueError:
        ocr_start = 3
    try:
        ocr_dpi = int(os.environ.get("FLGL_OCR_DPI", "220"))
    except ValueError:
        ocr_dpi = 220

    logger.info(
        "[GetTestMethod][parser] 子进程 OCR 调用: start_page=%s dpi=%s timeout=%s",
        ocr_start,
        ocr_dpi,
        timeout_s,
    )
    use_layout = _resolve_ocr_use_layout()
    raw = run_ocr_pdf(
        file_path,
        original_filename=os.path.basename(file_path),
        output_dir=output_dir,
        dpi=ocr_dpi,
        timeout=timeout_s,
        start_page=max(1, ocr_start),
        end_page=None,
        use_layout=use_layout,
    )
    if not isinstance(raw, dict) or not raw.get("success"):
        logger.warning("子进程 OCR 失败: %s", raw.get("error") if isinstance(raw, dict) else raw)
        return None
    data = raw.get("data")
    if not isinstance(data, dict):
        return None

    pages_raw, all_tables = _subprocess_ocr_data_to_pages_and_tables(data)
    pages = _clean_pages_noise(pages_raw)
    full_text = _rebuild_full_text_from_pages(pages)
    if not full_text.strip():
        logger.warning("子进程 OCR 返回空文本，将回退进程内 OCR")
        return None

    pdf_name = str(data.get("file_name") or os.path.basename(file_path))
    all_tables_final = all_tables or _normalize_all_tables_from_pages(pages)
    ocr_result = {
        "pdf_name": pdf_name,
        "total_pages": int(data.get("total_pages") or len(pages)),
        "pages": [
            {
                "page_number": p.get("page_number"),
                "text": (p.get("text") or "").strip(),
                "tables": list(p.get("tables_html") or []),
                "figures": list(p.get("figures") or []),
            }
            for p in pages
        ],
        "full_text": full_text,
        "all_tables": all_tables_final,
        "all_figures": list(data.get("all_figures") or []),
        "metadata": {"ocr_engine": "PaddleOCR(subprocess)"},
    }
    try:
        _write_ocr_cache_like_pdf_extractor(
            output_dir,
            file_path,
            {
                **ocr_result,
                "pages": pages,
            },
        )
    except OSError as e:
        logger.warning("写入 OCR 缓存失败（不影响本次解析）: %s", e)

    return {
        "mode": "pdf_ocr",
        "full_text": full_text,
        "pages": pages,
        "all_tables": list(all_tables_final),
        "ocr_used": True,
        "ocr_result": ocr_result,
    }


def _read_pdf_with_ocr(file_path: str, output_dir: str) -> Dict[str, Any]:
    subprocess_parsed = _read_pdf_with_subprocess_backend_ocr(file_path, output_dir)
    if subprocess_parsed is not None:
        return subprocess_parsed

    # 延迟导入：避免仅走 OCR 缓存或 LLM 调试时加载 PaddleOCR/imgaug（与 numpy 2.x 常冲突）
    from blueprints.FLGL.pdf_ocr_extractor import OCRExtractorConfig, PDFOCRExtractor

    config = OCRExtractorConfig(
        pdf_image_dpi=300,
        ocr_use_gpu=False,
        output_dir=output_dir,
    )
    extractor = PDFOCRExtractor(config)
    result = extractor.extract(file_path)

    pages = []
    for page in result.get("pages", []):
        pages.append({
            "page_number": page.get("page_number"),
            "text": (page.get("text") or "").strip(),
            "tables_html": list(page.get("tables", []) or []),
            "figures": list(page.get("figures", []) or []),
        })

    pages = _clean_pages_noise(pages)
    full_text = _rebuild_full_text_from_pages(pages)

    return {
        "mode": "pdf_ocr",
        "full_text": full_text,
        "pages": pages,
        "all_tables": list(result.get("all_tables", []) or []),
        "ocr_used": True,
        "ocr_result": result,
    }


def _ocr_stem_for_pdf(pdf_path: str) -> str:
    """与 pdf_ocr_extractor.PDFOCRExtractor.extract 中目录名规则一致: ocr_{base_name}。"""
    pdf_name = os.path.basename(pdf_path)
    return pdf_name.replace(".pdf", "").replace(".PDF", "").replace("+", "_")


def ocr_cache_extracted_text_path(pdf_path: str, ocr_output_dir: str) -> str:
    """如 backend/output_json/ocr_tmp/ocr_GBT38924.3-2020/extracted_text.txt"""
    base = _ocr_stem_for_pdf(pdf_path)
    return os.path.join(ocr_output_dir, f"ocr_{base}", "extracted_text.txt")


def ocr_cache_result_json_path(pdf_path: str, ocr_output_dir: str) -> str:
    base = _ocr_stem_for_pdf(pdf_path)
    return os.path.join(ocr_output_dir, f"ocr_{base}", "ocr_result.json")


def _normalize_table_html_items(items: Any) -> List[str]:
    out: List[str] = []
    for item in items or []:
        if isinstance(item, str):
            html = item.strip()
        elif isinstance(item, dict):
            html = str(item.get("html") or item.get("res", {}).get("html") or "").strip()
        else:
            html = str(item or "").strip()
        if html:
            out.append(html)
    return out


def _parsed_from_ocr_result_json(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    pages = []
    for page in payload.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        pages.append({
            "page_number": page.get("page_number"),
            "text": (page.get("text") or "").strip(),
            "tables_html": _normalize_table_html_items(page.get("tables") or page.get("tables_html") or []),
            "figures": list(page.get("figures") or []),
        })
    if not pages:
        return None
    pages = _clean_pages_noise(pages)
    return {
        "mode": "pdf_ocr",
        "full_text": _rebuild_full_text_from_pages(pages),
        "pages": pages,
        "all_tables": [
            {**x, "html": _normalize_table_html_items([x])[0] if _normalize_table_html_items([x]) else ""}
            if isinstance(x, dict) else {"page": None, "html": h}
            for x in (payload.get("all_tables", []) or [])
            for h in (_normalize_table_html_items([x]) or [])
        ],
        "ocr_used": True,
        "from_ocr_cache": True,
        "from_ocr_json_cache": True,
        "ocr_result": payload,
    }


def _parsed_from_cached_extracted_text(full_text: str) -> Optional[Dict[str, Any]]:
    """
    从历史上保存的 extracted_text.txt 内容还原与 pdf_ocr 兼容的页结构，便于跳过重跑 Paddle OCR。
    """
    s = (full_text or "").strip()
    if not s:
        return None
    page_start = re.compile(r"^\s*=+\s*Page\s+(\d+)\s*=+\s*$", re.IGNORECASE | re.MULTILINE)
    matches = list(page_start.finditer(s))
    if not matches:
        pages: List[Dict[str, Any]] = [
            {"page_number": 1, "text": s, "tables_html": [], "figures": []}
        ]
        pages = _clean_pages_noise(pages)
        return {
            "mode": "pdf_ocr",
            "full_text": _rebuild_full_text_from_pages(pages),
            "pages": pages,
            "all_tables": [],
            "ocr_used": True,
            "from_ocr_cache": True,
        }
    pages = []
    for i, m in enumerate(matches):
        pno = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        chunk = s[start:end].strip()
        if chunk:
            pages.append(
                {
                    "page_number": pno,
                    "text": chunk,
                    "tables_html": [],
                    "figures": [],
                }
            )
    if not pages:
        return None
    pages = _clean_pages_noise(pages)
    return {
        "mode": "pdf_ocr",
        "full_text": _rebuild_full_text_from_pages(pages),
        "pages": pages,
        "all_tables": [],
        "ocr_used": True,
        "from_ocr_cache": True,
    }


def _try_load_parsed_from_ocr_json_cache(cache_path: str) -> Optional[Dict[str, Any]]:
    if not (cache_path and os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return _parsed_from_ocr_result_json(payload)


def _try_load_parsed_from_ocr_cache(cache_path: str) -> Optional[Dict[str, Any]]:
    if not (cache_path and os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    return _parsed_from_cached_extracted_text(raw)


def _ocr_failure_network_hint(ocr_error: Optional[Exception]) -> str:
    if ocr_error is None:
        return ""
    msg = str(ocr_error).lower()
    keys = (
        "getaddrinfo",
        "failed to resolve",
        "name resolution",
        "connection",
        "max retries",
        "timed out",
        "timeout",
        "bcebos",
        "443",
    )
    if not any(k in msg for k in keys):
        return ""
    return (
        " 离线/DNS 无法访问模型下载地址时：请在可联网环境预先初始化 PaddleOCR（自动下载到用户目录 "
        "`.paddleocr/whl/`），将整个 `.paddleocr` 拷贝到本机相同路径；或改用已有 OCR 缓存 "
        "`backend/output_json/ocr_tmp/ocr_<pdf主文件名>/extracted_text.txt` 后重试。"
    )


def _choose_pdf_result(
    fitz_data: Dict[str, Any],
    ocr_data: Optional[Dict[str, Any]],
    fitz_bad: bool,
    ocr_error: Optional[Exception],
) -> Dict[str, Any]:
    if ocr_data is None:
        if fitz_bad:
            detail = f"；OCR失败：{ocr_error}" if ocr_error else ""
            hint = _ocr_failure_network_hint(ocr_error)
            raise RuntimeError(
                "PDF文本层提取质量过低，且无法获得可用的OCR结果。"
                "当前继续运行只会得到页眉/水印等无效内容，请先检查PaddleOCR环境或PDF可读性"
                f"{detail}"
                f"{hint}"
            )
        return fitz_data

    fitz_q = _pdf_text_quality(fitz_data)
    ocr_q = _pdf_text_quality(ocr_data)

    fitz_readable = (
        fitz_q["keyword_hits"] >= 5
        and fitz_q["meaningful_pages"] >= 3
        and fitz_q["chinese_chars"] >= 500
    )

    # fitz明显差时，优先用OCR，只要OCR有基本可用内容。
    if fitz_bad and (ocr_q["meaningful_pages"] >= 2 or ocr_q["chinese_chars"] >= 120):
        chosen = ocr_data
    elif fitz_readable:
        # GB/T 38924 这类 PDF 常见：文本层正文基本可读，但表格结构缺失；OCR 正文易重复乱序。
        # 因此优先保留 fitz 正文，只从 OCR 挂表格/图片。
        chosen = fitz_data
    else:
        # 一般情况下，比较质量而不是纯长度
        choose_ocr = False
        if ocr_q["meaningful_pages"] > fitz_q["meaningful_pages"]:
            choose_ocr = True
        elif ocr_q["keyword_hits"] > fitz_q["keyword_hits"]:
            choose_ocr = True
        elif ocr_q["chinese_chars"] > max(fitz_q["chinese_chars"] * 1.2, fitz_q["chinese_chars"] + 100):
            choose_ocr = True
        elif len(ocr_data.get("all_tables", []) or []) > 0 and fitz_q["keyword_hits"] == 0:
            choose_ocr = True

        chosen = ocr_data if choose_ocr else fitz_data

    # 若最终仍选fitz，也尽量把OCR表格挂上去；正文仍用 fitz 文本，避免 OCR 整页噪声污染。
    if chosen is fitz_data and ocr_data is not None:
        ocr_pages_by_num = {p["page_number"]: p for p in ocr_data.get("pages", [])}
        enriched_pages = []
        for page in chosen.get("pages", []):
            current = dict(page)
            ocr_page = ocr_pages_by_num.get(page.get("page_number"))
            if ocr_page:
                current["tables_html"] = list(ocr_page.get("tables_html", []) or [])
                current["figures"] = list(ocr_page.get("figures", []) or [])
            enriched_pages.append(current)
        chosen = dict(chosen)
        chosen["pages"] = enriched_pages
        chosen["all_tables"] = list(ocr_data.get("all_tables", []) or [])
        chosen["full_text"] = _rebuild_full_text_from_pages(enriched_pages)

    chosen = dict(chosen)
    chosen["pages"] = _clean_pages_noise(list(chosen.get("pages", []) or []))
    chosen["full_text"] = _rebuild_full_text_from_pages(chosen["pages"])
    return chosen


def _build_page_table_map(pages: List[Dict[str, Any]]) -> Dict[int, List[str]]:
    result: Dict[int, List[str]] = {}
    for page in pages:
        page_no = int(page.get("page_number", 0) or 0)
        tables = list(page.get("tables_html", []) or [])
        if page_no > 0:
            result[page_no] = tables
    return result


def _has_usable_table_html(tables_html: List[Any]) -> bool:
    for th in tables_html or []:
        hs = str(th or "").strip()
        if hs and "<td" in hs.lower():
            return True
    return False


def _matrix_to_html_table(matrix: List[List[Any]]) -> str:
    import html as html_module

    if not matrix:
        return ""
    rows_html: List[str] = []
    for row in matrix:
        if not row:
            continue
        cells = "".join(f"<td>{html_module.escape(str(c or '').strip())}</td>" for c in row)
        rows_html.append(f"<tr>{cells}</tr>")
    if not rows_html:
        return ""
    return "<table>" + "".join(rows_html) + "</table>"


def _table_cell_count(html: str) -> int:
    return str(html or "").lower().count("<td")


def _table_plain_signature(html: str) -> str:
    plain = _strip_table_tags(str(html or ""))
    return re.sub(r"\s+", "", plain)[:200]


def _table_html_similar(a: str, b: str) -> bool:
    sa = _table_plain_signature(a)
    sb = _table_plain_signature(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return len(short) >= 24 and short in long


def _figure_dimensions(fig: Dict[str, Any]) -> Tuple[int, int]:
    w = int(fig.get("width") or 0)
    h = int(fig.get("height") or 0)
    if w > 0 and h > 0:
        return w, h
    path = str(fig.get("path") or fig.get("file") or "")
    if path and os.path.isfile(path):
        try:
            from PIL import Image

            with Image.open(path) as im:
                return im.size
        except Exception:
            pass
    return w, h


def _figure_content_signature(fig: Dict[str, Any]) -> str:
    import hashlib

    w, h = _figure_dimensions(fig)
    path = str(fig.get("path") or fig.get("file") or "")
    if path and os.path.isfile(path):
        try:
            raw = open(path, "rb").read(8192)
            return hashlib.md5(raw).hexdigest()[:16]
        except Exception:
            pass
    return f"{w}x{h}"


def _is_content_figure(fig: Dict[str, Any]) -> bool:
    """过滤过小图片 / 疑似水印 / 页眉页脚装饰图。"""
    w, h = _figure_dimensions(fig)
    area = w * h
    if w <= 0 or h <= 0:
        return False
    if w < 150 or h < 150 or area < 30000:
        return False
    if area < 80000 and max(w, h) < 220:
        return False
    image_type = str(fig.get("image_type") or fig.get("type") or "").lower()
    if image_type in ("watermark", "stamp", "logo", "header", "footer"):
        return False
    return True


def _filter_watermark_figures(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """跨页重复的小图视为水印；每页最多保留 2 张内容图。"""
    sig_pages: Dict[str, set] = {}
    total_with_figs = 0

    for page in pages or []:
        figs = [f for f in (page.get("figures") or []) if isinstance(f, dict)]
        if figs:
            total_with_figs += 1
        for fig in figs:
            if not _is_content_figure(fig):
                continue
            sig = _figure_content_signature(fig)
            sig_pages.setdefault(sig, set()).add(
                int(page.get("page_number") or page.get("page_num") or 0),
            )

    repeat_threshold = max(3, int(total_with_figs * 0.35)) if total_with_figs else 999
    watermark_sigs = {sig for sig, pset in sig_pages.items() if len(pset) >= repeat_threshold}

    out_pages: List[Dict[str, Any]] = []
    for page in pages or []:
        p = dict(page)
        kept: List[Dict[str, Any]] = []
        for fig in p.get("figures") or []:
            if not isinstance(fig, dict):
                continue
            if not _is_content_figure(fig):
                continue
            sig = _figure_content_signature(fig)
            if sig in watermark_sigs:
                continue
            w, h = _figure_dimensions(fig)
            kept.append({**fig, "width": w, "height": h})

        kept.sort(key=lambda f: int(f.get("width") or 0) * int(f.get("height") or 0), reverse=True)
        p["figures"] = kept[:2]
        out_pages.append(p)
    return out_pages


def _resolve_ocr_use_layout() -> Optional[bool]:
    for key in ("WJGL_USE_LAYOUT", "FLGL_OCR_USE_LAYOUT", "OCR_USE_LAYOUT"):
        raw = os.environ.get(key, "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
    return None


def _extract_pdfplumber_page_tables(pl_page) -> List[str]:
    """pdfplumber 表格提取（仅 lines 策略，避免把正文/目录误识别为表）。"""
    found: List[str] = []
    seen_sigs: set = set()
    settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
    }

    def _add_matrix(matrix: List[List[Any]]) -> None:
        if not matrix or not any(any(str(c or "").strip() for c in row) for row in matrix):
            return
        th = _matrix_to_html_table(matrix)
        if not th or _table_cell_count(th) < 4:
            return
        sig = _table_plain_signature(th)
        if sig in seen_sigs:
            return
        seen_sigs.add(sig)
        found.append(th)

    try:
        for tbl in pl_page.find_tables(table_settings=settings) or []:
            _add_matrix(tbl.extract())
    except Exception:
        pass
    if not found:
        try:
            for matrix in pl_page.extract_tables() or []:
                _add_matrix(matrix)
        except Exception:
            pass
    return found


def _supplement_pdf_native_tables_and_figures(
    file_path: str,
    pages: List[Dict[str, Any]],
    output_dir: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    用 pdfplumber 提取表格、fitz 提取嵌入图片，补齐无 layout OCR 时缺失的 tables_html / figures。
    仅在对应页尚无 tables_html / figures 时补充，避免覆盖 PPStructure 结果。
    """
    if not (file_path or "").lower().endswith(".pdf"):
        return pages, []

    page_by_no: Dict[int, Dict[str, Any]] = {}
    for page in pages or []:
        pno = int(page.get("page_number") or page.get("page_num") or 0)
        if pno > 0:
            page_by_no[pno] = dict(page)

    extra_tables: List[Dict[str, Any]] = []

    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None  # type: ignore

    if pdfplumber is not None:
        try:
            with pdfplumber.open(file_path) as pdf:
                for i, pl_page in enumerate(pdf.pages):
                    pno = i + 1
                    existing = page_by_no.get(pno) or {
                        "page_number": pno,
                        "text": "",
                        "tables_html": [],
                        "figures": [],
                    }
                    existing_tables = list(existing.get("tables_html") or [])
                    pl_tables = _extract_pdfplumber_page_tables(pl_page)
                    if pl_tables and not _has_usable_table_html(existing_tables):
                        existing["tables_html"] = pl_tables
                        for th in pl_tables:
                            extra_tables.append({"page": pno, "html": th})
                    page_by_no[pno] = existing
                    if not (existing.get("text") or "").strip():
                        pt = (pl_page.extract_text() or "").strip()
                        if pt:
                            existing["text"] = pt
                    page_by_no[pno] = existing
        except Exception as e:
            logger.warning("[GetTestMethod][parser] pdfplumber 表格补充失败: %s", e)

    if fitz is not None:
        fig_dir = os.path.join(output_dir or os.path.join(os.getcwd(), "output_json", "ocr_tmp"), "native_figures")
        os.makedirs(fig_dir, exist_ok=True)
        try:
            doc = fitz.open(file_path)
            for i in range(len(doc)):
                pno = i + 1
                existing = page_by_no.get(pno) or {
                    "page_number": pno,
                    "text": "",
                    "tables_html": [],
                    "figures": [],
                }
                page = doc[i]
                if not (existing.get("text") or "").strip():
                    ft = (page.get_text("text") or "").strip()
                    if ft:
                        existing["text"] = ft
                native_figs: List[Dict[str, Any]] = []
                candidates: List[Dict[str, Any]] = []
                for img in page.get_images(full=True):
                    xref = img[0]
                    try:
                        base = doc.extract_image(xref)
                    except Exception:
                        continue
                    w = int(base.get("width") or 0)
                    h = int(base.get("height") or 0)
                    if not _is_content_figure({"width": w, "height": h}):
                        continue
                    candidates.append({"base": base, "w": w, "h": h, "area": w * h})
                candidates.sort(key=lambda x: x["area"], reverse=True)
                for fig_idx, cand in enumerate(candidates[:2], 1):
                    base = cand["base"]
                    ext = base.get("ext") or "png"
                    fname = f"p{pno:03d}_img{fig_idx}.{ext}"
                    fpath = os.path.join(fig_dir, fname)
                    with open(fpath, "wb") as f:
                        f.write(base["image"])
                    native_figs.append({
                        "path": fpath,
                        "page": pno,
                        "figure_index": fig_idx,
                        "width": cand["w"],
                        "height": cand["h"],
                        "image_type": "figure",
                    })
                ocr_figs = [f for f in (existing.get("figures") or []) if isinstance(f, dict) and _is_content_figure(f)]
                best_area = candidates[0]["area"] if candidates else 0
                if native_figs and (not ocr_figs or best_area > 50000):
                    existing["figures"] = native_figs
                elif ocr_figs:
                    existing["figures"] = ocr_figs
                page_by_no[pno] = existing
            doc.close()
        except Exception as e:
            logger.warning("[GetTestMethod][parser] fitz 图片补充失败: %s", e)

    if not page_by_no:
        return pages, extra_tables
    merged_pages = [page_by_no[k] for k in sorted(page_by_no.keys())]
    merged_pages = _filter_watermark_figures(merged_pages)
    return merged_pages, extra_tables


def _merge_pdf_pages_with_native_assets(
    file_path: str,
    parsed: Dict[str, Any],
    ocr_output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    pages = list(parsed.get("pages") or [])
    pages, extra_tables = _supplement_pdf_native_tables_and_figures(
        file_path, pages, ocr_output_dir,
    )
    out = dict(parsed)
    out["pages"] = pages
    all_tables = list(parsed.get("all_tables") or [])
    for item in extra_tables:
        html = str(item.get("html") or "").strip()
        if html and not any(str(t.get("html") or "").strip() == html for t in all_tables):
            all_tables.append(item)
    out["all_tables"] = all_tables
    if extra_tables:
        logger.info(
            "[GetTestMethod][parser] 原生 PDF 补充: tables=%s figures=%s",
            len(extra_tables),
            sum(len(p.get("figures") or []) for p in pages),
        )
    return out


def split_standard_sections(full_text: str) -> List[Dict[str, Any]]:
    lines = preprocess_merged_section_lines(full_text or "").splitlines()
    sections: List[Dict[str, Any]] = []
    current_page = 1
    max_page_seen = 1

    current = {
        "section_id": "S000",
        "title": DEFAULT_SECTION_TITLE,
        "heading": DEFAULT_SECTION_TITLE,
        "level": 0,
        "start_page": 1,
        "lines": [],
    }
    section_index = 1

    def flush_section(next_start_page: Optional[int] = None, final_page: Optional[int] = None) -> None:
        nonlocal current
        text = "\n".join(current.get("lines", [])).strip()
        if not text and current["title"] != DEFAULT_SECTION_TITLE:
            return
        if next_start_page and next_start_page > 0:
            end_page = max(current["start_page"], next_start_page - 1)
        else:
            end_page = max(current["start_page"], final_page or current_page)
        sec = {
            "section_id": current["section_id"],
            "title": current["title"],
            "heading": current["heading"],
            "level": current["level"],
            "start_page": current["start_page"],
            "end_page": end_page,
            "text": text,
        }
        sections.append(sec)

    for raw_line in lines:
        line = raw_line.rstrip()
        page_match = PAGE_MARKER_RE.match(line)
        if page_match:
            current_page = int(page_match.group(1))
            max_page_seen = max(max_page_seen, current_page)
            continue

        if _is_heading_candidate(line):
            flush_section(next_start_page=current_page)
            sec_id = f"S{section_index:03d}"
            section_index += 1
            current = {
                "section_id": sec_id,
                "title": _normalize_spaces(line),
                "heading": _normalize_spaces(line),
                "level": _heading_level(line),
                "start_page": current_page,
                "lines": [line],
            }
        else:
            current["lines"].append(line)

    flush_section(final_page=max_page_seen)

    if not sections:
        return [{
            "section_id": "S001",
            "title": DEFAULT_SECTION_TITLE,
            "heading": DEFAULT_SECTION_TITLE,
            "level": 0,
            "start_page": 1,
            "end_page": max_page_seen,
            "text": (full_text or "").strip(),
        }]

    cleaned = []
    for sec in sections:
        text = re.sub(PAGE_MARKER_RE, "", sec.get("text", "")).strip()
        if sec["title"] == DEFAULT_SECTION_TITLE and not text:
            continue
        sec["text"] = text
        cleaned.append(sec)

    return cleaned or [{
        "section_id": "S001",
        "title": DEFAULT_SECTION_TITLE,
        "heading": DEFAULT_SECTION_TITLE,
        "level": 0,
        "start_page": 1,
        "end_page": max_page_seen,
        "text": (full_text or "").strip(),
    }]


def build_section_catalogue(sections: List[Dict[str, Any]], snippet_chars: int = 180) -> List[Dict[str, Any]]:
    result = []
    for sec in sections:
        text = (sec.get("text") or "").strip().replace("\n", " ")
        result.append({
            "section_id": sec.get("section_id"),
            "title": sec.get("title"),
            "start_page": sec.get("start_page"),
            "end_page": sec.get("end_page"),
            "snippet": text[:snippet_chars],
        })
    return result


def get_sections_by_ids(sections: List[Dict[str, Any]], section_ids: List[str]) -> List[Dict[str, Any]]:
    wanted = set(section_ids or [])
    result = [sec for sec in sections if sec.get("section_id") in wanted]
    return result


def collect_tables_for_sections(
    sections: List[Dict[str, Any]],
    page_tables: Dict[int, List[str]],
    section_ids: List[str],
) -> List[Dict[str, Any]]:
    selected = get_sections_by_ids(sections, section_ids)
    used = []
    for sec in selected:
        start_page = int(sec.get("start_page") or 1)
        end_page = int(sec.get("end_page") or start_page)
        for page_no in range(start_page, end_page + 1):
            for html in page_tables.get(page_no, []):
                used.append({
                    "page": page_no,
                    "section_id": sec.get("section_id"),
                    "html": html,
                    "text_preview": _strip_table_tags(html)[:300],
                })
    return used


def use_mineru_for_flgl() -> bool:
    """FLGL 方法提取是否默认走 MinerU（与 WJGL 共用 WJGL_PDF_PARSE / FLGL_PDF_BACKEND）。"""
    mode = (
        os.environ.get("FLGL_PDF_BACKEND")
        or os.environ.get("WJGL_PDF_PARSE")
        or "mineru"
    ).strip().lower()
    return mode in ("mineru", "1", "true", "yes", "on")


def _flgl_pdf_fallback_enabled() -> bool:
    return str(os.environ.get("FLGL_PDF_FALLBACK", "") or "").strip().lower() in (
        "1", "true", "yes", "on", "fitz", "ocr",
    )


def _convert_office_to_pdf(file_path: str, out_dir: str) -> str:
    """
    Word/doc → PDF，兼容旧版 utils.office_to_pdf（无 convert_local_office_to_pdf 时回退 _run_soffice_convert）。
    """
    os.makedirs(out_dir, exist_ok=True)
    try:
        from utils.office_to_pdf import convert_office_to_pdf
        return convert_office_to_pdf(file_path, out_dir)
    except ImportError:
        pass
    try:
        from utils.office_to_pdf import convert_local_office_to_pdf
        return convert_local_office_to_pdf(file_path, out_dir)
    except ImportError:
        pass
    try:
        from utils.office_to_pdf import _run_soffice_convert
        return _run_soffice_convert(file_path, out_dir)
    except ImportError as exc:
        raise ImportError(
            "utils.office_to_pdf 过旧，缺少 convert_office_to_pdf / convert_local_office_to_pdf。"
            "请同步 backend/utils/office_to_pdf.py 至最新版本，并安装 LibreOffice（配置 LIBREOFFICE_PATH）。"
        ) from exc


def _ensure_pdf_for_mineru(file_path: str, ocr_output_dir: str) -> Tuple[str, bool]:
    """
    MinerU 仅支持 PDF：doc/docx 先转 PDF。
    返回 (pdf_path, converted_from_office)。
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return file_path, False
    if ext in (".docx", ".doc"):
        conv_dir = os.path.join(ocr_output_dir, "office_to_pdf")
        pdf_path = _convert_office_to_pdf(file_path, conv_dir)
        logger.info("[GetTestMethod][parser] Office→PDF 后走 MinerU: %s -> %s", file_path, pdf_path)
        return pdf_path, True
    raise ValueError(f"MinerU 不支持的文件类型：{ext}，目前支持 pdf / docx / doc")


def _read_with_mineru(file_path: str, ocr_output_dir: str) -> Dict[str, Any]:
    """直接使用 MinerU 解析 PDF/Word，返回与 _read_pdf_with_fitz 兼容的中间结构。"""
    from pdf_extract.mineru_adapter import finalize_mineru_parse, parse_pdf_with_mineru

    pdf_path, converted = _ensure_pdf_for_mineru(file_path, ocr_output_dir)
    logger.info("[GetTestMethod][parser] MinerU 解析: %s", os.path.basename(file_path))
    parsed = finalize_mineru_parse(parse_pdf_with_mineru(pdf_path, ocr_output_dir))
    parsed["mode"] = parsed.get("parse_mode") or "pdf_mineru"
    parsed["mineru_pdf_path"] = os.path.abspath(pdf_path)
    if converted:
        parsed["source_file_path"] = os.path.abspath(file_path)
        parsed["converted_from_office"] = True
    return parsed


def _read_pdf_with_fitz_or_ocr(file_path: str, ocr_output_dir: str) -> Dict[str, Any]:
    """fitz + OCR 回退路径（仅 FLGL_PDF_FALLBACK=1 时启用）。"""
    cache_json_path = ocr_cache_result_json_path(file_path, ocr_output_dir)
    cached = _try_load_parsed_from_ocr_json_cache(cache_json_path)
    if cached is not None:
        logger.info("已存在结构化 OCR 缓存，跳过 fitz+版面 OCR: %s", cache_json_path)
        parsed = cached
        try:
            fitz_data = _read_pdf_with_fitz(file_path)
            fitz_data["pages"] = _clean_pages_noise(list(fitz_data.get("pages", []) or []))
            fitz_data["full_text"] = _rebuild_full_text_from_pages(fitz_data["pages"])
            if not _should_force_ocr(fitz_data):
                parsed = _choose_pdf_result(fitz_data, parsed, fitz_bad=False, ocr_error=None)
        except Exception as e:
            logger.warning("[GetTestMethod][parser] OCR 缓存命中后 fitz 合并失败: %s", e)
        return parsed

    fitz_data = {
        "full_text": "",
        "pages": [],
        "all_tables": [],
        "ocr_used": False,
        "mode": "pdf_fitz_failed",
    }
    fitz_error: Optional[Exception] = None
    try:
        fitz_data = _read_pdf_with_fitz(file_path)
        fitz_data["pages"] = _clean_pages_noise(list(fitz_data.get("pages", []) or []))
        fitz_data["full_text"] = _rebuild_full_text_from_pages(fitz_data["pages"])
    except Exception as e:
        fitz_error = e

    fitz_bad = True if fitz_error else _should_force_ocr(fitz_data)

    ocr_data = None
    ocr_error: Optional[Exception] = None
    try:
        ocr_data = _read_pdf_with_ocr(file_path, output_dir=ocr_output_dir)
    except Exception as e:
        ocr_error = e

    parsed = _choose_pdf_result(fitz_data, ocr_data, fitz_bad=fitz_bad, ocr_error=ocr_error)
    if fitz_error is not None:
        parsed["fitz_error"] = str(fitz_error)
    if ocr_error is not None:
        parsed["ocr_error"] = str(ocr_error)
    return parsed


def parse_standard_file(file_path: str, ocr_output_dir: Optional[str] = None) -> Dict[str, Any]:
    ext = os.path.splitext(file_path)[1].lower()
    file_name = os.path.basename(file_path)
    ocr_output_dir = ocr_output_dir or os.path.join(os.getcwd(), "output_json", "ocr_tmp")

    logger.info("[GetTestMethod][parser] parse_standard_file 开始: %s", file_name)
    if ext == ".txt":
        parsed = _read_txt(file_path)
    elif ext in (".docx", ".doc"):
        logger.info("[GetTestMethod][parser] Word 原生解析（跳过 MinerU）: %s", file_name)
        parsed = _read_word_native(file_path, ocr_output_dir)
    elif ext == ".pdf" and use_mineru_for_flgl():
        try:
            parsed = _read_with_mineru(file_path, ocr_output_dir)
        except Exception as e:
            if _flgl_pdf_fallback_enabled():
                logger.warning("[GetTestMethod][parser] MinerU 失败，回退 fitz+OCR: %s", e)
                parsed = _read_pdf_with_fitz_or_ocr(file_path, ocr_output_dir)
            else:
                raise RuntimeError(f"MinerU 解析失败（未启用 FLGL_PDF_FALLBACK）: {e}") from e
    elif ext == ".pdf":
        parsed = _read_pdf_with_fitz_or_ocr(file_path, ocr_output_dir)
    else:
        raise ValueError(f"暂不支持的文件类型：{ext}，目前支持 txt / docx / doc / pdf")

    if ext == ".pdf":
        merge_path = parsed.get("mineru_pdf_path") or file_path
        if os.path.splitext(merge_path)[1].lower() == ".pdf":
            parsed = _merge_pdf_pages_with_native_assets(merge_path, parsed, ocr_output_dir)

    full_text = (parsed.get("full_text") or "").strip()
    if not full_text:
        raise RuntimeError("解析后文本为空，无法继续")

    try:
        from blueprints.ZSGL.WJGL1.document_structure import (
            analyze_document_structure,
            build_section_catalogue,
        )

        structure = analyze_document_structure(
            full_text=full_text,
            file_path=os.path.abspath(file_path),
            pages=list(parsed.get("pages") or []),
            parse_mode=str(parsed.get("parse_mode") or parsed.get("mode") or ""),
        )
        sections = list(structure.sections or [])
        section_catalogue = build_section_catalogue(sections)
        parsed["raw_full_text"] = structure.raw_full_text
        parsed["structure_method"] = structure.method
        parsed["structure_fallback"] = structure.structure_fallback
        parsed["flat_headings"] = structure.flat_headings
        parsed["toc_display"] = structure.toc_display
    except Exception as exc:
        logger.warning("[GetTestMethod][parser] document_structure 失败，回退 split: %s", exc)
        sections = split_standard_sections(full_text)
        section_catalogue = build_section_catalogue(sections)

    pages = list(parsed.get("pages", []) or [])
    page_table_map = _build_page_table_map(pages)

    logger.info(
        "[GetTestMethod][parser] 解析结束: ocr_used=%s pages=%s sections=%s text_len=%s tables=%s figures=%s",
        bool(parsed.get("ocr_used")),
        len(pages),
        len(sections),
        len(full_text),
        sum(len(v) for v in page_table_map.values()),
        sum(len(p.get("figures") or []) for p in pages),
    )
    return {
        "file_path": os.path.abspath(file_path),
        "file_name": file_name,
        "file_ext": ext,
        "parse_mode": parsed.get("mode"),
        "ocr_used": bool(parsed.get("ocr_used")),
        "full_text": full_text,
        "pages": pages,
        "all_tables": list(parsed.get("all_tables", []) or []),
        "page_table_map": page_table_map,
        "sections": sections,
        "section_catalogue": section_catalogue,
        "stats": {
            "page_count": len(pages),
            "section_count": len(sections),
            "text_length": len(full_text),
            "table_count": sum(len(v) for v in page_table_map.values()),
        },
        "full_html": parsed.get("full_html") or "",
        "full_markdown": parsed.get("full_markdown") or "",
    }
