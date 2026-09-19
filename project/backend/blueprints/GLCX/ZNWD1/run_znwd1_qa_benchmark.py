"""
ZNWD1 问答基准测试：对 qa_pairs_500 逐题调用流水线，对比期望答案并统计正确率。

用法（在 backend 目录）：
  python blueprints/GLCX/ZNWD1/run_znwd1_qa_benchmark.py
  python blueprints/GLCX/ZNWD1/run_znwd1_qa_benchmark.py --limit 20
  python blueprints/GLCX/ZNWD1/run_znwd1_qa_benchmark.py --resume
  python blueprints/GLCX/ZNWD1/run_znwd1_qa_benchmark.py --mode http --base-url http://127.0.0.1:5000

  python blueprints/GLCX/ZNWD1/run_znwd1_qa_benchmark.py --llm-model qwen2.5-14b-awq --llm-base-url http://192.168.15.101:8006/v1 --output-tag qwen25_14b_awq

输出：
  qa_benchmark_results[_tag].json   逐题明细（可断点续跑）
  qa_benchmark_report[_tag].md      汇总分析与错误标注
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
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("znwd1_benchmark")

QA_FILE = os.path.join(_HERE, "qa_pairs_500.json")
RESULTS_FILE = os.path.join(_HERE, "qa_benchmark_results.json")
REPORT_FILE = os.path.join(_HERE, "qa_benchmark_report.md")

# 运行时输出路径（可由 --output-tag 覆盖）
_OUTPUT: Dict[str, str] = {"results": RESULTS_FILE, "report": REPORT_FILE}

def _resolve_output_paths(tag: str = "") -> Tuple[str, str]:
    tag = (tag or "").strip()
    if not tag:
        return RESULTS_FILE, REPORT_FILE
    suffix = f"_{tag}" if not tag.startswith("_") else tag
    return (
        os.path.join(_HERE, f"qa_benchmark_results{suffix}.json"),
        os.path.join(_HERE, f"qa_benchmark_report{suffix}.md"),
    )


def _set_output_paths(tag: str = "") -> None:
    r, p = _resolve_output_paths(tag)
    _OUTPUT["results"] = r
    _OUTPUT["report"] = p


# 非 ZNWD1 业务能力题（不计入核心正确率，仅作参考）
_OUT_OF_SCOPE_CATEGORIES = frozenset({"元信息"})
_NO_RETRIEVAL_CATEGORIES = frozenset({"闲聊", "元信息"})

# 分类 → 判定阈值（召回率或相似度）
_CATEGORY_THRESHOLD: Dict[str, float] = {
    "闲聊": 0.45,
    "元信息": 0.55,
    "向量库-章节": 0.35,
    "向量库-文档概述": 0.35,
    "向量库-关键词": 0.35,
    "方法-SYNR": 0.50,
    "方法-SYTJ": 0.50,
    "方法-SYGC": 0.50,
    "方法-JGPD": 0.50,
}
_DEFAULT_THRESHOLD = 0.65

_NO_HIT_MARKERS = ("未找到", "未在清单库", "未检索到", "没有相关", "无法找到", "查无")


def _is_out_of_scope(category: str) -> bool:
    return (category or "") in _OUT_OF_SCOPE_CATEGORIES or _category_base(category) in _OUT_OF_SCOPE_CATEGORIES


def _needs_retrieval(category: str) -> bool:
    """该题是否应走 Neo4j/Chroma 检索（非 direct_llm）。"""
    c = (category or "").split("-变体")[0]
    if c in _NO_RETRIEVAL_CATEGORIES:
        return False
    if _category_base(c) in _NO_RETRIEVAL_CATEGORIES:
        return False
    return True


def _needs_chroma(category: str) -> bool:
    c = (category or "").split("-变体")[0]
    return c.startswith("向量库") or c.startswith("标准-") and "章节" in c


def _needs_routing_probe(category: str, question: str) -> bool:
    """路由阶段是否应调用 probe_keyword（装备/环境/项目等实体消歧）。"""
    if not _needs_retrieval(category):
        return False
    q = question or ""
    if re.search(r"(装备|设备|坦克|无人机|雷达|通信|环境|项目|指标|方法|标准|GB/T|GJB)", q):
        return True
    return category.startswith(("装备", "环境", "项目", "指标", "拼装", "标准", "方法"))


def analyze_tool_usage(
    *,
    category: str,
    question: str,
    telemetry: Dict[str, Any],
    correct: bool,
) -> Dict[str, Any]:
    """
    判定工具/检索调用情况。
    - expected_retrieval: 是否应查库
    - retrieval_executed: 是否实际执行了 Neo4j/Chroma 元操作
    - tool_not_called: 应查库但未执行检索（路由 direct_llm 等）
    - routing_probe_expected / routing_probe_called: 路由 probe 工具
    """
    expected = _needs_retrieval(category)
    plan = telemetry.get("route_plan") if isinstance(telemetry.get("route_plan"), dict) else {}
    strategy = str(
        telemetry.get("answer_strategy")
        or plan.get("answer_strategy")
        or ""
    ).strip().lower()
    business_id = str(telemetry.get("business_id") or plan.get("intended_business_id") or "").strip()
    neo_hits = int(telemetry.get("neo_hits") or 0)
    chroma_hits = int(telemetry.get("chroma_hits") or 0)
    retrieval_ran = bool(
        telemetry.get("retrieval_ran")
        or neo_hits > 0
        or chroma_hits > 0
    )
    direct_llm = bool(telemetry.get("direct_llm")) or strategy == "direct_llm" or (
        not business_id and not retrieval_ran
    )

    probe_snap = plan.get("probe_snapshot") if isinstance(plan.get("probe_snapshot"), dict) else {}
    probe_called = bool(
        probe_snap.get("best")
        or probe_snap.get("hits")
        or probe_snap.get("zvec_used")
        or str(plan.get("probe_source") or "").strip() in ("pcsr", "ecs")
    )
    probe_expected = _needs_routing_probe(category, question)
    routing_tools = list(telemetry.get("routing_tools_called") or [])
    if probe_called and "probe_keyword" not in routing_tools:
        routing_tools.append("probe_keyword")
    if plan.get("history_snapshot") and "history_recent" not in routing_tools:
        routing_tools.append("history_recent")

    tool_not_called = expected and not retrieval_ran
    tool_not_called_reason = ""
    if tool_not_called:
        if direct_llm or strategy == "direct_llm":
            tool_not_called_reason = "routing_direct_llm"
        elif not business_id:
            tool_not_called_reason = "no_business_id"
        else:
            tool_not_called_reason = "retrieval_skipped"

    routing_probe_missed = probe_expected and not probe_called and expected and not correct

    failure_due_to_no_tool = tool_not_called and not correct

    return {
        "expected_retrieval": expected,
        "expected_chroma": _needs_chroma(category),
        "retrieval_executed": retrieval_ran,
        "direct_llm": direct_llm,
        "answer_strategy": strategy,
        "business_id": business_id,
        "neo_hits": neo_hits,
        "chroma_hits": chroma_hits,
        "routing_tools_called": routing_tools,
        "routing_probe_expected": probe_expected,
        "routing_probe_called": probe_called,
        "routing_zvec_used": bool(probe_snap.get("zvec_used")),
        "routing_probe_missed": routing_probe_missed,
        "tool_not_called": tool_not_called,
        "tool_not_called_reason": tool_not_called_reason,
        "failure_due_to_no_tool": failure_due_to_no_tool,
    }


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", "", t)
    t = t.replace("测试", "试验")
    return t


def _extract_list_items(text: str) -> List[str]:
    """从答案中提取可比对实体（名称、指标等）。"""
    items: List[str] = []
    if not text:
        return items
    # 编号列表 1. xxx
    for m in re.finditer(r"(?:^|\n)\s*\d+[\.、．]\s*([^\n，,；;]{2,80})", text):
        items.append(m.group(1).strip())
    # 「有：/包括：」后的顿号清单
    for m in re.finditer(r"(?:有|包括|为|含)[：:]\s*([^。\n]{4,500})", text):
        segment = m.group(1)
        for part in re.split(r"[、，]", segment):
            part = re.sub(r"^[\d\.．、\s\-]+", "", part.strip())
            part = re.sub(r"(等|共).*?$", "", part).strip()
            if 2 <= len(part) <= 60:
                items.append(part)
    # 兜底：首句顿号分隔
    if not items and ("、" in text or "，" in text):
        chunk = re.split(r"[。！？\n]", text)[0]
        for part in re.split(r"[、，]", chunk):
            part = re.sub(r"^[\d\.．、\s\-]+", "", part.strip())
            part = re.sub(r"(共|包括|有|等).*$", "", part).strip()
            if 2 <= len(part) <= 60 and not part.startswith("共"):
                items.append(part)
    seen: Set[str] = set()
    out: List[str] = []
    for it in items:
        k = _normalize(it)
        if k and k not in seen and len(k) >= 2:
            seen.add(k)
            out.append(it)
    return out


def _extract_key_numbers(text: str) -> List[str]:
    return re.findall(r"\d+", text or "")


def _item_in_answer(item: str, answer: str) -> bool:
    ni = _normalize(item)
    na = _normalize(answer)
    if not ni:
        return False
    if ni in na:
        return True
    # 子串：去掉括号内容再比
    core = re.sub(r"[（(][^）)]*[）)]", "", item)
    return _normalize(core) in na if core else False


def _equip_env_score_adjustment(expected: str, actual: str, category: str) -> Tuple[float, List[str]]:
    """
    装备+环境→项目：须出现问句中的装备名，且不能误答成全库枚举（共 N 条且 N 远大于期望项数）。
    返回 (扣分, 备注项)。
    """
    if "装备环境" not in (category or ""):
        return 0.0, []
    exp = re.sub(r"\s+", "", (expected or "").strip())
    act = (actual or "").strip()
    notes: List[str] = []
    penalty = 0.0

    m = re.match(r"^(.+?)在(.+?)环境", exp)
    if m:
        equip, env = m.group(1).strip(), m.group(2).strip()
        if equip and _normalize(equip) not in _normalize(act):
            penalty += 0.55
            notes.append(f"缺少装备「{equip}」")
        if env and _normalize(env) not in _normalize(act):
            penalty += 0.15
            notes.append(f"缺少环境「{env}」")

    exp_items = _extract_list_items(expected)
    count_m = re.search(r"共\s*(\d+)\s*条", act)
    if count_m and exp_items:
        listed = int(count_m.group(1))
        if listed > max(len(exp_items) * 2 + 2, 8):
            penalty += 0.45
            notes.append(f"疑似全库枚举({listed}条，期望约{len(exp_items)}条)")

    return min(penalty, 0.95), notes


def _category_base(category: str) -> str:
    return (category or "").split("-变体")[0].split("-")[0]


def _threshold_for(category: str) -> float:
    c = category or ""
    if c in _CATEGORY_THRESHOLD:
        return _CATEGORY_THRESHOLD[c]
    for prefix, th in _CATEGORY_THRESHOLD.items():
        if c.startswith(prefix):
            return th
    base = _category_base(c)
    if base in ("枚举", "装备", "环境", "项目", "指标", "拼装", "标准", "反向", "属性"):
        return _DEFAULT_THRESHOLD
    return _DEFAULT_THRESHOLD


@dataclass
class ScoreResult:
    score: float
    correct: bool
    error_type: str = ""
    missing_items: List[str] = field(default_factory=list)
    extra_notes: str = ""
    recall: float = 0.0
    similarity: float = 0.0


def score_answer(expected: str, actual: str, category: str = "") -> ScoreResult:
    exp = (expected or "").strip()
    act = (actual or "").strip()
    th = _threshold_for(category)

    if not act:
        return ScoreResult(0.0, False, "empty_answer", extra_notes="实际回答为空")

    act_no_hit = any(m in act for m in _NO_HIT_MARKERS)
    exp_no_hit = any(m in exp for m in _NO_HIT_MARKERS)
    if act_no_hit and not exp_no_hit:
        return ScoreResult(0.15, False, "no_retrieval", extra_notes="系统返回未命中/未找到")

    sim = SequenceMatcher(None, _normalize(exp), _normalize(act)).ratio()

    exp_items = _extract_list_items(exp)
    if not exp_items and len(exp) > 20:
        # 长文本：取前若干中文词块作关键点
        exp_items = [w for w in re.findall(r"[\u4e00-\u9fff]{3,12}", exp) if len(w) >= 3][:15]

    recall = 0.0
    missing: List[str] = []
    if exp_items:
        hit = [it for it in exp_items if _item_in_answer(it, act)]
        missing = [it for it in exp_items if it not in hit]
        recall = len(hit) / len(exp_items)

    # 元信息：数字一致加分
    num_bonus = 0.0
    if category == "元信息" or _category_base(category) == "元信息":
        en = set(_extract_key_numbers(exp))
        an = set(_extract_key_numbers(act))
        if en and en <= an:
            num_bonus = 0.3

    # 闲聊：关键词
    chat_bonus = 0.0
    if category == "闲聊":
        kws = ("助手", "问答", "查询", "试验", "帮助", "您好", "你好", "可以")
        if any(k in act for k in kws):
            chat_bonus = 0.4

    combined = max(sim, recall, sim * 0.4 + recall * 0.6) + num_bonus + chat_bonus
    eq_penalty, eq_notes = _equip_env_score_adjustment(exp, act, category)
    if eq_penalty:
        combined = max(0.0, combined - eq_penalty)
    combined = min(combined, 1.0)
    correct = combined >= th

    error_type = ""
    if not correct:
        if recall < 0.4 and exp_items:
            error_type = "missing_entities"
        elif act_no_hit:
            error_type = "no_retrieval"
        elif sim < 0.3:
            error_type = "content_mismatch"
        else:
            error_type = "partial_match"

    extra_notes = ""
    if eq_notes:
        extra_notes = "; ".join(eq_notes)

    return ScoreResult(
        score=round(combined, 4),
        correct=correct,
        error_type=error_type,
        missing_items=(missing + eq_notes)[:10],
        extra_notes=extra_notes,
        recall=round(recall, 4),
        similarity=round(sim, 4),
    )


def load_qa_pairs(path: str = QA_FILE) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("pairs") or [])


def _parse_sse_events(raw: str) -> Tuple[str, str]:
    """从 SSE 文本提取 fullAnswer 与拼接的 answer 片段。"""
    full_answer = ""
    chunks: List[str] = []
    event_type = ""
    for line in raw.splitlines():
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event_type == "answer":
                chunks.append(str(obj.get("content") or ""))
            elif event_type == "done":
                fa = str(obj.get("fullAnswer") or "")
                if fa:
                    full_answer = fa
    return full_answer or "".join(chunks), full_answer


def call_znwd1_http(base_url: str, question: str, session_id: str, timeout: int) -> Tuple[str, str, float]:
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/api/znwd1/chat/stream"
    body = json.dumps(
        {
            "question": question,
            "sessionId": session_id,
            "userId": "znwd1_benchmark",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from e
    elapsed = time.time() - t0
    answer, _ = _parse_sse_events(raw)
    return answer, "", elapsed


def call_znwd1_pipeline(
    question: str,
    session_id: str,
    *,
    client: Any,
    model: str,
    driver: Any,
    chroma: Any,
    llm_limits: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, float, Dict[str, Any]]:
    from blueprints.GLCX.ZNWD1.pipeline import stream_znwd1_chat
    from blueprints.GLCX.ZNWD1.znwd1_langchain_routing import ROUTE_PLAN_INTERNAL

    t0 = time.time()
    chunks: List[str] = []
    full_answer = ""
    thinking_parts: List[str] = []
    telemetry: Dict[str, Any] = {
        "route_plan": {},
        "answer_strategy": "",
        "business_id": "",
        "user_biz": "",
        "scenario": "",
        "neo_hits": 0,
        "chroma_hits": 0,
        "retrieval_ran": False,
        "direct_llm": False,
        "routing_tools_called": [],
    }
    for event_type, evt_data in stream_znwd1_chat(
        client,
        model,
        question,
        user_id="znwd1_benchmark",
        session_id=session_id,
        neo4j_driver=driver,
        chroma_collection=chroma,
        llm_limits=llm_limits,
        skip_instant_thinking=True,
    ):
        if event_type == ROUTE_PLAN_INTERNAL and isinstance(evt_data, dict):
            plan = evt_data.get("plan") or {}
            telemetry["route_plan"] = plan
            telemetry["answer_strategy"] = str(plan.get("answer_strategy") or "")
            if plan.get("probe_snapshot"):
                telemetry["routing_tools_called"].append("probe_keyword")
            if plan.get("history_snapshot"):
                telemetry["routing_tools_called"].append("history_recent")
            continue
        if event_type == "step" and isinstance(evt_data, dict):
            st = str(evt_data.get("type") or "")
            if st == "routing" and evt_data.get("status") == "done":
                telemetry["business_id"] = str(evt_data.get("businessId") or telemetry.get("business_id") or "")
                telemetry["user_biz"] = str(evt_data.get("userBiz") or "")
                telemetry["scenario"] = str(evt_data.get("scenario") or "")
            if st == "retrieval":
                if evt_data.get("status") == "loading":
                    telemetry["retrieval_ran"] = True
                if evt_data.get("status") == "done":
                    telemetry["retrieval_ran"] = True
                    telemetry["neo_hits"] = int(evt_data.get("neoHits") or telemetry.get("neo_hits") or 0)
                    telemetry["chroma_hits"] = int(evt_data.get("chromaHits") or telemetry.get("chroma_hits") or 0)
            continue
        if event_type == "answer" and isinstance(evt_data, dict):
            chunks.append(str(evt_data.get("content") or ""))
        elif event_type == "thinking" and isinstance(evt_data, dict):
            thinking_parts.append(str(evt_data.get("content") or ""))
        elif event_type == "done" and isinstance(evt_data, dict):
            full_answer = str(evt_data.get("fullAnswer") or "")
        elif event_type == "error" and isinstance(evt_data, dict):
            raise RuntimeError(str(evt_data.get("message") or "pipeline error"))
    elapsed = time.time() - t0
    answer = full_answer or "".join(chunks)
    thinking = "\n".join(thinking_parts)
    if not telemetry["retrieval_ran"] and (
        telemetry.get("answer_strategy") == "direct_llm" or not telemetry.get("business_id")
    ):
        telemetry["direct_llm"] = True
    return answer, thinking, elapsed, telemetry


def _patch_chroma_sqlite_query(chroma_path: Optional[str] = None) -> None:
    """用 sqlite 关键词检索替代 Chroma ONNX，避免 benchmark 进程崩溃。"""
    from blueprints.GLCX.ZNWD1 import executor as ex_mod
    from blueprints.GLCX.ZNWD1.generate_qa_pairs import fetch_chroma_data_sqlite

    data = fetch_chroma_data_sqlite(chroma_path)
    chunks = data.get("chunks") or []

    def _sqlite_chroma_query(collection, query, top_k=12, doc_id="", title_keyword=""):
        _ = collection
        q = (query or "").strip()
        if not q:
            return []
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for ch in chunks:
            if doc_id and str(ch.get("doc_id") or "") != doc_id:
                continue
            title = str(ch.get("title") or "")
            if title_keyword and title_keyword not in title:
                continue
            content = str(ch.get("content") or "")
            text = title + content
            hit = sum(1 for w in re.findall(r"[\u4e00-\u9fff]{2,8}", q) if w in text)
            if hit == 0 and q[:6] not in text:
                continue
            scored.append((float(hit), {
                "doc_id": ch.get("doc_id", ""),
                "title": title,
                "content": content[:800],
                "distance": 1.0 / (hit + 1),
                "metadata": {k: v for k, v in ch.items() if k not in ("content",)},
            }))
        scored.sort(key=lambda x: -x[0])
        return [x[1] for x in scored[: max(1, min(top_k, 50))]]

    ex_mod._chroma_vector_query = _sqlite_chroma_query  # type: ignore[attr-defined]
    logger.info("已启用 Chroma sqlite 检索补丁（%d 片段）", len(chunks))


def bootstrap_pipeline_deps(
    *,
    chroma_mode: str = "sqlite",
    llm_base_url: str = "",
    llm_model: str = "",
    llm_api_key: str = "",
) -> Dict[str, Any]:
    """轻量初始化：Neo4j + OpenAI；Chroma 可选。"""
    from config import Config
    from neo4j import GraphDatabase
    from openai import OpenAI

    driver = GraphDatabase.driver(
        Config.NEO4J_URI,
        auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD),
    )
    with driver.session() as s:
        s.run("RETURN 1")

    base_url = (llm_base_url or Config.OPENAI_BASE_URL or "").strip()
    model = (llm_model or Config.OPENAI_MODEL or "").strip()
    api_key = (llm_api_key if llm_api_key is not None else Config.OPENAI_API_KEY or "").strip()
    if not api_key:
        api_key = Config.effective_openai_api_key() if hasattr(Config, "effective_openai_api_key") else "EMPTY"

    client_kwargs: Dict[str, Any] = {"max_retries": 0}
    if base_url:
        client_kwargs["base_url"] = base_url.rstrip("/")
    if api_key:
        client_kwargs["api_key"] = api_key
    else:
        client_kwargs["api_key"] = "EMPTY"
        client_kwargs["default_headers"] = {"Authorization": ""}
    client = OpenAI(**client_kwargs)
    logger.info("LLM: model=%s base_url=%s", model, base_url or "(default)")

    chroma: Any = object()  # 占位；实际检索走 sqlite 补丁
    if chroma_mode == "sqlite":
        _patch_chroma_sqlite_query()
    elif chroma_mode == "client":
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            cp = os.path.abspath(Config.CHROMA_PATH)
            cc = chromadb.PersistentClient(path=cp, settings=ChromaSettings(anonymized_telemetry=False))
            chroma = cc.get_collection(name=Config.CHROMA_COLLECTION)
        except Exception as e:
            logger.warning("Chroma 客户端不可用，回退 sqlite 补丁: %s", e)
            _patch_chroma_sqlite_query()
    elif chroma_mode == "none":
        chroma = None

    model = model or Config.OPENAI_MODEL
    llm_limits = {
        "context_window": int(getattr(Config, "MAX_CONTEXT_TOKENS", 8192) or 8192),
        "max_output_tokens": 4096,
    }
    return {
        "client": client,
        "model": model,
        "driver": driver,
        "chroma": chroma,
        "llm_limits": llm_limits,
        "llm_base_url": base_url,
    }


def load_existing_results() -> Dict[str, Any]:
    path = _OUTPUT.get("results") or RESULTS_FILE
    if not os.path.isfile(path):
        return {"run_id": str(uuid.uuid4()), "started_at": datetime.now().isoformat(), "items": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_results(data: Dict[str, Any]) -> None:
    data["updated_at"] = datetime.now().isoformat()
    path = _OUTPUT.get("results") or RESULTS_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_benchmark(args: argparse.Namespace) -> Dict[str, Any]:
    pairs = load_qa_pairs(args.qa_file)
    if args.offset:
        pairs = pairs[args.offset :]
    if args.limit:
        pairs = pairs[: args.limit]

    existing = load_existing_results() if args.resume else {
        "run_id": str(uuid.uuid4()),
        "started_at": datetime.now().isoformat(),
        "mode": args.mode,
        "llm_model": getattr(args, "llm_model", "") or "",
        "llm_base_url": getattr(args, "llm_base_url", "") or "",
        "output_tag": getattr(args, "output_tag", "") or "",
        "items": [],
    }
    done_questions = {it["question"] for it in existing.get("items", []) if it.get("question")}

    deps: Optional[Dict[str, Any]] = None
    if args.mode == "pipeline":
        logger.info("初始化 Neo4j / LLM …")
        deps = bootstrap_pipeline_deps(
            chroma_mode=args.chroma_mode,
            llm_base_url=getattr(args, "llm_base_url", "") or "",
            llm_model=getattr(args, "llm_model", "") or "",
            llm_api_key=getattr(args, "llm_api_key", "") or "",
        )

    total = len(pairs)
    logger.info("待测 %d 题（已跳过 %d 题）", total, len(done_questions))

    for idx, pair in enumerate(pairs, 1):
        q = pair["question"]
        if q in done_questions:
            continue

        expected = pair.get("answer") or ""
        category = pair.get("category") or ""
        session_id = f"bench-{uuid.uuid4().hex[:12]}"

        item: Dict[str, Any] = {
            "index": idx,
            "question": q,
            "expected": expected,
            "category": category,
            "session_id": session_id,
            "out_of_scope": _is_out_of_scope(category),
        }

        try:
            telemetry: Dict[str, Any] = {}
            if args.mode == "http":
                actual, thinking, elapsed = call_znwd1_http(
                    args.base_url, q, session_id, timeout=args.timeout
                )
            else:
                assert deps is not None
                actual, thinking, elapsed, telemetry = call_znwd1_pipeline(
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
            item.update(
                {
                    "actual": actual,
                    "thinking_preview": (thinking or "")[:500],
                    "elapsed_sec": round(elapsed, 2),
                    "score": sr.score,
                    "correct": sr.correct,
                    "error_type": err_type,
                    "missing_items": sr.missing_items,
                    "recall": sr.recall,
                    "similarity": sr.similarity,
                    "threshold": _threshold_for(category),
                    "status": "ok",
                    **tool_info,
                }
            )
            mark = "✓" if sr.correct else "✗"
            tool_flag = " [未调工具]" if tool_info.get("tool_not_called") else ""
            logger.info(
                "[%d/%d] %s score=%.2f %s%s | %s",
                idx,
                total,
                mark,
                sr.score,
                category[:20],
                tool_flag,
                q[:40],
            )
        except Exception as e:
            logger.exception("题目失败: %s", q[:50])
            item.update(
                {
                    "actual": "",
                    "score": 0.0,
                    "correct": False,
                    "error_type": "pipeline_error",
                    "status": "error",
                    "error_message": str(e),
                }
            )

        existing.setdefault("items", []).append(item)
        if idx % args.checkpoint == 0:
            save_results(existing)

        if args.delay > 0:
            time.sleep(args.delay)

    existing["finished_at"] = datetime.now().isoformat()
    if deps:
        existing["llm_model"] = deps.get("model") or existing.get("llm_model")
        existing["llm_base_url"] = deps.get("llm_base_url") or existing.get("llm_base_url")
    save_results(existing)
    return existing


def build_report(results: Dict[str, Any]) -> str:
    items = results.get("items") or []
    ok_items = [i for i in items if i.get("status") == "ok"]
    errors = [i for i in items if i.get("status") == "error"]
    correct = [i for i in ok_items if i.get("correct")]
    wrong = [i for i in ok_items if not i.get("correct")]
    in_scope = [i for i in ok_items if not _is_out_of_scope(i.get("category") or "")]
    in_scope_correct = [i for i in in_scope if i.get("correct")]
    oos = [i for i in ok_items if _is_out_of_scope(i.get("category") or "")]

    total = len(items)
    acc = len(correct) / len(ok_items) if ok_items else 0.0
    acc_core = len(in_scope_correct) / len(in_scope) if in_scope else 0.0

    need_ret = [i for i in ok_items if i.get("expected_retrieval")]
    tool_not_called = [i for i in ok_items if i.get("tool_not_called")]
    tool_not_called_wrong = [i for i in tool_not_called if not i.get("correct")]
    tool_called_wrong = [i for i in wrong if i.get("retrieval_executed")]
    probe_missed = [i for i in ok_items if i.get("routing_probe_missed")]
    # 排除「未调工具」题后的参考正确率
    called_and_scored = [
        i for i in ok_items
        if i.get("retrieval_executed") or not i.get("expected_retrieval")
    ]
    adj_ok = [i for i in called_and_scored if i.get("correct")]
    acc_adj = len(adj_ok) / len(called_and_scored) if called_and_scored else 0.0

    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for it in ok_items:
        base = it.get("category") or "unknown"
        # 合并变体
        base = re.sub(r"-变体.*", "", base)
        by_cat[base].append(it)

    lines = [
        "# ZNWD1 问答基准测试报告",
        "",
        f"- 运行 ID：`{results.get('run_id', '')}`",
        f"- 开始：{results.get('started_at', '')}",
        f"- 结束：{results.get('finished_at', results.get('updated_at', ''))}",
        f"- 模式：{results.get('mode', 'pipeline')}",
    ]
    if results.get("llm_model"):
        lines.append(f"- LLM 模型：`{results.get('llm_model')}`")
    if results.get("llm_base_url"):
        lines.append(f"- LLM 地址：`{results.get('llm_base_url')}`")
    lines.extend(["", "## 总体指标", ""])
    lines.extend([
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总题数 | {total} |",
        f"| 成功调用 | {len(ok_items)} |",
        f"| 调用失败 | {len(errors)} |",
        f"| 判定正确 | {len(correct)} |",
        f"| 判定错误 | {len(wrong)} |",
        f"| **正确率（全量）** | **{acc:.1%}** |",
        f"| 业务范围题数 | {len(in_scope)} |",
        f"| **核心正确率**（排除元信息等） | **{acc_core:.1%}** |",
        f"| 范围外参考题 | {len(oos)} |",
        f"| 应检索题数 | {len(need_ret)} |",
        f"| **未调用检索工具** | **{len(tool_not_called)}** |",
        f"| 未调工具且判错 | {len(tool_not_called_wrong)} |",
        f"| 已检索但判错 | {len(tool_called_wrong)} |",
        f"| 路由 probe 应调未调 | {len(probe_missed)} |",
        f"| **调工具后正确率**（排除未调工具题） | **{acc_adj:.1%}** |",
        "",
    ])

    if ok_items:
        avg_score = sum(i.get("score", 0) for i in ok_items) / len(ok_items)
        avg_time = sum(i.get("elapsed_sec", 0) for i in ok_items) / len(ok_items)
        lines.extend(
            [
                f"| 平均得分 | {avg_score:.3f} |",
                f"| 平均耗时 | {avg_time:.1f}s |",
                "",
            ]
        )

    lines.extend(["## 分场景正确率", "", "| 场景 | 题数 | 正确 | 正确率 | 均分 |", "|------|------|------|--------|------|"])
    cat_rows = []
    for cat, cat_items in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        c_ok = sum(1 for i in cat_items if i.get("correct"))
        c_acc = c_ok / len(cat_items) if cat_items else 0
        c_avg = sum(i.get("score", 0) for i in cat_items) / len(cat_items)
        cat_rows.append((cat, len(cat_items), c_ok, c_acc, c_avg))
        lines.append(f"| {cat} | {len(cat_items)} | {c_ok} | {c_acc:.1%} | {c_avg:.3f} |")

    err_types = Counter(i.get("error_type") or "unknown" for i in wrong)
    lines.extend(["", "## 错误类型分布（判错题目）", ""])
    for et, cnt in err_types.most_common():
        lines.append(f"- `{et}`：{cnt} 题")

    lines.extend(["", "## 工具/检索调用统计", ""])
    lines.append(f"- 应走 Neo4j/Chroma 检索：**{len(need_ret)}** 题")
    lines.append(f"- 实际执行检索：**{sum(1 for i in ok_items if i.get('retrieval_executed'))}** 题")
    lines.append(f"- **应检索但未调用工具**：**{len(tool_not_called)}** 题（其中判错 {len(tool_not_called_wrong)} 题）")
    if tool_not_called:
        tnc_reasons = Counter(i.get("tool_not_called_reason") or "unknown" for i in tool_not_called)
        lines.append("- 未调工具原因：")
        for r, n in tnc_reasons.most_common():
            lines.append(f"  - `{r}`：{n} 题")
    lines.append(f"- 已调用检索但判错：**{len(tool_called_wrong)}** 题（检索命中或合成问题）")
    lines.append(f"- 路由 probe 应调未调：**{len(probe_missed)}** 题")
    lines.append("")

    lines.extend(["", "## 未调工具导致失败（明细）", ""])
    shown_tnc = 0
    for it in tool_not_called_wrong:
        if shown_tnc >= 25:
            lines.append(f"… 另有 {len(tool_not_called_wrong) - shown_tnc} 题，见 results JSON")
            break
        shown_tnc += 1
        lines.append(f"### {shown_tnc}. [{it.get('category', '')}] `{it.get('tool_not_called_reason', '')}`")
        lines.append(f"**问：** {it.get('question', '')}")
        lines.append(f"**策略：** {it.get('answer_strategy') or 'direct_llm'} | business_id={it.get('business_id') or '(空)'}")
        lines.append(f"**实际：** {(it.get('actual') or '')[:300]}…")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.extend(["", "## 主要问题分析", ""])
    low_cats = [r for r in cat_rows if r[3] < 0.5 and r[1] >= 3]
    if low_cats:
        lines.append("### 低正确率场景（<50%）")
        for cat, n, c_ok, c_acc, _ in sorted(low_cats, key=lambda x: x[3]):
            lines.append(f"- **{cat}**：{c_acc:.1%}（{c_ok}/{n}）— 建议检查路由 Playbook 或检索参数")
        lines.append("")

    no_ret = sum(1 for i in wrong if i.get("error_type") == "no_retrieval")
    miss = sum(1 for i in wrong if i.get("error_type") == "missing_entities")
    if no_ret:
        lines.append(f"- **检索未命中**（{no_ret} 题）：路由关键词或 Neo4j/Chroma 召回需优化")
    if miss:
        lines.append(f"- **实体遗漏**（{miss} 题）：回答未覆盖期望清单中的关键项，直出 formatter 或合成 prompt 需加强")
    if tool_not_called_wrong:
        lines.append(
            f"- **未调用检索工具导致判错**（{len(tool_not_called_wrong)} 题）："
            "路由 LLM 走了 direct_llm 或未解析 business_id，应加强 Playbook 命中或路由提示词"
        )
    if probe_missed:
        lines.append(f"- **路由 probe 未调用**（{len(probe_missed)} 题）：实体消歧不足，关键词探测工具应触发")
    if errors:
        lines.append(f"- **流水线异常**（{len(errors)} 题）：检查 LLM 连通性、超时设置")
    lines.append("")

    lines.extend(["## 错误样例（前 30 条）", ""])
    shown = 0
    for it in wrong:
        if shown >= 30:
            break
        shown += 1
        lines.append(f"### {shown}. [{it.get('category', '')}] score={it.get('score', 0):.2f}")
        lines.append(f"**问：** {it.get('question', '')}")
        lines.append("")
        lines.append(f"**期望：** {(it.get('expected') or '')[:400]}{'…' if len(it.get('expected') or '') > 400 else ''}")
        lines.append("")
        lines.append(f"**实际：** {(it.get('actual') or '')[:400]}{'…' if len(it.get('actual') or '') > 400 else ''}")
        lines.append("")
        if it.get("missing_items"):
            lines.append(f"**遗漏项：** {', '.join(it['missing_items'][:8])}")
            lines.append("")
        if it.get("error_type"):
            lines.append(f"**错误类型：** `{it['error_type']}`")
            lines.append("")
        if it.get("tool_not_called"):
            lines.append(
                f"**工具：** 未调用检索 | 原因=`{it.get('tool_not_called_reason')}` | "
                f"strategy={it.get('answer_strategy')} business_id={it.get('business_id') or '空'}"
            )
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.extend(["## 总结", ""])
    if acc >= 0.8:
        lines.append("整体表现**良好**，多数场景检索与回答与期望一致。")
    elif acc >= 0.6:
        lines.append("整体表现**中等**，图库直出类较好，合成类与文档类仍有提升空间。")
    else:
        lines.append("整体表现**待改进**，建议优先排查路由准确率与 Neo4j 关系查询。")

    best = sorted(cat_rows, key=lambda x: -x[3])[:3]
    worst = sorted(cat_rows, key=lambda x: x[3])[:3]
    if best:
        lines.append("")
        lines.append("**表现最好：** " + "；".join(f"{c[0]}({c[3]:.0%})" for c in best))
    if worst:
        lines.append("**待加强：** " + "；".join(f"{c[0]}({c[3]:.0%})" for c in worst))

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="ZNWD1 500 题基准测试")
    parser.add_argument("--qa-file", default=QA_FILE)
    parser.add_argument("--limit", type=int, default=0, help="仅测前 N 题（0=全部）")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--mode", choices=("pipeline", "http"), default="pipeline")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--delay", type=float, default=0.5, help="题间间隔秒")
    parser.add_argument("--checkpoint", type=int, default=5, help="每 N 题保存")
    parser.add_argument("--resume", action="store_true", help="跳过已完成题目")
    parser.add_argument("--chroma-mode", choices=("sqlite", "client", "none"), default="sqlite")
    parser.add_argument("--llm-model", default="", help="LLM 模型名，如 qwen2.5-14b-awq")
    parser.add_argument("--llm-base-url", default="", help="OpenAI 兼容 API，如 http://192.168.15.101:8006/v1")
    parser.add_argument("--llm-api-key", default="", help="API Key，本地 vLLM 可留空或 EMPTY")
    parser.add_argument("--output-tag", default="", help="结果文件名后缀，如 qwen25_14b_awq")
    parser.add_argument("--report-only", action="store_true", help="仅从已有 results 生成报告")
    args = parser.parse_args()

    _set_output_paths(args.output_tag)

    if args.report_only:
        path = _OUTPUT.get("results") or RESULTS_FILE
        if not os.path.isfile(path):
            print(f"未找到 {path}")
            sys.exit(1)
        with open(path, encoding="utf-8") as f:
            results = json.load(f)
    else:
        results = run_benchmark(args)

    report = build_report(results)
    report_path = _OUTPUT.get("report") or REPORT_FILE
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    items = results.get("items") or []
    ok = [i for i in items if i.get("status") == "ok"]
    correct = sum(1 for i in ok if i.get("correct"))
    acc = correct / len(ok) if ok else 0
    tnc = sum(1 for i in ok if i.get("tool_not_called"))
    tnc_wrong = sum(1 for i in ok if i.get("failure_due_to_no_tool"))
    print(f"\n完成：{len(items)} 题，正确率 {acc:.1%}（{correct}/{len(ok)}）")
    print(f"未调工具：{tnc} 题（其中判错 {tnc_wrong} 题）")
    print(f"明细：{_OUTPUT.get('results')}")
    print(f"报告：{report_path}")


if __name__ == "__main__":
    main()
