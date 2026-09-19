"""
调试 GetTestMethod：使用已有 OCR 缓存 extracted_text.txt，打印每轮 LLM 原始输出与解析后结构。

无需上传文件。在 backend 目录下执行：
  python blueprints/FLGL/test_llm_extract_debug.py

可选环境变量（与 .env 一致）：OPENAI_*、FLGL_TESTMETHOD_*。
  FLGL_DEBUG_LLM_MOCK=1：强制占位 JSON，不调 LLM（与密钥是否为空无关）。
  OPENAI_API_KEY 为空时：自动走占位 JSON，不报错（仍需 OPENAI_MODEL 用于日志展示）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

# backend 根目录（本文件位于 backend/blueprints/FLGL/）
BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(BACKEND_ROOT, ".."))
_FLGL_DIR = os.path.abspath(os.path.join(BACKEND_ROOT, "blueprints", "FLGL"))
_GTM_ROOT = os.path.join(_FLGL_DIR, "GetTestMethod")

if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)
if _FLGL_DIR not in sys.path:
    sys.path.insert(0, _FLGL_DIR)
if _GTM_ROOT not in sys.path:
    sys.path.insert(0, _GTM_ROOT)

# 默认 OCR 文本（与 pdf 同名 stem 时走缓存）
DEFAULT_CACHE_TXT = os.path.join(
    BACKEND_ROOT,
    "output_json",
    "ocr_tmp",
    "ocr_GBT38924.7-2020",
    "extracted_text.txt",
)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.isfile(env_path):
        load_dotenv(env_path)


def _inject_gettestmethod_config() -> bool:
    """与 method_extract 一致：挂 GetTestMethod 的 backend.config 并注入主 Config。
    返回 True 表示使用占位 LLM（不调外部接口）。"""
    from config import Config as MainConfig

    path = os.path.join(_GTM_ROOT, "backend", "config.py")
    spec = importlib.util.spec_from_file_location("backend.config", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backend.config"] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    C = mod.Config

    # 仅用当前 os.environ：避免 MainConfig 类属性与「刚加载的 .env」不一致；密钥为空即走 MOCK
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL") or getattr(
        MainConfig, "OPENAI_BASE_URL", None
    )
    model = (os.environ.get("FLGL_TESTMETHOD_OPENAI_MODEL") or "").strip()
    if not model:
        model = (os.environ.get("OPENAI_MODEL") or "").strip()
    if not model:
        model = (getattr(MainConfig, "OPENAI_MODEL", None) or "").strip()
    if not model:
        raise SystemExit("缺少 OPENAI_MODEL（或 FLGL_TESTMETHOD_OPENAI_MODEL）")

    mock_env = os.environ.get("FLGL_DEBUG_LLM_MOCK", "").strip().lower()
    force_mock = mock_env in ("1", "true", "yes", "on")
    use_mock = force_mock or not api_key

    if use_mock:
        C.OPENAI_CLIENT = None
        if not api_key:
            print(
                "[提示] OPENAI_API_KEY 为空：使用占位 JSON 跑通解析与合并逻辑，"
                "不会请求外部 LLM。真实抽取请配置密钥或取消 FLGL_DEBUG_LLM_MOCK。"
            )
        else:
            print("[提示] FLGL_DEBUG_LLM_MOCK 已开启：使用占位 JSON，不请求外部 LLM。")
    else:
        from openai import OpenAI

        C.OPENAI_CLIENT = OpenAI(api_key=api_key, base_url=base_url)

    C.OPENAI_MODEL = model

    def _ci(attr: str, env: str, default: int) -> int:
        ev = os.environ.get(env)
        if ev is not None and str(ev).strip():
            try:
                return int(ev)
            except ValueError:
                pass
        v = getattr(MainConfig, attr, None)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
        return default

    C.LLM_MAX_COMPLETION_TOKENS = _ci(
        "FLGL_TESTMETHOD_LLM_MAX_COMPLETION_TOKENS",
        "FLGL_TESTMETHOD_LLM_MAX_COMPLETION_TOKENS",
        3072,
    )
    C.LLM_CONTEXT_TOKENS = _ci(
        "FLGL_TESTMETHOD_LLM_CONTEXT_TOKENS",
        "FLGL_TESTMETHOD_LLM_CONTEXT_TOKENS",
        8192,
    )
    C.DISCOVERY_MAX_PROMPT_CHARS = _ci(
        "FLGL_TESTMETHOD_DISCOVERY_MAX_PROMPT_CHARS",
        "FLGL_TESTMETHOD_DISCOVERY_MAX_PROMPT_CHARS",
        11000,
    )
    C.DISCOVERY_CATALOGUE_MAX_CHARS = _ci(
        "FLGL_TESTMETHOD_DISCOVERY_CATALOGUE_MAX_CHARS",
        "FLGL_TESTMETHOD_DISCOVERY_CATALOGUE_MAX_CHARS",
        5000,
    )
    C.DISCOVERY_PREVIEW_MAX_CHARS = _ci(
        "FLGL_TESTMETHOD_DISCOVERY_PREVIEW_MAX_CHARS",
        "FLGL_TESTMETHOD_DISCOVERY_PREVIEW_MAX_CHARS",
        6000,
    )
    C.DETAIL_MAX_PROMPT_CHARS = _ci(
        "FLGL_TESTMETHOD_DETAIL_MAX_PROMPT_CHARS",
        "FLGL_TESTMETHOD_DETAIL_MAX_PROMPT_CHARS",
        10000,
    )
    C.DETAIL_SECTION_MAX_CHARS = _ci(
        "FLGL_TESTMETHOD_DETAIL_SECTION_MAX_CHARS",
        "FLGL_TESTMETHOD_DETAIL_SECTION_MAX_CHARS",
        2000,
    )
    C.DETAIL_CHUNK_BUDGET_CHARS = _ci(
        "FLGL_TESTMETHOD_DETAIL_CHUNK_BUDGET_CHARS",
        "FLGL_TESTMETHOD_DETAIL_CHUNK_BUDGET_CHARS",
        8000,
    )
    C.DETAIL_TABLE_HTML_MAX_CHARS = _ci(
        "FLGL_TESTMETHOD_DETAIL_TABLE_HTML_MAX_CHARS",
        "FLGL_TESTMETHOD_DETAIL_TABLE_HTML_MAX_CHARS",
        1200,
    )
    C.DETAIL_MAX_TABLES = _ci(
        "FLGL_TESTMETHOD_DETAIL_MAX_TABLES",
        "FLGL_TESTMETHOD_DETAIL_MAX_TABLES",
        40,
    )
    return use_mock


def _mock_discovery_json() -> str:
    """发现阶段占位：候选为空时 extractor 会用章节标题兜底生成候选。"""
    return json.dumps(
        {
            "document_class": "未判定",
            "document_summary": "[MOCK] 无 API Key / FLGL_DEBUG_LLM_MOCK，占位发现结果",
            "terms": [],
            "method_candidates": [],
        },
        ensure_ascii=False,
    )


def _patch_verbose_llm(extractor_mod, *, mock_llm: bool) -> None:
    _call_no = [0]

    def _verbose(prompt: str, max_tokens=None, temperature: float = 0.1):
        _call_no[0] += 1
        n = _call_no[0]
        mt = extractor_mod._llm_max_completion_tokens(max_tokens)
        model = getattr(extractor_mod.Config, "OPENAI_MODEL", "") or ""
        print(
            f"\n{'='*60}\n[LLM #{n}] model={model} max_tokens={mt} "
            f"prompt_chars={len(prompt)}{' [MOCK]' if mock_llm else ''}\n{'='*60}"
        )
        if mock_llm:
            content = _mock_discovery_json() if n == 1 else "{}"
        else:
            client, model_rt = extractor_mod._get_runtime_client()
            mt = extractor_mod._clamp_completion_tokens(prompt, mt)
            resp = client.chat.completions.create(
                model=model_rt,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=mt,
            )
            content = (resp.choices[0].message.content or "").strip()
        show = 16000
        tail = "" if len(content) <= show else f"\n... 省略 {len(content) - show} 字符"
        print(f"[RAW 共 {len(content)} 字符]\n{content[:show]}{tail}\n")

        data = extractor_mod._safe_json_loads(content, {})
        if isinstance(data, dict):
            print("[解析后顶层键]", list(data.keys()))
            for k in ("name", "description", "candidate_id", "class", "type"):
                if k in data:
                    v = data.get(k)
                    print(f"  {k}: {str(v)[:500]}{'...' if len(str(v)) > 500 else ''}")
            for blk in ("sytj", "sygc", "jgpd"):
                if blk in data and isinstance(data[blk], dict):
                    t = str(data[blk].get("text", ""))[:400]
                    th = data[blk].get("tables_html") or []
                    print(f"  {blk}.text (前400字): {t!r}...")
                    print(f"  {blk}.tables_html 条数: {len(th) if isinstance(th, list) else 'n/a'}")
        else:
            print("[解析失败或非 dict]", type(data))
        return data

    extractor_mod._call_llm_json = _verbose


def main() -> None:
    _load_dotenv()
    cache_txt = os.environ.get("FLGL_DEBUG_EXTRACT_TEXT", DEFAULT_CACHE_TXT)
    cache_txt = os.path.abspath(cache_txt)
    if not os.path.isfile(cache_txt):
        raise SystemExit(f"找不到文本缓存: {cache_txt}")

    stem = os.path.basename(os.path.dirname(cache_txt))  # ocr_GBT38924.7-2020
    if stem.startswith("ocr_"):
        pdf_basename = stem[len("ocr_") :] + ".pdf"
    else:
        pdf_basename = "debug.pdf"
    fake_pdf = os.path.join(os.path.dirname(cache_txt), pdf_basename)
    ocr_dir = os.path.join(BACKEND_ROOT, "output_json", "ocr_tmp")

    print(f"使用缓存文本: {cache_txt}")
    print(f"假 PDF 路径(仅用于解析逻辑): {fake_pdf}")
    print(f"ocr_tmp 根目录: {ocr_dir}")

    mock_llm = _inject_gettestmethod_config()

    from GetTestMethod import extractor as extractor_mod
    from GetTestMethod.parser import parse_standard_file

    _patch_verbose_llm(extractor_mod, mock_llm=mock_llm)

    parsed_doc = parse_standard_file(file_path=fake_pdf, ocr_output_dir=ocr_dir)
    print(
        f"\n[解析结果] sections={len(parsed_doc.get('sections', []))} "
        f"text_len={len(parsed_doc.get('full_text', '') or '')}"
    )

    out = extractor_mod.extract_document_methods(parsed_doc=parsed_doc, max_methods=None)
    methods = out.get("methods") or []
    print(f"\n[最终 methods 数量] {len(methods)}")
    for i, m in enumerate(methods, 1):
        print(f"\n--- 方法 {i}: {m.get('name', '')} ---")
        print("description:", (m.get("description") or "")[:300], "...")
        for blk in ("sytj", "sygc", "jgpd"):
            b = m.get(blk) or {}
            if isinstance(b, dict):
                tx = (b.get("text") or "")[:500]
                print(f"{blk}.text (500字): {tx!r}")
                print(f"{blk}.tables_html: {len(b.get('tables_html') or [])} 条")
            else:
                print(f"{blk}: {b!r}")


if __name__ == "__main__":
    main()
