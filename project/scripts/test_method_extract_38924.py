#!/usr/bin/env python3
"""诊断 GB/T 38924.6 方法提取：章节 4/5/7，输出全流程中间数据。"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

PDF = Path(r"F:\D.c-system\项目\2601-方案生成项目\标准\GBT+38924.6-2020.pdf")
MD_CACHE = ROOT / "output/wjgl_ocr_cache/法规标准/fgbz0007/mineru_fgbz0007_9780c68e/GBT+38924.6-2020/txt/GBT+38924.6-2020.md"
OUT = ROOT / "output/method_extract_debug_38924.json"
CATEGORY = "法规标准"
DOC_ID = "fgbz0007_test"
SECTIONS = ["4", "5", "7"]


def _plain_preview(html: str, n: int = 400) -> str:
    t = re.sub(r"<[^>]+>", " ", html or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def main():
    from pdf_extract.mineru_wjgl_service import ensure_parse_bundle_from_markdown, local_doc_dir
    from blueprints.ZSGL.WJGL1.document_parse_service import fetch_section_content_html
    from blueprints.FLGL.method_section_enrich import (
        enrich_section_method,
        split_gb_standard_method_html,
        finalize_method_blocks,
    )
    from blueprints.FLGL.method_routes import (
        _section_nos_from_group,
        _min_covering_section_roots,
        _extract_section_from_parse_cache,
        _method_parts_to_synr,
    )

    md = MD_CACHE.read_text(encoding="utf-8", errors="replace")
    local_dir = local_doc_dir(CATEGORY, DOC_ID)
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "document.md").write_text(md, encoding="utf-8")

    bundle = ensure_parse_bundle_from_markdown(CATEGORY, DOC_ID, markdown=md, persist_local=True)
    sections = bundle.get("sections") or []
    slices = bundle.get("node_slices") or {}

    report = {
        "pdf": str(PDF),
        "md_cache": str(MD_CACHE),
        "section_titles": [
            {"no": s.get("section_no"), "title": s.get("title"), "id": s.get("section_id")}
            for s in sections
        ],
        "selected_roots": SECTIONS,
        "per_section_fetch": {},
        "merged_raw_html_len": 0,
        "merged_raw_preview": "",
        "split_buckets": {},
        "final_blocks_preview": {},
        "synr_preview": "",
    }

    parts = []
    for pno in SECTIONS:
        html = fetch_section_content_html(
            CATEGORY, DOC_ID, pno, include_children=True,
            sections=sections, node_slices=slices,
        )
        report["per_section_fetch"][pno] = {
            "html_len": len(html or ""),
            "has_table": bool(re.search(r"<\s*table\b", html or "", re.I)),
            "preview": _plain_preview(html, 500),
        }
        if html:
            parts.append(html)

    merged = "".join(parts)
    report["merged_raw_html_len"] = len(merged)
    report["merged_raw_preview"] = _plain_preview(merged, 800)

    split = split_gb_standard_method_html(merged)
    report["split_buckets"] = {k: {"len": len(v), "preview": _plain_preview(v, 300)} for k, v in split.items()}

    group = {
        "parent": {"sectionNo": "4", "title": "试验设备和仪表", "level": 1},
        "children": [
            {"sectionNo": "5", "title": "试验过程", "level": 1},
            {"sectionNo": "7", "title": "试验结果评定", "level": 1},
        ],
    }
    section_nos = _section_nos_from_group(group)
    roots = _min_covering_section_roots(section_nos)
    report["group_section_nos"] = section_nos
    report["group_fetch_roots"] = roots

    parse_cache = {"sections": sections, "node_slices": slices, "full_markdown": md}
    extracted = _extract_section_from_parse_cache(
        parse_cache, "4", "试验设备和仪表",
        category=CATEGORY, doc_id=DOC_ID, section_nos=section_nos,
    )
    report["extract_from_group_len"] = len(extracted)
    report["extract_from_group_preview"] = _plain_preview(extracted, 600)

    enriched = enrich_section_method(
        extracted,
        "振动试验-试验方法",
        use_llm_merge=False,
        full_text="",
        all_tables=[],
        std_name="GBT 38924.6-2020",
        category=CATEGORY,
        doc_id=DOC_ID,
        section_extract_mode=True,
    )
    blocks = enriched.get("blocks") or {}
    blocks = finalize_method_blocks(blocks, renumber=False)
    report["enrich_source"] = enriched.get("extract_source")
    report["content_html_len"] = len(enriched.get("content_html") or "")
    report["final_blocks_preview"] = {
        k: _plain_preview(str((blocks.get(k) or {}).get("text") or ""), 400)
        for k in ("sytj", "sygc", "jgpd")
    }
    report["synr_preview"] = _plain_preview(_method_parts_to_synr(blocks), 1200)

    # 检查是否混入第3章或附录/汽车相关词
    synr_plain = report["synr_preview"]
    report["flags"] = {
        "has_chapter3_title": "3.1 受试设备" in _method_parts_to_synr(blocks),
        "has_表A": "表A" in synr_plain or "表犃" in synr_plain,
        "has_汽车": "汽车" in synr_plain,
        "has_车载": "车载" in synr_plain,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", OUT)
    print(json.dumps(report["flags"], ensure_ascii=False, indent=2))
    print("split:", {k: v["len"] for k, v in report["split_buckets"].items()})
    print("synr preview:", report["synr_preview"][:500])


if __name__ == "__main__":
    main()
