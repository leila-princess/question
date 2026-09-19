"""
试验大纲 Word 文档 — LLM 自动提取试验项目与方法（独立模块，不修改现有 pipeline）。

能力：
  1. 上传 .docx（CLI 亦支持 .txt 便于用 docs/fftq.md 模拟）
  2. 默认流程：LLM 先判断文档是**分散**（每项目各有一套条件/过程/结果）还是**整合**（全文共用一套需拆解）或**单一方法**；再按形态抽取
  3. 落盘：parsed_document.json、document_body_for_llm.txt、llm_prompt.txt、llm_response_raw/json
  4. 图库 embedding 粗召回 + LLM 精选推荐节点
  5. **增量落盘**：每抽取出一个试验方法立即写入 method_NNN_*.json

  环境变量 WORD_LLM_LEGACY_PHASED=1 可恢复旧版 Phase0/发现项目/逐项目抽取流程。

用法见文件末尾 __main__ 或 README 测试命令。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 固定切片参数（16384 上下文）
# ---------------------------------------------------------------------------
FIXED_LLM_CONTEXT_TOKENS = 16384
FIXED_LLM_MAX_COMPLETION_TOKENS = 4096
FIXED_CHUNK_TEXT_CHARS = 6000
FIXED_CHUNK_OVERLAP_CHARS = 300
FIXED_PROMPT_RESERVE_CHARS = 4500

ALLOWED_UPLOAD_EXT = {".docx", ".txt", ".md"}
# parser 原生支持 docx/txt；.md 在本模块内按文本读取
_PARSER_NATIVE_EXT = {".docx", ".txt"}


def _normalize_upload_path(file_path: str) -> str:
    """去掉引号/空白，展开 ~，转为绝对路径。"""
    raw = str(file_path or "").strip().strip('"').strip("'").strip()
    if not raw:
        return ""
    return os.path.abspath(os.path.expanduser(raw))


def _file_ext(path: str) -> str:
    """从路径取扩展名；兼容引号、末尾空格/点、全角点、不可见字符。"""
    p = str(path or "").strip().strip('"').strip("'").rstrip(" \t.")
    base = os.path.basename(p)
    ext = os.path.splitext(base)[1].lower().strip()
    ext = ext.replace("．", ".").replace("。", ".")
    ext = re.sub(r"[\u200b-\u200d\ufeff\r\n]", "", ext)
    return ext


def resolve_upload_file(file_path: str) -> str:
    """
    解析 CLI/上传路径：兼容带引号路径、无扩展名、.md 模拟文档。
    返回存在的绝对路径。
    """
    p = _normalize_upload_path(file_path)
    if not p:
        raise ValueError(
            "文件路径为空。示例：\n"
            "  python blueprints/FLGL/GetTestMethod/word_llm_upload_extract.py extract ..\\docs\\fftq.md --skip-recall"
        )
    if re.match(r"^[A-Za-z]:$", p):
        raise ValueError(
            f"文件路径不完整：{p!r}。请传入完整路径，例如 D:\\SVFAXTX 0429 v1.0\\docs\\大纲.docx"
        )

    if os.path.isfile(p):
        ext = _file_ext(p)
        if ext in ALLOWED_UPLOAD_EXT or ext == ".doc":
            return p
        raise ValueError(
            f"不支持的文件类型 {ext or '(无扩展名)'}：{p}\n"
            f"仅支持：{', '.join(sorted(ALLOWED_UPLOAD_EXT))} 以及 .doc"
        )
    for suffix in (".docx", ".txt", ".md"):
        cand = p if p.lower().endswith(suffix) else p + suffix
        if os.path.isfile(cand):
            return os.path.abspath(cand)

    raise FileNotFoundError(f"找不到文件：{p}（也尝试了追加 .docx / .txt / .md）")


def _load_gettestmethod_parser():
    """按文件路径加载 GetTestMethod/parser.py，避免与 Python 标准库 parser 冲突。"""
    import importlib.util

    parser_path = _FILE_DIR / "parser.py"
    spec = importlib.util.spec_from_file_location(
        "flgl_gettestmethod_parser", str(parser_path)
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 parser 模块：{parser_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_parsed_document_dict(
    *,
    resolved: str,
    file_name: str,
    ext: str,
    parse_mode: str,
    full_text: str,
    pages: List[Dict[str, Any]],
    all_tables: List[Any],
    ocr_used: bool,
    split_standard_sections,
    build_section_catalogue,
    full_markdown: str = "",
    full_html: str = "",
    word_paragraph_map: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    text = (full_text or "").strip()
    if not text:
        raise RuntimeError(f"解析后文本为空，无法继续：{resolved}")
    page_table_map: Dict[int, List[str]] = {}
    for page in pages or []:
        pno = int(page.get("page_number") or 1)
        th = page.get("tables_html") or []
        if th:
            page_table_map[pno] = list(th)
    try:
        from blueprints.ZSGL.WJGL1.document_structure import analyze_document_structure

        structure = analyze_document_structure(
            full_text=text,
            file_path=resolved,
            pages=pages,
            parse_mode=parse_mode,
            word_paragraph_map=word_paragraph_map,
        )
        sections = list(structure.sections or [])
        catalogue = build_section_catalogue(sections)
        extra = {
            "raw_full_text": structure.raw_full_text,
            "structure_method": structure.method,
            "structure_fallback": structure.structure_fallback,
            "flat_headings": structure.flat_headings,
            "toc_display": structure.toc_display,
        }
    except Exception as exc:
        logger.warning("[word_llm] document_structure 失败，回退 split: %s", exc)
        sections = split_standard_sections(text)
        catalogue = build_section_catalogue(sections)
        extra = {"raw_full_text": text, "structure_method": "fallback"}
    return {
        "file_path": resolved,
        "file_name": file_name,
        "file_ext": ext,
        "parse_mode": parse_mode,
        "ocr_used": ocr_used,
        "full_text": text,
        "full_markdown": str(full_markdown or "").strip(),
        "full_html": str(full_html or "").strip(),
        "word_paragraph_map": list(word_paragraph_map or []),
        "pages": pages,
        "all_tables": all_tables,
        "page_table_map": page_table_map,
        "sections": sections,
        "section_catalogue": catalogue,
        "stats": {
            "page_count": len(pages or []),
            "section_count": len(sections),
            "text_length": len(text),
            "table_count": sum(len(v) for v in page_table_map.values()),
        },
        **extra,
    }


def parse_upload_document(file_path: str) -> Dict[str, Any]:
    """
    解析上传文件。
    .md / .txt / .docx 走轻量原生解析（不依赖 MinerU / LibreOffice）；
    复杂扫描版 docx 可设环境变量 WORD_LLM_DOCX_USE_MINERU=1 走 parser 全链路。
    """
    _parser = _load_gettestmethod_parser()
    parse_standard_file = _parser.parse_standard_file
    split_standard_sections = _parser.split_standard_sections
    build_section_catalogue = _parser.build_section_catalogue
    _read_docx = _parser._read_docx
    _read_txt = _parser._read_txt

    resolved = resolve_upload_file(file_path)
    ext = _file_ext(resolved)
    file_name = os.path.basename(resolved)

    if ext == ".md":
        with open(resolved, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return _build_parsed_document_dict(
            resolved=resolved,
            file_name=file_name,
            ext=ext,
            parse_mode="md_as_text",
            full_text=text,
            full_markdown=text,
            pages=[{"page_number": 1, "text": text, "tables_html": [], "figures": []}],
            all_tables=[],
            ocr_used=False,
            split_standard_sections=split_standard_sections,
            build_section_catalogue=build_section_catalogue,
        )

    if ext == ".txt":
        native = _read_txt(resolved)
        logger.info("[word_llm] txt 原生解析: %s", file_name)
        txt_body = str(native.get("full_text", "") or "")
        return _build_parsed_document_dict(
            resolved=resolved,
            file_name=file_name,
            ext=ext,
            parse_mode="txt",
            full_text=txt_body,
            full_markdown=txt_body,
            pages=list(native.get("pages") or []),
            all_tables=list(native.get("all_tables") or []),
            ocr_used=False,
            split_standard_sections=split_standard_sections,
            build_section_catalogue=build_section_catalogue,
        )

    if ext in (".docx", ".doc"):
        use_mineru = os.environ.get("WORD_LLM_DOCX_USE_MINERU", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if not use_mineru:
            _read_word_native = getattr(_parser, "_read_word_native", _read_docx)
            native = _read_word_native(resolved)
            logger.info("[word_llm] Word 原生解析（跳过 MinerU）: %s", file_name)
            return _build_parsed_document_dict(
                resolved=resolved,
                file_name=file_name,
                ext=ext,
                parse_mode="docx_native",
                full_text=str(native.get("full_text", "") or ""),
                full_markdown=str(native.get("full_markdown") or ""),
                full_html=str(native.get("full_html") or ""),
                word_paragraph_map=list(native.get("word_paragraph_map") or []),
                pages=list(native.get("pages") or []),
                all_tables=list(native.get("all_tables") or []),
                ocr_used=False,
                split_standard_sections=split_standard_sections,
                build_section_catalogue=build_section_catalogue,
            )

    if ext not in _PARSER_NATIVE_EXT:
        raise ValueError(f"内部错误：未实现的解析类型 {ext}")

    parsed = parse_standard_file(file_path=resolved)
    parsed["file_path"] = resolved
    parsed["file_name"] = file_name
    parsed["file_ext"] = ext
    return parsed

# 试验方法结构化五段（与 fftq 类大纲 4.x.1~4.x.5 对应）
METHOD_SECTION_KEYS = ("symd", "sytj", "syff", "sjsjcl", "jgpd")
METHOD_SECTION_LABELS = {
    "symd": "试验目的",
    "sytj": "试验条件",
    "syff": "试验方法",
    "sjsjcl": "试验数据处理",
    "jgpd": "结果评定",
}
# sygc 为图库召回兼容字段，内容与 syff 同步
RECALL_TEXT_KEYS = ("symd", "sytj", "syff", "sjsjcl", "jgpd", "sygc")

_FILE_DIR = Path(__file__).resolve().parent
_FLGL_DIR = _FILE_DIR.parent
_BACKEND_ROOT = _FLGL_DIR.parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent

# 必须用仓库根目录导入 backend.config；若把 GetTestMethod 放在最前会误加载
# GetTestMethod/backend/config.py 占位 stub（无 OPENAI_API_KEY / create_openai_client）。
for _p in (_REPO_ROOT, _FLGL_DIR, _FILE_DIR):
    sp = str(_p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
# backend/services/ 与 blueprints/FLGL/services.py 同名；backend 须优先，否则 JYLLM 等
# `from services.xxx` 会误命中 FLGL 的单文件 services.py。
_backend_sp = str(_BACKEND_ROOT)
if _backend_sp in sys.path:
    sys.path.remove(_backend_sp)
sys.path.insert(0, _backend_sp)

from llm_text_prep import (
    build_llm_heading_outline,
    sanitize_text_for_llm,
    split_text_at_section_boundaries,
)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = _REPO_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(str(env_path))


_MAIN_CONFIG_CLS = None


def _get_main_config():
    """
    加载主工程 backend/config.py 的 Config。
    不能 `from backend.config import Config`：GetTestMethod/backend/config.py 占位 stub
    会因 sys.path 优先级覆盖主 Config，导致 OPENAI_API_KEY=EMPTY 仍报「未配置」。
    """
    global _MAIN_CONFIG_CLS
    if _MAIN_CONFIG_CLS is not None:
        return _MAIN_CONFIG_CLS

    _load_dotenv()
    import importlib.util

    cfg_path = _BACKEND_ROOT / "config.py"
    spec = importlib.util.spec_from_file_location("ant_demo_backend_config", str(cfg_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载主 Config：{cfg_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MAIN_CONFIG_CLS = mod.Config
    return _MAIN_CONFIG_CLS


def _load_gettestmethod_recall_module():
    """按路径加载 GetTestMethod/recall.py，并绑定主工程 Config。"""
    import importlib.util

    recall_path = _FILE_DIR / "recall.py"
    spec = importlib.util.spec_from_file_location(
        "flgl_gettestmethod_recall", str(recall_path)
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 recall 模块：{recall_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.Config = _get_main_config()
    return mod


def _import_recall_module():
    """兼容旧名。"""
    return _load_gettestmethod_recall_module()


def _empty_graph_matches() -> Dict[str, List[Any]]:
    return {
        "equipment": [],
        "environment": [],
        "test_project": [],
        "indicator": [],
    }

# ---------------------------------------------------------------------------
# Prompts — 针对「试验大纲 / 试验项目与方法」结构
# ---------------------------------------------------------------------------

ANALYZE_DOCUMENT_STRUCTURE_PROMPT = """
你是一名试验文档结构分析专家。请根据目录大纲与正文采样，判断本文档的**试验方法组织方式**（不要抽取正文内容）。

【输出要求】
1. 只输出纯 JSON，不要 markdown。
2. 仅分析结构，不要编造文档中不存在的章节。

【输出格式】
{{
  "document_style": "outline|trial_plan|mixed",
  "method_unit": "per_sub_item|per_chapter|single_block",
  "project_heading_patterns": ["如何识别一个独立试验章节的标题特征"],
  "section_aliases": {{
    "symd": ["试验目的"],
    "sytj": ["试验条件"],
    "syff": ["试验方法", "试验过程", "试验步骤"],
    "sjsjcl": ["试验数据处理"],
    "jgpd": ["结果评定", "结果判定"]
  }},
  "sub_method_pattern": "numbered_paren|decimal_list|none",
  "warnings": []
}}

【目录大纲（前 80 条）】
{heading_outline}

【正文采样】
{body_sample}
""".strip()

JUDGE_METHOD_ORGANIZATION_PROMPT = """
你是一名试验文档结构判断专家。请根据目录与正文采样，判断本文档试验方法的组织方式，只输出 JSON。

【判断标准 — 必须遵守】
1. **dispersed（分散）**：存在多个试验项目/并列章节，**每个项目或方法单元下各自有一套完整的**「试验条件 + 试验过程/方法 + 结果评定」。例如高温试验、低温试验各自章内均有条件/过程/评定。
2. **integrated（整合）**：全文**只共用一套**试验条件、试验过程、结果评定（常集中在统一章节），其下多个子项（如（1）（2）…或多个并列试验名称）需从共用内容中**拆解**为多条方法。
3. **single_method（单一方法）**：全文实质上**只有一个**完整试验方法（共用一套条件/过程/评定，且无可拆分的多个并列方法单元）。

【注意】
- 关键看「条件+过程+评定」是**每节各一套**还是**全文共用一套**，不要仅凭章节数量判断。
- 多个并列试验章且各有条件/过程/评定子节 → dispersed。
- 多个子项共享同一「试验条件」「结果评定」大节 → integrated。
- 不确定时选 integrated，并降低 confidence。

【输出格式】
{{
  "organization_mode": "dispersed|integrated|single_method",
  "confidence": 0.85,
  "reason": "判断理由",
  "project_count_hint": 0,
  "method_count_hint": 0,
  "dispersed_project_hints": ["章节标题示例"],
  "warnings": []
}}

【文件名】{file_name}

【目录大纲】
{heading_outline}

【正文采样】
{body_sample}
""".strip()

UNIFIED_DOCUMENT_PLAN_PROMPT = """
你是一名试验文档结构分析专家。请根据**目录大纲**与**正文采样**，一次性完成全文结构规划（不要抽取正文细节）。

【输出要求】
1. 只输出纯 JSON，不要 markdown。
2. 仅分析结构，不要编造文档中不存在的章节。
3. 目录大纲仅含节号与标题；正文采样已去除图片编码。

【组织形态判断标准】
- **dispersed**：多个试验项目/并列章节，各自有一套「试验条件 + 试验过程/方法 + 结果评定」。
- **integrated**：全文共用一套条件/过程/评定，其下多个子项需拆解为多条方法。
- **single_method**：全文实质上只有一个完整试验方法。

【输出格式】
{{
  "document_style": "outline|trial_plan|mixed",
  "method_unit": "per_sub_item|per_chapter|single_block",
  "project_heading_patterns": ["如何识别独立试验章节的标题特征"],
  "section_aliases": {{
    "symd": ["试验目的"],
    "sytj": ["试验条件"],
    "syff": ["试验方法", "试验过程", "试验步骤"],
    "sjsjcl": ["试验数据处理"],
    "jgpd": ["结果评定", "结果判定"]
  }},
  "sub_method_pattern": "numbered_paren|decimal_list|none",
  "organization_mode": "dispersed|integrated|single_method",
  "confidence": 0.85,
  "reason": "判断理由",
  "project_count_hint": 0,
  "method_count_hint": 0,
  "dispersed_project_hints": ["章节标题示例"],
  "projects": [
    {{
      "project_id": "P001",
      "project_name": "试验名称（不含编号）",
      "section_hint": "8.2 或章节标题原文",
      "sub_method_titles": ["并列试验子项，如低温试验（非工作状态）；无则 []"]
    }}
  ],
  "warnings": []
}}

【sub_method_titles 规则】仅填并列试验子项（宜含「试验/测试」语义）；禁止填阶段N、步骤N、试验准备、返程测试、结果计算等过程性标题；无并列子项则 []。

【文件名】{file_name}

【目录大纲】
{heading_outline}

【正文采样】
{body_sample}
""".strip()

EXTRACT_INTEGRATED_DOCUMENT_PROMPT = """
你是一名试验文档方法提取专家。本文档为**整合型**：全文共用一套试验条件/试验过程/结果评定，多个试验方法需从中拆解。

【文档结构分析（Phase0，务必遵循）】
{structure_profile_json}

按 section_aliases 将非标准标题（如「测试条件」「工况」「合格判据」「实施程序」）映射到 sytj/syff/jgpd。

【整合型抽取规则】
1. 共用章节中的 sytj/syff/jgpd 可按语义分配给各子方法；子项（1）（2）… 通常各为一条 method。
2. **至少一项即可输出**：sytj、syff、jgpd 任一有原文即可输出该方法；三项齐全最佳，缺项留空 ""，不要因此丢弃整条方法。
3. 不要把共用章节重复复制到每条方法的全部正文；按子方法实际涉及的步骤/条件摘录。
4. 若仅有（1）（2）… 子项而无独立条件/评定节，syff 取子项步骤，sytj/jgpd 从共用节摘录。

【严禁编造】所有 text 须能在【文档全文】中逐字找到。

【输出格式】同整文档提取，纯 JSON：
{{
  "document_summary": "说明为整合型及拆解方式",
  "methods": [{{ "method_id": "M001", "name": "方法短名", "test_project_name": "项目名", ... }}]
}}

字段：symd/sjsjcl 可选；sytj/syff/jgpd 至少一项有原文。

【文件名】{file_name}
【目录参考】
{heading_outline}

【文档全文】
{document_body}
{truncated_note}
""".strip()

EXTRACT_SINGLE_METHOD_DOCUMENT_PROMPT = """
你是一名试验文档方法提取专家。本文档为**单一试验方法**：全文共用一套条件/过程/结果，只输出 **1 条** method。

【文档结构分析（Phase0）】
{structure_profile_json}

按 section_aliases 识别非标准小节标题并映射到 sytj/syff/jgpd。

【规则】
1. methods 数组长度必须为 1（除非全文明确无任何可提取内容则返回空数组）。
2. 合并全文相关章节到一条方法；sytj/syff/jgpd **至少一项**有原文即可，三项齐全最佳。
3. 禁止拆成多条。

【输出格式】纯 JSON，含 document_summary 与 methods（0 或 1 条）。

【文件名】{file_name}
【目录参考】
{heading_outline}

【文档全文】
{document_body}
{truncated_note}
""".strip()

DISCOVER_PROJECTS_PROMPT = """
你是一名试验大纲/试验方案文档分析专家。请从下面文档片段中识别**试验项目级章节**（独立试验/试验方法单元）。

【文档结构分析（Phase0，务必遵循）】
{structure_profile_json}

【目录大纲摘要】
{heading_outline}

【可识别的章节形式（任选其一）】
1. 大纲体例：「试验项目一：飞行性能试验」「试验项目2：××试验」
2. 方案体例：带编号的独立试验章节，如「3.1 最大起飞重量试验」「4.2 风载试验」
3. 标题含「试验」且其下含试验条件/过程/结果评定等子节；一个试验下可有多个编号子方法（1）（2）…

【规则】
1. 不要把「试验目的/条件/方法/过程/步骤/数据处理/结果评定」等子节标题当成试验项目。
2. 若片段中无试验项目，返回空数组。
3. sub_method_titles 仅记录**并列的试验子项**（如「（1）低温试验（非工作状态）」「喷淋试验」）；无并列子项则 []。
4. **禁止**把过程步骤当子方法：如「阶段1/阶段2」「试验准备」「设备安装」「返程测试」「结果计算与判定」等。
5. project_name 填试验名称（不含章节编号）；section_hint 填原文标题或编号。
6. 输出纯 JSON，不要 markdown。

【输出格式】
{{
  "projects": [
    {{
      "project_id": "P001",
      "project_name": "飞行性能试验",
      "section_hint": "4.2 或章节标题原文",
      "sub_method_titles": ["最大/巡航平飞速度测试"]
    }}
  ]
}}

【文档片段】（第 {chunk_index}/{chunk_total} 批，共 {chunk_chars} 字）
{chunk_text}
""".strip()

EXTRACT_PROJECT_METHODS_PROMPT = """
你是一名试验方法结构化抽取专家。请针对**单个试验项目**，从文档片段中提取其下所有**试验方法**。

【文档结构分析（Phase0，务必遵循）】
{structure_profile_json}

【严禁编造 — 必须遵守】
1. 所有 text 必须能在下方【文档片段】中逐字找到；找不到则 text 必须为 ""。
2. **禁止**总结、扩写、补全、推断文档未写明的条件/步骤/判定标准。
3. **禁止**自行生成试验条件、试验过程或结果评定；只能从片段中复制原文。

【核心定义 — 方法 vs 步骤】
- **一条 method = 一个可独立实施的试验子项**（如「低温试验（非工作状态）」「喷淋试验」），不是过程里的「阶段1/步骤2/返程测试/结果计算」。
- **五条必选字段**：symd=试验目的，sytj=试验条件，syff=试验过程/方法/步骤，sjsjcl=试验数据处理，jgpd=结果评定；JSON 中五个对象必须齐全。
- 某字段在本片段无直接原文时：从**本试验项目**其他小节摘录补全（如共用条件/评定）；仍找不到则 text 为 ""，**禁止编造**。
- symd 仅填简短目的句；设备/样品/配置归入 sytj，勿写入 symd。
- syff 子方法：name 如「低温试验（非工作状态）」时，syff 取对应（1）（2）… 步骤原文，勿填设备清单。
- 按 section_aliases 映射「测试条件」「实施程序」「合格判据」等。
- 若有（1）（2）… **并列试验子项**：每条子项一个 methods 元素；sytj/jgpd/symd/sjsjcl 可共享项目级原文，syff 取该子项步骤。
- 若仅有过程步骤、无并列子试验：整个项目输出 **1 条** method，过程步骤全部写入 syff。
- name 只填方法短名（不带项目名、不带编号前缀）。

【试验项目】
名称：{project_name}
章节提示：{section_hint}
已知子方法标题（可为空）：{sub_method_titles}

【抽取要求】
1. 每个 methods 元素须含 symd/sytj/syff/sjsjcl/jgpd 五个字段（缺项可 ""，但对象必须存在）；tables_html 放表格 HTML
2. 禁止把同一试验的过程步骤拆成多条 method
3. 为 graph 召回填写 recall_plan
4. 可选 evidence_quotes：每项从原文摘 1 句短引文（≤80字）便于校验
5. 输出纯 JSON

【输出格式】
{{
  "project_name": "{project_name}",
  "methods": [
    {{
      "method_id": "M001",
      "name": "方法短名",
      "type": "试验方法",
      "description": "一句话摘要（勿编造细节）",
      "recall_plan": {{
        "equipment": {{"keywords": [], "reason": ""}},
        "environment": {{"keywords": [], "reason": ""}},
        "test_project": {{"keywords": [], "reason": ""}},
        "indicator": {{"keywords": [], "reason": ""}}
      }},
      "evidence_quotes": {{"sytj": "", "syff": "", "jgpd": ""}},
      "symd": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sytj": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "syff": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sjsjcl": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "jgpd": {{"text": "", "tables_html": [], "formulas_latex": []}}
    }}
  ]
}}

【文档片段】（第 {chunk_index}/{chunk_total} 批）
{chunk_text}
""".strip()

EXTRACT_ONE_SUBMETHOD_PROMPT = """
你是一名试验方法结构化抽取专家。请针对**单个试验项目**下的**一条子方法**，从文档片段中提取结构化内容。

【文档结构分析（Phase0，务必遵循）】
{structure_profile_json}

【严禁编造 — 必须遵守】
1. 所有 text 必须能在下方【文档片段】中逐字找到；找不到则 text 必须为 ""。
2. **禁止**总结、扩写、补全、推断文档未写明的条件/步骤/判定标准。
3. **禁止**自行生成试验条件、试验过程或结果评定；只能从片段中复制原文。

【核心定义】
- **五条必选字段**：symd/sytj/syff/sjsjcl/jgpd 五个对象必须齐全；缺项从本试验项目其他小节摘录补全，找不到则 ""，禁止编造。
- syff 须取「{sub_method_name}」对应试验步骤原文，勿填设备清单或无关章节。
- symd 仅简短目的句；设备/样品/配置归入 sytj。
- name 只填方法短名「{sub_method_name}」（不带项目名、不带编号前缀）
- **本次只提取一条方法**：methods 数组长度必须为 0 或 1
- 仅摘录【文档片段】及同项目相关内容，勿混入其他试验项目正文。

【试验项目】
名称：{project_name}
章节提示：{section_hint}
目标子方法：{sub_method_name}

【输出格式】
{{
  "project_name": "{project_name}",
  "methods": [
    {{
      "method_id": "M001",
      "name": "{sub_method_name}",
      "type": "试验方法",
      "description": "一句话摘要（勿编造细节）",
      "recall_plan": {{
        "equipment": {{"keywords": [], "reason": ""}},
        "environment": {{"keywords": [], "reason": ""}},
        "test_project": {{"keywords": [], "reason": ""}},
        "indicator": {{"keywords": [], "reason": ""}}
      }},
      "symd": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sytj": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "syff": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sjsjcl": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "jgpd": {{"text": "", "tables_html": [], "formulas_latex": []}}
    }}
  ]
}}

【文档片段】
{chunk_text}
""".strip()

EXTRACT_ALL_METHODS_FULL_DOCUMENT_PROMPT = """
你是一名试验文档方法提取专家。下面是一份**完整**试验大纲/试验方案文档正文（已由程序解析，无 MinerU 分片）。
请通读全文，自行理解结构，提取其中**全部**试验方法。

【文档结构分析（Phase0，务必遵循）】
{structure_profile_json}

按 section_aliases 将非标准标题映射到 sytj/syff/jgpd。

【试验方法定义】
- **sytj** 试验条件 / 测试条件 / 工况 / 样品要求 / **设备与仪器配置**
- **syff** 试验过程 / 试验方法 / 试验步骤 / 实施程序（含（1）低温试验…等子项步骤）
- **jgpd** 结果评定 / 结果判定 / 合格判据
- **symd** 试验目的（**可选**；仅简短目的句，勿填设备/样品/步骤）
- **sjsjcl** 试验数据处理（可选）
- **优先填 sytj、syff、jgpd**；至少一项有原文即可输出，三项齐全最佳

【严禁编造 — 必须遵守】
1. 所有 text 必须能在下方【文档全文】中逐字找到；找不到则 text 必须为 ""。
2. **禁止**总结、扩写、补全、推断文档未写明的条件/步骤/判定。
3. **禁止**自行生成试验条件、试验过程或结果评定。

【结构理解（由你判断，不要机械套用固定模板）】
- 可能是：试验目的 → 试验方法（内含多个子项）→ 结果评定，整体为 1 条或多条方法
- 可能是：多个并列独立试验章节，各含条件/过程/评定，应拆为多条 methods
- 可能是：条件、过程、评定分散在不同小节，但属于同一试验 — 可合并为 1 条方法
- 子项（1）（2）… 是否拆成多条，由你根据原文语义判断

【输出要求】
1. 只输出纯 JSON，不要 markdown
2. name 为方法短名；test_project_name 为所属试验项目（无则与 name 相同）
3. 为 graph 召回填写 recall_plan（keywords 从原文提炼）
4. 仅当 sytj、syff、jgpd **全部为空**时才不要输出该条

【输出格式】
{{
  "document_summary": "一两句话说明文档体例与你识别到的方法组织方式",
  "methods": [
    {{
      "method_id": "M001",
      "name": "方法短名",
      "test_project_name": "试验项目名",
      "type": "试验方法",
      "description": "一句话摘要（勿编造）",
      "recall_plan": {{
        "equipment": {{"keywords": [], "reason": ""}},
        "environment": {{"keywords": [], "reason": ""}},
        "test_project": {{"keywords": [], "reason": ""}},
        "indicator": {{"keywords": [], "reason": ""}}
      }},
      "symd": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sytj": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "syff": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sjsjcl": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "jgpd": {{"text": "", "tables_html": [], "formulas_latex": []}}
    }}
  ]
}}

【文件名】{file_name}

【目录参考（仅辅助理解，不是抽取边界）】
{heading_outline}

【文档全文】
{document_body}
{truncated_note}
""".strip()

EXTRACT_ALL_METHODS_CHUNK_PROMPT = """
你是一名试验文档方法提取专家。下面是一份试验大纲/试验方案文档的**片段**（第 {chunk_index}/{chunk_total} 批，共 {chunk_chars} 字）。
请从本片段中提取其中可识别的全部试验方法。

【文档结构分析（Phase0）】
{structure_profile_json}

【重要 — 分片合并策略】
1. 本片段能确定的 sytj/syff/jgpd 字段先填，缺项留空 ""；**不要**因缺项就不输出该方法。
2. 同名方法可能跨片段：程序会按 name 合并各片段字段。
3. 所有 text 必须能在下方【文档片段】中逐字找到；禁止编造、总结、扩写。

【试验方法定义】
- **sytj** 试验条件 / 测试条件 / 工况（有则填）
- **syff** 试验过程 / 试验方法 / 试验步骤（有则填）
- **jgpd** 结果评定 / 结果判定 / 合格判据（有则填）
- **至少一项有原文即可输出**；本片段三项皆空则 methods 返回空数组

【输出要求】
1. 只输出纯 JSON，不要 markdown
2. name 为方法短名；test_project_name 为所属试验项目（无则与 name 相同）
3. 为 graph 召回填写 recall_plan

【输出格式】
{{
  "document_summary": "本片段体例简述（可空）",
  "methods": [
    {{
      "method_id": "M001",
      "name": "方法短名",
      "test_project_name": "试验项目名",
      "type": "试验方法",
      "description": "一句话摘要（勿编造）",
      "recall_plan": {{
        "equipment": {{"keywords": [], "reason": ""}},
        "environment": {{"keywords": [], "reason": ""}},
        "test_project": {{"keywords": [], "reason": ""}},
        "indicator": {{"keywords": [], "reason": ""}}
      }},
      "symd": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sytj": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "syff": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "sjsjcl": {{"text": "", "tables_html": [], "formulas_latex": []}},
      "jgpd": {{"text": "", "tables_html": [], "formulas_latex": []}}
    }}
  ]
}}

【文件名】{file_name}

【目录参考（全文结构，仅辅助理解）】
{heading_outline}

【文档片段】
{chunk_text}
""".strip()

LLM_GRAPH_RECOMMEND_PROMPT = """
你是试验方法图数据库关联推荐专家。请根据试验方法内容与 embedding 粗召回候选，从**候选列表中**挑选最匹配的图节点。

【规则】
1. 只能从各 slot 的 candidates 里选择 id，禁止编造 id。
2. 每个 slot 最多选 pick_limits 中对应个数；无合适候选则返回空数组。
3. 给出简短 reason 与 0~1 的 confidence。
4. 输出纯 JSON。

【pick_limits】
{pick_limits_json}

【试验方法】
名称：{method_name}
所属试验项目：{test_project_name}
摘要：{method_description}

【方法正文摘要】
{method_excerpt}

【recall_plan 关键词】
{recall_plan_json}

【embedding 粗召回候选】
{candidates_json}

【输出格式】
{{
  "equipment": [{{"id": "节点Id", "reason": "...", "confidence": 0.85}}],
  "environment": [],
  "test_project": [],
  "indicator": []
}}
""".strip()

GRAPH_RECOMMEND_SLOTS = ("equipment", "environment", "test_project", "indicator")


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_json_loads(raw: str, default: Any) -> Any:
    if not raw:
        return default
    text = _strip_code_fences(raw)
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        cand = text[start : end + 1]
        cand = re.sub(r",\s*}", "}", cand)
        cand = re.sub(r",\s*]", "]", cand)
        try:
            return json.loads(cand)
        except Exception:
            pass
    return default


def _dedupe_str(items: List[Any]) -> List[str]:
    seen, out = set(), []
    for x in items or []:
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _sanitize_name_part(text: str) -> str:
    s = str(text or "").strip().strip("*# ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r'[\\/:*?"<>|]', "", s)
    return s


def _compose_method_name(project_name: str, method_part: str) -> str:
    """
    方法命名：项目_方法（下划线连接）。
    method_part 为子方法短名；无子条目时用「整体试验」。
    """
    project = _sanitize_name_part(project_name)
    part = _sanitize_name_part(method_part)
    if not part or part == project:
        part = "整体试验"
    for sep in ("_", "-", "—", "：", ":"):
        prefix = f"{project}{sep}"
        if part.startswith(prefix):
            part = part[len(prefix):].strip()
            break
    if part.startswith(project):
        part = part[len(project):].lstrip("_-：: ").strip()
    if not part:
        part = "整体试验"
    if project and project not in part:
        return f"{project}_{part}"
    return part


def _method_has_core_content(method: Dict[str, Any]) -> bool:
    """兼容旧名：sytj/syff/jgpd 至少一项有非空原文即可通过。"""
    return _method_has_min_content(method)


CORE_SLOT_KEYS = ("sytj", "syff", "jgpd")
OPTIONAL_SLOT_KEYS = ("symd", "sjsjcl")
_SYNR_DISPLAY_SLOTS = (
    ("symd", "试验目的"),
    ("sytj", "试验条件"),
    ("syff", "试验方法"),
    ("sjsjcl", "试验数据处理"),
    ("jgpd", "结果评定"),
)
_CONDITION_BODY_MARKERS = (
    "试验箱", "数据采集", "样品状态", "安全防护", "温控范围", "压力范围",
    "环境条件", "预处理环境", "布点于", "试验室温度", "应急泄压",
)


def _method_core_slots(method: Dict[str, Any]) -> Dict[str, Any]:
    secs = _normalize_method_sections(method)
    filled = [k for k in CORE_SLOT_KEYS if str(secs[k].get("text", "") or "").strip()]
    missing = [k for k in CORE_SLOT_KEYS if k not in filled]
    optional_filled = [
        k for k in OPTIONAL_SLOT_KEYS if str(secs[k].get("text", "") or "").strip()
    ]
    return {
        "filled": filled,
        "missing": missing,
        "missing_required": missing,
        "optional_filled": optional_filled,
        "complete": len(filled) == len(CORE_SLOT_KEYS),
    }


def _method_has_min_content(method: Dict[str, Any]) -> bool:
    return bool(_method_core_slots(method)["filled"])


def _attach_content_slots(method: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(method)
    out["content_slots"] = _method_core_slots(out)
    return out


def _text_has_condition_markers(text: str) -> bool:
    t = str(text or "")
    if not t.strip():
        return False
    hits = sum(1 for m in _CONDITION_BODY_MARKERS if m in t)
    return hits >= 2 or ("（1）" in t and "试验箱" in t)


def _sub_method_title_match(short_name: str, sub_title: str) -> bool:
    sn = re.sub(r"\s+", "", str(short_name or ""))
    st = re.sub(r"\s+", "", str(sub_title or ""))
    if not sn or not st:
        return False
    if sn in st or st in sn:
        return True
    tokens = ("低温", "高温", "低气压", "防水", "砂尘", "浸水", "跌水", "喷淋")
    sn_hits = [tok for tok in tokens if tok in sn]
    if sn_hits and all(tok in st for tok in sn_hits):
        return True
    if "非工作" in sn and "非工作" in st:
        return True
    if "工作" in sn and "工作" in st and "非工作" not in sn:
        return True
    return False


_PROCEDURAL_SUB_TITLE_HINTS = (
    "阶段", "步骤", "返程", "顺风", "逆风", "单程",
    "结果计算", "合格判据", "数据统算", "试验准备",
    "设备安装", "风场监测", "航向规划", "速度边界",
    "往程飞行", "返程飞行",
)
_SECTION_HEADING_TITLES = frozenset({
    "试验目的", "试验条件", "试验方法", "试验过程", "试验步骤",
    "试验数据处理", "结果评定", "结果判定", "结果评判",
})


def _is_procedural_sub_method_title(title: str) -> bool:
    """过程性步骤标题不应作为独立试验方法。"""
    t = re.sub(r"\s+", "", str(title or "").strip())
    if not t:
        return True
    if t in _SECTION_HEADING_TITLES:
        return True
    if any(h in t for h in _PROCEDURAL_SUB_TITLE_HINTS):
        return True
    if re.match(r"^[（(]?\d+[）)]?$", t):
        return True
    return False


def _filter_sub_method_titles(titles: List[str]) -> List[str]:
    """过滤 LLM/规则误识别的过程步骤标题，保留并列试验子项。"""
    out: List[str] = []
    for raw in _dedupe_str(titles or []):
        title = str(raw or "").strip()
        if not title or _is_procedural_sub_method_title(title):
            continue
        out.append(title)
    return out


def _match_sub_method_syff(
    short_name: str,
    rule_sections: Dict[str, Dict[str, Any]],
) -> str:
    syff_text = str((rule_sections.get("syff") or {}).get("text") or "")
    if not syff_text.strip() or not str(short_name or "").strip():
        return ""
    sub_items = _parse_sub_methods_rule(syff_text)
    for sub in sub_items:
        if _sub_method_title_match(short_name, sub.get("name") or ""):
            return str(sub.get("text") or "").strip()
    return ""


def _rebalance_llm_method_sections(
    method: Dict[str, Any],
    rule_sections: Dict[str, Dict[str, Any]],
    *,
    block_text: str = "",
    full_text: str = "",
) -> Dict[str, Any]:
    """纠正 LLM 误把 8.2.1 设备/样品内容写入 symd、子方法 syff 漏填等问题。"""
    out = dict(method)
    secs = _normalize_method_sections(out)
    symd_txt = str(secs["symd"].get("text") or "").strip()
    sytj_txt = str(secs["sytj"].get("text") or "").strip()
    syff_txt = str(secs["syff"].get("text") or "").strip()
    short = str(out.get("method_short_name") or out.get("name") or "")

    if symd_txt and _text_has_condition_markers(symd_txt):
        if not sytj_txt:
            secs["sytj"] = _normalize_text_block({"text": symd_txt})
        elif symd_txt not in sytj_txt:
            secs["sytj"] = _normalize_text_block({"text": f"{sytj_txt}\n\n{symd_txt}".strip()})
        secs["symd"] = _normalize_text_block({"text": ""})

    if not syff_txt:
        matched = _match_sub_method_syff(short, rule_sections)
        if not matched and block_text.strip():
            inline_rule = _parse_project_sections_rule(block_text)
            matched = _match_sub_method_syff(short, inline_rule)
        if matched:
            secs["syff"] = _normalize_text_block({"text": matched})
            syff_txt = matched

    sub_index = 0
    sub_title = short
    for sub in _parse_sub_methods_rule(str((rule_sections.get("syff") or {}).get("text") or "")):
        if _sub_method_title_match(short, sub.get("name") or ""):
            sub_index = int(sub.get("index") or 0)
            sub_title = str(sub.get("name") or short)
            break

    for key in CORE_SLOT_KEYS:
        cur = str(secs[key].get("text") or "").strip()
        if cur:
            continue
        if key == "syff":
            matched = _match_sub_method_syff(short, rule_sections)
            if matched:
                secs[key] = _normalize_text_block({"text": matched})
            continue
        rule_txt = str((rule_sections.get(key) or {}).get("text") or "").strip()
        if not rule_txt:
            continue
        if key == "jgpd" and sub_index:
            secs[key] = _slice_jgpd_for_submethod(
                _normalize_text_block({"text": rule_txt}),
                sub_index,
                sub_title,
            )
        else:
            secs[key] = _normalize_text_block({"text": rule_txt})

    for key in METHOD_SECTION_KEYS:
        out[key] = secs[key]
    return _attach_recall_alias(out)


def _normalize_ws_for_match(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _text_found_in_source(text: str, *sources: str) -> bool:
    """校验抽取块是否能在原文中找到（允许空白差异）。"""
    t = str(text or "").strip()
    if not t:
        return True
    norm = _normalize_ws_for_match(t)
    if len(norm) < 20:
        for src in sources:
            if t in (src or ""):
                return True
        return False
    head = norm[: min(40, len(norm))]
    for src in sources:
        if head and head in _normalize_ws_for_match(src or ""):
            return True
    return False


def _apply_mandatory_sections_from_rule(
    method: Dict[str, Any],
    rule_sections: Dict[str, Dict[str, Any]],
    *sources: str,
) -> Dict[str, Any]:
    """LLM 字段校验失败时用规则解析结果覆盖；仍缺项则返回原 method。"""
    out = dict(method)
    secs = _normalize_method_sections(out)
    for key in METHOD_SECTION_KEYS:
        txt = str(secs[key].get("text", "") or "").strip()
        if txt and _text_found_in_source(txt, *sources):
            continue
        rule_txt = str((rule_sections.get(key) or {}).get("text", "") or "").strip()
        if rule_txt:
            secs[key] = _normalize_text_block({"text": rule_txt})
    for key in METHOD_SECTION_KEYS:
        out[key] = secs[key]
    return _attach_recall_alias(out)


def _structure_profile_json(profile: Optional[Dict[str, Any]]) -> str:
    if not profile:
        return "{}"
    try:
        return json.dumps(profile, ensure_ascii=False, indent=0)[:4000]
    except Exception:
        return "{}"


def _build_heading_outline(parsed_doc: Dict[str, Any], limit: int = 80) -> str:
    """净化后的目录大纲：去重节号、过滤表体/列表项，仅保留标题行。"""
    outline = build_llm_heading_outline(parsed_doc, limit=limit)
    if outline and outline != "（无目录）":
        return outline
    lines: List[str] = []
    file_path = str(parsed_doc.get("file_path") or "").strip()
    ext = str(parsed_doc.get("file_ext") or "").lower()
    if file_path and ext in (".docx", ".doc") and os.path.isfile(file_path):
        try:
            from blueprints.ZSGL.WJGL1.word_native_parse import extract_word_headings_merged
            from blueprints.ZSGL.WJGL1.document_heading_extract import flatten_toc_to_headings

            toc, _ = extract_word_headings_merged(file_path)
            stub_doc = {"flat_headings": flatten_toc_to_headings(toc, source="word_llm")}
            outline = build_llm_heading_outline(stub_doc, limit=limit)
            if outline and outline != "（无目录）":
                return outline
        except Exception as exc:
            logger.debug("[word_llm] heading outline 失败: %s", exc)
    return outline if outline else "（无目录）"


def _sample_full_text_for_structure(full_text: str) -> str:
    text = sanitize_text_for_llm(full_text or "")
    if not text.strip():
        return "（正文为空）"
    n = len(text)
    head = text[:3000]
    mid_start = max(0, n // 2 - 750)
    mid = text[mid_start: mid_start + 1500]
    tail = text[max(0, n - 1500):]
    parts = [f"【开头】\n{head}"]
    if n > 6000:
        parts.append(f"【中段】\n{mid}")
    if n > 4500:
        parts.append(f"【末尾】\n{tail}")
    return "\n\n".join(parts)


def _structure_profile_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """从统一规划结果提取 Phase0 结构 profile。"""
    if not isinstance(plan, dict):
        plan = {}
    aliases = plan.get("section_aliases")
    if not isinstance(aliases, dict):
        aliases = {
            "symd": ["试验目的"],
            "sytj": ["试验条件"],
            "syff": ["试验方法", "试验过程", "试验步骤"],
            "sjsjcl": ["试验数据处理"],
            "jgpd": ["结果评定", "结果判定"],
        }
    return {
        "document_style": str(plan.get("document_style") or "trial_plan"),
        "method_unit": str(plan.get("method_unit") or "per_chapter"),
        "project_heading_patterns": _dedupe_str(plan.get("project_heading_patterns") or []),
        "section_aliases": aliases,
        "sub_method_pattern": str(plan.get("sub_method_pattern") or "numbered_paren"),
        "warnings": _dedupe_str(plan.get("warnings") or []),
    }


def _organization_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        plan = {}
    mode = _normalize_organization_mode(plan.get("organization_mode"))
    try:
        confidence = float(plan.get("confidence") or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "organization_mode": mode or "integrated",
        "confidence": confidence,
        "reason": str(plan.get("reason") or "").strip(),
        "project_count_hint": plan.get("project_count_hint"),
        "method_count_hint": plan.get("method_count_hint"),
        "dispersed_project_hints": _dedupe_str(plan.get("dispersed_project_hints") or []),
        "warnings": _dedupe_str(plan.get("warnings") or []),
    }


def _normalize_projects_from_plan(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    merged: Dict[str, Dict[str, Any]] = {}
    for p in raw:
        if not isinstance(p, dict):
            continue
        name = str(p.get("project_name") or "").strip()
        if not name:
            continue
        key = re.sub(r"\s+", "", name)
        if key not in merged:
            merged[key] = {
                "project_id": p.get("project_id") or f"P{len(merged)+1:03d}",
                "project_name": name,
                "section_hint": str(p.get("section_hint") or "").strip(),
                "sub_method_titles": _dedupe_str(p.get("sub_method_titles") or []),
            }
        else:
            exist = merged[key]
            exist["sub_method_titles"] = _dedupe_str(
                exist.get("sub_method_titles", []) + (p.get("sub_method_titles") or [])
            )
            if not exist.get("section_hint") and p.get("section_hint"):
                exist["section_hint"] = str(p["section_hint"]).strip()
    return list(merged.values())


def llm_unified_document_plan(parsed_doc: Dict[str, Any]) -> Dict[str, Any]:
    """先总后分：一次 LLM 完成结构 + 组织形态 + 试验项目列表。"""
    cached = parsed_doc.get("_llm_document_plan")
    if isinstance(cached, dict) and cached.get("organization_mode"):
        return cached

    body = _build_document_body_for_llm(parsed_doc)
    heading_outline = _build_heading_outline(parsed_doc, limit=100)
    body_sample = _sample_full_text_for_structure(body)
    prompt = UNIFIED_DOCUMENT_PLAN_PROMPT.format(
        file_name=str(parsed_doc.get("file_name") or ""),
        heading_outline=heading_outline[:5000] or "（无）",
        body_sample=body_sample[:8000],
    )
    logger.info("[word_llm] 统一文档规划 prompt=%d 字", len(prompt))
    data = _call_llm_json(prompt)
    if not isinstance(data, dict):
        data = {}

    mode = _normalize_organization_mode(data.get("organization_mode"))
    if not mode:
        mode = _heuristic_organization_mode(parsed_doc, body)
        data.setdefault("warnings", []).append("LLM 未返回有效 organization_mode，已用规则兜底")
    data["organization_mode"] = mode
    data.setdefault("document_style", "trial_plan")
    data.setdefault("method_unit", "per_chapter")
    data.setdefault("section_aliases", {
        "sytj": ["试验条件"],
        "syff": ["试验方法", "试验过程"],
        "jgpd": ["结果评定"],
    })
    data["projects"] = _normalize_projects_from_plan(data.get("projects"))

    parsed_doc["_llm_document_plan"] = data
    try:
        debug_dir = str(parsed_doc.get("_document_dir") or "")
        if debug_dir:
            with open(os.path.join(debug_dir, "document_plan.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    logger.info(
        "[word_llm] 统一规划 mode=%s style=%s projects=%d",
        mode,
        data.get("document_style"),
        len(data.get("projects") or []),
    )
    return data


def analyze_document_structure(parsed_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Phase0：优先复用统一规划结果，避免重复 LLM。"""
    plan = parsed_doc.get("_llm_document_plan")
    if isinstance(plan, dict) and plan.get("document_style"):
        return _structure_profile_from_plan(plan)

    plan = llm_unified_document_plan(parsed_doc)
    profile = _structure_profile_from_plan(plan)
    if not profile.get("document_style"):
        profile["warnings"] = _dedupe_str(
            (profile.get("warnings") or []) + ["统一规划未返回 document_style，使用默认"]
        )
    logger.info(
        "[word_llm] Phase0 完成 style=%s unit=%s",
        profile.get("document_style"),
        profile.get("method_unit"),
    )
    return profile


def _normalize_recall_plan(value: Any) -> Dict[str, Dict[str, Any]]:
    slots = ("equipment", "environment", "test_project", "indicator")
    src = value if isinstance(value, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for slot in slots:
        block = src.get(slot, {})
        if not isinstance(block, dict):
            block = {}
        kws = block.get("keywords", [])
        if not isinstance(kws, list):
            kws = []
        out[slot] = {
            "keywords": _dedupe_str(kws),
            "reason": str(block.get("reason", "") or "").strip(),
        }
    return out


def _normalize_text_block(value: Any) -> Dict[str, Any]:
    block = value if isinstance(value, dict) else {}
    text = str(block.get("text", "") or "").strip()
    th = block.get("tables_html", [])
    fl = block.get("formulas_latex", [])
    return {
        "text": text,
        "tables_html": [str(x) for x in (th or []) if str(x).strip()],
        "formulas_latex": [str(x) for x in (fl or []) if str(x).strip()],
    }


def _merge_text_blocks(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    na, nb = _normalize_text_block(a), _normalize_text_block(b)
    ta, tb = na["text"], nb["text"]
    if tb and tb not in ta:
        merged = f"{ta}\n\n{tb}".strip() if ta else tb
    else:
        merged = ta or tb
    return {
        "text": merged,
        "tables_html": _dedupe_str(na["tables_html"] + nb["tables_html"]),
        "formulas_latex": _dedupe_str(na["formulas_latex"] + nb["formulas_latex"]),
    }


def _empty_sections() -> Dict[str, Dict[str, Any]]:
    return {k: _normalize_text_block({}) for k in METHOD_SECTION_KEYS}


def _normalize_method_sections(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """归一化五项；sygc 与 syff 同步供图库召回。"""
    out = _empty_sections()
    for key in METHOD_SECTION_KEYS:
        out[key] = _normalize_text_block(item.get(key))
    # 兼容 LLM 仍输出 sygc 的情况
    legacy_gc = _normalize_text_block(item.get("sygc"))
    if legacy_gc["text"] and not out["syff"]["text"]:
        out["syff"] = legacy_gc
    return out


def _attach_recall_alias(method: Dict[str, Any]) -> Dict[str, Any]:
    """sygc 供 retriever 使用，内容与 syff 一致。"""
    method["sygc"] = _normalize_text_block(method.get("syff", {}))
    return method


def _merge_method_partial(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key in METHOD_SECTION_KEYS:
        if key in patch:
            out[key] = _merge_text_blocks(out.get(key, {}), patch[key])
    if "sygc" in patch and "syff" not in patch:
        out["syff"] = _merge_text_blocks(out.get("syff", {}), patch["sygc"])
    rp = patch.get("recall_plan")
    if isinstance(rp, dict):
        merged_rp = _normalize_recall_plan(out.get("recall_plan"))
        for slot, block in _normalize_recall_plan(rp).items():
            if block.get("keywords"):
                merged_rp[slot]["keywords"] = _dedupe_str(
                    merged_rp[slot]["keywords"] + block["keywords"]
                )
            if block.get("reason") and not merged_rp[slot].get("reason"):
                merged_rp[slot]["reason"] = block["reason"]
        out["recall_plan"] = merged_rp
    if patch.get("description") and not out.get("description"):
        out["description"] = patch["description"]
    return _attach_recall_alias(out)


# ---------------------------------------------------------------------------
# 固定切片
# ---------------------------------------------------------------------------

def fixed_split_text_chunks(
    text: str,
    chunk_chars: int = FIXED_CHUNK_TEXT_CHARS,
    overlap_chars: int = FIXED_CHUNK_OVERLAP_CHARS,
) -> List[str]:
    """按段落贪心分包，批与批之间保留固定重叠字符。"""
    raw = (text or "").strip()
    if not raw:
        return []
    if len(raw) <= chunk_chars:
        return [raw]

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw[i : i + chunk_chars] for i in range(0, len(raw), chunk_chars)]

    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
        buf, buf_len = [], 0

    for para in paragraphs:
        plen = len(para) + (2 if buf else 0)
        if buf and buf_len + plen > chunk_chars:
            flush()
        if len(para) > chunk_chars:
            if buf:
                flush()
            for i in range(0, len(para), chunk_chars):
                chunks.append(para[i : i + chunk_chars])
            continue
        buf.append(para)
        buf_len += plen

    flush()

    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks

    overlapped: List[str] = [chunks[0]]
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        tail = prev[-overlap_chars:] if len(prev) > overlap_chars else prev
        overlapped.append(f"{tail}\n\n{chunks[i]}".strip())
    return overlapped


def _section_no_depth(section_no: str) -> int:
    return len([p for p in str(section_no or "").strip().split(".") if p])


def _parse_section_no_from_hint(hint: str) -> str:
    m = re.match(r"(\d+(?:\.\d+)*)", str(hint or "").strip())
    return m.group(1) if m else ""


def _find_next_peer_section_pos(text: str, from_pos: int, section_no: str) -> int:
    """下一同级节起点（8.2→8.3），跳过 8.2.1/8.2.3 等子节。"""
    depth = _section_no_depth(section_no)
    if depth < 1:
        return len(text)
    tail = text[from_pos:]
    for m in re.finditer(r"(?:^|\n)\s*(\d+(?:\.\d+)*)\s+", tail):
        other = m.group(1)
        od = _section_no_depth(other)
        if od < depth:
            return from_pos + m.start()
        if od == depth and other != section_no:
            return from_pos + m.start()
    return len(text)


def slice_project_block_by_section_hint(full_text: str, section_hint: str) -> str:
    """按节号截取试验项目整块（如 8.2 至 8.3 前），含全部 8.2.x 子节。"""
    sno = _parse_section_no_from_hint(section_hint)
    if not sno or not full_text:
        return ""
    start_m = re.search(rf"(?:^|\n)\s*{re.escape(sno)}\s+", full_text)
    if not start_m:
        return ""
    start = start_m.start()
    end = _find_next_peer_section_pos(full_text, start_m.end(), sno)
    return full_text[start:end].strip()


_HTML_WJGL_HEADING_RE = re.compile(
    r'(<h[1-6]\b[^>]*class="[^"]*wjgl-section-heading[^"]*"[^>]*data-section-no="([^"]+)"[^>]*>.*?</h[1-6]>)',
    re.I | re.S,
)
_HTML_SUB_METHOD_P_RE = re.compile(
    r"<p[^>]*>\s*[（(](\d+)[）)]\s*([^<]+)</p>",
    re.I | re.S,
)


def _heading_plain_title(heading_html: str) -> str:
    plain = re.sub(r"<[^>]+>", "", str(heading_html or ""))
    return re.sub(r"\s+", " ", plain).strip()


def _is_peer_section_no(base: str, other: str) -> bool:
    """判断 other 是否为 base 的同级节（8.2 与 8.3；忽略列表样式产生的 sec-1）。"""
    bd = _section_no_depth(base)
    od = _section_no_depth(other)
    if od != bd or other == base:
        return False
    if bd == 1:
        return True
    return base.split(".")[:-1] == other.split(".")[:-1]


def _find_next_peer_html_pos(html: str, from_pos: int, section_no: str) -> int:
    """HTML 中下一同级 wjgl-section-heading 起点。"""
    for m in _HTML_WJGL_HEADING_RE.finditer(html, from_pos):
        other = m.group(2).strip()
        if _is_peer_section_no(section_no, other):
            return m.start()
    return len(html)


def slice_project_html_by_section_hint(full_html: str, section_hint: str) -> str:
    """按节号从 Word/MinerU 原生 HTML 截取试验项目整块。"""
    sno = _parse_section_no_from_hint(section_hint)
    if not sno or not full_html:
        return ""
    start_pos: Optional[int] = None
    start_end = 0
    for m in _HTML_WJGL_HEADING_RE.finditer(full_html):
        sec_no = m.group(2).strip()
        title = _heading_plain_title(m.group(1))
        if sec_no == sno or re.match(rf"^{re.escape(sno)}(?:\s|$)", title):
            start_pos = m.start()
            start_end = m.end()
            break
    if start_pos is None:
        return ""
    end = _find_next_peer_html_pos(full_html, start_end, sno)
    return full_html[start_pos:end].strip()


def extract_project_html_block(
    full_html: str,
    project_name: str,
    section_hint: str = "",
) -> str:
    """从 full_html 截取单个试验项目块（优先节号，其次项目名）。"""
    html = str(full_html or "").strip()
    if not html:
        return ""
    by_hint = slice_project_html_by_section_hint(html, section_hint)
    if by_hint:
        return by_hint
    name = str(project_name or "").strip()
    if not name:
        return ""
    name_pat = re.escape(name)
    for m in _HTML_WJGL_HEADING_RE.finditer(html):
        title = _heading_plain_title(m.group(1))
        if re.search(name_pat, title):
            sec_no = m.group(2).strip()
            end = _find_next_peer_html_pos(html, m.end(), sec_no)
            return html[m.start():end].strip()
    return ""


def _resolve_project_section_no_from_html(
    full_html: str,
    project_name: str,
    section_hint: str = "",
) -> str:
    sno = _parse_section_no_from_hint(section_hint)
    if sno:
        return sno
    html = str(full_html or "").strip()
    name = str(project_name or "").strip()
    if not html or not name:
        return ""
    name_pat = re.escape(name)
    for m in _HTML_WJGL_HEADING_RE.finditer(html):
        title = _heading_plain_title(m.group(1))
        if re.search(name_pat, title):
            return m.group(2).strip()
    return ""


def _heading_label_to_section_key(
    title_plain: str,
    structure_profile: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    label = re.sub(r"^\d+(?:\.\d+)*\s*", "", str(title_plain or "").strip()).strip()
    if not label:
        return None
    label_map = _label_to_section_key_map(structure_profile)
    if label in label_map:
        return label_map[label]
    for lbl, key in sorted(label_map.items(), key=lambda x: len(x[0]), reverse=True):
        if label == lbl or label.endswith(lbl) or lbl in label:
            return key
    return None


def split_method_sections_from_html(
    project_html: str,
    project_section_no: str,
    structure_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """按五段标题从项目 HTML 切片（保留 table/段落结构）。"""
    sections = {k: "" for k in METHOD_SECTION_KEYS}
    html = str(project_html or "").strip()
    if not html:
        return sections
    prefix = f"{project_section_no}." if project_section_no else ""
    markers: List[Tuple[int, int, str]] = []
    for m in _HTML_WJGL_HEADING_RE.finditer(html):
        sec_no = m.group(2).strip()
        if prefix and not sec_no.startswith(prefix):
            continue
        key = _heading_label_to_section_key(_heading_plain_title(m.group(1)), structure_profile)
        if not key:
            continue
        markers.append((m.start(), m.end(), key))
    if not markers:
        return sections
    for i, (_start, end, key) in enumerate(markers):
        body_end = markers[i + 1][0] if i + 1 < len(markers) else len(html)
        body = html[end:body_end].strip()
        if not body:
            continue
        if sections[key]:
            sections[key] = f"{sections[key]}{body}"
        else:
            sections[key] = body
    for key in sections:
        if sections[key]:
            sections[key] = _demote_inline_list_headings(sections[key])
    return sections


_INLINE_LIST_HEADING_RE = re.compile(
    r"<h[1-6]\b[^>]*class=\"[^\"]*wjgl-section-heading[^\"]*\"[^>]*data-section-no=\"(\d{1,2})\"[^>]*>.*?</h[1-6]>",
    re.I | re.S,
)


def _demote_inline_list_headings(html: str) -> str:
    """Word 编号列表误识别为 h1（sec-1/sec-2）时降回段落，保留版式可读性。"""
    h = str(html or "").strip()
    if not h:
        return ""

    def repl(m: re.Match) -> str:
        sec_no = m.group(1).strip()
        if not sec_no.isdigit() or int(sec_no) > 30:
            return m.group(0)
        plain = _heading_plain_title(m.group(0))
        if not plain:
            return m.group(0)
        return f"<p><strong>{plain}</strong></p>"

    return _INLINE_LIST_HEADING_RE.sub(repl, h)


def _clean_native_section_body_html(html: str, section_label: str) -> str:
    """原生 HTML 切片：保留 8.3.1 等节号标题与表格，仅去掉与五段大标题完全重复的孤立行。"""
    h = str(html or "").strip()
    if not h:
        return ""
    labels = {section_label}
    lines_out: List[str] = []
    for ln in re.split(r"(?<=\</p\>)|(?<=\</tr\>)|(?<=\</h[1-6]\>)", h):
        plain = re.sub(r"<[^>]+>", "", ln).strip()
        if not plain:
            lines_out.append(ln)
            continue
        if plain in labels and not re.search(r"\d+(?:\.\d+)+", plain):
            continue
        lines_out.append(ln)
    return "".join(lines_out).strip()


def _slice_sub_method_syff_html(syff_html: str, short_name: str) -> str:
    html = str(syff_html or "").strip()
    short = str(short_name or "").strip()
    if not html or not short:
        return ""
    matches = list(_HTML_SUB_METHOD_P_RE.finditer(html))
    if not matches:
        plain = re.sub(r"<[^>]+>", "\n", html)
        matched = _match_sub_method_syff(short, {"syff": {"text": plain}})
        if not matched:
            return ""
        idx = plain.find(matched[: min(40, len(matched))])
        if idx < 0:
            return matched
        return html[idx:].split("<h", 1)[0].strip() if "<h" in html[idx:] else html[idx:].strip()
    for i, m in enumerate(matches):
        title = str(m.group(2) or "").strip()
        if not _sub_method_title_match(short, title):
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        return html[start:end].strip()
    return ""


def _slice_sub_method_jgpd_html(
    jgpd_html: str,
    short_name: str,
    syff_html: str = "",
) -> str:
    html = str(jgpd_html or "").strip()
    short = str(short_name or "").strip()
    if not html or not short:
        return html
    sub_index = 0
    plain_syff = re.sub(r"<[^>]+>", "\n", syff_html or "")
    for sub in _parse_sub_methods_rule(plain_syff):
        if _sub_method_title_match(short, sub.get("name") or ""):
            sub_index = int(sub.get("index") or 0)
            break
    if not sub_index:
        return html
    matches = list(_HTML_SUB_METHOD_P_RE.finditer(html))
    for i, m in enumerate(matches):
        if int(m.group(1)) != sub_index:
            continue
        start = m.start()
        end = len(html)
        for m2 in matches[i + 1 :]:
            end = m2.start()
            break
        return html[start:end].strip()
    sliced = _slice_jgpd_for_submethod(
        _normalize_text_block({"text": re.sub(r"<[^>]+>", "\n", html)}),
        sub_index,
        short,
    )
    txt = str(sliced.get("text") or "").strip()
    if not txt:
        return html
    return "".join(f"<p>{ln}</p>" for ln in txt.splitlines() if ln.strip())


def extract_project_section_text(full_text: str, project_name: str, section_hint: str = "") -> str:
    """从全文截取单个试验项目块（从项目标题到下一同级试验章节或文档末）。"""
    text = full_text or ""
    name_pat = re.escape(project_name.strip())
    start_re = re.compile(
        rf"(?:^|\n)\s*(?:#{{1,4}}\s*)?(?:\d+(?:\.\d+)+\s*)?试验项目[^：:\n]*[：:]\s*{name_pat}",
        re.MULTILINE,
    )
    m = start_re.search(text)
    if not m:
        scheme_re = re.compile(
            rf"(?:^|\n)\s*(?:\d+(?:\.\d+)+\s*)?{name_pat}\b",
            re.MULTILINE,
        )
        m = scheme_re.search(text)
    if not m and section_hint:
        hint = re.escape(section_hint.strip()[:60])
        m = re.search(rf"(?:^|\n).{{0,120}}{hint}", text, re.MULTILINE)
    if not m:
        m = re.search(rf"(?:^|\n).{{0,80}}{name_pat}", text, re.MULTILINE)
    if not m:
        return text[:8000]

    start = m.start()
    sno = _parse_section_no_from_hint(section_hint)
    if not sno:
        title_line = text[m.start(): text.find("\n", m.start())]
        sm = re.match(r"(?:^|\n)\s*(\d+(?:\.\d+)*)", title_line)
        if sm:
            sno = sm.group(1)
    if sno:
        end = _find_next_peer_section_pos(text, m.end(), sno)
    else:
        rest = text[m.end():]
        next_m = re.search(
            r"(?:^|\n)\s*(?:#{1,4}\s*)?"
            r"(?:(?:\d+(?:\.\d+)+\s*)?试验项目[一二三四五六七八九十0-9]+[：:]"
            r"|(?:\d+(?:\.\d+)+\s*)?[^\n]{2,40}试验\b)",
            rest,
            re.MULTILINE,
        )
        end = m.end() + next_m.start() if next_m else len(text)
    block = text[start:end].strip()
    return block or text[start : start + 8000]


_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d+(?:\.\d+)+[\s　.]*)?"
    r"(试验目的|试验条件|试验方法|试验过程|试验步骤|试验数据处理|结果评定|结果判定)\s*$",
    re.MULTILINE,
)
_SECTION_KEY_MAP = {
    "试验目的": "symd",
    "试验条件": "sytj",
    "试验方法": "syff",
    "试验过程": "syff",
    "试验步骤": "syff",
    "试验数据处理": "sjsjcl",
    "结果评定": "jgpd",
    "结果判定": "jgpd",
}

DEFAULT_SECTION_ALIASES: Dict[str, List[str]] = {
    "symd": ["试验目的", "测试目的"],
    "sytj": [
        "试验条件", "测试条件", "环境条件", "工况", "样品要求", "试样要求", "试验要求",
    ],
    "syff": [
        "试验方法", "试验过程", "试验步骤", "测试方法", "操作方法", "实施步骤", "实施程序",
    ],
    "sjsjcl": ["试验数据处理", "数据处理"],
    "jgpd": [
        "结果评定", "结果判定", "结果评价", "合格判据", "评定准则", "判定标准", "考核要求",
        "试验结果评判/评定", "试验结果评判", "试验结果评定",
    ],
}


def _merge_section_aliases(structure_profile: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    out = {k: list(v) for k, v in DEFAULT_SECTION_ALIASES.items()}
    aliases = (structure_profile or {}).get("section_aliases")
    if isinstance(aliases, dict):
        for key, labels in aliases.items():
            slot = str(key or "").strip()
            if slot not in out or not isinstance(labels, list):
                continue
            for lbl in labels:
                text = str(lbl or "").strip()
                if text and text not in out[slot]:
                    out[slot].append(text)
    return out


def _label_to_section_key_map(structure_profile: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    label_map: Dict[str, str] = {}
    for key, labels in _merge_section_aliases(structure_profile).items():
        for lbl in labels:
            if lbl:
                label_map[lbl] = key
    for lbl, key in _SECTION_KEY_MAP.items():
        if lbl not in label_map:
            label_map[lbl] = key
    return label_map


def _section_heading_pattern(structure_profile: Optional[Dict[str, Any]] = None) -> re.Pattern:
    label_map = _label_to_section_key_map(structure_profile)
    labels = sorted(label_map.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(lbl) for lbl in labels)
    return re.compile(
        rf"(?:^|\n)\s*(?:#{{1,6}}\s*)?(?:\d+(?:\.\d+)+[\s　.]*)?({alt})\s*$",
        re.MULTILINE,
    )


def _section_numbered_label_pattern(structure_profile: Optional[Dict[str, Any]] = None) -> re.Pattern:
    """匹配 8.2.3 试验方法…（标题与正文同行）及独立标题行。"""
    label_map = _label_to_section_key_map(structure_profile)
    labels = sorted(label_map.keys(), key=len, reverse=True)
    alt = "|".join(re.escape(lbl) for lbl in labels)
    return re.compile(
        rf"(?:^|\n)\s*(?:#{{1,6}}\s*)?(\d+(?:\.\d+)*)\s*({alt})(?=\s|\s*$|[\u4e00-\u9fff（(])",
        re.MULTILINE,
    )
_SUB_METHOD_RE = re.compile(
    r"^\s*(?:\*{2})?[（(](\d+)[）)]\s*(.+?)(?:\*{2})?\s*$",
    re.MULTILINE,
)
# 优先匹配 fftq 风格的加粗子方法标题：**（1）xxx**
_SUB_METHOD_BOLD_RE = re.compile(
    r"^\s*\*\*[（(](\d+)[）)]\s*(.+?)\*\*\s*$",
    re.MULTILINE,
)
# 无加粗编号：1. xxx / 1、xxx / 1) xxx
_SUB_METHOD_NUM_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d+(?:\.\d+)*[\s.]*)?(\d+)[\.．、\)]\s*(.+?)\s*$",
    re.MULTILINE,
)


def _collect_sub_method_matches(text: str) -> List[re.Match]:
    for pattern in (_SUB_METHOD_BOLD_RE, _SUB_METHOD_RE, _SUB_METHOD_NUM_RE):
        matches = list(pattern.finditer(text or ""))
        if matches:
            return matches
    return []


def _slice_jgpd_for_submethod(jgpd: Dict[str, Any], sub_index: int, sub_title: str) -> Dict[str, Any]:
    """结果评定中尽量截取与子方法相关的行/段落（文档不固定时匹配失败则保留全文）。"""
    base = _normalize_text_block(jgpd)
    text = base.get("text", "") or ""
    if not text.strip():
        return base
    markers = [
        f"({sub_index})",
        f"（{sub_index}）",
        f".{sub_index})",
        f".{sub_index}）",
    ]
    title_key = (sub_title or "")[:12].strip()
    lines = text.splitlines()
    picked: List[str] = []
    for ln in lines:
        if any(m in ln for m in markers) or (title_key and title_key in ln):
            picked.append(ln)
    if not picked:
        return base
    header = lines[0] if lines and "|" in lines[0] else ""
    body = "\n".join(picked).strip()
    if header and "|" in header and header not in body:
        body = f"{header}\n{body}"
    return _normalize_text_block({"text": body, "tables_html": base.get("tables_html", [])})


def _fill_method_sections_from_project(
    method: Dict[str, Any],
    sections: Dict[str, Dict[str, Any]],
    syff_text: str = "",
) -> Dict[str, Any]:
    """用项目级五段补齐 LLM 漏填字段。"""
    out = dict(method)
    for key in METHOD_SECTION_KEYS:
        cur = _normalize_text_block(out.get(key))
        if cur.get("text"):
            if key == "symd" and _text_has_condition_markers(cur.get("text", "")):
                continue
            out[key] = cur
            continue
        if key == "syff" and syff_text:
            out[key] = _normalize_text_block({"text": syff_text})
        elif sections.get(key, {}).get("text"):
            sec_txt = str(sections[key].get("text") or "")
            if key == "symd" and _text_has_condition_markers(sec_txt):
                if not str(out.get("sytj", {}).get("text") or "").strip():
                    out["sytj"] = _normalize_text_block({"text": sec_txt})
                continue
            out[key] = _normalize_text_block(sections[key])
        else:
            out[key] = cur
    return _attach_recall_alias(out)


def _parse_project_sections_rule(
    block_text: str,
    structure_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """规则解析试验项目块内的五段正文（支持 section_aliases）。"""
    sections = _empty_sections()
    text = block_text or ""
    if not text.strip():
        return sections

    label_map = _label_to_section_key_map(structure_profile)
    pattern = _section_numbered_label_pattern(structure_profile)
    matches = list(pattern.finditer(text))
    if not matches:
        pattern = _section_heading_pattern(structure_profile)
        matches = list(pattern.finditer(text))
    if not matches:
        return sections

    for i, m in enumerate(matches):
        label = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
        key = label_map.get(label)
        if not key:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections[key] = _normalize_text_block({"text": body})

    symd_body = str(sections["symd"].get("text") or "").strip()
    if symd_body and _text_has_condition_markers(symd_body):
        sytj_body = str(sections["sytj"].get("text") or "").strip()
        merged = f"{sytj_body}\n\n{symd_body}".strip() if sytj_body else symd_body
        sections["sytj"] = _normalize_text_block({"text": merged})
        sections["symd"] = _normalize_text_block({"text": ""})

    return sections


def _parse_sub_methods_rule(syff_text: str) -> List[Dict[str, str]]:
    """从试验方法正文中切分子方法（1）（2）…；无子条目则返回空列表。"""
    text = syff_text or ""
    if not text.strip():
        return []

    matches = _collect_sub_method_matches(text)
    if not matches:
        return []

    parts: List[Dict[str, str]] = []
    for i, m in enumerate(matches, start=1):
        title = m.group(2).strip().strip("*")
        start = m.start()
        end = matches[i].start() if i < len(matches) else len(text)
        chunk = text[start:end].strip()
        if title and not _is_procedural_sub_method_title(title):
            parts.append({"index": i, "name": title, "text": chunk})
    return parts


def _build_methods_from_rule(block_text: str, project_name: str) -> List[Dict[str, Any]]:
    """
    LLM 失败或文档结构不固定时的规则兜底：
    每个方法 = 五项一组；命名 项目_方法。
    """
    sections = _parse_project_sections_rule(block_text)
    sub_items = _parse_sub_methods_rule(sections["syff"]["text"])
    if not sub_items:
        method_part = "整体试验"
        if sections["syff"].get("text"):
            method_part = "试验方法"
        full_name = _compose_method_name(project_name, method_part)
        method = {
            "candidate_id": "M001",
            "class": "试验大纲",
            "section_ids": [],
            "name": full_name,
            "method_short_name": method_part,
            "type": "试验方法",
            "description": f"{project_name}：{method_part}",
            "test_project_name": project_name,
            "recall_plan": _normalize_recall_plan({}),
            "symd": sections["symd"],
            "sytj": sections["sytj"],
            "syff": sections["syff"],
            "sjsjcl": sections["sjsjcl"],
            "jgpd": sections["jgpd"],
        }
        return [_attach_recall_alias(method)]

    methods: List[Dict[str, Any]] = []
    for i, sub in enumerate(sub_items, start=1):
        sub_name = sub["name"]
        sub_index = int(sub.get("index") or i)
        full_name = _compose_method_name(project_name, sub_name)
        method = {
            "candidate_id": f"M{i:03d}",
            "class": "试验大纲",
            "section_ids": [],
            "name": full_name,
            "method_short_name": sub_name,
            "type": "试验方法",
            "description": f"{project_name}：{sub_name}",
            "test_project_name": project_name,
            "recall_plan": _normalize_recall_plan({}),
            "symd": sections["symd"],
            "sytj": sections["sytj"],
            "syff": _normalize_text_block({"text": sub["text"]}),
            "sjsjcl": sections["sjsjcl"],
            "jgpd": _slice_jgpd_for_submethod(sections["jgpd"], sub_index, sub_name),
        }
        methods.append(_attach_recall_alias(method))
    return methods


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def inject_flask_runtime_into_word_llm(flask_app: Any) -> None:
    """SSE 后台线程：复用 Flask 已初始化的 LLM / Neo4j 客户端。"""
    Config = _get_main_config()
    try:
        client = flask_app.config.get("OPENAI_CLIENT")
        if client is not None:
            Config.OPENAI_CLIENT = client
        model = (
            flask_app.config.get("OPENAI_MODEL")
            or getattr(Config, "FLGL_TESTMETHOD_OPENAI_MODEL", None)
            or os.environ.get("OPENAI_MODEL", "")
        )
        if model:
            Config.OPENAI_MODEL = str(model)
        neo = (
            flask_app.config.get("FLGL_TESTMETHOD_NEO4J_DRIVER")
            or flask_app.config.get("NEO4J_DRIVER")
        )
        if neo is not None:
            Config.NEO4J_DRIVER = neo
    except Exception as exc:
        logger.warning("[word_llm] 注入 Flask runtime 失败: %s", exc)


def _init_runtime_from_config() -> None:
    Config = _get_main_config()

    if not hasattr(Config, "create_openai_client"):
        raise RuntimeError(
            "主 Config 缺少 create_openai_client，"
            f"请检查 {_BACKEND_ROOT / 'config.py'!s} 是否为最新版本。"
        )

    if getattr(Config, "OPENAI_CLIENT", None) is None:
        client = Config.create_openai_client()
        if client is None:
            base = getattr(Config, "OPENAI_BASE_URL", "") or ""
            key = getattr(Config, "OPENAI_API_KEY", "") or ""
            raise RuntimeError(
                "未配置 LLM 客户端（OPENAI_CLIENT=None）。"
                "云端请设置真实 OPENAI_API_KEY；"
                "本地 vLLM 请将 OPENAI_BASE_URL 指向内网/本机，"
                "并设置 OPENAI_API_KEY=EMPTY 或 OPENAI_ALLOW_EMPTY_KEY=1。"
                f" 当前 OPENAI_BASE_URL={base!r}, OPENAI_API_KEY={key!r}"
            )
        Config.OPENAI_CLIENT = client
        logger.info(
            "OpenAI 客户端已就绪 base_url=%s model=%s local_no_auth=%s",
            getattr(Config, "OPENAI_BASE_URL", ""),
            getattr(Config, "OPENAI_MODEL", ""),
            Config.effective_openai_api_key() == "EMPTY",
        )

    if not getattr(Config, "OPENAI_MODEL", None):
        model = getattr(Config, "FLGL_TESTMETHOD_OPENAI_MODEL", None) or os.environ.get(
            "OPENAI_MODEL", ""
        )
        if model:
            Config.OPENAI_MODEL = model
        else:
            raise RuntimeError("OPENAI_MODEL 未配置")

    Config.LLM_CONTEXT_TOKENS = FIXED_LLM_CONTEXT_TOKENS
    Config.LLM_MAX_COMPLETION_TOKENS = FIXED_LLM_MAX_COMPLETION_TOKENS

    try:
        from neo4j import GraphDatabase
    except ImportError:
        GraphDatabase = None

    if getattr(Config, "NEO4J_DRIVER", None) is None and GraphDatabase is not None:
        uri = getattr(Config, "FLGL_TESTMETHOD_NEO4J_URI", None) or getattr(
            Config, "NEO4J_URI", None
        )
        user = getattr(Config, "FLGL_TESTMETHOD_NEO4J_USER", None) or getattr(
            Config, "NEO4J_USER", None
        )
        pwd = getattr(Config, "FLGL_TESTMETHOD_NEO4J_PASSWORD", None) or getattr(
            Config, "NEO4J_PASSWORD", None
        )
        if uri and user is not None and pwd is not None:
            try:
                Config.NEO4J_DRIVER = GraphDatabase.driver(uri, auth=(user, pwd))
                with Config.NEO4J_DRIVER.session() as session:
                    session.run("RETURN 1")
            except Exception as exc:
                logger.warning("Neo4j 初始化失败，graph_matches 将为空: %s", exc)
                Config.NEO4J_DRIVER = None

    if not hasattr(Config, "BGE_MODEL_PATH"):
        Config.BGE_MODEL_PATH = (
            getattr(Config, "FLGL_TESTMETHOD_BGE_MODEL_PATH", None)
            or os.environ.get("BGE_MODEL_PATH")
            or r"./models/bge-small-zh-v1.5"
        )
    if not hasattr(Config, "BGE_LOCAL_FILES_ONLY"):
        Config.BGE_LOCAL_FILES_ONLY = bool(
            getattr(Config, "FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY", False)
        )
    if not hasattr(Config, "TESTMETHOD_RETRIEVER"):
        Config.TESTMETHOD_RETRIEVER = None


def _call_llm(prompt: str, temperature: float = 0.1) -> str:
    Config = _get_main_config()

    client = Config.OPENAI_CLIENT
    if client is None:
        logger.warning("[word_llm] OPENAI_CLIENT 未初始化，跳过 LLM 调用")
        return ""
    model = Config.OPENAI_MODEL
    max_tokens = min(FIXED_LLM_MAX_COMPLETION_TOKENS, FIXED_LLM_CONTEXT_TOKENS // 4)
    input_est = max(512, len(prompt) // 2 + 400)
    room = FIXED_LLM_CONTEXT_TOKENS - input_est - 200
    max_tokens = max(512, min(max_tokens, room))

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("[word_llm] LLM 调用失败: %s", exc)
        return ""
    choice = resp.choices[0] if resp and resp.choices else None
    return (choice.message.content or "").strip() if choice and choice.message else ""


def _call_llm_json(prompt: str) -> Dict[str, Any]:
    raw = _call_llm(prompt)
    if not raw:
        return {}
    result = _safe_json_loads(raw, {})
    return result if isinstance(result, dict) else {}


def _word_llm_legacy_phased_enabled() -> bool:
    return os.environ.get("WORD_LLM_LEGACY_PHASED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _llm_input_char_budget() -> int:
    """估算可放入单次 LLM 请求的文档正文字符上限。"""
    override = os.environ.get("WORD_LLM_FULL_DOC_MAX_CHARS", "").strip()
    if override.isdigit():
        return max(4000, int(override))
    return max(
        8000,
        int(FIXED_LLM_CONTEXT_TOKENS * 1.6)
        - FIXED_PROMPT_RESERVE_CHARS
        - FIXED_LLM_MAX_COMPLETION_TOKENS,
    )


def _build_document_body_for_llm(parsed_doc: Dict[str, Any]) -> str:
    """整文档正文：优先 Markdown 真源，其次 raw_full_text / full_text，再 HTML / sections。"""
    full_markdown = str(parsed_doc.get("full_markdown") or "").strip()
    if full_markdown:
        return sanitize_text_for_llm(full_markdown)
    full_text = str(
        parsed_doc.get("raw_full_text") or parsed_doc.get("full_text") or ""
    ).strip()
    if full_text:
        return sanitize_text_for_llm(full_text)
    full_html = str(parsed_doc.get("full_html") or "").strip()
    if full_html:
        plain = re.sub(r"<[^>]+>", "\n", full_html)
        return sanitize_text_for_llm(re.sub(r"\n{3,}", "\n\n", plain).strip())
    parts: List[str] = []
    for sec in parsed_doc.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        t = str(sec.get("text") or "").strip()
        if t:
            parts.append(t)
    return sanitize_text_for_llm("\n\n".join(parts).strip())


def _fit_document_body_for_llm(
    body: str,
    max_chars: Optional[int] = None,
    *,
    sections: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, bool]:
    """超长时按章节分块拼接（每节限额），避免中间整段丢失。"""
    max_chars = max_chars or _llm_input_char_budget()
    text = str(body or "").strip()
    if not text or len(text) <= max_chars:
        return text, False

    sec_list = [s for s in (sections or []) if isinstance(s, dict)]
    if sec_list:
        parts: List[str] = []
        total = 0
        per_sec = max(600, max_chars // max(len(sec_list), 1))
        for sec in sec_list:
            title = str(sec.get("title") or sec.get("heading") or "").strip()
            chunk_text = str(sec.get("text") or "").strip()
            if not chunk_text and not title:
                continue
            header = f"=== {title} ===" if title else "=== 章节 ==="
            chunk = f"{header}\n{chunk_text}" if chunk_text else header
            if len(chunk) > per_sec:
                chunk = chunk[:per_sec] + "\n...[本节截断]..."
            if total + len(chunk) + 2 > max_chars:
                remain = max_chars - total - 2
                if remain > 150:
                    parts.append(chunk[:remain] + "\n...[后续章节因长度省略]...")
                break
            parts.append(chunk)
            total += len(chunk) + 2
        if parts:
            fitted = "\n\n".join(parts).strip()
            if fitted:
                return fitted, len(fitted) < len(text)

    head = int(max_chars * 0.65)
    tail = max(2000, max_chars - head - 120)
    omitted = len(text) - head - tail
    if omitted < 0:
        return text[:max_chars], True
    fitted = (
        f"{text[:head]}\n\n"
        f"...[中间省略约 {omitted} 字，请结合首尾理解全文结构]...\n\n"
        f"{text[-tail:]}"
    )
    return fitted, True


def _llm_chunk_text_char_budget(*, prompt_shell_chars: int = 0) -> int:
    """单次 LLM 请求中可用于文档正文的字符预算（基于 16384 上下文）。"""
    shell = prompt_shell_chars or FIXED_PROMPT_RESERVE_CHARS
    return max(
        FIXED_CHUNK_TEXT_CHARS,
        _llm_input_char_budget() - shell,
    )


def _split_body_into_llm_chunks(
    body: str,
    *,
    sections: Optional[List[Dict[str, Any]]] = None,
    chunk_chars: Optional[int] = None,
) -> List[str]:
    """
    将文档正文切成适合单次 LLM（16384 上下文）的片段。
    优先按 sections 整节打包，单节过长再按段落切分并保留重叠。
    """
    budget = chunk_chars or _llm_chunk_text_char_budget()
    text = str(body or "").strip()
    if not text:
        return []
    if len(text) <= budget:
        return [text]

    sec_list = [s for s in (sections or []) if isinstance(s, dict)]
    if sec_list:
        chunks: List[str] = []
        buf_parts: List[str] = []
        buf_len = 0

        def flush_buf() -> None:
            nonlocal buf_parts, buf_len
            if buf_parts:
                chunks.append("\n\n".join(buf_parts).strip())
            buf_parts, buf_len = [], 0

        for sec in sec_list:
            title = str(sec.get("title") or sec.get("heading") or "").strip()
            chunk_text = str(sec.get("text") or "").strip()
            header = f"=== {title} ===" if title else ""
            block = f"{header}\n{chunk_text}".strip() if chunk_text or header else ""
            if not block:
                continue
            if len(block) > budget:
                flush_buf()
                chunks.extend(
                    fixed_split_text_chunks(
                        block,
                        chunk_chars=budget,
                        overlap_chars=FIXED_CHUNK_OVERLAP_CHARS,
                    )
                )
                continue
            if buf_parts and buf_len + len(block) + 2 > budget:
                flush_buf()
            buf_parts.append(block)
            buf_len += len(block) + (2 if buf_len else 0)
        flush_buf()
        if chunks:
            return chunks

    return split_text_at_section_boundaries(
        text,
        budget,
        overlap_chars=FIXED_CHUNK_OVERLAP_CHARS,
    )


def save_llm_run_debug(
    document_dir: str,
    *,
    prompt: str,
    raw_response: str,
    parsed_json: Any,
    document_body: str,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """落盘：给 LLM 的正文、完整提示词、原始返回、解析后 JSON。"""
    try:
        os.makedirs(document_dir, exist_ok=True)
        paths = {
            "document_body_for_llm.txt": document_body or "",
            "llm_prompt.txt": prompt or "",
            "llm_response_raw.txt": raw_response or "",
        }
        for name, content in paths.items():
            with open(os.path.join(document_dir, name), "w", encoding="utf-8") as f:
                f.write(content)
        with open(os.path.join(document_dir, "llm_response.json"), "w", encoding="utf-8") as f:
            json.dump(parsed_json if parsed_json is not None else {}, f, ensure_ascii=False, indent=2)
        if meta:
            with open(os.path.join(document_dir, "llm_run_meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.info("[word_llm] 已保存 LLM 调试文件至 %s", document_dir)
    except OSError as exc:
        logger.warning("[word_llm] 保存 LLM 调试文件失败: %s", exc)


def _safe_debug_dir_name(name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", str(name or "").strip())
    return (safe[:60] or "project").strip("_")


def _save_project_llm_chunk_debug(
    document_dir: str,
    project_name: str,
    chunk_index: int,
    *,
    prompt: str,
    raw_data: Any,
    normalized_methods: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """分散型：按项目/分片保存 LLM 提示词与原始 JSON 返回。"""
    if not document_dir:
        return
    try:
        base = os.path.join(document_dir, "llm_project_debug", _safe_debug_dir_name(project_name))
        os.makedirs(base, exist_ok=True)
        idx = f"{chunk_index:02d}"
        with open(os.path.join(base, f"chunk_{idx}_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt or "")
        with open(os.path.join(base, f"chunk_{idx}_response.json"), "w", encoding="utf-8") as f:
            json.dump(raw_data if isinstance(raw_data, dict) else {}, f, ensure_ascii=False, indent=2)
        if normalized_methods is not None:
            with open(os.path.join(base, f"chunk_{idx}_methods.json"), "w", encoding="utf-8") as f:
                json.dump(normalized_methods, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("[word_llm] 保存项目 LLM 分片调试失败 project=%s: %s", project_name, exc)


def _save_llm_extract_output(
    document_dir: str,
    methods: List[Dict[str, Any]],
    projects: List[Dict[str, Any]],
    run_meta: Dict[str, Any],
) -> None:
    """汇总落盘：全部方法的 LLM 结构化结果（含各字段原文）。"""
    if not document_dir:
        return
    try:
        os.makedirs(document_dir, exist_ok=True)
        payload = {
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "run_meta": run_meta,
            "projects": projects,
            "methods": [
                {
                    "name": m.get("name"),
                    "method_short_name": m.get("method_short_name"),
                    "test_project_name": m.get("test_project_name"),
                    "content_slots": m.get("content_slots"),
                    "sections": {
                        k: (m.get(k) or {}) for k in METHOD_SECTION_KEYS
                    },
                }
                for m in (methods or [])
                if isinstance(m, dict)
            ],
        }
        with open(os.path.join(document_dir, "llm_extract_output.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("[word_llm] 已保存 llm_extract_output.json -> %s", document_dir)
    except OSError as exc:
        logger.warning("[word_llm] 保存 llm_extract_output.json 失败: %s", exc)


def _warn_unverified_source_fields(method: Dict[str, Any], full_text: str) -> None:
    """原文校验未命中时保留内容，仅打日志。"""
    name = str(method.get("name") or "")
    for key in CORE_SLOT_KEYS:
        txt = str((method.get(key) or {}).get("text") or "").strip()
        if txt and not _text_found_in_source(txt, full_text):
            logger.warning(
                "[word_llm] 方法 '%s' 字段 %s 原文校验未命中，保留 LLM 内容",
                name, key,
            )


def _finalize_methods_batch(
    methods: List[Dict[str, Any]],
    full_text: str,
    structure_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """分片合并后：规则补全空字段、附加 content_slots、过滤全空方法。"""
    out: List[Dict[str, Any]] = []
    dropped_empty = 0
    incomplete = 0
    for raw in methods or []:
        if not isinstance(raw, dict):
            continue
        m = dict(raw)
        combined = "\n\n".join(
            str((m.get(k) or {}).get("text") or "") for k in CORE_SLOT_KEYS
        )
        if combined.strip():
            rule_local = _parse_project_sections_rule(combined, structure_profile)
            m = _apply_mandatory_sections_from_rule(m, rule_local, combined, full_text)
        project = str(m.get("test_project_name") or m.get("name") or "").strip()
        block = extract_project_section_text(full_text, project, project)
        if not block.strip():
            block = full_text
        rule_full = _parse_project_sections_rule(block, structure_profile)
        if not rule_full.get("sytj", {}).get("text"):
            rule_full = _parse_project_sections_rule(full_text, structure_profile)
        m = _apply_mandatory_sections_from_rule(m, rule_full, full_text)
        m = _rebalance_llm_method_sections(m, rule_full, block_text=block, full_text=full_text)
        _warn_unverified_source_fields(m, full_text)
        m = _attach_content_slots(m)
        if not _method_has_min_content(m):
            dropped_empty += 1
            logger.warning(
                "[word_llm] 跳过方法 '%s'：条件/过程/评定均为空",
                m.get("name"),
            )
            continue
        if m.get("content_slots", {}).get("missing"):
            incomplete += 1
        out.append(m)
    stats = {
        "methods_dropped_empty": dropped_empty,
        "methods_with_incomplete_slots": incomplete,
    }
    return out, stats


def _normalize_full_document_llm_methods(
    data: Dict[str, Any],
    full_text: str,
) -> List[Dict[str, Any]]:
    """将整文档 LLM 返回的 methods 规范化为内部方法结构（单批，允许部分字段）。"""
    methods_raw = data.get("methods")
    if not isinstance(methods_raw, list):
        return []

    out: List[Dict[str, Any]] = []
    seen_keys: set = set()

    for idx, item in enumerate(methods_raw, start=1):
        if not isinstance(item, dict):
            continue
        sub_name = str(item.get("name") or "").strip() or f"方法{idx}"
        project = str(item.get("test_project_name") or item.get("project_name") or sub_name).strip()
        full_name = _compose_method_name(project, sub_name)

        raw_secs = dict(item)
        if "syff" not in raw_secs and "sygc" in raw_secs:
            raw_secs["syff"] = raw_secs.get("sygc")
        secs = _normalize_method_sections(raw_secs)

        normalized = _attach_recall_alias({
            "candidate_id": item.get("method_id") or f"M{idx:03d}",
            "class": "试验大纲",
            "section_ids": [],
            "name": full_name,
            "method_short_name": _sanitize_name_part(sub_name),
            "type": str(item.get("type") or "试验方法").strip(),
            "description": str(item.get("description") or "").strip() or f"{project}：{sub_name}",
            "test_project_name": project,
            "recall_plan": _normalize_recall_plan(item.get("recall_plan")),
            **secs,
        })

        for key in ("sytj", "syff", "jgpd"):
            txt = str((normalized.get(key) or {}).get("text") or "").strip()
            if txt and not _text_found_in_source(txt, full_text):
                logger.warning(
                    "[word_llm] 整文档提取 '%s' 字段 %s 未在原文中找到，保留内容",
                    full_name, key,
                )

        if not _method_has_min_content(normalized):
            logger.warning(
                "[word_llm] 跳过方法 '%s'：条件/过程/评定均为空",
                full_name,
            )
            continue

        normalized = _attach_content_slots(normalized)

        key = re.sub(r"\s+", "", full_name)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(normalized)

    return out


def _normalize_organization_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in ("dispersed", "integrated", "single_method"):
        return mode
    if mode in ("scatter", "scattered", "per_project", "per_chapter"):
        return "dispersed"
    if mode in ("merge", "merged", "unified", "single", "one"):
        return "integrated"
    return ""


def _heuristic_organization_mode(
    parsed_doc: Dict[str, Any],
    full_text: str,
) -> str:
    """规则兜底：多个并列试验章且各有条件/过程/评定关键词 → 分散。"""
    sections = list(parsed_doc.get("sections") or [])
    triple_hits = 0
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title") or sec.get("heading") or "")
        body = str(sec.get("text") or "").strip()
        if full_text and len(body) < 80:
            body = _get_section_body_full(sec, sections, full_text)
        if not _is_trial_project_heading(title, text_len=len(body)):
            if not (re.search(r"\d+(?:\.\d+)+", title) and "试验" in title):
                continue
        has_cond = any(k in body for k in ("试验条件", "环境条件", "试验要求"))
        has_proc = any(k in body for k in ("试验过程", "试验方法", "试验步骤"))
        has_judge = any(k in body for k in ("结果评定", "结果判定"))
        if has_cond and has_proc and has_judge:
            triple_hits += 1
    if triple_hits >= 2:
        return "dispersed"
    if triple_hits == 1:
        return "single_method"
    return "integrated"


def llm_judge_method_organization(parsed_doc: Dict[str, Any]) -> Dict[str, Any]:
    """判断文档试验方法是分散、整合还是单一（复用统一规划，避免重复 LLM）。"""
    plan = parsed_doc.get("_llm_document_plan")
    if not isinstance(plan, dict) or not plan.get("organization_mode"):
        plan = llm_unified_document_plan(parsed_doc)
    data = dict(plan)
    mode = _normalize_organization_mode(data.get("organization_mode"))
    if not mode:
        full_text = _build_document_body_for_llm(parsed_doc)
        mode = _heuristic_organization_mode(parsed_doc, full_text)
        data.setdefault("warnings", []).append("LLM 未返回有效 organization_mode，已用规则兜底")
    data["organization_mode"] = mode
    try:
        data["confidence"] = float(data.get("confidence") or 0.5)
    except (TypeError, ValueError):
        data["confidence"] = 0.5
    logger.info(
        "[word_llm] 组织形态判断 mode=%s confidence=%s reason=%s",
        mode,
        data.get("confidence"),
        str(data.get("reason") or "")[:120],
    )
    try:
        debug_dir = str(parsed_doc.get("_document_dir") or "")
        if debug_dir:
            with open(os.path.join(debug_dir, "organization_judge.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return _organization_from_plan(data)


def _compute_section_span_end_offset_local(
    sec: dict,
    sections: List[dict],
    *,
    full_text_len: int = 0,
) -> int:
    """章节正文结束偏移（至同级下一节）。"""
    if sec.get("end_offset") is not None:
        try:
            end = int(sec["end_offset"])
            start = int(sec.get("start_offset") or 0)
            if end > start:
                return end
        except (TypeError, ValueError):
            pass
    sec_id = str(sec.get("section_id") or "")
    sec_level = int(sec.get("level") or 1)
    try:
        sec_off = int(sec.get("start_offset") or 0)
    except (TypeError, ValueError):
        sec_off = 0
    ordered: List[tuple] = []
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            continue
        try:
            off = int(s.get("start_offset") or 0)
        except (TypeError, ValueError):
            off = 0
        ordered.append((off, i, s))
    ordered.sort(key=lambda x: (x[0], x[1]))
    my_idx = next(
        (j for j, (_, _, s) in enumerate(ordered) if str(s.get("section_id") or "") == sec_id),
        None,
    )
    if my_idx is None:
        return full_text_len or 0
    for j in range(my_idx + 1, len(ordered)):
        off, _, other = ordered[j]
        if off > sec_off and int(other.get("level") or 1) <= sec_level:
            return off
    return full_text_len or 0


def _get_section_body_full(
    sec: dict,
    sections: List[dict],
    full_text: str,
) -> str:
    """取章节整段正文（含（1）（a）等子项，至同级下一节）。"""
    if not sec:
        return ""
    text = str(sec.get("text") or "").strip()
    if not full_text:
        return text
    start = sec.get("start_offset")
    if start is None:
        return text
    try:
        from blueprints.ZSGL.WJGL1.document_structure import _slice_body_between_offsets

        start_i = int(start)
        end_i = _compute_section_span_end_offset_local(
            sec, sections, full_text_len=len(full_text),
        )
        if end_i <= start_i:
            end_i = len(full_text)
        chunk = _slice_body_between_offsets(
            full_text, start_i, end_i, str(sec.get("title") or ""),
        )
        if not chunk:
            chunk = full_text[start_i:end_i].strip()
        if len(chunk) > len(text):
            return chunk
    except Exception as exc:
        logger.debug("[word_llm] section 整段读取失败: %s", exc)
    return text


def _prepare_stream_extraction_context(
    parsed_doc: Dict[str, Any],
    *,
    organization_mode: str = "auto",
    organization: Optional[Dict[str, Any]] = None,
    on_event: Optional[Any] = None,
) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], str]:
    """流式提取前置：统一规划组织形态与试验项目（先总后分）。"""
    parsed_doc = dict(parsed_doc or {})
    full_text = _build_document_body_for_llm(parsed_doc)

    plan = llm_unified_document_plan(parsed_doc)
    structure_profile = _structure_profile_from_plan(plan)
    org = organization or _organization_from_plan(plan)
    mode = _normalize_organization_mode(organization_mode)
    if not mode or organization_mode == "auto":
        mode = _normalize_organization_mode(org.get("organization_mode")) or "integrated"
    else:
        org = dict(org or {})
        org["organization_mode"] = mode

    if on_event:
        try:
            on_event("organization_judged", {
                "organization_mode": mode,
                "confidence": org.get("confidence"),
                "reason": org.get("reason"),
            })
        except Exception:
            pass

    projects: List[Dict[str, Any]] = []
    if mode == "dispersed":
        projects = _normalize_projects_from_plan(plan.get("projects"))
        if projects:
            logger.info("[word_llm] 统一规划发现 %d 个试验项目", len(projects))
        else:
            projects = discover_all_test_projects(
                full_text, parsed_doc, structure_profile=structure_profile,
            )
    return mode, org, projects, structure_profile, full_text


def iter_llm_extract_method_stream(
    parsed_doc: Dict[str, Any],
    document_dir: str,
    *,
    on_event: Optional[Any] = None,
    organization_mode: str = "auto",
    organization: Optional[Dict[str, Any]] = None,
    run_meta_out: Optional[Dict[str, Any]] = None,
    mode: Optional[str] = None,
    org: Optional[Dict[str, Any]] = None,
    projects: Optional[List[Dict[str, Any]]] = None,
    structure_profile: Optional[Dict[str, Any]] = None,
    full_text: Optional[str] = None,
) -> Iterator[Tuple[str, Dict[str, Any], str]]:
    """
    流式产出 LLM 试验方法：(method_key, method_dict, event)。
    event 为 new 或 update。完成后写入 run_meta_out（若提供）。
    """
    parsed_doc = dict(parsed_doc or {})
    parsed_doc["_document_dir"] = document_dir

    if not full_text:
        full_text = _build_document_body_for_llm(parsed_doc)
    if not full_text.strip():
        if run_meta_out is not None:
            run_meta_out.clear()
            run_meta_out.update({"error": "empty_document_body"})
        return

    if not mode or org is None:
        mode, org, projects, structure_profile, full_text = _prepare_stream_extraction_context(
            parsed_doc,
            organization_mode=organization_mode,
            organization=organization,
            on_event=on_event if org is None else None,
        )
    else:
        structure_profile = structure_profile or analyze_document_structure(parsed_doc)
        projects = list(projects or [])

    logger.info(
        "[word_llm] 流式提取 mode=%s confidence=%s",
        mode,
        (org or {}).get("confidence"),
    )

    methods_by_key: Dict[str, Dict[str, Any]] = {}
    project_names: List[str] = []
    run_meta: Dict[str, Any] = {
        "organization_mode": mode,
        "organization": org or {},
    }

    if mode == "dispersed":
        if not projects:
            logger.warning("[word_llm] 分散型但未发现试验项目，回退整合型整文档提取")
            methods, batch_meta = _llm_run_document_extract_with_prompts(
                parsed_doc,
                document_dir,
                full_prompt_template=EXTRACT_INTEGRATED_DOCUMENT_PROMPT,
                extract_mode_label="integrated_llm_fallback",
                organization=org,
                on_event=on_event,
                structure_profile=structure_profile,
            )
            run_meta.update(batch_meta or {})
            for idx, method in enumerate(methods or [], start=1):
                if on_event:
                    try:
                        on_event("method_extract_started", {
                            "method_name": method.get("name"),
                            "index": idx,
                            "total": len(methods),
                        })
                    except Exception:
                        pass
                key = re.sub(r"\s+", "", str(method.get("name") or "")) or f"m_{idx}"
                methods_by_key[key] = method
                yield key, method, "new"
        else:
            run_meta["extract_mode"] = "dispersed_per_project"
            for proj_idx, proj in enumerate(projects, start=1):
                project_name = str(proj.get("project_name") or "").strip()
                project_names.append(project_name)
                if on_event:
                    try:
                        on_event("project_discovered", {
                            "project_name": project_name,
                            "index": proj_idx,
                            "total": len(projects),
                        })
                        on_event("project_extract_started", {
                            "project_index": proj_idx,
                            "project_total": len(projects),
                            "project_name": project_name,
                        })
                    except Exception:
                        pass
                for event, method_key, method in iter_extract_methods_for_project(
                    full_text,
                    proj,
                    parsed_doc,
                    structure_profile=structure_profile,
                    document_dir=document_dir,
                    on_event=on_event,
                ):
                    if method_key in methods_by_key:
                        methods_by_key[method_key] = _merge_method_partial(
                            methods_by_key[method_key], method,
                        )
                        method = methods_by_key[method_key]
                    else:
                        methods_by_key[method_key] = method
                    yield method_key, method, event
            run_meta["project_count"] = len(projects)
            run_meta["project_names"] = project_names
    elif mode == "single_method":
        methods, batch_meta = _llm_run_document_extract_with_prompts(
            parsed_doc,
            document_dir,
            full_prompt_template=EXTRACT_SINGLE_METHOD_DOCUMENT_PROMPT,
            extract_mode_label="single_method_llm",
            organization=org,
            on_event=on_event,
            structure_profile=structure_profile,
        )
        run_meta.update(batch_meta or {})
        for idx, method in enumerate(methods or [], start=1):
            if on_event:
                try:
                    on_event("method_extract_started", {
                        "method_name": method.get("name"),
                        "index": idx,
                        "total": len(methods),
                    })
                except Exception:
                    pass
            key = re.sub(r"\s+", "", str(method.get("name") or "")) or f"m_{idx}"
            methods_by_key[key] = method
            yield key, method, "new"
    else:
        methods, batch_meta = _llm_run_document_extract_with_prompts(
            parsed_doc,
            document_dir,
            full_prompt_template=EXTRACT_INTEGRATED_DOCUMENT_PROMPT,
            extract_mode_label="integrated_llm",
            organization=org,
            on_event=on_event,
            structure_profile=structure_profile,
        )
        run_meta.update(batch_meta or {})
        for idx, method in enumerate(methods or [], start=1):
            if on_event:
                try:
                    on_event("method_extract_started", {
                        "method_name": method.get("name"),
                        "index": idx,
                        "total": len(methods),
                    })
                except Exception:
                    pass
            key = re.sub(r"\s+", "", str(method.get("name") or "")) or f"m_{idx}"
            methods_by_key[key] = method
            yield key, method, "new"

    methods_list = list(methods_by_key.values())
    run_meta["methods_valid_count"] = len(methods_list)
    run_meta["document_chars"] = len(full_text)
    if not run_meta.get("extract_mode"):
        run_meta["extract_mode"] = f"{mode}_llm"

    logger.info(
        "[word_llm] 流式提取完成 methods=%d mode=%s",
        len(methods_list),
        mode,
    )
    try:
        with open(os.path.join(document_dir, "llm_run_meta.json"), "w", encoding="utf-8") as f:
            json.dump(run_meta, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    proj_for_save = projects if mode == "dispersed" and projects else _projects_from_methods(methods_list)
    _save_llm_extract_output(document_dir, methods_list, proj_for_save, run_meta)

    if run_meta_out is not None:
        run_meta_out.clear()
        run_meta_out.update(run_meta)


def _llm_extract_methods_dispersed(
    parsed_doc: Dict[str, Any],
    document_dir: str,
    *,
    organization: Optional[Dict[str, Any]] = None,
    on_event: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """分散型：发现各试验项目后逐项目提取方法（阻塞式收集）。"""
    run_meta: Dict[str, Any] = {}
    methods_by_key: Dict[str, Dict[str, Any]] = {}
    for method_key, method, _event in iter_llm_extract_method_stream(
        parsed_doc,
        document_dir,
        organization=organization,
        organization_mode="dispersed",
        on_event=on_event,
        run_meta_out=run_meta,
    ):
        methods_by_key[method_key] = method
    methods = list(methods_by_key.values())
    return methods, run_meta


def _llm_run_document_extract_with_prompts(
    parsed_doc: Dict[str, Any],
    document_dir: str,
    *,
    full_prompt_template: str,
    extract_mode_label: str,
    organization: Optional[Dict[str, Any]] = None,
    on_event: Optional[Any] = None,
    structure_profile: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按指定提示词对整文档（或分片）做 LLM 提取。"""
    full_text = _build_document_body_for_llm(parsed_doc)
    sections = list(parsed_doc.get("sections") or [])
    chunks = _split_body_into_llm_chunks(full_text, sections=sections)
    heading_outline = _build_heading_outline(parsed_doc, limit=120)
    file_name = str(parsed_doc.get("file_name") or "")
    profile = structure_profile or analyze_document_structure(parsed_doc)
    profile_json = _structure_profile_json(profile)

    if len(chunks) <= 1:
        body_for_llm = chunks[0] if chunks else full_text
        truncated = len(body_for_llm) < len(full_text)
        truncated_note = (
            "\n\n【说明】上文因模型上下文限制已对中间部分省略，请结合首尾与目录理解全文。"
            if truncated
            else ""
        )
        prompt = full_prompt_template.format(
            file_name=file_name,
            heading_outline=heading_outline[:4000] or "（无）",
            document_body=body_for_llm,
            truncated_note=truncated_note,
            structure_profile_json=profile_json,
        )
        raw_response = _call_llm(prompt, temperature=0.1)
        data = _safe_json_loads(raw_response, {})
        if not isinstance(data, dict):
            data = {}
        methods, slot_stats = _finalize_methods_batch(
            _normalize_full_document_llm_methods(data, full_text),
            full_text,
            profile,
        )
        run_meta = {
            "extract_mode": extract_mode_label,
            "organization_mode": (organization or {}).get("organization_mode"),
            "organization": organization or {},
            "prompt_chars": len(prompt),
            "document_chars": len(full_text),
            "body_chars_sent": len(body_for_llm),
            "truncated": truncated,
            "chunk_count": 1,
            "raw_response_chars": len(raw_response or ""),
            "document_summary": str(data.get("document_summary") or "").strip(),
            "methods_raw_count": len(data.get("methods") or []) if isinstance(data.get("methods"), list) else 0,
            "methods_valid_count": len(methods),
            "llm_context_tokens": FIXED_LLM_CONTEXT_TOKENS,
            "chunk_text_budget": _llm_chunk_text_char_budget(),
            **slot_stats,
        }
        save_llm_run_debug(
            document_dir,
            prompt=prompt,
            raw_response=raw_response,
            parsed_json=data,
            document_body=body_for_llm,
            meta=run_meta,
        )
        return methods, run_meta

    merged_by_key: Dict[str, Dict[str, Any]] = {}
    chunk_details: List[Dict[str, Any]] = []
    summaries: List[str] = []
    total_raw = 0

    for idx, chunk in enumerate(chunks, start=1):
        if on_event:
            try:
                on_event("llm_extract_chunk_started", {
                    "chunk_index": idx,
                    "chunk_total": len(chunks),
                    "chunk_chars": len(chunk),
                })
            except Exception:
                pass
        prompt = EXTRACT_ALL_METHODS_CHUNK_PROMPT.format(
            structure_profile_json=profile_json,
            file_name=file_name,
            heading_outline=heading_outline[:4000] or "（无）",
            chunk_index=idx,
            chunk_total=len(chunks),
            chunk_chars=len(chunk),
            chunk_text=chunk,
        )
        raw_response = _call_llm(prompt, temperature=0.1)
        data = _safe_json_loads(raw_response, {})
        if not isinstance(data, dict):
            data = {}
        batch = _normalize_full_document_llm_methods(data, full_text)
        total_raw += len(data.get("methods") or []) if isinstance(data.get("methods"), list) else 0
        summary = str(data.get("document_summary") or "").strip()
        if summary:
            summaries.append(summary)
        for method in batch:
            key = re.sub(r"\s+", "", str(method.get("name") or "")) or f"m_{len(merged_by_key)+1}"
            if key in merged_by_key:
                merged_by_key[key] = _merge_method_partial(merged_by_key[key], method)
            else:
                merged_by_key[key] = method
        chunk_details.append({
            "chunk_index": idx,
            "chunk_chars": len(chunk),
            "prompt_chars": len(prompt),
            "methods_valid_count": len(batch),
        })

    methods, slot_stats = _finalize_methods_batch(
        list(merged_by_key.values()),
        full_text,
        profile,
    )
    body_for_llm = "\n\n---\n\n".join(
        f"=== 切片 {i}/{len(chunks)} ===\n{c}" for i, c in enumerate(chunks, start=1)
    )
    run_meta = {
        "extract_mode": f"{extract_mode_label}_chunked",
        "organization_mode": (organization or {}).get("organization_mode"),
        "organization": organization or {},
        "document_chars": len(full_text),
        "chunk_count": len(chunks),
        "chunk_details": chunk_details,
        "methods_raw_count": total_raw,
        "methods_valid_count": len(methods),
        "document_summary": "；".join(summaries[:3]),
        "llm_context_tokens": FIXED_LLM_CONTEXT_TOKENS,
        **slot_stats,
    }
    save_llm_run_debug(
        document_dir,
        prompt=f"[{extract_mode_label} 分片共 {len(chunks)} 批]",
        raw_response=json.dumps(chunk_details, ensure_ascii=False, indent=2),
        parsed_json={"methods": methods},
        document_body=body_for_llm,
        meta=run_meta,
    )
    return methods, run_meta


def llm_extract_all_methods_from_document(
    parsed_doc: Dict[str, Any],
    document_dir: str,
    *,
    on_event: Optional[Any] = None,
    organization_mode: str = "auto",
    organization: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    LLM 提取试验方法：先判断分散/整合/单一，再按形态抽取（阻塞式收集全部结果）。
    """
    run_meta: Dict[str, Any] = {}
    methods_by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for method_key, method, _event in iter_llm_extract_method_stream(
        parsed_doc,
        document_dir,
        on_event=on_event,
        organization_mode=organization_mode,
        organization=organization,
        run_meta_out=run_meta,
    ):
        if method_key not in methods_by_key:
            order.append(method_key)
        methods_by_key[method_key] = method
    methods = [methods_by_key[k] for k in order]

    if not methods:
        logger.warning(
            "[word_llm] LLM 未得到有效方法 mode=%s raw=%s",
            run_meta.get("organization_mode"),
            run_meta.get("methods_raw_count"),
        )
    else:
        logger.info(
            "[word_llm] LLM 提取完成 methods=%d mode=%s extract=%s",
            len(methods),
            run_meta.get("organization_mode"),
            run_meta.get("extract_mode"),
        )

    return methods, run_meta


def _projects_from_methods(methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从已提取方法反推试验项目列表（仅用于 checkpoint 展示）。"""
    merged: Dict[str, Dict[str, Any]] = {}
    for m in methods or []:
        name = str(m.get("test_project_name") or "").strip()
        if not name:
            continue
        key = re.sub(r"\s+", "", name)
        if key not in merged:
            merged[key] = {
                "project_id": f"P{len(merged)+1:03d}",
                "project_name": name,
                "section_hint": name,
                "sub_method_titles": [],
            }
        short = str(m.get("method_short_name") or "").strip()
        if short:
            merged[key]["sub_method_titles"].append(short)
    for p in merged.values():
        p["sub_method_titles"] = _dedupe_str(p.get("sub_method_titles"))
    return list(merged.values())


def discover_test_projects(
    full_text: str,
    *,
    structure_profile: Optional[Dict[str, Any]] = None,
    heading_outline: str = "",
) -> List[Dict[str, Any]]:
    clean_text = sanitize_text_for_llm(full_text)
    chunks = split_text_at_section_boundaries(
        clean_text,
        FIXED_CHUNK_TEXT_CHARS,
        overlap_chars=FIXED_CHUNK_OVERLAP_CHARS,
    )
    merged: Dict[str, Dict[str, Any]] = {}
    profile_json = _structure_profile_json(structure_profile)
    outline = heading_outline or "（无）"

    for idx, chunk in enumerate(chunks, start=1):
        prompt = DISCOVER_PROJECTS_PROMPT.format(
            structure_profile_json=profile_json,
            heading_outline=outline[:3000],
            chunk_index=idx,
            chunk_total=len(chunks),
            chunk_chars=len(chunk),
            chunk_text=chunk,
        )
        logger.info("[word_llm] 发现试验项目 切片 %d/%d, prompt=%d 字", idx, len(chunks), len(prompt))
        data = _call_llm_json(prompt)
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            continue
        for p in projects:
            if not isinstance(p, dict):
                continue
            name = str(p.get("project_name", "") or "").strip()
            if not name:
                continue
            key = re.sub(r"\s+", "", name)
            if key not in merged:
                merged[key] = {
                    "project_id": p.get("project_id") or f"P{len(merged)+1:03d}",
                    "project_name": name,
                    "section_hint": str(p.get("section_hint", "") or "").strip(),
                    "sub_method_titles": _dedupe_str(p.get("sub_method_titles", [])),
                }
            else:
                exist = merged[key]
                exist["sub_method_titles"] = _dedupe_str(
                    exist.get("sub_method_titles", []) + (p.get("sub_method_titles") or [])
                )
                if not exist.get("section_hint") and p.get("section_hint"):
                    exist["section_hint"] = str(p["section_hint"]).strip()

    return list(merged.values())


_FIVE_SECTION_LABELS = frozenset(METHOD_SECTION_LABELS.values())


def _parse_heading_title(raw: str) -> Tuple[str, str]:
    """从章节标题解析 (section_no, clean_title)。"""
    title = str(raw or "").strip()
    if not title:
        return "", ""
    m = re.match(r"^(\d+(?:\.\d+)+)\s+(.+)$", title)
    if m:
        return m.group(1), m.group(2).strip()
    m = re.match(r"^第[一二三四五六七八九十百零0-9]+[章节部分篇]\s*(.*)$", title)
    if m:
        return "", (m.group(1) or title).strip()
    return "", title


def _is_trial_project_heading(title: str, *, text_len: int = 0) -> bool:
    clean = _parse_heading_title(title)[1] or title
    if not clean or clean in _FIVE_SECTION_LABELS or clean in ("正文", "前言", "目录"):
        return False
    if "试验" not in clean:
        return False
    if len(clean) < 4:
        return False
    if re.match(r"^试验(目的|条件|方法|数据处理|结果评定)$", clean):
        return False
    return text_len >= 40 or len(clean) >= 6


def _discover_projects_from_sections(parsed_doc: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 parser 章节切片识别试验方案体例的试验项目（无「试验项目一：」前缀）。"""
    if not isinstance(parsed_doc, dict):
        return []
    projects: List[Dict[str, Any]] = []
    seen: set = set()
    sections = list(parsed_doc.get("sections") or [])
    if not sections:
        for item in parsed_doc.get("section_catalogue") or []:
            if isinstance(item, dict):
                sections.append(item)
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        raw_title = str(sec.get("title") or sec.get("heading") or "").strip()
        level = int(sec.get("level") or 0)
        text = str(sec.get("text") or "").strip()
        if not _is_trial_project_heading(raw_title, text_len=len(text)):
            continue
        if level > 0 and level < 2 and len(text) < 120:
            continue
        _sno, project_name = _parse_heading_title(raw_title)
        project_name = project_name or raw_title
        key = re.sub(r"\s+", "", project_name)
        if key in seen:
            continue
        seen.add(key)
        projects.append({
            "project_id": f"P{len(projects) + 1:03d}",
            "project_name": project_name,
            "section_hint": raw_title[:120],
            "sub_method_titles": [],
            "_section_id": str(sec.get("section_id") or "").strip(),
        })
    return projects


def _discover_projects_from_word_headings(
    parsed_doc: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """试验方案 Word：用 word_native 合并标题识别试验项目（split_standard_sections 常漏检）。"""
    if not isinstance(parsed_doc, dict):
        return []
    file_path = str(parsed_doc.get("file_path") or "").strip()
    ext = str(parsed_doc.get("file_ext") or "").lower()
    if ext not in (".docx", ".doc") or not file_path or not os.path.isfile(file_path):
        return []
    try:
        from blueprints.ZSGL.WJGL1.word_native_parse import extract_word_headings_merged
        from blueprints.ZSGL.WJGL1.document_heading_extract import flatten_toc_to_headings

        toc, extract_method = extract_word_headings_merged(file_path)
        flat = flatten_toc_to_headings(toc, source="word_llm")
    except Exception as exc:
        logger.warning("[word_llm] Word 标题识别失败: %s", exc)
        return []

    projects: List[Dict[str, Any]] = []
    seen: set = set()
    for h in flat:
        if not isinstance(h, dict):
            continue
        raw_title = str(h.get("title") or "").strip()
        if not _is_trial_project_heading(raw_title):
            continue
        _sno, project_name = _parse_heading_title(raw_title)
        project_name = project_name or raw_title
        key = re.sub(r"\s+", "", project_name)
        if key in seen:
            continue
        seen.add(key)
        projects.append({
            "project_id": f"P{len(projects) + 1:03d}",
            "project_name": project_name,
            "section_hint": raw_title[:120],
            "sub_method_titles": [],
            "_section_id": str(h.get("section_id") or "").strip(),
        })
    if projects:
        logger.info(
            "[word_llm] Word 标题识别发现 %d 个试验项目 (extract_method=%s)",
            len(projects),
            extract_method,
        )
    return projects


def discover_all_test_projects(
    full_text: str,
    parsed_doc: Optional[Dict[str, Any]] = None,
    *,
    structure_profile: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """LLM 发现 → 章节切片 → Word 标题 → 规则兜底。"""
    heading_outline = _build_heading_outline(parsed_doc or {}) if parsed_doc else ""
    by_llm = discover_test_projects(
        full_text,
        structure_profile=structure_profile,
        heading_outline=heading_outline,
    )
    if by_llm:
        logger.info("[word_llm] LLM 发现 %d 个试验项目", len(by_llm))
        return by_llm
    from_sections = _discover_projects_from_sections(parsed_doc)
    if from_sections:
        logger.info("[word_llm] 章节切片发现 %d 个试验项目", len(from_sections))
        return from_sections
    from_word = _discover_projects_from_word_headings(parsed_doc)
    if from_word:
        return from_word
    from_rules = _rule_fallback_projects(full_text)
    if from_rules:
        logger.info("[word_llm] 规则兜底发现 %d 个试验项目", len(from_rules))
    else:
        logger.warning(
            "[word_llm] 未发现任何试验项目（LLM/章节/规则均未命中），请检查 OPENAI 配置或文档结构"
        )
    return from_rules


def _resolve_project_sub_method_titles(
    project: Dict[str, Any],
    rule_sections: Dict[str, Any],
) -> List[str]:
    """合并项目 hint 与规则解析出的子方法标题，去重保序；过滤过程性步骤标题。"""
    titles = _filter_sub_method_titles(project.get("sub_method_titles") or [])
    if titles:
        return titles
    syff_text = str((rule_sections.get("syff") or {}).get("text") or "")
    rule_subs = _parse_sub_methods_rule(syff_text)
    if rule_subs:
        return _filter_sub_method_titles([
            str(s.get("name") or "").strip() for s in rule_subs if s.get("name")
        ])
    return []


def _consume_llm_methods_for_project(
    methods_raw: List[Any],
    *,
    project_name: str,
    section_hint: str = "",
    rule_sections: Dict[str, Any],
    block: str,
    chunk: str,
    full_text: str,
    methods_by_name: Dict[str, Dict[str, Any]],
    emitted_keys: set,
) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    """将 LLM methods 数组规范化为 (event, method_key, method_dict) 流。"""
    for item in methods_raw:
        if not isinstance(item, dict):
            continue
        sub_name = str(item.get("name", "") or "").strip()
        if not sub_name:
            sub_name = "整体试验"
        full_name = _compose_method_name(project_name, sub_name)
        secs = _normalize_method_sections(item)
        normalized = _attach_recall_alias({
            "candidate_id": item.get("method_id") or f"M{len(methods_by_name)+1:03d}",
            "class": "试验大纲",
            "section_ids": [],
            "name": full_name,
            "method_short_name": _sanitize_name_part(sub_name),
            "type": str(item.get("type", "") or "试验方法").strip(),
            "description": str(item.get("description", "") or "").strip()
            or f"{project_name}：{sub_name}",
            "test_project_name": project_name,
            "section_hint": str(section_hint or "").strip(),
            "recall_plan": _normalize_recall_plan(item.get("recall_plan")),
            **secs,
        })
        normalized = _fill_method_sections_from_project(
            normalized,
            rule_sections,
            syff_text=str(secs["syff"].get("text", "") or ""),
        )
        normalized = _apply_mandatory_sections_from_rule(
            normalized, rule_sections, block, chunk, full_text,
        )
        normalized = _rebalance_llm_method_sections(
            normalized, rule_sections, block_text=block, full_text=full_text,
        )
        normalized = _attach_content_slots(normalized)
        if not _method_has_min_content(normalized):
            logger.warning(
                "[word_llm] 跳过方法 '%s'：条件/过程/评定均为空",
                full_name,
            )
            continue
        key = re.sub(r"\s+", "", full_name)
        if key in methods_by_name:
            methods_by_name[key] = _merge_method_partial(methods_by_name[key], normalized)
            yield ("update", key, methods_by_name[key])
        else:
            methods_by_name[key] = normalized
            event = "update" if key in emitted_keys else "new"
            emitted_keys.add(key)
            yield (event, key, normalized)


def iter_extract_methods_for_project(
    full_text: str,
    project: Dict[str, Any],
    parsed_doc: Optional[Dict[str, Any]] = None,
    *,
    structure_profile: Optional[Dict[str, Any]] = None,
    document_dir: str = "",
    on_event: Optional[Any] = None,
) -> Iterator[Tuple[str, str, Dict[str, Any]]]:
    """
    逐条产出试验方法：(event, method_key, method_dict)。
    event 为 new（首次）或 update（切片合并更新同一方法）。
    已知子方法标题时按子方法逐条调用 LLM，产出后立即 yield。
    """
    project_name = str(project.get("project_name", "") or "").strip()
    section_hint = str(project.get("section_hint", "") or "").strip()
    sub_titles_hint = "、".join(_dedupe_str(project.get("sub_method_titles") or [])) or "（无）"
    block = ""
    section_id = str(project.get("_section_id") or "").strip()
    sections_list = list((parsed_doc or {}).get("sections") or []) if isinstance(parsed_doc, dict) else []
    full_body_text = _build_document_body_for_llm(parsed_doc) if isinstance(parsed_doc, dict) else full_text
    if section_id and isinstance(parsed_doc, dict):
        for sec in sections_list:
            if isinstance(sec, dict) and str(sec.get("section_id") or "") == section_id:
                block = _get_section_body_full(sec, sections_list, full_body_text)
                break
    if not block:
        block = extract_project_section_text(full_text, project_name, section_hint)
    hint = str(project.get("section_hint") or section_hint or "").strip()
    if hint and re.match(r"^\d", hint):
        by_hint = slice_project_block_by_section_hint(full_body_text, hint)
        if len(by_hint) > len(block or ""):
            block = by_hint
    rule_sections = _parse_project_sections_rule(block, structure_profile)
    chunks = fixed_split_text_chunks(block)
    if not chunks:
        chunks = fixed_split_text_chunks(full_text)

    methods_by_name: Dict[str, Dict[str, Any]] = {}
    emitted_keys: set = set()
    profile_json = _structure_profile_json(structure_profile)
    sub_title_list = _resolve_project_sub_method_titles(project, rule_sections)
    raw_hints = _dedupe_str(project.get("sub_method_titles") or [])
    if raw_hints and not sub_title_list:
        logger.info(
            "[word_llm] 项目 %s 子方法标题均为过程步骤，改为整项目抽取（原 hint: %s）",
            project_name,
            "、".join(raw_hints[:6]),
        )

    if sub_title_list:
        chunk_text = block or (chunks[0] if chunks else full_text)
        for sub_idx, sub_name in enumerate(sub_title_list, start=1):
            if on_event:
                try:
                    on_event("method_extract_started", {
                        "project_name": project_name,
                        "sub_method_name": sub_name,
                        "index": sub_idx,
                        "total": len(sub_title_list),
                    })
                except Exception:
                    pass
            prompt = EXTRACT_ONE_SUBMETHOD_PROMPT.format(
                structure_profile_json=profile_json,
                project_name=project_name,
                section_hint=section_hint or project_name,
                sub_method_name=sub_name,
                chunk_text=chunk_text,
            )
            logger.info(
                "[word_llm] 逐条抽取 %s / %s (%d/%d), prompt=%d 字",
                project_name,
                sub_name,
                sub_idx,
                len(sub_title_list),
                len(prompt),
            )
            data = _call_llm_json(prompt)
            methods = data.get("methods", [])
            if not isinstance(methods, list):
                methods = []
            chunk_normalized: List[Dict[str, Any]] = []
            for event, key, normalized in _consume_llm_methods_for_project(
                methods,
                project_name=project_name,
                section_hint=hint or section_hint,
                rule_sections=rule_sections,
                block=block,
                chunk=chunk_text,
                full_text=full_text,
                methods_by_name=methods_by_name,
                emitted_keys=emitted_keys,
            ):
                chunk_normalized.append({
                    "name": normalized.get("name"),
                    "llm_raw": next(
                        (m for m in methods if isinstance(m, dict)),
                        {},
                    ),
                    "sections": {k: normalized.get(k) for k in METHOD_SECTION_KEYS},
                    "content_slots": normalized.get("content_slots"),
                })
                yield event, key, normalized
            if document_dir:
                _save_project_llm_chunk_debug(
                    document_dir,
                    project_name,
                    sub_idx,
                    prompt=prompt,
                    raw_data=data,
                    normalized_methods=chunk_normalized,
                )
    else:
        for idx, chunk in enumerate(chunks, start=1):
            if on_event:
                try:
                    on_event("method_extract_started", {
                        "project_name": project_name,
                        "sub_method_name": "",
                        "chunk_index": idx,
                        "chunk_total": len(chunks),
                    })
                except Exception:
                    pass
            prompt = EXTRACT_PROJECT_METHODS_PROMPT.format(
                structure_profile_json=profile_json,
                project_name=project_name,
                section_hint=section_hint or project_name,
                sub_method_titles=sub_titles_hint,
                chunk_index=idx,
                chunk_total=len(chunks),
                chunk_text=chunk,
            )
            logger.info(
                "[word_llm] 抽取方法 %s 切片 %d/%d, prompt=%d 字",
                project_name,
                idx,
                len(chunks),
                len(prompt),
            )
            data = _call_llm_json(prompt)
            methods = data.get("methods", [])
            if not isinstance(methods, list):
                continue
            chunk_normalized: List[Dict[str, Any]] = []
            for event, key, normalized in _consume_llm_methods_for_project(
                methods,
                project_name=project_name,
                section_hint=hint or section_hint,
                rule_sections=rule_sections,
                block=block,
                chunk=chunk,
                full_text=full_text,
                methods_by_name=methods_by_name,
                emitted_keys=emitted_keys,
            ):
                chunk_normalized.append({
                    "name": normalized.get("name"),
                    "llm_raw": {},
                    "sections": {k: normalized.get(k) for k in METHOD_SECTION_KEYS},
                    "content_slots": normalized.get("content_slots"),
                })
                yield event, key, normalized

            if document_dir:
                _save_project_llm_chunk_debug(
                    document_dir,
                    project_name,
                    idx,
                    prompt=prompt,
                    raw_data=data,
                    normalized_methods=chunk_normalized,
                )

    if not methods_by_name:
        logger.info("[word_llm] 项目 %s LLM 无结果，使用规则五段解析", project_name)
        for m in _build_methods_from_rule(block, project_name):
            m = _attach_content_slots(m)
            if not _method_has_min_content(m):
                logger.warning(
                    "[word_llm] 规则解析跳过 '%s'：条件/过程/评定均为空",
                    m.get("name"),
                )
                continue
            key = re.sub(r"\s+", "", str(m.get("name", "") or "")) or f"rule_{id(m)}"
            yield ("new", key, m)


def extract_methods_for_project(
    full_text: str,
    project: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """兼容旧接口：收集 iter_extract_methods_for_project 的最终结果（同 key 取最后版本）。"""
    by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for event, key, method in iter_extract_methods_for_project(full_text, project):
        if key not in by_key:
            order.append(key)
        by_key[key] = method
    return [by_key[k] for k in order]


def finalize_method_for_save(
    method: Dict[str, Any],
    method_no: int,
    parsed_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """单条方法：命名、编号、生成 SYNR。"""
    m = dict(method)
    project = str(m.get("test_project_name") or "").strip()
    short_name = str(m.get("method_short_name") or "").strip()
    if not short_name:
        base_name = _strip_method_name_prefix(str(m.get("name", "") or ""))
        if project and base_name.startswith(f"{project}_"):
            short_name = base_name[len(project) + 1 :].strip()
        elif project and base_name.startswith(f"{project}-"):
            short_name = base_name[len(project) + 1 :].strip()
        else:
            short_name = base_name or "整体试验"
    composed = _compose_method_name(project, short_name) if project else _sanitize_name_part(short_name)
    m["method_short_name"] = _sanitize_name_part(short_name) or "整体试验"
    m["method_no"] = method_no
    m["name"] = f"{method_no} {composed}" if composed else f"{method_no} 未命名方法"
    synr = build_method_synr_from_native_html(m, parsed_doc) if parsed_doc else build_method_synr(m)
    try:
        from blueprints.FLGL.method_section_enrich import polish_method_synr_for_editor
    except ImportError:
        from method_section_enrich import polish_method_synr_for_editor
    synr = polish_method_synr_for_editor(synr, force_restructure=True)
    m["synr"] = synr
    m["SYNR"] = synr
    return _attach_recall_alias(m)


def _apply_outline_document_trace(
    method_item: Dict[str, Any],
    parsed_doc: Dict[str, Any],
) -> Dict[str, Any]:
    """LLM 大纲方法：对 SYNR 做全文级见/按/照/见表溯源。"""
    out = dict(method_item)
    synr = str(out.get("SYNR") or out.get("synr") or "").strip()
    if not synr:
        return out
    try:
        from blueprints.FLGL.method_section_enrich import apply_document_trace_to_html
    except ImportError:
        from method_section_enrich import apply_document_trace_to_html
    full_text = str(parsed_doc.get("full_text") or "")
    all_tables = list(parsed_doc.get("all_tables") or [])
    traced = apply_document_trace_to_html(
        synr,
        full_text=full_text,
        all_tables=all_tables,
    )
    try:
        from blueprints.FLGL.method_section_enrich import format_latex_in_html_content
    except ImportError:
        from method_section_enrich import format_latex_in_html_content
    traced = format_latex_in_html_content(traced)
    out["synr"] = traced
    out["SYNR"] = traced
    out["trace_applied"] = True
    return out


def finalize_methods_for_save(methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量 finalize（兼容旧调用）。"""
    return [finalize_method_for_save(m, idx) for idx, m in enumerate(methods or [], start=1)]


def _build_method_candidates_from_methods(methods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "candidate_id": m.get("candidate_id") or f"M{i:03d}",
            "candidate_title": m.get("name"),
            "candidate_type_hint": m.get("type", ""),
            "summary_hint": m.get("description", ""),
            "section_ids": m.get("section_ids", []),
            "reason": f"试验项目: {m.get('test_project_name', '')}",
        }
        for i, m in enumerate(methods or [], start=1)
    ]


def _build_extraction_data_skeleton(
    parsed_doc: Dict[str, Any],
    projects: List[Dict[str, Any]],
    *,
    status: str = "in_progress",
    structure_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    doc_meta: Dict[str, Any] = {
        "class": "试验大纲",
        "status": status,
        "summary": f"Word/大纲 LLM 抽取进行中，已识别 {len(projects)} 个试验项目",
    }
    if structure_profile:
        doc_meta["structure_profile"] = structure_profile
    return {
        "source_file": {
            "file_name": parsed_doc["file_name"],
            "file_path": parsed_doc["file_path"],
            "file_ext": parsed_doc["file_ext"],
            "parse_mode": parsed_doc.get("parse_mode"),
            "ocr_used": bool(parsed_doc.get("ocr_used")),
        },
        "document_meta": doc_meta,
        "chunk_config": {
            "llm_context_tokens": FIXED_LLM_CONTEXT_TOKENS,
            "chunk_text_chars": FIXED_CHUNK_TEXT_CHARS,
            "chunk_overlap_chars": FIXED_CHUNK_OVERLAP_CHARS,
            "method_section_keys": list(METHOD_SECTION_KEYS),
            "method_section_labels": METHOD_SECTION_LABELS,
        },
        "test_projects": projects,
        "terms": [],
        "method_candidates": [],
        "methods": [],
    }


def _equipment_candidates_from_graph_matches(
    graph_matches: Dict[str, Any],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for eq in graph_matches.get("equipment") or []:
        if not isinstance(eq, dict):
            continue
        eid = str(eq.get("id") or eq.get("Id") or "").strip()
        if not eid:
            continue
        out.append({
            "node_id": eid,
            "node_name": str(eq.get("name") or eq.get("Name") or eid).strip(),
            "score": round(min(1.0, float(eq.get("score") or 0.5)), 3),
            "llm_reason": eq.get("llm_reason", ""),
        })
    return out


def _equipment_only_relations_from_matches(
    graph_matches: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """仅装备/环境推荐行，不含项目/指标（二步推荐第一阶段）。"""
    recommended_nodes: List[Dict[str, Any]] = []
    recommended_relations: List[Dict[str, Any]] = []
    for env in graph_matches.get("environment") or []:
        if not isinstance(env, dict):
            continue
        eid = str(env.get("id") or "").strip()
        if not eid:
            continue
        recommended_relations.append({
            "target_type": "Environment",
            "target_id": eid,
            "node_name": str(env.get("name") or eid),
            "rel_type": "CON",
            "rate": round(min(1.0, float(env.get("score") or 0.5)), 3),
        })
    for eq in graph_matches.get("equipment") or []:
        if not isinstance(eq, dict):
            continue
        eid = str(eq.get("id") or "").strip()
        ename = str(eq.get("name") or eid).strip()
        escore = float(eq.get("score") or 0.5)
        if not eid:
            continue
        recommended_nodes.append({
            "node_type": "Equipment",
            "node_id": eid,
            "node_name": ename,
            "score": escore,
            "llm_reason": eq.get("llm_reason", ""),
        })
        recommended_relations.append({
            "target_type": "Equipment",
            "target_id": eid,
            "node_name": ename,
            "rel_type": "CON",
            "rate": round(min(1.0, escore), 3),
            "equipment_id": eid,
        })
    return recommended_nodes, recommended_relations


def build_scoped_recommendations_for_equipment_ids(
    graph_matches: Dict[str, Any],
    equipment_ids: List[str],
    *,
    neo4j_driver=None,
    per_equipment_top: int = 3,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """仅对选定装备填充项目/指标 scoped 推荐（二步推荐第二阶段）。"""
    ids_set = {str(x).strip() for x in (equipment_ids or []) if str(x).strip()}
    if not ids_set:
        return [], [], []
    filtered = dict(graph_matches) if isinstance(graph_matches, dict) else {}
    filtered["equipment"] = [
        e for e in (graph_matches.get("equipment") or [])
        if isinstance(e, dict) and str(e.get("id") or "").strip() in ids_set
    ]
    return build_scoped_recommendations_from_matches(
        filtered,
        neo4j_driver=neo4j_driver,
        per_equipment_top=per_equipment_top,
    )


def recall_method_scoped_for_equipment(
    method: Dict[str, Any],
    equipment_ids: List[str],
    *,
    neo4j_driver=None,
    per_equipment_top: int = 3,
) -> Dict[str, Any]:
    """对单条方法按用户选定装备做 scoped 召回，更新推荐字段。"""
    out = dict(method)
    graph_matches = out.get("graph_matches") or out.get("graph_matches_embedding") or _empty_graph_matches()
    rec_nodes, rec_rels, eq_recs = build_scoped_recommendations_for_equipment_ids(
        graph_matches,
        equipment_ids,
        neo4j_driver=neo4j_driver,
        per_equipment_top=per_equipment_top,
    )
    out["recommended_nodes"] = rec_nodes
    out["recommended_relations"] = rec_rels
    out["equipment_recommendations"] = eq_recs
    out["selected_equipment_ids"] = list(equipment_ids)
    out["recommendation_source"] = "scoped"
    return out


def _enrich_single_method_with_recall(
    method: Dict[str, Any],
    topk_config: Optional[Dict[str, int]] = None,
    *,
    recall_phase: str = "full",
) -> Dict[str, Any]:
    """单条方法 embedding 召回 + LLM 精选。

    recall_phase:
      - equipment: 仅返回装备候选，不填充项目/指标
      - full: 完整 scoped 推荐（兼容旧逻辑）
    """
    base = dict(method)
    try:
        recall_mod = _load_gettestmethod_recall_module()
        normalize_topk_config = recall_mod.normalize_topk_config
        recall_candidates_for_document = recall_mod.recall_candidates_for_document

        normalized = normalize_topk_config(topk_config)
        mini_doc = {"methods": [base]}
        enriched = recall_candidates_for_document(
            extraction_data=mini_doc, topk_config=normalized
        )
        if not isinstance(enriched, dict):
            logger.warning("[word_llm] recall_candidates_for_document 返回空，跳过 embedding 召回")
            out = dict(base)
            out["graph_matches"] = _empty_graph_matches()
        else:
            methods = enriched.get("methods") or []
            out = dict(methods[0]) if methods else dict(base)

        Config = _get_main_config()
        neo4j_driver = getattr(Config, "NEO4J_DRIVER", None)
        pick_limits = _default_pick_limits(normalized)
        raw_matches = out.get("graph_matches") or _empty_graph_matches()
        out["graph_matches_embedding"] = raw_matches

        try:
            refined = llm_refine_graph_matches(out, raw_matches, pick_limits=pick_limits)
            out["graph_matches"] = refined
            if recall_phase == "equipment":
                out["equipment_candidates"] = _equipment_candidates_from_graph_matches(refined)
                rec_nodes, rec_rels = _equipment_only_relations_from_matches(refined)
                out["recommended_nodes"] = rec_nodes
                out["recommended_relations"] = rec_rels
                out["equipment_recommendations"] = []
                out["recommendation_source"] = "embedding+llm_equipment"
            else:
                rec_nodes, rec_rels, eq_recs = build_scoped_recommendations_from_matches(
                    refined,
                    neo4j_driver=neo4j_driver,
                )
                out["recommended_nodes"] = rec_nodes
                out["recommended_relations"] = rec_rels
                out["equipment_recommendations"] = eq_recs
                out["equipment_candidates"] = _equipment_candidates_from_graph_matches(refined)
                out["recommendation_source"] = "embedding+llm"
        except Exception as llm_exc:
            logger.warning("[word_llm] LLM 精选推荐失败，保留 embedding 粗召回: %s", llm_exc)
            out["graph_matches"] = raw_matches
            out["equipment_candidates"] = _equipment_candidates_from_graph_matches(raw_matches)
            out["recommended_nodes"] = []
            out["recommended_relations"] = []
            out["equipment_recommendations"] = []
            out["recommendation_source"] = "embedding_only"
        return out
    except Exception as exc:
        logger.warning("[word_llm] 图库召回失败，方法仍落盘（无推荐）: %s", exc)
        base["graph_matches"] = _empty_graph_matches()
        base["graph_matches_embedding"] = _empty_graph_matches()
        base["equipment_candidates"] = []
        base["recommended_nodes"] = []
        base["recommended_relations"] = []
        base["equipment_recommendations"] = []
        base["recommendation_source"] = "recall_failed"
        return base


def _persist_method_incremental(
    *,
    method: Dict[str, Any],
    method_key: str,
    method_key_to_index: Dict[str, int],
    all_methods: List[Dict[str, Any]],
    extraction_data: Dict[str, Any],
    document_dir: str,
    parsed_doc: Dict[str, Any],
    source_file_id: str,
    user_id: str,
    method_id_prefix: str,
    skip_recall: bool,
    topk_config: Optional[Dict[str, int]],
    save_extraction_checkpoint,
    save_single_method_payload,
    on_event: Optional[Any] = None,
    recall_phase: str = "full",
) -> Tuple[int, str]:
    """Finalize → 更新 checkpoint → 写入单条 method JSON。返回 (method_index, json_path)。"""
    if method_key in method_key_to_index:
        method_index = method_key_to_index[method_key]
        method["candidate_id"] = all_methods[method_index - 1].get("candidate_id") or f"M{method_index:03d}"
    else:
        method_index = len(all_methods) + 1
        method_key_to_index[method_key] = method_index
        method["candidate_id"] = f"M{method_index:03d}"

    finalized = finalize_method_for_save(method, method_index, parsed_doc)
    finalized = _apply_outline_document_trace(finalized, parsed_doc)
    if method_index <= len(all_methods):
        all_methods[method_index - 1] = finalized
    else:
        all_methods.append(finalized)

    method_item = dict(finalized)
    if not skip_recall:
        try:
            method_item = _enrich_single_method_with_recall(
                method_item, topk_config, recall_phase=recall_phase
            )
        except Exception as exc:
            logger.warning("[word_llm] 单条召回异常（已忽略）: %s", exc)
        all_methods[method_index - 1] = method_item

    extraction_data["methods"] = all_methods
    extraction_data["method_candidates"] = _build_method_candidates_from_methods(all_methods)
    extraction_data["document_meta"]["summary"] = (
        f"Word/大纲 LLM 抽取进行中：{len(extraction_data.get('test_projects') or [])} 个项目，"
        f"已写入 {len(all_methods)} 个方法"
    )
    save_extraction_checkpoint(extraction_data=extraction_data, document_dir=document_dir)

    payload = _build_outline_method_payload(
        method_item=method_item,
        file_name=parsed_doc["file_name"],
        source_file_id=source_file_id,
        user_id=user_id,
        document_class="试验大纲",
        method_index=method_index,
        method_id_prefix=method_id_prefix,
    )
    json_path = save_single_method_payload(payload, document_dir, method_index)
    logger.info(
        "[word_llm] 已落盘 method #%d: %s (%s)",
        method_index,
        method_item.get("name"),
        os.path.basename(json_path),
    )
    if on_event:
        try:
            on_event("method_ready", {
                "method_index": method_index,
                "method_key": method_key,
                "method": method_item,
                "json_path": json_path,
            })
        except Exception as evt_exc:
            logger.warning("[word_llm] on_event 回调失败: %s", evt_exc)
    return method_index, json_path


def _run_word_llm_extract_full_document(
    *,
    parsed_doc: Dict[str, Any],
    full_text: str,
    document_dir: str,
    source_file_id: str,
    user_id: str,
    method_id_prefix: str,
    skip_recall: bool,
    normalized_topk: Optional[Dict[str, int]],
    recall_mod: Any,
    on_event: Optional[Any],
    recall_phase: str,
    stream_job_id: str,
    save_extraction_checkpoint,
    save_single_method_payload,
    save_recall_checkpoint,
    save_manifest,
    build_document_manifest,
) -> Dict[str, Any]:
    """按组织形态分支 LLM 提取（默认路径）。"""
    if on_event:
        try:
            on_event("job_started", {
                "job_id": stream_job_id,
                "document_dir": document_dir,
                "extract_engine": "word_llm",
                "extract_mode": "organization_routed",
            })
        except Exception as evt_exc:
            logger.warning("[word_llm] job_started 事件失败: %s", evt_exc)

    if on_event:
        try:
            on_event("llm_extract_started", {
                "job_id": stream_job_id,
                "message": "判断文档组织形态并提取试验方法…",
            })
        except Exception:
            pass

    mode, org, projects, structure_profile, body_text = _prepare_stream_extraction_context(
        parsed_doc,
        on_event=on_event,
    )

    extraction_data = _build_extraction_data_skeleton(
        parsed_doc, projects, status="in_progress", structure_profile=structure_profile,
    )
    extraction_data["document_meta"]["extract_mode"] = "organization_routed_stream"
    extraction_data["document_meta"]["organization_mode"] = mode
    extraction_data["document_meta"]["organization"] = org
    extraction_data["test_projects"] = projects
    save_extraction_checkpoint(extraction_data=extraction_data, document_dir=document_dir)

    all_methods: List[Dict[str, Any]] = []
    method_key_to_index: Dict[str, int] = {}
    method_json_paths: List[str] = []
    run_meta: Dict[str, Any] = {}

    for method_key, method, _event in iter_llm_extract_method_stream(
        parsed_doc,
        document_dir,
        on_event=on_event,
        mode=mode,
        org=org,
        projects=projects,
        structure_profile=structure_profile,
        full_text=body_text or full_text,
        run_meta_out=run_meta,
    ):
        try:
            _idx, json_path = _persist_method_incremental(
                method=method,
                method_key=method_key,
                method_key_to_index=method_key_to_index,
                all_methods=all_methods,
                extraction_data=extraction_data,
                document_dir=document_dir,
                parsed_doc=parsed_doc,
                source_file_id=source_file_id,
                user_id=user_id,
                method_id_prefix=method_id_prefix,
                skip_recall=skip_recall,
                topk_config=normalized_topk,
                save_extraction_checkpoint=save_extraction_checkpoint,
                save_single_method_payload=save_single_method_payload,
                on_event=on_event,
                recall_phase=recall_phase,
            )
        except Exception as persist_exc:
            logger.error("[word_llm] 方法落盘失败 key=%s: %s", method_key, persist_exc)
            if on_event:
                try:
                    on_event("method_failed", {
                        "job_id": stream_job_id,
                        "method_key": method_key,
                        "error": str(persist_exc),
                    })
                except Exception:
                    pass
            continue
        if _idx > len(method_json_paths):
            method_json_paths.append(json_path)
        else:
            method_json_paths[_idx - 1] = json_path

    if not projects and all_methods:
        projects = _projects_from_methods(all_methods)
        extraction_data["test_projects"] = projects

    extraction_data["document_meta"]["extract_mode"] = run_meta.get("extract_mode") or "organization_routed"
    extraction_data["document_meta"]["organization_mode"] = run_meta.get("organization_mode") or mode
    extraction_data["document_meta"]["organization"] = run_meta.get("organization") or org
    extraction_data["document_meta"]["llm_run"] = run_meta

    final_status = "extracted_no_recall" if skip_recall else "completed"
    extraction_data["document_meta"]["status"] = final_status
    extraction_data["document_meta"]["summary"] = (
        f"整文档 LLM 抽取完成，共落盘 {len(all_methods)} 个方法"
    )
    if not skip_recall:
        extraction_data["recommendation_source"] = "embedding+llm"
    extraction_path = save_extraction_checkpoint(
        extraction_data=extraction_data, document_dir=document_dir
    )

    result: Dict[str, Any] = {
        "status": final_status,
        "job_id": stream_job_id,
        "document_dir": document_dir,
        "extraction_checkpoint_path": extraction_path,
        "test_project_count": len(projects),
        "method_count": len(all_methods),
        "method_json_paths": method_json_paths,
        "extraction_data": extraction_data,
        "llm_run_meta": run_meta,
    }

    if on_event:
        try:
            on_event("done", {
                "job_id": stream_job_id,
                "methods_count": len(all_methods),
                "document_dir": document_dir,
                "test_project_count": len(projects),
                "extract_mode": "full_document_llm",
            })
        except Exception as evt_exc:
            logger.warning("[word_llm] done 事件失败: %s", evt_exc)

    if skip_recall:
        return result

    recall_path = save_recall_checkpoint(recall_data=extraction_data, document_dir=document_dir)
    payloads = [
        _build_outline_method_payload(
            method_item=m,
            file_name=parsed_doc["file_name"],
            source_file_id=source_file_id,
            user_id=user_id,
            document_class="试验大纲",
            method_index=idx,
            method_id_prefix=method_id_prefix,
        )
        for idx, m in enumerate(all_methods, start=1)
    ]
    manifest_path = save_manifest(
        manifest=build_document_manifest(extraction_data=extraction_data, payloads=payloads),
        document_dir=document_dir,
    )
    result["recall_checkpoint_path"] = recall_path
    result["manifest_path"] = manifest_path
    return result


def run_word_llm_extract(
    file_path: str,
    *,
    source_file_id: str = "WJ_outline0001",
    user_id: str = "admin",
    output_root: str = "output_json",
    skip_recall: bool = False,
    topk_config: Optional[Dict[str, int]] = None,
    method_id_prefix: str = "FF",
    on_event: Optional[Any] = None,
    recall_phase: str = "full",
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    from assembler import build_document_manifest
    from storage import (
        build_document_output_dir,
        save_extraction_checkpoint,
        save_manifest,
        save_parsed_document,
        save_recall_checkpoint,
        save_single_method_payload as _save_single_method_payload,
    )

    file_path = resolve_upload_file(file_path)
    _init_runtime_from_config()

    recall_mod = None
    normalized_topk = None
    if not skip_recall:
        recall_mod = _import_recall_module()
        if topk_config is None:
            topk_config = {
                "equipment": int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_EQUIPMENT", "3")),
                "environment": int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_ENVIRONMENT", "3")),
                "test_project": int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT", "3")),
                "indicator": int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR", "3")),
            }
        normalized_topk = recall_mod.normalize_topk_config(topk_config)

    parsed_doc = parse_upload_document(file_path=file_path)
    full_text = str(parsed_doc.get("full_text", "") or "")
    logger.info(
        "[word_llm] 解析完成 file=%s chars=%d sections=%d",
        parsed_doc.get("file_name"),
        len(full_text),
        len(parsed_doc.get("sections") or []),
    )

    document_dir = build_document_output_dir(
        output_root=output_root, file_name=parsed_doc["file_name"]
    )
    save_parsed_document(parsed_doc=parsed_doc, document_dir=document_dir)

    # 始终保存完整正文副本，便于与送入 LLM 的 body 对照
    try:
        body_full = _build_document_body_for_llm(parsed_doc)
        with open(os.path.join(document_dir, "document_full_text.txt"), "w", encoding="utf-8") as f:
            f.write(body_full)
    except OSError as exc:
        logger.debug("[word_llm] 保存 document_full_text.txt 失败: %s", exc)

    stream_job_id = job_id or uuid.uuid4().hex
    all_methods: List[Dict[str, Any]] = []
    method_key_to_index: Dict[str, int] = {}
    method_json_paths: List[str] = []

    if not _word_llm_legacy_phased_enabled():
        return _run_word_llm_extract_full_document(
            parsed_doc=parsed_doc,
            full_text=full_text,
            document_dir=document_dir,
            source_file_id=source_file_id,
            user_id=user_id,
            method_id_prefix=method_id_prefix,
            skip_recall=skip_recall,
            normalized_topk=normalized_topk,
            recall_mod=recall_mod,
            on_event=on_event,
            recall_phase=recall_phase,
            stream_job_id=stream_job_id,
            save_extraction_checkpoint=save_extraction_checkpoint,
            save_single_method_payload=_save_single_method_payload,
            save_recall_checkpoint=save_recall_checkpoint,
            save_manifest=save_manifest,
            build_document_manifest=build_document_manifest,
        )

    structure_profile = analyze_document_structure(parsed_doc)

    projects = discover_all_test_projects(full_text, parsed_doc, structure_profile=structure_profile)
    if not projects:
        logger.warning("[word_llm] 无试验项目可抽取，任务将立即结束")

    extraction_data = _build_extraction_data_skeleton(
        parsed_doc, projects, status="in_progress", structure_profile=structure_profile,
    )
    extraction_path = save_extraction_checkpoint(
        extraction_data=extraction_data, document_dir=document_dir
    )
    logger.info("[word_llm] 已识别 %d 个试验项目，开始逐条抽取落盘", len(projects))

    all_methods.clear()
    method_key_to_index.clear()
    method_json_paths.clear()

    if on_event:
        try:
            on_event("job_started", {
                "job_id": stream_job_id,
                "document_dir": document_dir,
                "extract_engine": "word_llm",
            })
        except Exception as evt_exc:
            logger.warning("[word_llm] job_started 事件失败: %s", evt_exc)

    for proj_idx, proj in enumerate(projects, start=1):
        project_name = str(proj.get("project_name", "") or "").strip()
        logger.info("[word_llm] 开始项目: %s", project_name)
        if on_event:
            try:
                on_event("project_discovered", {
                    "job_id": stream_job_id,
                    "project_name": project_name,
                    "index": proj_idx,
                    "total": len(projects),
                })
            except Exception as evt_exc:
                logger.warning("[word_llm] project_discovered 事件失败: %s", evt_exc)
        project_method_count = 0
        for _event, method_key, method in iter_extract_methods_for_project(
            full_text, proj, parsed_doc, structure_profile=structure_profile,
            document_dir=document_dir,
            on_event=on_event,
        ):
            try:
                _idx, json_path = _persist_method_incremental(
                    method=method,
                    method_key=method_key,
                    method_key_to_index=method_key_to_index,
                    all_methods=all_methods,
                    extraction_data=extraction_data,
                    document_dir=document_dir,
                    parsed_doc=parsed_doc,
                    source_file_id=source_file_id,
                    user_id=user_id,
                    method_id_prefix=method_id_prefix,
                    skip_recall=skip_recall,
                    topk_config=normalized_topk,
                    save_extraction_checkpoint=save_extraction_checkpoint,
                    save_single_method_payload=_save_single_method_payload,
                    on_event=on_event,
                    recall_phase=recall_phase,
                )
            except Exception as persist_exc:
                logger.error("[word_llm] 方法落盘失败 key=%s: %s", method_key, persist_exc)
                if on_event:
                    try:
                        on_event("method_failed", {
                            "job_id": stream_job_id,
                            "method_key": method_key,
                            "error": str(persist_exc),
                        })
                    except Exception:
                        pass
                continue
            if _idx > len(method_json_paths):
                method_json_paths.append(json_path)
            else:
                method_json_paths[_idx - 1] = json_path
            project_method_count += 1
        logger.info(
            "[word_llm] 项目 %s 落盘 %d 条方法（累计 %d）",
            project_name,
            project_method_count,
            len(all_methods),
        )

    final_status = "extracted_no_recall" if skip_recall else "completed"
    extraction_data["document_meta"]["status"] = final_status
    extraction_data["document_meta"]["summary"] = (
        f"Word/大纲 LLM 抽取完成，共 {len(projects)} 个试验项目、{len(all_methods)} 个方法"
    )
    if not skip_recall:
        extraction_data["recommendation_source"] = "embedding+llm"
    extraction_path = save_extraction_checkpoint(
        extraction_data=extraction_data, document_dir=document_dir
    )

    result: Dict[str, Any] = {
        "status": final_status,
        "job_id": stream_job_id,
        "document_dir": document_dir,
        "extraction_checkpoint_path": extraction_path,
        "test_project_count": len(projects),
        "method_count": len(all_methods),
        "method_json_paths": method_json_paths,
        "extraction_data": extraction_data,
    }

    if on_event:
        try:
            on_event("done", {
                "job_id": stream_job_id,
                "methods_count": len(all_methods),
                "document_dir": document_dir,
                "test_project_count": len(projects),
            })
        except Exception as evt_exc:
            logger.warning("[word_llm] done 事件失败: %s", evt_exc)

    if skip_recall:
        return result

    recall_path = save_recall_checkpoint(recall_data=extraction_data, document_dir=document_dir)
    payloads = [
        _build_outline_method_payload(
            method_item=m,
            file_name=parsed_doc["file_name"],
            source_file_id=source_file_id,
            user_id=user_id,
            document_class="试验大纲",
            method_index=idx,
            method_id_prefix=method_id_prefix,
        )
        for idx, m in enumerate(all_methods, start=1)
    ]
    manifest_path = save_manifest(
        manifest=build_document_manifest(extraction_data=extraction_data, payloads=payloads),
        document_dir=document_dir,
    )

    result.update({
        "status": "completed",
        "recall_checkpoint_path": recall_path,
        "manifest_path": manifest_path,
        "result": extraction_data,
    })
    return result


def _rule_fallback_projects(full_text: str) -> List[Dict[str, Any]]:
    """LLM 发现失败时，用标题正则兜底（适配 fftq 类大纲）。"""
    pat = re.compile(
        r"试验项目[一二三四五六七八九十0-9]+[：:]\s*([^\n]+)",
        re.MULTILINE,
    )
    projects = []
    for i, m in enumerate(pat.finditer(full_text or ""), start=1):
        name = m.group(1).strip().strip("*# ")
        if name:
            projects.append({
                "project_id": f"P{i:03d}",
                "project_name": name,
                "section_hint": m.group(0).strip()[:60],
                "sub_method_titles": [],
            })
    return projects


# ---------------------------------------------------------------------------
# SYNR：五项合并 + 标题重编号（每方法从 1 起，下级顺延）
# ---------------------------------------------------------------------------

_HEADING_LINE_RE = re.compile(r"^(\d+(?:\.\d+){0,5})\s+(.+)$")
_OLD_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\d+(?:\.\d+)+[\s　.]*)?"
    r"(试验目的|试验条件|试验方法|试验数据处理|结果评定)\s*$",
    re.MULTILINE,
)
_OLD_GB_NUM_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?\d+(?:\.\d+)+\s*[\u4e00-\u9fff].*$",
    re.MULTILINE,
)
_SUB_METHOD_HEADING_RE = re.compile(
    r"^\s*\*\*[（(](\d+)[）)]\s*(.+?)\*\*\s*$",
    re.MULTILINE,
)
_LEADING_METHOD_NO_RE = re.compile(r"^\d+[\.．、\s]+")


def _strip_method_name_prefix(name: str) -> str:
    return _LEADING_METHOD_NO_RE.sub("", str(name or "").strip()).strip()


def _block_to_html(block: Any) -> str:
    try:
        from method_block_markdown import text_block_to_html
    except ImportError:
        from blueprints.FLGL.method_block_markdown import text_block_to_html
    return str(text_block_to_html(block or {}) or "").strip()


def _clean_section_body_html(html: str, section_label: str) -> str:
    """去掉与五段大标题重复的旧国标/大纲标题行。"""
    h = str(html or "").strip()
    if not h:
        return ""
    labels = {section_label, *METHOD_SECTION_LABELS.values()}
    lines_out: List[str] = []
    for ln in re.split(r"(?<=\</p\>)|(?<=\</tr\>)|\n", h):
        plain = re.sub(r"<[^>]+>", "", ln).strip()
        if not plain:
            continue
        if plain in labels:
            continue
        if _OLD_SECTION_HEADING_RE.match(plain):
            continue
        if re.match(r"^\d+(?:\.\d+)+\s*" + re.escape(section_label) + r"\s*$", plain):
            continue
        lines_out.append(ln)
    cleaned = "".join(lines_out).strip()
    cleaned = _OLD_GB_NUM_HEADING_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _normalize_sub_method_headings(html: str) -> str:
    """将 **（1）标题** 转为普通段落，便于后续统一重编号。"""
    def repl(m: re.Match) -> str:
        title = m.group(2).strip()
        return f"<p><strong>{title}</strong></p>"
    return _SUB_METHOD_HEADING_RE.sub(repl, html or "")


def _build_synr_html_raw(method_item: Dict[str, Any]) -> str:
    """拼成单一 HTML：固定输出 1–5 节（试验目的→结果评定），空节保留标题。"""
    parts: List[str] = []
    for sec_no, (key, label) in enumerate(_SYNR_DISPLAY_SLOTS, start=1):
        body = _clean_section_body_html(_block_to_html(method_item.get(key)), label)
        if key == "syff":
            body = _normalize_sub_method_headings(body)
        parts.append(
            f'<h4 class="wjgl-section-heading" data-section-no="{sec_no}">'
            f"<strong>{sec_no} {label}</strong></h4>"
        )
        if body:
            parts.append(body)
    return "".join(parts).strip()


def _renumber_synr_html(html: str) -> str:
    """按文档顺序将各级标题重编号为 1 / 1.1 / 2 / 2.1 …"""
    h = str(html or "").strip()
    if not h:
        return h
    try:
        from method_section_enrich import renumber_document_sequential, _apply_section_ref_map_safe
    except ImportError:
        from blueprints.FLGL.method_section_enrich import (
            renumber_document_sequential,
            _apply_section_ref_map_safe,
        )
    renumbered, ref_map = renumber_document_sequential(h)
    return _apply_section_ref_map_safe(renumbered, ref_map).strip()


def build_method_synr_from_native_html(
    method_item: Dict[str, Any],
    parsed_doc: Optional[Dict[str, Any]] = None,
) -> str:
    """从 Word/MinerU 原生 full_html 切五段拼 SYNR；失败时回退 LLM 文本转 HTML。"""
    doc = parsed_doc if isinstance(parsed_doc, dict) else {}
    full_html = str(doc.get("full_html") or "").strip()
    if not full_html:
        return build_method_synr(method_item)

    project = str(method_item.get("test_project_name") or "").strip()
    hint = str(method_item.get("section_hint") or "").strip()
    short = str(method_item.get("method_short_name") or "").strip()
    structure_profile = doc.get("structure_profile") or method_item.get("structure_profile")

    project_html = extract_project_html_block(full_html, project, hint)
    if not project_html:
        return build_method_synr(method_item)

    project_sno = _resolve_project_section_no_from_html(full_html, project, hint)
    native_sections = split_method_sections_from_html(
        project_html,
        project_sno,
        structure_profile if isinstance(structure_profile, dict) else None,
    )
    if not any(str(native_sections.get(k) or "").strip() for k in METHOD_SECTION_KEYS):
        return build_method_synr(method_item)

    syff_native = str(native_sections.get("syff") or "").strip()
    if short and syff_native:
        sliced_syff = _slice_sub_method_syff_html(syff_native, short)
        if sliced_syff:
            native_sections["syff"] = sliced_syff

    jgpd_native = str(native_sections.get("jgpd") or "").strip()
    if short and jgpd_native:
        sliced_jgpd = _slice_sub_method_jgpd_html(
            jgpd_native,
            short,
            native_sections.get("syff") or "",
        )
        if sliced_jgpd:
            native_sections["jgpd"] = sliced_jgpd

    parts: List[str] = []
    for sec_no, (key, label) in enumerate(_SYNR_DISPLAY_SLOTS, start=1):
        body = str(native_sections.get(key) or "").strip()
        if not body:
            body = _clean_section_body_html(_block_to_html(method_item.get(key)), label)
        else:
            body = _clean_native_section_body_html(body, label)
        parts.append(
            f'<h4 class="wjgl-section-heading" data-section-no="{sec_no}">'
            f"<strong>{sec_no} {label}</strong></h4>"
        )
        if body:
            parts.append(body)
    raw = "".join(parts).strip()
    try:
        from blueprints.FLGL.method_section_enrich import (
            _finalize_structured_synr_html,
            polish_native_outline_synr,
        )
    except ImportError:
        from method_section_enrich import (
            _finalize_structured_synr_html,
            polish_native_outline_synr,
        )
    normalized = _finalize_structured_synr_html(raw)
    return polish_native_outline_synr(normalized)


def build_method_synr(method_item: Dict[str, Any]) -> str:
    """合并五项为单一 SYNR；顶层固定 1–5 节标题，不做全文重编号。"""
    raw = _build_synr_html_raw(method_item)
    try:
        from blueprints.FLGL.method_section_enrich import _finalize_structured_synr_html
    except ImportError:
        from method_section_enrich import _finalize_structured_synr_html
    return _finalize_structured_synr_html(raw)


# finalize_method_for_save / finalize_methods_for_save 定义见 run_word_llm_extract 上方


# ---------------------------------------------------------------------------
# 图库推荐：recall.py 粗召回 + LLM 精选 + 装备维度关联（同 method_section_enrich 格式）
# ---------------------------------------------------------------------------

def _method_excerpt_for_recommend(method: Dict[str, Any], max_chars: int = 2800) -> str:
    synr = str(method.get("SYNR") or method.get("synr") or "").strip()
    if synr:
        plain = re.sub(r"<[^>]+>", "\n", synr)
        plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
        if plain:
            return plain[:max_chars]
    parts = []
    for key in METHOD_SECTION_KEYS:
        text = str((method.get(key) or {}).get("text", "") or "").strip()
        if text:
            parts.append(f"{METHOD_SECTION_LABELS[key]}：{text[:600]}")
    return "\n\n".join(parts)[:max_chars]


def _default_pick_limits(topk_config: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    cfg = topk_config or {}
    return {
        "equipment": max(0, int(cfg.get("equipment", 3) or 0)),
        "environment": max(0, int(cfg.get("environment", 2) or 0)),
        "test_project": max(0, int(cfg.get("test_project", 3) or 0)),
        "indicator": max(0, int(cfg.get("indicator", 3) or 0)),
    }


def _fallback_pick_from_embedding(
    graph_matches: Dict[str, List[Dict[str, Any]]],
    pick_limits: Dict[str, int],
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for slot in GRAPH_RECOMMEND_SLOTS:
        lim = pick_limits.get(slot, 0)
        items = list(graph_matches.get(slot) or [])[:lim]
        out[slot] = items
    return out


def llm_refine_graph_matches(
    method: Dict[str, Any],
    graph_matches: Dict[str, List[Dict[str, Any]]],
    *,
    pick_limits: Optional[Dict[str, int]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """在 recall.py embedding 候选中，由 LLM 精选各 slot 节点。"""
    pick_limits = pick_limits or _default_pick_limits(method.get("topk_config"))
    graph_matches = graph_matches if isinstance(graph_matches, dict) else {}

    cand_payload: Dict[str, List[Dict[str, Any]]] = {}
    id_index: Dict[str, Dict[str, Dict[str, Any]]] = {s: {} for s in GRAPH_RECOMMEND_SLOTS}
    has_any = False
    for slot in GRAPH_RECOMMEND_SLOTS:
        slim = []
        for item in graph_matches.get(slot) or []:
            if not isinstance(item, dict):
                continue
            nid = str(item.get("id") or item.get("Id") or "").strip()
            if not nid:
                continue
            row = {
                "id": nid,
                "name": str(item.get("name") or item.get("Name") or nid).strip(),
                "description": str(item.get("description") or "")[:160],
                "score": item.get("score"),
            }
            slim.append(row)
            id_index[slot][nid] = item
        cand_payload[slot] = slim[:12]
        if slim:
            has_any = True

    if not has_any:
        return _fallback_pick_from_embedding(graph_matches, pick_limits)

    prompt = LLM_GRAPH_RECOMMEND_PROMPT.format(
        method_name=str(method.get("name") or "").strip(),
        test_project_name=str(method.get("test_project_name") or "").strip(),
        method_description=str(method.get("description") or "").strip()[:500],
        method_excerpt=_method_excerpt_for_recommend(method),
        recall_plan_json=json.dumps(method.get("recall_plan") or {}, ensure_ascii=False),
        candidates_json=json.dumps(
            {k: v for k, v in cand_payload.items() if v},
            ensure_ascii=False,
            indent=2,
        ),
        pick_limits_json=json.dumps(pick_limits, ensure_ascii=False),
    )
    logger.info("[word_llm] LLM 图节点推荐 prompt=%d 字", len(prompt))
    try:
        raw = _call_llm_json(prompt)
    except Exception as exc:
        logger.warning("[word_llm] LLM 图节点推荐失败，回退 embedding TopK: %s", exc)
        return _fallback_pick_from_embedding(graph_matches, pick_limits)

    if not isinstance(raw, dict):
        return _fallback_pick_from_embedding(graph_matches, pick_limits)

    refined: Dict[str, List[Dict[str, Any]]] = {s: [] for s in GRAPH_RECOMMEND_SLOTS}
    for slot in GRAPH_RECOMMEND_SLOTS:
        lim = pick_limits.get(slot, 0)
        if lim <= 0:
            continue
        picks = raw.get(slot, [])
        if not isinstance(picks, list):
            picks = []
        seen: set = set()
        for pick in picks:
            if not isinstance(pick, dict):
                continue
            nid = str(pick.get("id") or "").strip()
            if not nid or nid in seen or nid not in id_index[slot]:
                continue
            seen.add(nid)
            base = dict(id_index[slot][nid])
            conf = pick.get("confidence", pick.get("score"))
            try:
                score = float(conf) if conf is not None else float(base.get("score") or 0.5)
            except (TypeError, ValueError):
                score = float(base.get("score") or 0.5)
            base["score"] = round(min(1.0, max(0.0, score)), 4)
            base["llm_reason"] = str(pick.get("reason") or "").strip()
            base["recommend_source"] = "llm"
            refined[slot].append(base)
            if len(refined[slot]) >= lim:
                break
        if not refined[slot]:
            refined[slot] = _fallback_pick_from_embedding(graph_matches, {slot: lim})[slot]

    return refined


def build_scoped_recommendations_from_matches(
    graph_matches: Dict[str, List[Dict[str, Any]]],
    *,
    neo4j_driver=None,
    per_equipment_top: int = 3,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    将 LLM 精选后的 graph_matches 转为 recommended_nodes / recommended_relations /
    equipment_recommendations（与 method_section_enrich.build_equipment_scoped_recommendations 一致）。
    """
    try:
        from method_section_enrich import (
            _fill_equipment_slot_candidates,
            _load_equipment_related_from_graph,
        )
    except ImportError:
        from blueprints.FLGL.method_section_enrich import (
            _fill_equipment_slot_candidates,
            _load_equipment_related_from_graph,
        )

    graph_matches = graph_matches if isinstance(graph_matches, dict) else {}
    equip_items = list(graph_matches.get("equipment") or [])
    project_pool = list(graph_matches.get("test_project") or [])
    indicator_pool = list(graph_matches.get("indicator") or [])

    recommended_nodes: List[Dict[str, Any]] = []
    recommended_relations: List[Dict[str, Any]] = []
    equipment_recommendations: List[Dict[str, Any]] = []

    def _as_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "node_id": str(item.get("id") or item.get("Id") or "").strip(),
            "node_name": str(item.get("name") or item.get("Name") or "").strip(),
            "score": float(item.get("score") or 0.5),
        }

    for env in graph_matches.get("environment") or []:
        if not isinstance(env, dict):
            continue
        eid = str(env.get("id") or "").strip()
        if not eid:
            continue
        recommended_relations.append({
            "target_type": "Environment",
            "target_id": eid,
            "node_name": str(env.get("name") or eid),
            "rel_type": "CON",
            "rate": round(min(1.0, float(env.get("score") or 0.5)), 3),
        })

    for eq in equip_items:
        if not isinstance(eq, dict):
            continue
        eid = str(eq.get("id") or "").strip()
        ename = str(eq.get("name") or eid).strip()
        escore = float(eq.get("score") or 0.5)
        if not eid:
            continue
        recommended_nodes.append({
            "node_type": "Equipment",
            "node_id": eid,
            "node_name": ename,
            "score": escore,
            "llm_reason": eq.get("llm_reason", ""),
        })
        recommended_relations.append({
            "target_type": "Equipment",
            "target_id": eid,
            "node_name": ename,
            "rel_type": "CON",
            "rate": round(min(1.0, escore), 3),
            "equipment_id": eid,
        })
        eq_projects, eq_indicators = _load_equipment_related_from_graph(
            neo4j_driver, eid, top_n=per_equipment_top,
        )
        eq_projects = _fill_equipment_slot_candidates(
            eq_projects,
            [_as_candidate(x) for x in project_pool],
            equipment_name=ename,
            top_n=per_equipment_top,
        )
        eq_indicators = _fill_equipment_slot_candidates(
            eq_indicators,
            [_as_candidate(x) for x in indicator_pool],
            equipment_name=ename,
            top_n=per_equipment_top,
        )
        for p in eq_projects:
            pid = str(p.get("node_id") or "").strip()
            if not pid:
                continue
            recommended_relations.append({
                "target_type": "TestProject",
                "target_id": pid,
                "node_name": p.get("node_name") or pid,
                "rel_type": "CON",
                "rate": round(min(1.0, float(p.get("score") or 0) * 0.9 + escore * 0.1), 3),
                "equipment_id": eid,
            })
        for ind in eq_indicators:
            iid = str(ind.get("node_id") or "").strip()
            if not iid:
                continue
            recommended_relations.append({
                "target_type": "Indicator",
                "target_id": iid,
                "node_name": ind.get("node_name") or iid,
                "rel_type": "CON",
                "rate": round(min(1.0, float(ind.get("score") or 0) * 0.9 + escore * 0.1), 3),
                "equipment_id": eid,
            })
        equipment_recommendations.append({
            "equipment_id": eid,
            "equipment_name": ename,
            "score": escore,
            "projects": eq_projects,
            "indicators": eq_indicators,
        })

    if not equip_items:
        for p in project_pool[:3]:
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            recommended_relations.append({
                "target_type": "TestProject",
                "target_id": pid,
                "node_name": str(p.get("name") or pid),
                "rel_type": "CON",
                "rate": round(min(1.0, float(p.get("score") or 0.5)), 3),
            })
        for ind in indicator_pool[:3]:
            iid = str(ind.get("id") or "").strip()
            if not iid:
                continue
            recommended_relations.append({
                "target_type": "Indicator",
                "target_id": iid,
                "node_name": str(ind.get("name") or iid),
                "rel_type": "CON",
                "rate": round(min(1.0, float(ind.get("score") or 0.5)), 3),
            })

    return recommended_nodes, recommended_relations, equipment_recommendations


def recall_and_llm_recommend_document(
    extraction_data: Dict[str, Any],
    topk_config: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """embedding 粗召回（recall.py）→ LLM 精选 → 装备维度推荐关系。"""
    recall_mod = _import_recall_module()
    normalized = recall_mod.normalize_topk_config(topk_config)
    enriched = dict(extraction_data)
    out_methods: List[Dict[str, Any]] = []
    for method in enriched.get("methods") or []:
        if not isinstance(method, dict):
            continue
        out_methods.append(_enrich_single_method_with_recall(method, normalized))
    enriched["methods"] = out_methods
    enriched["recommendation_source"] = "embedding+llm"
    return enriched


def _build_outline_method_payload(
    method_item: Dict[str, Any],
    file_name: str,
    source_file_id: str,
    user_id: str,
    document_class: str,
    method_index: int,
    method_id_prefix: str = "FF",
) -> Dict[str, Any]:
    """大纲方法落库 JSON：正文仅存 method_node.SYNR，不拆分 SYTJ/SYGC/JGPD 子节点。"""
    method_id = f"{method_id_prefix}_{method_index:04d}"
    recall_plan = method_item.get("recall_plan", {}) or {}
    synr = str(
        method_item.get("SYNR") or method_item.get("synr") or build_method_synr(method_item) or ""
    ).strip()

    return {
        "source_file": {
            "file_id": source_file_id,
            "file_name": file_name,
            "user_id": user_id,
        },
        "class_node": {
            "Id": f"{method_id}_CLASS",
            "Name": document_class or method_item.get("class", "试验大纲"),
            "Source_name": file_name,
            "Source_id": source_file_id,
            "Status": "启动",
            "User_id": user_id,
            "Create_time": "",
        },
        "terms": [],
        "method_node": {
            "Id": method_id,
            "Candidate_id": method_item.get("candidate_id"),
            "Section_ids": list(method_item.get("section_ids", []) or []),
            "Name": method_item.get("name", ""),
            "Type": method_item.get("type", ""),
            "Label": recall_plan,
            "Description": method_item.get("description", ""),
            "SY": "",
            "SYNR": synr,
            "Sytj": "",
            "Sygc": "",
            "Jgpd": "",
            "Source_name": file_name,
            "Source_id": source_file_id,
            "Status": "启动",
            "User_id": user_id,
            "Create_time": "",
            "Method_no": method_item.get("method_no"),
        },
        "graph_matches": method_item.get("graph_matches", {
            "equipment": [],
            "environment": [],
            "test_project": [],
            "indicator": [],
        }),
        "topk_config": method_item.get("topk_config", {}),
        "recommended_nodes": list(method_item.get("recommended_nodes") or []),
        "recommended_relations": list(method_item.get("recommended_relations") or []),
        "equipment_recommendations": list(method_item.get("equipment_recommendations") or []),
        "recommendation_source": method_item.get("recommendation_source", ""),
        "method_sections": {k: method_item.get(k, {}) or {} for k in METHOD_SECTION_KEYS},
        "method_section_labels": METHOD_SECTION_LABELS,
    }


def _method_section_lengths(method: Dict[str, Any]) -> Dict[str, int]:
    return {
        METHOD_SECTION_LABELS[k]: len(str((method.get(k) or {}).get("text", "") or ""))
        for k in METHOD_SECTION_KEYS
    }


def create_upload_blueprint() -> Any:
    from flask import Blueprint, jsonify, request

    bp = Blueprint("word_llm_method_extract", __name__)

    @bp.route("/api/FLGL/word-method-extract/upload", methods=["POST"])
    def upload_word_and_extract():
        """
        multipart/form-data:
          file: .docx 文件（必填）
          source_file_id, user_id, skip_recall (0|1) 可选
        """
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "请上传 file 字段（.docx）"}), 400

        ext = _file_ext(upload.filename or "")
        if ext not in ALLOWED_UPLOAD_EXT:
            return jsonify({
                "ok": False,
                "error": (
                    f"仅支持 {sorted(ALLOWED_UPLOAD_EXT)}，"
                    f"当前: {ext or '(无扩展名)'} "
                    f"(filename={upload.filename!r})"
                ),
            }), 400

        source_file_id = (request.form.get("source_file_id") or "WJ_outline0001").strip()
        user_id = (request.form.get("user_id") or "admin").strip()
        skip_recall = str(request.form.get("skip_recall", "0")).lower() in ("1", "true", "yes")

        suffix = ext or ".docx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            upload.save(tmp.name)
            tmp_path = tmp.name

        try:
            _init_runtime_from_config()
            out = run_word_llm_extract(
                tmp_path,
                source_file_id=source_file_id,
                user_id=user_id,
                skip_recall=skip_recall,
            )
            summary = {
                "ok": True,
                "status": out["status"],
                "document_dir": out["document_dir"],
                "test_project_count": out["test_project_count"],
                "method_count": out["method_count"],
                "projects": [
                    p.get("project_name") for p in out["extraction_data"].get("test_projects", [])
                ],
                "methods": [
                    {
                        "name": m.get("name"),
                        "method_no": m.get("method_no"),
                        "synr_len": len(str(m.get("SYNR") or m.get("synr") or "")),
                        "sections": _method_section_lengths(m),
                        "graph_matches": {
                            k: len(v or [])
                            for k, v in (m.get("graph_matches") or {}).items()
                        },
                        "recommended_relations": len(m.get("recommended_relations") or []),
                        "recommendation_source": m.get("recommendation_source", ""),
                    }
                    for m in (out.get("result") or out.get("extraction_data", {})).get(
                        "methods", []
                    )
                ],
                "extraction_checkpoint_path": out.get("extraction_checkpoint_path"),
                "manifest_path": out.get("manifest_path"),
            }
            return jsonify(summary)
        except Exception as exc:
            logger.exception("word-method-extract 失败")
            return jsonify({"ok": False, "error": str(exc)}), 500
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return bp


def _serve_standalone(host: str, port: int) -> None:
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(create_upload_blueprint())
    print(f"[INFO] Word 方法抽取服务: http://{host}:{port}/api/FLGL/word-method-extract/upload")
    app.run(host=host, port=port, debug=False)


def _cli_main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="试验大纲 Word 文档 LLM 提取（16384 上下文，超长自动分片）"
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    p_extract = sub.add_parser("extract", help="上传/指定文件路径，抽取+召回")
    p_extract.add_argument("file", help=".docx / .txt / .md（路径可带引号）")
    p_extract.add_argument("--source-file-id", default="WJ_outline0001")
    p_extract.add_argument("--user-id", default="admin")
    p_extract.add_argument("--output-root", default="output_json")
    p_extract.add_argument("--skip-recall", action="store_true", help="只做 LLM 抽取，不跑图库召回")
    p_extract.add_argument("--method-id-prefix", default="FF")

    p_serve = sub.add_parser("serve", help="启动独立 Flask 上传服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=5010)

    args = parser.parse_args()

    if args.mode == "serve":
        _serve_standalone(args.host, args.port)
        return

    out = run_word_llm_extract(
        args.file,
        source_file_id=args.source_file_id,
        user_id=args.user_id,
        output_root=args.output_root,
        skip_recall=args.skip_recall,
        method_id_prefix=args.method_id_prefix,
    )
    print(f"[OK] status={out['status']}")
    print(f"[OK] 试验项目数={out['test_project_count']}  子方法数={out['method_count']}")
    print(f"[OK] 输出目录: {out['document_dir']}")
    print(f"[OK] checkpoint: {out['extraction_checkpoint_path']}")
    if out.get("manifest_path"):
        print(f"[OK] manifest: {out['manifest_path']}")
    for p in out["extraction_data"].get("test_projects", []):
        print(f"  项目: {p.get('project_name')}  子方法标题数={len(p.get('sub_method_titles') or [])}")


if __name__ == "__main__":
    _cli_main()
