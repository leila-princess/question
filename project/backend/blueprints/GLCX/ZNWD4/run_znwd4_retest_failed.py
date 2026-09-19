"""
ZNWD4 失败题复测：从上次基准 results JSON 抽取判错题，重跑并对比提升。

用法（在 backend 目录）：

  # 1) 仅从基线导出失败题清单（不调用 LLM）
  python blueprints/GLCX/ZNWD4/run_znwd4_retest_failed.py --export-only

  # 2) 复测全部失败题（默认基线 qa_benchmark_results_qwen25_14b_awq.json）
  python blueprints/GLCX/ZNWD4/run_znwd4_retest_failed.py

  # 3) 指定基线与输出 tag
  python blueprints/GLCX/ZNWD4/run_znwd4_retest_failed.py \\
    --baseline blueprints/GLCX/ZNWD4/qa_benchmark_results_qwen25_14b_awq.json \\
    --output-tag retest_after_p0 \\
    --limit 20

  # 4) 仅生成报告（已有复测 results）
  python blueprints/GLCX/ZNWD4/run_znwd4_retest_failed.py --report-only --output-tag retest_after_p0

输出（ZNWD4 目录）：
  qa_failed_from_baseline[_tag].json     失败题子集（可复用）
  qa_retest_failed_results[_tag].json   复测原始结果 + 基线对照
  qa_retest_failed_report[_tag].md       修复率 / 仍失败 / 分场景对比
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("znwd4_retest_failed")

DEFAULT_BASELINE = os.path.join(_HERE, "qa_benchmark_results_qwen25_14b_awq.json")

from blueprints.GLCX.ZNWD1.run_znwd1_qa_benchmark import (  # noqa: E402
    _is_out_of_scope,
    _threshold_for,
    analyze_tool_usage,
    score_answer,
)
from blueprints.GLCX.ZNWD4.run_znwd4_qa_benchmark import (  # noqa: E402
    bootstrap_pipeline_deps,
    call_znwd4_http,
    call_znwd4_pipeline,
)

_OUTPUT: Dict[str, str] = {}


def _suffix(tag: str) -> str:
    tag = (tag or "").strip()
    if not tag:
        return ""
    return tag if tag.startswith("_") else f"_{tag}"


def _set_output_paths(tag: str = "") -> None:
    sfx = _suffix(tag)
    _OUTPUT["failed_subset"] = os.path.join(_HERE, f"qa_failed_from_baseline{sfx}.json")
    _OUTPUT["results"] = os.path.join(_HERE, f"qa_retest_failed_results{sfx}.json")
    _OUTPUT["report"] = os.path.join(_HERE, f"qa_retest_failed_report{sfx}.md")


def load_baseline(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_failed_items(
    baseline: Dict[str, Any],
    *,
    include_pipeline_error: bool = False,
) -> List[Dict[str, Any]]:
    """从基线 results 抽取判错题（默认仅 status=ok 且 correct=false）。"""
    out: List[Dict[str, Any]] = []
    for it in baseline.get("items") or []:
        if not it.get("question"):
            continue
        status = str(it.get("status") or "ok")
        if status == "error" and not include_pipeline_error:
            continue
        if it.get("correct"):
            continue
        out.append(dict(it))
    return out


def build_failed_subset(
    failed: List[Dict[str, Any]],
    baseline: Dict[str, Any],
) -> Dict[str, Any]:
    pairs = []
    for it in failed:
        pairs.append(
            {
                "question": it.get("question") or "",
                "answer": it.get("expected") or "",
                "category": it.get("category") or "",
                "baseline_index": it.get("index"),
                "baseline_score": it.get("score"),
                "baseline_error_type": it.get("error_type") or "",
            }
        )
    return {
        "source_run_id": baseline.get("run_id"),
        "source_finished_at": baseline.get("finished_at") or baseline.get("updated_at"),
        "source_results": baseline.get("output_tag") or os.path.basename(DEFAULT_BASELINE),
        "failed_count": len(pairs),
        "pairs": pairs,
    }


def export_failed_subset(baseline_path: str, tag: str = "") -> str:
    baseline = load_baseline(baseline_path)
    failed = extract_failed_items(baseline)
    subset = build_failed_subset(failed, baseline)
    subset["exported_at"] = datetime.now().isoformat()
    path = _OUTPUT.get("failed_subset") or os.path.join(_HERE, "qa_failed_from_baseline.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(subset, f, ensure_ascii=False, indent=2)
    logger.info("已导出 %d 道失败题 -> %s", len(failed), path)
    return path


def _baseline_map(failed: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(it.get("question") or ""): it for it in failed}


def run_retest(args: argparse.Namespace, failed: List[Dict[str, Any]], baseline: Dict[str, Any]) -> Dict[str, Any]:
    if args.limit:
        failed = failed[: args.limit]

    existing: Dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "started_at": datetime.now().isoformat(),
        "engine": "ZNWD4",
        "mode": args.mode,
        "retest_kind": "failed_only",
        "baseline_run_id": baseline.get("run_id"),
        "baseline_results_path": os.path.abspath(args.baseline),
        "baseline_failed_count": len(extract_failed_items(baseline)),
        "llm_model": getattr(args, "llm_model", "") or "",
        "llm_base_url": getattr(args, "llm_base_url", "") or "",
        "output_tag": getattr(args, "output_tag", "") or "",
        "items": [],
    }

    if args.resume:
        path = _OUTPUT.get("results") or ""
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
    done = {it["question"] for it in existing.get("items", []) if it.get("question")}

    deps: Optional[Dict[str, Any]] = None
    if args.mode == "pipeline":
        logger.info("初始化 Neo4j / LLM …")
        deps = bootstrap_pipeline_deps(
            chroma_mode=args.chroma_mode,
            llm_base_url=getattr(args, "llm_base_url", "") or "",
            llm_model=getattr(args, "llm_model", "") or "",
            llm_api_key=getattr(args, "llm_api_key", "") or "",
        )

    base_by_q = _baseline_map(extract_failed_items(baseline))
    total = len(failed)
    logger.info("待复测 %d 题（基线失败 %d 题，已跳过 %d）", total, existing.get("baseline_failed_count"), len(done))

    for idx, base_it in enumerate(failed, 1):
        q = str(base_it.get("question") or "")
        if not q or q in done:
            continue

        expected = base_it.get("expected") or ""
        category = base_it.get("category") or ""
        session_id = f"retest4-{uuid.uuid4().hex[:12]}"

        item: Dict[str, Any] = {
            "index": idx,
            "question": q,
            "expected": expected,
            "category": category,
            "session_id": session_id,
            "out_of_scope": _is_out_of_scope(category),
            "baseline": {
                "index": base_it.get("index"),
                "score": base_it.get("score"),
                "correct": base_it.get("correct"),
                "error_type": base_it.get("error_type") or "",
                "actual_preview": (base_it.get("actual") or "")[:300],
                "route_id": base_it.get("route_id") or "",
            },
        }

        try:
            telemetry: Dict[str, Any] = {}
            if args.mode == "http":
                actual, thinking, elapsed = call_znwd4_http(
                    args.base_url, q, session_id, timeout=args.timeout
                )
            else:
                assert deps is not None
                actual, thinking, elapsed, telemetry = call_znwd4_pipeline(
                    q,
                    session_id,
                    client=deps["client"],
                    model=deps["model"],
                    driver=deps["driver"],
                    chroma=deps["chroma"],
                    llm_limits=deps["llm_limits"],
                )
            sr = score_answer(expected, actual, category)
            tool_info = analyze_tool_usage(
                category=category,
                question=q,
                telemetry=telemetry,
                correct=sr.correct,
            )
            err_type = sr.error_type
            if tool_info.get("failure_due_to_no_tool"):
                err_type = "tool_not_called"

            fixed = bool(sr.correct)
            item.update(
                {
                    "actual": actual,
                    "thinking_preview": (thinking or "")[:500],
                    "elapsed_sec": round(elapsed, 2),
                    "score": sr.score,
                    "correct": sr.correct,
                    "fixed": fixed,
                    "still_wrong": not fixed,
                    "score_delta": round(sr.score - float(base_it.get("score") or 0), 4),
                    "error_type": err_type,
                    "baseline_error_type": base_it.get("error_type") or "",
                    "missing_items": sr.missing_items,
                    "recall": sr.recall,
                    "similarity": sr.similarity,
                    "threshold": _threshold_for(category),
                    "status": "ok",
                    **tool_info,
                }
            )
            if telemetry.get("route_id"):
                item["route_id"] = telemetry["route_id"]

            mark = "FIXED" if fixed else "STILL"
            logger.info(
                "[%d/%d] %s score=%.2f (was %.2f) | %s | %s",
                idx,
                total,
                mark,
                sr.score,
                float(base_it.get("score") or 0),
                category[:18],
                q[:36],
            )
        except Exception as e:
            logger.exception("复测失败: %s", q[:50])
            item.update(
                {
                    "actual": "",
                    "score": 0.0,
                    "correct": False,
                    "fixed": False,
                    "still_wrong": True,
                    "error_type": "pipeline_error",
                    "status": "error",
                    "error_message": str(e),
                }
            )

        existing.setdefault("items", []).append(item)
        if idx % args.checkpoint == 0:
            _save_json(_OUTPUT["results"], existing)

        if args.delay > 0:
            time.sleep(args.delay)

    existing["finished_at"] = datetime.now().isoformat()
    if deps:
        existing["llm_model"] = deps.get("model") or existing.get("llm_model")
        existing["llm_base_url"] = deps.get("llm_base_url") or existing.get("llm_base_url")
    _save_json(_OUTPUT["results"], existing)
    return existing


def _save_json(path: str, data: Dict[str, Any]) -> None:
    data["updated_at"] = datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_retest_report(results: Dict[str, Any]) -> str:
    items = [i for i in (results.get("items") or []) if i.get("status") == "ok"]
    errors = [i for i in (results.get("items") or []) if i.get("status") == "error"]
    fixed = [i for i in items if i.get("fixed")]
    still = [i for i in items if i.get("still_wrong")]
    n = len(items)
    fix_rate = len(fixed) / n if n else 0.0

    by_cat: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "fixed": 0, "still": 0})
    for it in items:
        cat = re.sub(r"-变体.*", "", it.get("category") or "unknown")
        by_cat[cat]["total"] += 1
        if it.get("fixed"):
            by_cat[cat]["fixed"] += 1
        else:
            by_cat[cat]["still"] += 1

    err_before = Counter((it.get("baseline") or {}).get("error_type") or "unknown" for it in still)
    err_after = Counter(it.get("error_type") or "unknown" for it in still)

    lines = [
        "# ZNWD4 失败题复测报告",
        "",
        f"- 复测运行 ID：`{results.get('run_id', '')}`",
        f"- 基线 run ID：`{results.get('baseline_run_id', '')}`",
        f"- 基线 results：`{results.get('baseline_results_path', '')}`",
        f"- 基线失败题数：**{results.get('baseline_failed_count', '?')}**",
        f"- 本次复测：**{len(results.get('items') or [])}** 题（成功 {n}，异常 {len(errors)}）",
        f"- 开始：{results.get('started_at', '')}",
        f"- 结束：{results.get('finished_at', results.get('updated_at', ''))}",
    ]
    if results.get("llm_model"):
        lines.append(f"- LLM：`{results.get('llm_model')}` @ `{results.get('llm_base_url', '')}`")
    lines.extend(
        [
            "",
            "## 总体对比",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 复测成功 | {n} |",
            f"| **已修复**（基线错 → 现对） | **{len(fixed)}** |",
            f"| 仍失败 | {len(still)} |",
            f"| **修复率** | **{fix_rate:.1%}** |",
            "",
        ]
    )

    if items:
        avg_delta = sum(i.get("score_delta", 0) for i in items) / len(items)
        avg_time = sum(i.get("elapsed_sec", 0) for i in items) / len(items)
        lines.extend(
            [
                f"| 平均得分变化 | {avg_delta:+.3f} |",
                f"| 平均耗时 | {avg_time:.1f}s |",
                "",
            ]
        )

    lines.extend(
        [
            "## 分场景修复情况",
            "",
            "| 场景 | 复测题数 | 已修复 | 仍失败 | 修复率 |",
            "|------|----------|--------|--------|--------|",
        ]
    )
    for cat, st in sorted(by_cat.items(), key=lambda x: -x[1]["total"]):
        t, fx = st["total"], st["fixed"]
        rate = fx / t if t else 0
        lines.append(f"| {cat} | {t} | {fx} | {t - fx} | {rate:.1%} |")

    lines.extend(["", "## 仍失败题的错误类型（复测后）", ""])
    for et, cnt in err_after.most_common():
        lines.append(f"- `{et}`：{cnt} 题")

    lines.extend(["", "## 仍失败题的原错误类型（基线）", ""])
    for et, cnt in err_before.most_common():
        lines.append(f"- `{et}`：{cnt} 题")

    lines.extend(["", "## 已修复样例（前 15）", ""])
    for i, it in enumerate(fixed[:15], 1):
        lines.extend(
            [
                f"### {i}. [{it.get('category', '')}] score {it.get('baseline', {}).get('score', 0):.2f} → {it.get('score', 0):.2f}",
                f"**问：** {it.get('question', '')}",
                f"**期望：** {(it.get('expected') or '')[:200]}",
                f"**现答：** {(it.get('actual') or '')[:400]}",
                "",
            ]
        )

    lines.extend(["", "## 仍失败样例（前 15）", ""])
    for i, it in enumerate(still[:15], 1):
        b = it.get("baseline") or {}
        lines.extend(
            [
                f"### {i}. [{it.get('category', '')}] score {b.get('score', 0):.2f} → {it.get('score', 0):.2f}",
                f"**问：** {it.get('question', '')}",
                f"**期望：** {(it.get('expected') or '')[:200]}",
                f"**现答：** {(it.get('actual') or '')[:400]}",
                f"**基线错因：** `{b.get('error_type', '')}` → **现错因：** `{it.get('error_type', '')}`",
                "",
            ]
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="ZNWD4：仅复测上次基准中的失败题")
    parser.add_argument(
        "--baseline",
        default=DEFAULT_BASELINE,
        help="上次完整基准的 results JSON（默认 qa_benchmark_results_qwen25_14b_awq.json）",
    )
    parser.add_argument("--export-only", action="store_true", help="只导出失败题子集，不跑 LLM")
    parser.add_argument("--report-only", action="store_true", help="仅从已有复测 results 生成报告")
    parser.add_argument("--resume", action="store_true", help="跳过复测 results 中已有题目")
    parser.add_argument("--limit", type=int, default=0, help="仅复测前 N 道失败题")
    parser.add_argument("--mode", choices=("pipeline", "http"), default="pipeline")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--checkpoint", type=int, default=5)
    parser.add_argument("--chroma-mode", choices=("sqlite", "client", "none"), default="client")
    parser.add_argument("--llm-model", default="qwen2.5-14b-awq")
    parser.add_argument("--llm-base-url", default="http://192.168.15.101:8006/v1")
    parser.add_argument("--llm-api-key", default="")
    parser.add_argument("--output-tag", default="after_fix", help="输出文件名后缀，如 after_fix")
    args = parser.parse_args()

    _set_output_paths(args.output_tag)

    if not os.path.isfile(args.baseline):
        print(f"未找到基线 results：{args.baseline}")
        sys.exit(1)

    if args.export_only:
        export_failed_subset(args.baseline, args.output_tag)
        return

    if args.report_only:
        path = _OUTPUT["results"]
        if not os.path.isfile(path):
            print(f"未找到复测 results：{path}")
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            results = json.load(f)
    else:
        baseline = load_baseline(args.baseline)
        failed = extract_failed_items(baseline)
        export_failed_subset(args.baseline, args.output_tag)
        if not failed:
            print("基线中没有失败题，无需复测。")
            sys.exit(0)
        results = run_retest(args, failed, baseline)

    report = build_retest_report(results)
    report_path = _OUTPUT["report"]
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    items = [i for i in results.get("items", []) if i.get("status") == "ok"]
    fixed = sum(1 for i in items if i.get("fixed"))
    n = len(items)
    print(f"\n复测完成：{n} 题，修复 {fixed} 题（{fixed / n:.1%}）" if n else "\n无成功复测项")
    print(f"结果：{_OUTPUT['results']}")
    print(f"报告：{report_path}")


if __name__ == "__main__":
    main()
