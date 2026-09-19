"""WJGL 自动解析 LLM 诊断（避免 review_routes ↔ routes 循环导入）。"""
from __future__ import annotations

import os

from config import Config


def wjgl_llm_status() -> dict:
    """供 auto-parse / auto-parse-text 诊断 LLM 是否可用。"""
    key_set = bool((Config.OPENAI_API_KEY or "").strip())
    eff_key = Config.effective_openai_api_key()
    client_ok = Config.OPENAI_CLIENT is not None
    local_no_auth = eff_key == "EMPTY"
    base_url = (Config.OPENAI_BASE_URL or "").strip()
    model = (Config.OPENAI_MODEL or "").strip()

    llm_reachable = False
    llm_ping_error = ""
    if base_url:
        try:
            import httpx

            ping_url = base_url.rstrip("/") + "/models"
            trust_env = os.environ.get("OPENAI_HTTP_TRUST_ENV", "").strip().lower() in (
                "1", "true", "yes", "on",
            )
            if Config.is_local_openai_base_url(base_url):
                trust_env = False
            resp = httpx.get(ping_url, timeout=5.0, trust_env=trust_env)
            llm_reachable = resp.status_code < 500
            if not llm_reachable:
                llm_ping_error = f"HTTP {resp.status_code}"
        except Exception as e:
            llm_ping_error = str(e)

    if not client_ok:
        hint = (
            "未配置 LLM：云端填 OPENAI_API_KEY；本地 vLLM 设 OPENAI_ALLOW_EMPTY_KEY=1 "
            "且 OPENAI_BASE_URL=http://127.0.0.1:8000/v1（或 OPENAI_API_KEY=EMPTY），然后重启 Flask。"
            if not eff_key
            else "OPENAI_CLIENT 初始化失败，请查看后端启动日志。"
        )
    elif not llm_reachable:
        hint = (
            f"LLM 客户端已配置，但无法连接 {base_url}（{llm_ping_error or '连接失败'}）。"
            "请确认 vLLM/Ollama 已启动、端口正确，且 Flask 所在机器能访问该地址。"
        )
    else:
        hint = (
            "LLM 就绪，可正常自动解析摘要/关键词。"
            + ("（本地无鉴权模式）" if local_no_auth else "")
        )

    return {
        "openai_client_ready": client_ok,
        "openai_api_key_set": key_set,
        "openai_local_no_auth": local_no_auth,
        "openai_allow_empty_key": bool(getattr(Config, "OPENAI_ALLOW_EMPTY_KEY", False)),
        "openai_auto_local_no_auth": (
            not eff_key and Config.is_local_openai_base_url()
        ),
        "openai_base_url": base_url,
        "openai_model": model,
        "llm_reachable": llm_reachable,
        "llm_ping_error": llm_ping_error,
        "hint": hint,
    }
