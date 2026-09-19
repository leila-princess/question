"""章节勾选提取调试：落盘 Word 解析与提取全过程中间文件。"""
from __future__ import annotations

import html as html_module
import json
import logging
import os
import platform
import re
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def section_extract_debug_enabled() -> bool:
    return str(os.environ.get("FLGL_SECTION_EXTRACT_DEBUG", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _backend_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def purge_flgl_local_artifacts_for_document(
    *,
    doc_id: str = "",
    category: str = "",
) -> dict:
    """删除与文档 doc_id 关联的本地 FLGL 调试/预览/引导提取目录，避免编号与缓存错乱。"""
    stats = {"debug_dirs": 0, "preview_cache": 0, "guided_dirs": 0}
    doc_id = str(doc_id or "").strip()
    category = str(category or "").strip()
    if not doc_id:
        return stats

    root = os.path.join(_backend_root(), "output_json")

    def _rm_dir(path: str, key: str) -> None:
        if os.path.isdir(path):
            try:
                shutil.rmtree(path, ignore_errors=True)
                stats[key] = int(stats.get(key) or 0) + 1
            except OSError as exc:
                logger.debug("[section-debug] rmtree %s: %s", path, exc)

    _rm_dir(os.path.join(root, "flgl_extract_debug", doc_id), "debug_dirs")
    _rm_dir(os.path.join(root, "flgl_guided_extract", doc_id), "guided_dirs")

    preview_dir = os.path.join(root, "flgl_preview_cache")
    preview_path = os.path.join(preview_dir, f"{doc_id}.json")
    if os.path.isfile(preview_path):
        try:
            os.remove(preview_path)
            stats["preview_cache"] += 1
        except OSError as exc:
            logger.debug("[section-debug] remove preview %s: %s", preview_path, exc)

    debug_root = os.path.join(root, "flgl_extract_debug")
    if os.path.isdir(debug_root):
        for name in os.listdir(debug_root):
            if name == doc_id:
                continue
            session_dir = os.path.join(debug_root, name)
            if not os.path.isdir(session_dir):
                continue
            manifest_path = os.path.join(session_dir, "manifest.json")
            matched = False
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, encoding="utf-8") as mf:
                        manifest = json.load(mf) or {}
                    src = str(manifest.get("source_path") or "")
                    if doc_id in src and (not category or f"/{category}/" in src.replace("\\", "/")):
                        matched = True
                except Exception:
                    pass
            if matched:
                _rm_dir(session_dir, "debug_dirs")

    if os.path.isdir(preview_dir):
        for fn in os.listdir(preview_dir):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(preview_dir, fn)
            try:
                with open(fp, encoding="utf-8") as pf:
                    data = json.load(pf) or {}
                cached_doc = str(data.get("doc_id") or "").strip()
                cached_cat = str(data.get("category") or "").strip()
                if cached_doc == doc_id and (not category or cached_cat == category or cached_cat == "_preview"):
                    os.remove(fp)
                    stats["preview_cache"] += 1
            except Exception:
                continue

    if stats["debug_dirs"] or stats["preview_cache"] or stats["guided_dirs"]:
        logger.info(
            "[section-debug] 已清理本地 FLGL 缓存 doc_id=%s category=%s stats=%s",
            doc_id,
            category,
            stats,
        )
    return stats


def debug_session_dir(session_id: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", str(session_id or "").strip())[:120] or "unknown"
    root = os.path.join(_backend_root(), "output_json", "flgl_extract_debug", safe)
    os.makedirs(root, exist_ok=True)
    return root


def extract_pipeline_dir(session_id: str) -> str:
    root = debug_session_dir(session_id)
    extract_dir = os.path.join(root, "extract")
    os.makedirs(extract_dir, exist_ok=True)
    return extract_dir


def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content or "")


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _plain_len(s: str) -> int:
    return len(re.sub(r"<[^>]+>", " ", s or "").strip())


def save_extract_pipeline_step(
    session_id: str,
    filename: str,
    content: Any,
    *,
    as_json: bool = False,
) -> None:
    """写入 extract/ 下单步调试文件。"""
    if not section_extract_debug_enabled() or not session_id:
        return
    extract_dir = extract_pipeline_dir(session_id)
    path = os.path.join(extract_dir, filename)
    if as_json:
        _write_json(path, content)
    else:
        _write_text(path, str(content or ""))


def save_word_parse_debug_bundle(
    session_id: str,
    *,
    source_path: str = "",
    ctx: Optional[Dict[str, Any]] = None,
    heading_result: Optional[Dict[str, Any]] = None,
    stage: str = "parse",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """保存 Word/文档解析中间结果，返回调试目录绝对路径。"""
    if not section_extract_debug_enabled():
        return ""

    root = debug_session_dir(session_id)
    ctx = ctx if isinstance(ctx, dict) else {}
    parse_cache = ctx.get("parse_cache") if isinstance(ctx.get("parse_cache"), dict) else {}

    if source_path and os.path.isfile(source_path):
        try:
            shutil.copy2(source_path, os.path.join(root, "00_source" + os.path.splitext(source_path)[1].lower()))
        except OSError as exc:
            logger.warning("[section-debug] 复制源文件失败: %s", exc)

    _write_text(os.path.join(root, "01_full_text.txt"), str(ctx.get("full_text") or ""))
    _write_text(os.path.join(root, "02_full_html.html"), str(parse_cache.get("full_html") or ""))
    _write_text(os.path.join(root, "03_full_markdown.md"), str(parse_cache.get("full_markdown") or ""))

    sections = list(parse_cache.get("sections") or [])
    _write_json(os.path.join(root, "04_sections.json"), sections)

    slices_dir = os.path.join(root, "05_node_slices")
    os.makedirs(slices_dir, exist_ok=True)
    node_slices = parse_cache.get("node_slices") if isinstance(parse_cache.get("node_slices"), dict) else {}
    for sid, slice_html in node_slices.items():
        safe_sid = re.sub(r"[^\w\-]", "_", str(sid))[:80]
        _write_text(os.path.join(slices_dir, f"{safe_sid}.html"), str(slice_html or ""))

    md_slices_dir = os.path.join(root, "05_node_slices_md")
    node_slices_md = parse_cache.get("node_slices_md") if isinstance(parse_cache.get("node_slices_md"), dict) else {}
    if node_slices_md:
        os.makedirs(md_slices_dir, exist_ok=True)
        for sid, slice_md in node_slices_md.items():
            safe_sid = re.sub(r"[^\w\-]", "_", str(sid))[:80]
            _write_text(os.path.join(md_slices_dir, f"{safe_sid}.md"), str(slice_md or ""))

    section_catalogue = (
        (parse_cache.get("parsed_document") or {}).get("section_catalogue")
        or parse_cache.get("section_catalogue")
        or []
    )
    _write_json(os.path.join(root, "06_section_catalogue.json"), section_catalogue)

    if heading_result:
        hr_out = dict(heading_result)
        if str(heading_result.get("source") or "") == "parse_sections":
            hr_out["note"] = "与 04_sections 同源，非 PDF 书签/规则 heading_extract"
        _write_json(os.path.join(root, "07_heading_extract.json"), hr_out)
        _write_json(os.path.join(root, "08_toc.json"), heading_result.get("toc") or [])
        _write_json(os.path.join(root, "09_full_headings.json"), heading_result.get("full_headings") or [])

    manifest = {
        "stage": stage,
        "session_id": session_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_path": source_path,
        "ctx_source": ctx.get("source"),
        "text_len": len(str(ctx.get("full_text") or "")),
        "sections_count": len(sections),
        "node_slices_count": len(node_slices),
        "node_slices_md_count": len(node_slices_md),
        "full_markdown_len": len(str(parse_cache.get("full_markdown") or "")),
        "python": sys.version,
        "platform": platform.platform(),
        "extra": extra or {},
    }
    _write_json(os.path.join(root, "manifest.json"), manifest)
    logger.info("[section-debug] 已保存解析调试包 stage=%s dir=%s", stage, root)
    return root


def save_section_extract_debug_bundle(
    session_id: str,
    *,
    request_sections: Optional[List[Dict[str, Any]]] = None,
    group: Optional[Dict[str, Any]] = None,
    resolved_items: Optional[List[Dict[str, Any]]] = None,
    chunk_results: Optional[List[Dict[str, Any]]] = None,
    fetch_traces: Optional[List[Dict[str, Any]]] = None,
    merged_content: str = "",
    prepared_html: str = "",
    formatted_synr: str = "",
    final_method: Optional[Dict[str, Any]] = None,
    group_index: int = 0,
    stage: str = "extract",
) -> str:
    """保存单次章节勾选提取的中间结果，并生成 pipeline 报告。"""
    if not section_extract_debug_enabled():
        return ""

    extract_dir = extract_pipeline_dir(session_id)

    _write_json(os.path.join(extract_dir, "00_request_group.json"), group or {})
    _write_json(os.path.join(extract_dir, "01_resolved_items.json"), resolved_items or [])

    traces = list(fetch_traces or [])
    for tr in traces:
        sid = str(tr.get("section_id") or "unknown")
        safe_sid = re.sub(r"[^\w\-]", "_", sid)[:60]
        expected = str(tr.get("expected_span") or "")
        if expected:
            _write_text(
                os.path.join(extract_dir, f"03_expected_span_{safe_sid}.txt"),
                expected,
            )
    _write_json(os.path.join(extract_dir, "02_fetch_trace.json"), traces)

    root = debug_session_dir(session_id)
    md_slices_dir = os.path.join(root, "05_node_slices_md")

    for idx, chunk in enumerate(chunk_results or []):
        sid = re.sub(r"[^\w\-]", "_", str(chunk.get("section_id") or f"chunk{idx}"))[:60]
        fetch_path = re.sub(r"[^\w\-]", "_", str(chunk.get("fetch_path") or "unknown"))[:40]
        ext = ".md" if str(chunk.get("content_format") or "") == "markdown" else ".html"
        chunk_name = f"04_chunk_{idx:02d}_{sid}__{fetch_path}{ext}"
        _write_text(
            os.path.join(extract_dir, chunk_name),
            str(chunk.get("content") or ""),
        )
        source_sid = str(chunk.get("section_id") or sid)
        source_md_path = os.path.join(md_slices_dir, f"{source_sid}.md")
        if os.path.isfile(source_md_path):
            try:
                with open(source_md_path, encoding="utf-8") as sf:
                    source_md = sf.read()
                _write_text(
                    os.path.join(extract_dir, f"03_source_md_{sid}.md"),
                    source_md,
                )
            except OSError:
                pass

    _write_text(os.path.join(extract_dir, "05_merged_raw.html"), merged_content or "")
    if prepared_html:
        _write_text(os.path.join(extract_dir, "06_prepared.html"), prepared_html)
    if formatted_synr:
        _write_text(os.path.join(extract_dir, "07_formatted_synr.html"), formatted_synr)
    if final_method:
        _write_json(os.path.join(extract_dir, "08_final_method.json"), final_method)

    payload = {
        "stage": stage,
        "group_index": group_index,
        "request_sections": request_sections or [],
        "group": group or {},
        "resolved_items": resolved_items or [],
        "chunk_results": [
            {k: v for k, v in (c or {}).items() if k != "content"}
            for c in (chunk_results or [])
        ],
        "fetch_traces": traces,
        "merged_len": len(merged_content or ""),
        "prepared_len": len(prepared_html or ""),
        "formatted_len": len(formatted_synr or ""),
    }
    _write_json(os.path.join(extract_dir, "extract_request.json"), payload)

    report_path = build_pipeline_report_html(session_id, group_index=group_index)
    logger.info("[section-debug] 已保存提取调试 stage=%s dir=%s report=%s", stage, extract_dir, report_path)
    return extract_dir


def build_pipeline_report_html(session_id: str, *, group_index: int = 0) -> str:
    """生成单页 HTML 对比报告，标出各步骤长度骤降。"""
    if not section_extract_debug_enabled():
        return ""
    root = debug_session_dir(session_id)
    extract_dir = os.path.join(root, "extract")
    report_path = os.path.join(extract_dir, "pipeline_report.html")

    sections_path = os.path.join(root, "04_sections.json")
    sections: List[dict] = []
    if os.path.isfile(sections_path):
        try:
            with open(sections_path, encoding="utf-8") as f:
                sections = json.load(f) or []
        except Exception:
            sections = []

    traces: List[dict] = []
    trace_path = os.path.join(extract_dir, "02_fetch_trace.json")
    if os.path.isfile(trace_path):
        try:
            with open(trace_path, encoding="utf-8") as f:
                traces = json.load(f) or []
        except Exception:
            traces = []

    def _read(name: str) -> str:
        p = os.path.join(extract_dir, name)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return ""
        return ""

    merged = _read("05_merged_raw.html")
    prepared = _read("06_prepared.html")
    formatted = _read("07_formatted_synr.html")

    steps = [
        ("section.text (节内)", sum(len(str(s.get("text") or "")) for s in sections if isinstance(s, dict))),
        ("expected_span (应有)", sum(len(str(t.get("expected_span") or "")) for t in traces)),
        ("chunk 实际抓取", sum(int(t.get("chunk_len") or 0) for t in traces)),
        ("05_merged_raw", len(merged)),
        ("06_prepared", len(prepared)),
        ("07_formatted_synr", len(formatted)),
    ]

    rows_html: List[str] = []
    prev_len = None
    for label, ln in steps:
        flag = ""
        if prev_len is not None and ln > 0 and prev_len > 0 and ln < prev_len * 0.5:
            flag = ' class="warn"'
        rows_html.append(
            f"<tr{flag}><td>{html_module.escape(label)}</td>"
            f"<td>{ln}</td>"
            f"<td>{_plain_len(merged) if 'merged' in label else ''}</td></tr>"
        )
        if ln > 0:
            prev_len = ln

    trace_rows: List[str] = []
    for tr in traces:
        sid = html_module.escape(str(tr.get("section_id") or ""))
        title = html_module.escape(str(tr.get("title") or "")[:60])
        path = html_module.escape(str(tr.get("fetch_path") or ""))
        fmt = html_module.escape(str(tr.get("content_format") or ""))
        md_len = int(tr.get("node_slices_md_len") or 0)
        sec_text_len = int(tr.get("section_text_len") or 0)
        exp_len = int(tr.get("expected_span_len") or 0)
        chunk_len = int(tr.get("chunk_len") or 0)
        warn = ""
        if md_len > 80 and chunk_len < md_len * 0.5:
            warn = ' class="warn"'
        elif path not in ("node_slices_md", "minio_md", "full_markdown_slice") and md_len > chunk_len:
            warn = ' class="warn"'
        trace_rows.append(
            f"<tr{warn}><td>{sid}</td><td>{title}</td><td><code>{path}</code></td>"
            f"<td>{fmt}</td><td>{md_len}</td><td>{sec_text_len}</td>"
            f"<td>{chunk_len}</td>"
            f"<td>{html_module.escape(str(tr.get('fetch_warning') or ''))}</td></tr>"
        )

    chunk_blocks: List[str] = []
    if os.path.isdir(extract_dir):
        for fn in sorted(os.listdir(extract_dir)):
            if fn.startswith("04_chunk_") and (fn.endswith(".html") or fn.endswith(".md")):
                chunk_blocks.append(
                    f"<details><summary>{html_module.escape(fn)}</summary>"
                    f"<iframe src=\"{html_module.escape(fn)}\" style=\"width:100%;height:280px;border:1px solid #ccc\"></iframe></details>"
                )
            elif fn.startswith("03_source_md_") and fn.endswith(".md"):
                chunk_blocks.append(
                    f"<details><summary>解析源 {html_module.escape(fn)}</summary>"
                    f"<iframe src=\"{html_module.escape(fn)}\" style=\"width:100%;height:280px;border:1px solid #e8f5e9\"></iframe></details>"
                )

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>章节提取 Pipeline 报告 — {html_module.escape(session_id)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:16px;max-width:1200px}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
th,td{{border:1px solid #ddd;padding:8px;text-align:left}}
th{{background:#f5f5f5}}
.warn{{background:#fff3cd}}
h2{{margin-top:24px}}
iframe{{background:#fff}}
</style></head><body>
<h1>章节提取 Pipeline 诊断</h1>
<p>session: <code>{html_module.escape(session_id)}</code> · group_index: {group_index}</p>
<h2>长度汇总（标黄 = 相对上一步骤降 &gt;50%）</h2>
<table><tr><th>步骤</th><th>字符数</th><th>备注</th></tr>
{"".join(rows_html)}
</table>
<h2>逐节抓取轨迹（标黄 = chunk 远短于 node_slices_md）</h2>
<p>正常应为 <code>fetch_path=node_slices_md</code>，且 chunk_len ≈ node_slices_md_len。勿与 section.text 对比（后者常为残片）。</p>
<table><tr><th>section_id</th><th>标题</th><th>fetch_path</th><th>格式</th><th>md_slice</th><th>sec.text</th><th>chunk</th><th>警告</th></tr>
{"".join(trace_rows) if trace_rows else "<tr><td colspan=8>无</td></tr>"}
</table>
<h2>各节 chunk 与解析源对照</h2>
{"".join(chunk_blocks) if chunk_blocks else "<p>无 chunk 文件</p>"}
<h2>合并与后处理预览</h2>
<details open><summary>05_merged_raw ({len(merged)} 字)</summary>
<iframe src="05_merged_raw.html" style="width:100%;height:320px;border:1px solid #ccc"></iframe></details>
<details><summary>07_formatted_synr ({len(formatted)} 字)</summary>
<iframe src="07_formatted_synr.html" style="width:100%;height:320px;border:1px solid #ccc"></iframe></details>
</body></html>"""
    _write_text(report_path, html_doc)
    return report_path
