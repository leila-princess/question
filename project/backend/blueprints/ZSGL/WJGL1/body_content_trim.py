"""跳过封面/目录页，正文从「1 范围」等第一章节开始。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .text_normalize import (
    clean_document_text,
    has_cid_garbled,
    has_symbol_font_garbled,
    is_toc_noise_line,
    is_toc_noise_section,
)


def _l1_section_no_from_line(line: str) -> Optional[str]:
    from blueprints.FLGL.GetTestMethod.parser import (
        DECIMAL_HEADING_RE,
        _is_heading_candidate,
        _normalize_spaces,
    )

    norm = _normalize_spaces(line or "")
    if not norm or is_toc_noise_line(norm) or not _is_heading_candidate(norm):
        return None
    m = DECIMAL_HEADING_RE.match(norm)
    if not m:
        return None
    sec = m.group(1).lstrip(".")
    if "." in sec:
        return None
    if not sec.isdigit():
        return None
    return sec


def find_body_start_page(pages: List[Dict[str, Any]]) -> int:
    """定位正文中第一个一级章节「1 xxx」所在页码。"""
    fallback = 3
    max_pno = 1
    for page in pages or []:
        pno = int(page.get("page_number") or page.get("page_num") or 0)
        if pno < 1:
            continue
        max_pno = max(max_pno, pno)
        for line in (page.get("text") or "").splitlines():
            if _l1_section_no_from_line(line) == "1":
                return pno
    # Word 等单页文档 fallback 不得超过实际页数，避免裁掉全部正文
    return min(fallback, max_pno) if max_pno else 1


def trim_pages_from_body_start(pages: List[Dict[str, Any]], start_page: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in pages or []:
        pno = int(page.get("page_number") or page.get("page_num") or 0)
        if pno >= start_page:
            out.append(page)
    return out


def trim_full_text_from_body_start(full_text: str, start_page: int) -> str:
    from blueprints.FLGL.GetTestMethod.parser import PAGE_MARKER_RE

    lines = (full_text or "").splitlines()
    out: List[str] = []
    current_page = 1
    body_started = False

    for line in lines:
        stripped = line.strip()
        pm = PAGE_MARKER_RE.match(stripped)
        if pm:
            current_page = int(pm.group(1))
            if current_page >= start_page:
                out.append(line)
            continue
        if current_page < start_page:
            continue
        if not body_started:
            if _l1_section_no_from_line(stripped) == "1":
                body_started = True
            else:
                continue
        out.append(line)

    if body_started:
        return clean_document_text("\n".join(out))

    # 回退：仅按页码裁剪
    out = []
    current_page = 1
    for line in lines:
        stripped = line.strip()
        pm = PAGE_MARKER_RE.match(stripped)
        if pm:
            current_page = int(pm.group(1))
        if current_page >= start_page:
            out.append(line)
    return clean_document_text("\n".join(out))


def trim_sections_from_body_start(sections_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    started = False
    for sec in sections_raw or []:
        title = clean_document_text(sec.get("title") or sec.get("heading") or "")
        if is_toc_noise_section(title, sec.get("text") or ""):
            continue
        sec_no = _l1_section_no_from_line(title) or ""
        if not started:
            if sec_no == "1" or title.startswith("1 ") or title.startswith("1."):
                started = True
            else:
                continue
        out.append(sec)
    return out


def trim_html_from_body_start(html: str) -> str:
    h = html or ""
    if not h.strip():
        return h
    patterns = [
        r'<p[^>]*\bid="wjgl-sec-1"[^>]*\bclass="wjgl-section-heading"',
        r'<p[^>]*\bclass="wjgl-section-heading"[^>]*\bid="wjgl-sec-1"',
        r'<p[^>]*\bdata-section-no="1"[^>]*\bclass="wjgl-section-heading"',
    ]
    for pat in patterns:
        m = re.search(pat, h, re.I)
        if m:
            start = h.rfind("<p", 0, m.start())
            return h[start if start >= 0 else m.start():]
    return h


def strip_section_no_from_title(section_no: str, title: str) -> str:
    """去掉标题里重复的章节号前缀，避免 UI 显示「6.2.3 6.2.3试验」。"""
    t = (title or "").strip()
    sn = (section_no or "").strip().lstrip(".")
    if not sn or not t:
        return t
    esc = re.escape(sn)
    stripped = re.sub(rf"^{esc}[\s\.．、]+", "", t).strip()
    if stripped and stripped != t:
        return stripped
    # 粘连：6.2.3试验前检测（MinerU / OCR 常见）
    if t.startswith(sn) and len(t) > len(sn):
        rest = t[len(sn):].lstrip(".．、 ")
        if rest:
            return rest
    return t


# 规范性引用文件列表、正文误识别等
_REF_PART_TITLE_RE = re.compile(r"^第\s*\d+\s*部分\s*[:：]")
_MAX_TOC_TITLE_LEN = 56


_GB38924_SECTION_TITLES = {
    "1": "范围",
    "2": "规范性引用文件",
    "3": "术语和定义",
    "4": "试验条件",
    "5": "试验设备和仪器",
    "6": "试验过程",
    "7": "试验中断和恢复",
    "8": "试验结果评定",
    "9": "试验报告",
}


def normalize_gb_standard_toc_title(section_no: str, title: str) -> str:
    """Symbol 字体乱码时，用国标常见章节名补全一级目录。"""
    sn = (section_no or "").strip().lstrip(".").replace("．", ".")
    t = shorten_toc_display_title(title)
    if re.search(r"[\u4e00-\u9fff]{2,}", t):
        return t
    top = sn.split(".")[0] if sn else ""
    if top in _GB38924_SECTION_TITLES and sn == top:
        return _GB38924_SECTION_TITLES[top]
    return t or sn


def shorten_toc_display_title(title: str, max_len: int = 40) -> str:
    """目录展示用短标题（MinerU 常把整句正文粘在标题行）。"""
    t = re.sub(r"[……\.．·\s]+$", "", (title or "").strip())
    for sep in ("，", ",", "。", "；", ";", ":", "："):
        if sep in t:
            t = t.split(sep, 1)[0].strip()
    if len(t) > max_len:
        t = t[:max_len].rstrip("，,、 ")
    return t


def is_table_row_toc_noise(title: str) -> bool:
    """表格行、试验矩阵被误识别为目录标题。"""
    t = (title or "").strip()
    if not t:
        return False
    if t.count("|") >= 2 or t.count("｜") >= 2:
        return True
    if re.search(r"\d+\s*\|", t):
        return True
    if "√" in t or "✓" in t or "/--" in t or " /--" in t:
        return True
    if re.search(r"\d+\s*km/", t, re.I):
        return True
    if re.search(r"不小于\d+", t) and "|" in t:
        return True
    return False


def is_formal_section_heading(section_no: str, title: str) -> bool:
    """标题须以节号开头（如「1.2 编制依据」），排除表格首列误识别的「1」。"""
    sn = (section_no or "").strip().lstrip(".").replace("．", ".")
    t = (title or "").strip()
    if not sn or not t:
        return False
    if is_table_row_toc_noise(t):
        return False
    pat = re.compile(r"^" + re.escape(sn) + r"(?:\s|[\.．、]|$)")
    if not pat.match(t):
        return False
    rest = pat.sub("", t, count=1).lstrip(".．、 ")
    if not rest:
        return True
    if len(rest) > 52:
        return False
    if re.search(r"\d{4,}", rest):
        return False
    # 长句正文（含多个指标/单位）不是章标题
    if sum(1 for c in "，,；;：" if c in rest) >= 2 and len(rest) > 24:
        return False
    return True


def is_toc_tree_heading(title: str, section_no: str = "") -> bool:
    """目录树专用：比正文噪声过滤宽松，保留带节号的国标章节。"""
    raw = (title or "").strip()
    t = shorten_toc_display_title(raw)
    sn = (section_no or "").strip().lstrip(".").replace("．", ".")
    if not t and not sn:
        return False
    if is_table_row_toc_noise(raw) or is_table_row_toc_noise(t):
        return False
    if _REF_PART_TITLE_RE.match(t):
        return False
    if re.search(r"第\s*\d+\s*部分\s*[:：]", t) and len(t) <= 36:
        return False
    if sn and not re.match(r"^\d+(?:\.\d+)*$", sn):
        return False
    if not sn and len(t) <= 24 and "部分" in t:
        return False
    if has_symbol_font_garbled(t) or has_cid_garbled(t):
        return False
    if re.match(r"^[（(]?\d+[）)]", t):
        return False
    if sn and not is_formal_section_heading(sn, raw):
        return False
    if sn and sn.count(".") == 0:
        try:
            top = int(sn)
        except ValueError:
            return False
        if top > 15:
            return False
        noise_hints = ("阶段", "步骤", "表 ", "｜", "|", "判定", "合格")
        if any(h in t for h in noise_hints) and len(t) <= 32:
            return False
    if re.search(r"[\u4e00-\u9fff]", t):
        return True
    if sn and re.match(r"^\d+(?:\.\d+)*$", sn):
        return len(t) <= 24
    return False


def is_toc_heading_noise(title: str, section_no: str = "") -> bool:
    """过滤非标题行：引用标准分则列表、长句正文、温度碎片等。"""
    t = (title or "").strip()
    if not t:
        return True
    if _REF_PART_TITLE_RE.match(t):
        return True
    if re.search(r"第\s*\d+\s*部分\s*[:：]", t) and len(t) <= 36:
        return True
    if len(t) > _MAX_TOC_TITLE_LEN:
        return True
    if re.match(r"^[\d\.．]+\s*℃", t) or re.match(r"^[\-\d\.]+\s*℃", t):
        return True
    if t.endswith("。") or t.count("，") >= 2 or "；" in t:
        return True
    body_hints = ("如下", "规定的", "满足产品", "应从", "恢复至", "试验箱", "同6.", "同 6.", "按 4.", "按4.")
    if sum(1 for h in body_hints if h in t) >= 1 and len(t) > 18:
        return True
    sn = (section_no or "").strip().lstrip(".")
    if sn and not re.match(r"^\d+(?:\.\d+)*$", sn.replace("．", ".")):
        return True
    # 无章节号且标题过短重复（引用列表）
    if not sn and len(t) <= 24 and "部分" in t:
        return True
    return False


def filter_toc_tree_noise(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """递归剔除噪声目录节点。"""
    out: List[Dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        no = str(node.get("sectionNo") or "").strip()
        title = str(node.get("title") or "").strip()
        children = filter_toc_tree_noise(list(node.get("children") or []))
        if is_toc_heading_noise(title, no):
            out.extend(children)
            continue
        out.append({**node, "title": strip_section_no_from_title(no, title), "children": children})
    return out


def normalize_toc_tree_titles(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        no = str(node.get("sectionNo") or "").strip()
        title = strip_section_no_from_title(no, str(node.get("title") or ""))
        children = normalize_toc_tree_titles(list(node.get("children") or []))
        out.append({**node, "title": title, "children": children})
    return out


def build_toc_tree_from_candidates(candidates: list, skip_pages: int = 3) -> list:
    """验证编号层级并构建目录树（供 parse 缓存 / fitz 快速提取共用）。"""
    from .section_number_utils import section_no_sort_key, sort_toc_tree

    if not candidates:
        return []

    filtered = [c for c in candidates if c["page"] > skip_pages]
    if not filtered:
        filtered = candidates

    depth_nums: Dict[int, set] = {}
    for c in filtered:
        d = c["depth"]
        if d not in depth_nums:
            depth_nums[d] = set()
        depth_nums[d].add(c["no"])

    valid_depths: set = set()
    for d in sorted(depth_nums.keys()):
        nums = depth_nums[d]
        if d == 1:
            top_nums = set()
            for no in nums:
                try:
                    top_nums.add(int(str(no).split(".")[0]))
                except ValueError:
                    continue
            if len(top_nums) >= 2:
                valid_depths.add(1)
        elif d > 1:
            parent_d = d - 1
            if parent_d in valid_depths:
                valid_depths.add(d)

    if 1 not in valid_depths:
        return []

    max_depth = max(valid_depths) if valid_depths else 1

    seen = set()
    unique_candidates = []
    for c in sorted(filtered, key=lambda x: (x["page"], section_no_sort_key(str(x["no"] or "")))):
        if c["depth"] not in valid_depths:
            continue
        if c["depth"] > max_depth:
            continue
        key = (c["no"], c["title"])
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(c)

    toc: List[Dict[str, Any]] = []
    stack: List[tuple] = []
    for c in unique_candidates:
        node = {
            "title": c["title"],
            "sectionNo": c["no"],
            "level": c["depth"],
            "children": [],
        }
        while stack and stack[-1][0] >= c["depth"]:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            toc.append(node)
        stack.append((c["depth"], node))

    return sort_toc_tree(normalize_toc_tree_titles(toc))


def filter_toc_from_chapter_one(toc: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """目录树从一级章节「1」开始展示，并剔除噪声。"""
    if not toc:
        return toc
    trimmed = toc
    for i, node in enumerate(toc):
        no = str(node.get("sectionNo") or "").strip()
        top = no.split(".")[0] if no else ""
        title = str(node.get("title") or "")
        if top == "1" or title.startswith("1 ") or title.startswith("范围"):
            trimmed = toc[i:]
            break
    return filter_toc_tree_noise(normalize_toc_tree_titles(trimmed))
